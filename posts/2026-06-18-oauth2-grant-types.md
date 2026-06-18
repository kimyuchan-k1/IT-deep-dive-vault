---
title: SPA엔 client_secret을 숨길 곳이 없어서 Implicit grant를 썼는데, 액세스 토큰이 브라우저 히스토리에 박혀 유출됐다
date: 2026-06-18
day: 16
category: security
tags: [oauth2, authorization-code, pkce, implicit, client-credentials, authentication]
related: ["[[jwt-vs-session]]", "[[rbac-abac-rebac]]", "[[mtls-zero-trust]]", "[[csrf-samesite]]", "[[rate-limit-token-bucket]]", "[[secret-management]]"]
difficulty: 3
short_text: |
  ⚠️ [Day 16] SPA에 Implicit 쓰니 토큰 URL 유출
  오해: grant는 취향
  실제: secret숨기나×사용자있나
  "히스토리 유출→PKCE로 교체"
  📖 https://github.com/kimyuchan-k1/IT-deep-dive-vault/blob/main/posts/2026-06-18-oauth2-grant-types.md
---

# SPA엔 client_secret을 숨길 곳이 없어서 Implicit grant를 썼는데, 액세스 토큰이 브라우저 히스토리에 박혀 유출됐다

## 흔한 오해

"OAuth2 grant type? 그건 그냥 '로그인을 어떻게 시작하느냐'의 취향 차이 아닌가. Authorization Code, Implicit, Password, Client Credentials 네 개가 있고, 서버 앱이면 Code 쓰고, SPA처럼 `client_secret`을 숨길 데가 없으면 토큰을 바로 돌려주는 Implicit을 쓰면 되지. 결국 다 액세스 토큰 받아오는 거라 뭘 써도 똑같은 거 아닌가."

OAuth를 처음 붙이면 거의 다 이렇게 생각한다. RFC 6749가 네 가지 grant를 나열해 놓으니, 입문 자료도 "환경에 맞는 걸 고르세요" 정도로 가르친다. 그래서 "secret 못 숨기는 프런트엔드 = Implicit"이라는 공식이 한동안 정설처럼 돌았다.

**grant type은 취향이 아니라 보안 모델이다.** 무엇으로 갈리느냐 — 두 축이다. ① 클라이언트가 `client_secret`을 **안전하게 보관할 수 있는가**(confidential vs public), ② **사용자 본인이 그 자리에 있는가**(위임 vs 기계 대 기계). Implicit은 이 중 ①을 잘못 푼 grant라서, 2020년 OAuth 2.0 Security BCP와 OAuth 2.1 초안에서 **공식적으로 폐기**됐다.

## 실제 원리

OAuth2의 본질은 "비밀번호를 넘기지 않고 **권한을 위임**한다"는 것이다. 핵심은 누가 어떤 자격증명을 들고, 토큰을 **어느 채널로** 받느냐다.

### 두 축으로 grant를 가른다

- **confidential client**: 서버 사이드 앱. `client_secret`을 백엔드에 숨길 수 있다.
- **public client**: SPA, 모바일 앱, CLI. 코드가 사용자 기기/브라우저에 통째로 내려가니 **어떤 비밀도 숨길 수 없다.**
- **사용자 있음**: 사람이 "이 앱에 권한 줄게"를 누른다 → Authorization Code 계열.
- **사용자 없음**: 서비스가 서비스를 호출한다(크론, 백엔드 간 API) → Client Credentials.

### Authorization Code — 토큰을 프런트 채널로 흘리지 않는다

여기가 핵심이다. Authorization Code는 토큰을 **두 단계**로 받는다. ① 브라우저(프런트 채널)로는 **임시 코드(authorization code)**만 받는다. ② 그 코드를 백엔드가 `client_secret`과 함께 **백 채널(서버→서버)**로 토큰 엔드포인트에 보내 진짜 토큰으로 교환한다.

왜 굳이 두 단계인가. **브라우저 URL은 새는 채널**이기 때문이다. 주소창, 브라우저 히스토리, `Referer` 헤더, 프록시 로그, 액세스 로그 — URL에 실린 값은 사방에 복사된다. 그래서 진짜 토큰은 URL에 절대 안 싣고, **한 번 쓰면 폐기되는 코드**만 URL로 보낸다. 코드가 새도 `client_secret`이 없으면 토큰으로 못 바꾼다.

### Implicit — 그 핵심을 포기한 grant

Implicit은 ②를 생략한다. 토큰을 **곧바로** 리다이렉트 URL의 `#fragment`로 돌려준다. "SPA는 secret이 없으니 코드 교환을 못 하잖아"라는 논리였다. 결과는 **액세스 토큰이 브라우저 히스토리·`Referer`·로그에 그대로 박힌다.** 게다가 fragment의 토큰은 폐기 불가능한데다, 토큰을 발급해 준 상대가 정당한 클라이언트인지 증명할 수단도 없다. 그래서 폐기됐다.

### PKCE — public client가 코드 교환을 안전하게 하는 법

그럼 secret 없는 SPA는 어떻게 Authorization Code를 쓰나. **PKCE(Proof Key for Code Exchange, RFC 7636)**가 답이다. 클라이언트가 매 요청마다 랜덤 `code_verifier`를 만들고, 그 **해시(`code_challenge` = SHA-256)**를 인가 요청에 같이 보낸다. 나중에 코드를 토큰으로 교환할 때 원본 `code_verifier`를 제시한다. 인가 서버는 `SHA256(verifier) == challenge`를 검증한다.

핵심은 이거다 — **탈취당한 코드는 `code_verifier` 없이는 쓸모가 없다.** 공격자가 리다이렉트로 코드를 가로채도, 그 코드와 짝인 verifier(클라이언트 메모리에만 있음)를 모르니 교환이 막힌다. `client_secret`이 하던 "이 코드 교환은 정당한 클라이언트가 한다"는 증명을, **동적으로 생성한 1회용 비밀**이 대신한다. 그래서 OAuth 2.1은 **모든 클라이언트(confidential 포함)에 PKCE를 사실상 의무화**했다.

### Client Credentials / Device Code — 사용자가 없거나, 입력이 없을 때

- **Client Credentials**: 사용자 위임이 아예 없다. 클라이언트가 **자기 자신의 권한**으로 `client_id` + `client_secret`을 토큰 엔드포인트에 보내 토큰을 받는다. 백엔드 간 통신, 크론 잡 전용. 사람을 끼우면 안 된다.
- **Resource Owner Password(ROPC)**: 앱이 사용자의 **아이디·비밀번호를 직접 받아** 토큰으로 바꾼다. OAuth가 없애려던 바로 그 안티패턴이라, 2.1에서 폐기됐다.
- **Device Code**: TV·CLI처럼 키보드가 빈약한 기기용. 기기는 코드를 띄우고, 사용자는 **폰으로** 그 코드를 입력해 인가한다.

## 현장 시나리오

한 사내 분석 대시보드(React SPA)가 OAuth로 사외 데이터 API에 붙었다. 2019년 설계라 "SPA는 secret이 없으니 Implicit"이라는 당시 공식을 따랐다. 인과 사슬은 이랬다:

- 로그인 성공 후 액세스 토큰이 `https://dash.example.com/callback#access_token=ya29....`처럼 **fragment로 떨어졌다.** 동작은 멀쩡했다
- 대시보드가 외부 차트 위젯(서드파티 CDN의 `<img>`, 임베드 iframe)을 불렀는데, 콜백 페이지에서 외부로 나가는 요청의 **`Referer` 헤더에 fragment까지 실려 나갔다.** 토큰이 서드파티 로그에 적재됐다
- 동시에 그 토큰들은 사용자들 **브라우저 히스토리**에 그대로 남았다. 공용 PC 한 대에서 히스토리를 뒤지자 살아있는 토큰이 나왔다
- Implicit 토큰엔 **refresh가 없어** 수명을 1시간으로 길게 잡아뒀던 게 화를 키웠다. 유출 토큰이 한 시간씩 유효했고, **폐기할 방법도 없었다**
- 보안팀이 발견했을 땐 이미 외부 위젯 제공자 로그에 수천 건의 토큰이 쌓여 있었다

수정은 **grant를 Authorization Code + PKCE로 교체**하는 것이었다. 토큰은 더 이상 URL에 실리지 않고 백 채널 교환으로만 받았다. verifier 검증이 코드 탈취를 막았고, 짧은 액세스 토큰 + refresh 토큰 회전으로 유출 시 폭발 반경을 줄였다. 원인은 데이터 유출이 아니라, **"secret을 못 숨기니 토큰을 프런트로 직접 받자"는 grant 선택 그 자체**였다. 못 숨기는 건 PKCE로 푸는 문제지, 안전한 채널을 포기할 이유가 아니었다.

## 실무 적용 포인트

1. **새 프로젝트면 무조건 Authorization Code + PKCE**: SPA·모바일·서버 앱 **전부**. OAuth 2.1이 Implicit과 ROPC를 폐기했으니 신규 설계에서 둘은 선택지가 아니다. `code_challenge_method=S256`(plain 말고 SHA-256)을 명시하라.
2. **public client에 `client_secret`을 박지 마라**: SPA 번들이나 모바일 앱 바이너리의 secret은 **공개된 것과 같다.** 디컴파일·소스 보기로 5분이면 추출된다. public client는 secret 없이 PKCE로 신원을 증명한다.
3. **`redirect_uri`는 정확 일치(exact match)로 화이트리스트**: 와일드카드·접두 매칭은 오픈 리다이렉트로 코드 탈취를 부른다. 등록된 URI와 **문자 단위로 일치**할 때만 허용하라.
4. **`state` 파라미터로 CSRF를 막아라**: 인가 요청에 추측 불가한 `state`를 실어 콜백에서 대조한다. 이게 빠지면 인가 코드 주입 공격이 열린다([[csrf-samesite]]의 그 CSRF다).
5. **Client Credentials엔 사용자를 끼우지 마라**: 사람의 권한이 필요하면 Authorization Code다. Client Credentials는 **서비스 자신의 권한** 전용 — 여기에 사용자 컨텍스트를 욱여넣으면 감사·취소가 깨진다.
6. **액세스 토큰은 짧게, refresh는 회전시켜라**: 액세스 토큰 수명은 **5~15분** 수준으로 짧게 두고, refresh 토큰은 **1회용으로 회전(rotation)**시켜 재사용을 탐지하라. 유출돼도 폭발 반경이 분 단위로 줄어든다.

## 더 깊은 토끼굴

- IETF 공식 — [RFC 6749: The OAuth 2.0 Authorization Framework](https://datatracker.ietf.org/doc/html/rfc6749): 네 가지 grant의 1차 정의
- IETF 공식 — [RFC 7636: Proof Key for Code Exchange (PKCE)](https://datatracker.ietf.org/doc/html/rfc7636): `code_verifier`/`code_challenge`의 출처
- IETF 공식 — [OAuth 2.0 Security Best Current Practice (RFC 9700)](https://datatracker.ietf.org/doc/html/rfc9700): Implicit·ROPC 폐기와 PKCE 의무화의 근거
- [[jwt-vs-session]]: 발급받은 토큰을 무엇에 담아 검증하나 — OAuth 다음 단계
- [[rbac-abac-rebac]]: 인증으로 받은 신원에 **무슨 권한**을 줄지(인가)는 별개 문제
- [[mtls-zero-trust]]: 사용자 위임이 아니라 서비스 신원으로 신뢰를 다루는 확장
