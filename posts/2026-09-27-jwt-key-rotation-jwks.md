---
title: 키 하나 갈아끼웠다. 전 서비스가 401을 뱉었고 되돌릴 수도 없었다
date: 2026-09-27
day: 98
category: security
tags: [jwt, jwks, key-rotation, oidc, rfc7517]
related: ["[[jwt-vs-session]]", "[[oauth2-grant-types]]", "[[secret-management]]", "[[cache-stampede]]", "[[mtls-zero-trust]]"]
difficulty: 3
short_text: |
  🔥 [Day 98] 키 하나 갈아끼우고 전 서비스 401

  오해: 새 키로 바꾸면 끝
  실제: 구 kid 삭제→검증 실패→JWKS 재조회 폭주→429

  📖 https://github.com/kimyuchan-k1/IT-deep-dive-vault/blob/main/posts/2026-09-27-jwt-key-rotation-jwks.md
---

# 키 하나 갈아끼웠다. 전 서비스가 401을 뱉었고 되돌릴 수도 없었다

## 흔한 오해

> "JWT 서명 키 교체는 배포랑 비슷한 거 아닌가? 새 키 만들고, JWKS에 올리고, 서명을 새 키로 바꾸면 끝. 검증자는 `jwks_uri`를 걸어놨으니 알아서 새 키를 가져간다."

키 교체를 **배포**로 상상하기 때문에 이렇게 생각한다. 배포는 새 버전이 뜨는 순간 구 버전이 필요 없어진다. 키는 다르다. 이미 발급된 토큰은 구 키로 서명된 채로 세상에 나가 있고, 그 토큰들은 자기 `exp`까지 유효하다고 주장한다.

즉 키 교체에는 **구 키와 신 키가 동시에 유효한 구간**이 반드시 존재한다. 이 구간을 0으로 잡으면 이미 나간 토큰을 전부 무효화하는 것과 같다.

## 실제 원리

### 1. 검증자는 `kid`로 키를 찾는다

JWS 헤더의 `kid`(Key ID, RFC 7515 §4.1.4)는 "이 서명을 검증할 키의 이름"이다. 검증자는 `jwks_uri`에서 받은 JWK Set(RFC 7517) — `keys` 배열 — 에서 같은 `kid`를 가진 항목을 찾아 공개키를 꺼낸다.

```json
{"keys": [
  {"kty":"RSA","kid":"2026-06-a","alg":"RS256","use":"sig","n":"...","e":"AQAB"},
  {"kty":"RSA","kid":"2026-09-b","alg":"RS256","use":"sig","n":"...","e":"AQAB"}
]}
```

여기서 핵심은 `keys`가 **집합**이라는 점이다. 배열에 두 개를 동시에 두는 게 예외 상황이 아니라 정상 상태다. OIDC Core 10.1.1은 회전 절차를 명시한다. 새 키를 먼저 JWKS에 **공개**하고, 검증자들이 가져갈 시간을 준 뒤에, 그때부터 새 키로 **서명**하기 시작한다. 공개와 사용 사이에 시간 간격이 있다.

### 2. 진짜 상태 기계는 키 하나에 4단계다

키 하나의 생애는 이렇게 흐른다.

```
생성 → 공개(JWKS에 있지만 서명 안 함) → 활성(서명 중) → 은퇴(서명 안 하지만 JWKS에 남음) → 제거
         ↑ 검증자 캐시 전파 대기                          ↑ 이미 나간 토큰의 exp 대기
```

두 대기 구간의 길이가 다른 값에서 나온다. 앞의 것은 **검증자 캐시 TTL**, 뒤의 것은 **access token 최대 수명**이다. 이걸 같은 값으로 잡거나 둘 다 생략하면 한쪽에서 터진다.

### 3. 비용이 실제로 붙는 곳은 검증자 캐시다

검증자가 요청마다 JWKS를 가져오면 IdP가 인증 트래픽 전체를 정면으로 받는다. 그래서 라이브러리는 전부 캐시한다. 그리고 전부 같은 탈출구를 갖는다. **캐시에 없는 `kid`를 보면 JWKS를 다시 가져온다.**

이 탈출구가 회전 시점에 정확히 문제가 된다. 서명 키가 바뀌는 순간, 캐시를 들고 있는 모든 프로세스가 동시에 미지의 `kid`를 만난다. 재조회에 쿨다운도 단일 인플라이트 제어도 없으면 수백 개 프로세스가 같은 엔드포인트를 동시에 때린다. [[cache-stampede]]와 구조가 같고, 차이는 뒤에 있는 게 DB가 아니라 인증 시스템 전체라는 점이다.

반대로 쿨다운을 길게 잡으면 신 키 토큰이 그 시간만큼 통째로 401이 된다. 어느 쪽으로 틀어도 대가가 있다.

### 4. `alg`는 토큰이 아니라 서버가 정한다

`kid`로 키를 찾는 코드를 쓰다 보면 헤더의 `alg`도 따라 신뢰하게 된다. 이건 별개의 함정이다. `alg`를 토큰에서 읽어 그대로 쓰면 `alg: none`이나 RS256→HS256 혼동 공격(공개키를 HMAC 비밀키로 쓰게 만드는)이 열린다. RFC 8725는 검증자가 허용 알고리즘을 **미리 고정**하라고 요구한다. 키 회전 작업 중에 검증 코드를 만지게 되므로, 같이 점검할 자리다.

## 현장 시나리오

사내 IdP 1대, 뒤에 API 게이트웨이 20대와 마이크로서비스 60개. RS256, access token 수명 15분, 각 서비스의 JWKS 캐시 TTL 1시간. 컴플라이언스 요구로 90일 주기 키 교체를 스크립트로 자동화했다.

스크립트는 두 줄이었다. 새 키쌍 생성, `jwks.json`을 새 키 **하나만** 담아 덮어쓰기. 그리고 서명 키를 즉시 전환.

```
JWKS를 append가 아니라 replace
  → 유통 중인 토큰 약 40만 건의 kid가 JWKS에서 사라짐
  → 검증자: kid 못 찾음 → 401
  → 동시에 신 키 kid도 미지 → 60개 서비스가 일제히 JWKS 재조회
  → IdP 앞단 rate limit이 429로 막음 (초당 수천 건)
  → 재조회 실패 → 캐시 갱신 실패 → 신 키 토큰도 401
  → 클라이언트가 401을 보고 재로그인 시도 → 토큰 발급 트래픽까지 증폭
  → 복구로 구 공개키를 JWKS에 다시 넣음. 그런데 캐시 TTL 1시간
  → TTL이 이미 갱신된 일부 서비스는 최대 1시간 동안 구 키 토큰 거부 상태 유지
  → 전면 401 8분, 부분 실패 23분
```

필요했던 조치는 순서 변경 두 줄이었다. 신 키를 `keys` 배열에 **추가**만 하고 2시간(캐시 TTL의 2배) 뒤 서명을 전환, 구 공개키는 그날 밤에 제거.

원인 한 줄. 키 회전을 집합에 대한 **추가**가 아니라 값에 대한 **대입**으로 구현했다.

진짜 함정은 롤백이 안 됐다는 점이다. 캐시가 낀 시스템의 회전은 되돌릴 때도 같은 캐시 TTL을 다시 기다려야 한다. 전진과 후퇴의 비용이 같다.

## 실무 적용 포인트

1. **JWKS는 append-only로 다뤄라.** 배포 스크립트에서 `jwks.json`을 덮어쓰는 코드를 금지하고, 항목 추가와 항목 제거를 별개의 작업으로 분리한다. 제거는 최소 하루 뒤 별도 실행.

2. **두 대기 구간을 각각 계산해서 박아라.** 공개→활성 대기 ≥ 검증자 JWKS 캐시 TTL × 2. 은퇴→제거 대기 ≥ access token 최대 수명(`exp − iat`) + 시계 오차 허용치(`leeway` 보통 60초) × 2. 토큰 15분·TTL 1시간이면 각각 2시간, 32분이다. 이 숫자를 runbook에 계산식째로 적는다.

3. **`kid`를 키 내용에서 파생시켜라.** RFC 7638 JWK Thumbprint(JWK 정규형의 SHA-256)를 `kid`로 쓰면 키가 바뀌면 `kid`도 반드시 바뀌고, 같은 키는 어디서 계산해도 같은 `kid`가 나온다. `key-v2` 같은 수동 이름은 재사용 사고가 난다. 검증자는 `kid` 매칭 실패 시 "모든 키로 시도"하지 말고 그냥 실패시킨다.

4. **검증자 캐시에 세 가지를 건다.** 미지 `kid` 재조회 최소 간격(예: 30초), 프로세스당 단일 인플라이트(singleflight), 재조회 실패 시 스테일 캐시 계속 서빙. IdP 쪽은 `jwks_uri`에 `Cache-Control: max-age`와 `ETag`를 주고 CDN을 앞에 둔다. 재조회 실패 재시도는 [[retry-exponential-backoff-jitter]]를 그대로 적용.

5. **허용 알고리즘을 화이트리스트로 고정한다.** 검증 호출에 `algorithms=["RS256","ES256"]`을 명시하고 `none`은 라이브러리 수준에서 차단, `kty`와 `alg`의 짝이 맞는지 확인. `use: "sig"`가 아닌 키는 서명 검증에서 제외.

6. **회전용 지표를 미리 만든다.** 401을 사유별로 쪼갠 카운터(`invalid_kid` / `expired` / `bad_signature`), JWKS fetch의 429·5xx 비율, 서비스별 캐시 항목 나이. `invalid_kid`가 0이 아닌 상태로 회전을 시작하면 안 된다.

7. **취소를 키 회전으로 하지 마라.** 키를 지우면 그 키로 서명된 모든 세션이 동시에 끊긴다. 특정 사용자·클라이언트를 끊는 건 refresh token 폐기와 짧은 access token 수명의 일이다([[jwt-vs-session]]). 키 유출이 확인된 경우만 예외이고, 그때는 전면 로그아웃이 의도된 결과다.

## 더 깊은 토끼굴

- [[jwt-vs-session]] — 무상태 검증을 고른 대가가 취소 불가로 돌아오는 지점. 키 회전이 취소 수단이 될 수 없는 이유.
- [[oauth2-grant-types]] — `jwks_uri`가 어디서 나오는지. Discovery 문서와 grant별 토큰 수명.
- [[secret-management]] — 개인키를 HSM/KMS에 두면 회전 절차가 어떻게 바뀌는가. 서명 위임과 키 버전 관리.
- [[cache-stampede]] — 미지 `kid` 재조회 폭주의 원형. singleflight와 스테일 서빙이 같은 해법인 이유.
- [[mtls-zero-trust]] — 인증서 회전은 같은 겹침 구간 문제를 반대편(클라이언트 신뢰 저장소)에서 푼다.

**1차 출처**
- RFC 7517 — JSON Web Key (JWK), `keys` 배열과 JWK Set: https://datatracker.ietf.org/doc/html/rfc7517
- RFC 7515 — JSON Web Signature, `kid` 헤더 파라미터 §4.1.4: https://datatracker.ietf.org/doc/html/rfc7515#section-4.1.4
- RFC 7638 — JSON Web Key Thumbprint: https://datatracker.ietf.org/doc/html/rfc7638
- RFC 8725 — JWT Best Current Practices (알고리즘 고정, 혼동 공격): https://datatracker.ietf.org/doc/html/rfc8725
- OpenID Connect Core 1.0 §10.1.1 — Rotation of Asymmetric Signing Keys: https://openid.net/specs/openid-connect-core-1_0.html#RotateSigKeys
