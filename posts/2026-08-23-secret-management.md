---
title: 비밀번호를 Vault로 옮겼다. 그런데 유출된 토큰은 6개월 전 그대로였다
date: 2026-08-23
day: 76
category: security
tags: [secret-management, vault, aws-secrets-manager, parameter-store, rotation, dynamic-secrets]
related: ["[[mtls-zero-trust]]", "[[oauth2-grant-types]]", "[[rbac-abac-rebac]]", "[[k8s-pod-death-5-reasons]]", "[[jwt-vs-session]]"]
difficulty: 2
short_text: |
  ⚠️ [Day 76] Vault로 옮겼는데 유출 토큰은 6개월 전 그대로

  오해: 저장소 바꾸면 안전
  실제: 앱이 부팅 때 1회 읽고 캐시→로테이션 무의미→노출 창 그대로

  📖 https://github.com/kimyuchan-k1/IT-deep-dive-vault/blob/main/posts/2026-08-23-secret-management.md
---

# 비밀번호를 Vault로 옮겼다. 그런데 유출된 토큰은 6개월 전 그대로였다

## 흔한 오해

> "시크릿이 `.env`랑 깃 레포에 흩어져 있는 게 문제였다. Vault나 AWS Secrets Manager로 옮기고 암호화 저장하면 해결이다."

이 문장은 절반만 맞다. 그리고 그 절반이 어디까지인지 모르면 도입 비용만 쓰고 위험은 그대로 남는다.

통념이 이렇게 굳은 이유는 명확하다. 사고 기사에 나오는 원인이 거의 항상 "하드코딩된 키가 퍼블릭 레포에 커밋됐다"이기 때문이다. 그래서 대책도 "저장 위치를 옮긴다"로 수렴한다. 마이그레이션이 끝나면 체크리스트에 ✅이 찍히고 그걸로 끝난다.

빠진 게 있다. **시크릿 관리의 목적은 유출 확률을 0으로 만드는 게 아니라, 유출된 뒤 그 시크릿이 살아 있는 시간을 짧게 만드는 것이다.** 저장소만 바꾸면 앞쪽 절반만 건드린 거다.

## 실제 원리

### 정적 시크릿은 옮겨도 여전히 정적이다

Secrets Manager에 넣은 DB 비밀번호를 앱이 어떻게 쓰는지 보면 대부분 이렇다. 부팅 시 SDK로 한 번 `GetSecretValue` 호출 → 문자열을 커넥션 풀 설정에 넣음 → 프로세스가 죽을 때까지 그 값을 메모리에 들고 있음.

이 시점에서 값의 수명은 저장소가 아니라 **프로세스의 수명**이 결정한다. 파드가 3개월째 안 죽었으면 그 비밀번호는 3개월째 메모리에 떠 있다. 어딘가에서 힙 덤프 하나 유출되거나, 로그에 커넥션 스트링 한 줄 찍히거나, 사이드카 컨테이너가 뚫리면 저장소가 Vault든 `.env`든 결과가 같다.

여기가 핵심이다. 저장소 교체는 **최초 배포 경로**의 노출만 줄인다. **런타임 노출 창**은 로테이션이 실제로 프로세스까지 도달해야 줄어든다.

### 로테이션은 저장소가 아니라 소비자가 완성한다

AWS Secrets Manager는 로테이션을 스테이징 레이블로 관리한다. 새 값에 `AWSPENDING`을 붙이고, 검증이 끝나면 `AWSCURRENT`로 승격하고, 직전 값에는 `AWSPREVIOUS`가 남는다. 이 3단계가 존재하는 이유가 바로 위 문제다. 저장소가 값을 바꾼 순간과 모든 소비자가 새 값을 집는 순간 사이에 시차가 있고, 그 시차 동안 두 값이 동시에 유효해야 한다.

그런데 이 유예는 **한 세대**뿐이다. 다음 로테이션이 돌면 `AWSPREVIOUS`는 밀려난다. 앱이 그때까지도 재시작을 안 했다면, 앱이 들고 있는 값은 이제 어디에도 유효하지 않은 문자열이다.

Kubernetes로 넘어오면 함정이 하나 더 있다. Secret을 **볼륨으로 마운트하면** kubelet이 주기적으로 동기화해서 파일 내용이 갱신된다(기본 동기화 주기 1분 + 캐시 지연). 하지만 **환경변수로 주입하면 영원히 안 바뀐다.** 환경변수는 프로세스 생성 시점에 커널로 복사되는 값이라 갱신할 방법 자체가 없다. `envFrom: secretRef`로 편하게 쓰던 그 한 줄이 로테이션을 통째로 무력화한다.

### 동적 시크릿이 뒤집는 지점

Vault의 데이터베이스 시크릿 엔진은 접근 방식이 다르다. 저장된 비밀번호를 꺼내주는 게 아니라, 요청이 올 때마다 DB에 계정을 새로 만들고 lease를 붙여서 발급한다. lease TTL이 지나면 Vault가 그 계정을 DB에서 삭제한다.

이러면 "유출된 크레덴셜의 수명"이 애플리케이션 배포 주기와 완전히 분리된다. TTL을 1시간으로 잡으면 유출돼도 최대 1시간이다. Vault의 기본 `default_lease_ttl`은 768시간(32일)이라 이걸 안 줄이면 동적 시크릿을 써도 이점이 크게 줄어든다.

대신 새 문제가 생긴다. **secret zero 문제**다. Vault에서 시크릿을 받으려면 먼저 Vault에 인증해야 하는데, 그 인증용 자격증명은 또 어디에 두나. 답은 "저장하지 않는다"이다. 워크로드가 이미 갖고 있는 신원을 쓴다 — EC2/EKS면 IAM 역할, 쿠버네티스면 ServiceAccount 토큰. 플랫폼이 서명해준 신원을 Vault가 검증하는 구조라 저장할 비밀이 없다. 이건 [[mtls-zero-trust]]에서 mTLS가 푸는 문제와 정확히 같은 형태다.

## 현장 시나리오

결제팀이 RDS 비밀번호를 Secrets Manager로 옮겼다. 30일 자동 로테이션도 켰다. 로테이션 Lambda는 기본 제공되는 single-user 전략을 골랐다 — 같은 계정의 비밀번호를 그 자리에서 바꾸는 방식이다.

앱은 부팅 시 1회 `GetSecretValue`를 호출해 HikariCP 설정에 넣는 구조였다. 배포는 2주에 한 번.

- **D+30 새벽 3시**: Lambda가 DB 계정 비밀번호를 교체. Secrets Manager의 `AWSCURRENT`가 새 값이 됨.
- 앱은 아무것도 모름. 이미 열려 있던 커넥션 20개는 인증이 끝난 상태라 **계속 정상 동작**. 알람 없음.
- **D+30 오전 9시 40분**: 출근 트래픽으로 풀이 20 → 50으로 확장. 새 커넥션이 옛 비밀번호로 인증 시도.
- `PSQLException: password authentication failed for user "payment_app"` 가 초당 수백 건. 기존 20개 커넥션은 여전히 살아 있어서 요청의 일부만 실패 — **에러율 40%대에서 헬스체크는 통과**.
- 리버스 프록시는 파드를 정상으로 보고 계속 트래픽을 보냄. 롤링 재시작을 하고 나서야 복구. 총 22분.

원인 한 줄: 저장소는 값을 바꿨는데, 값을 쓰는 쪽에는 아무도 알려주지 않았다.

## 실무 적용 포인트

1. **환경변수 주입을 먼저 끊어라.** `envFrom: secretRef` → 볼륨 마운트로 전환. 볼륨은 kubelet이 갱신하지만(기본 sync 주기 1분) 환경변수는 프로세스 수명 동안 절대 안 바뀐다. 더 확실하게 하려면 Secrets Store CSI Driver나 `vault-agent` 사이드카로 파일을 갱신시킨다.
2. **앱이 파일을 다시 읽게 만들어라.** 마운트된 파일이 갱신돼도 앱이 안 읽으면 의미 없다. 파일 watch로 커넥션 풀을 재생성하거나, 최소한 SIGHUP 핸들러를 붙인다. HikariCP는 `HikariDataSource#getHikariConfigMXBean().setPassword()` 후 `softEvictConnections()`로 무중단 교체가 된다.
3. **로테이션은 반드시 dual-user 전략으로.** Secrets Manager 로테이션 Lambda에서 single-user(같은 계정 비밀번호 교체)는 유예가 0이다. `payment_app_a` / `payment_app_b` 두 계정을 번갈아 쓰는 alternating-users 방식이면 최소 한 세대의 유예가 생긴다.
4. **동적 시크릿을 쓸 거면 TTL부터 줄여라.** Vault 기본 `default_lease_ttl`은 768h(32일)다. DB 크레덴셜은 `1h`~`24h`, `max_lease_ttl`은 그 2~3배 정도로 잡는다. 줄이지 않으면 정적 시크릿과 노출 창이 사실상 같다.
5. **헬스체크에 DB 신규 커넥션 획득을 포함시켜라.** 위 시나리오가 22분간 안 잡힌 진짜 이유는 readiness probe가 기존 커넥션만 봤기 때문이다. probe에서 풀 밖으로 새 커넥션을 한 번 열어보게 하면 로테이션 실패가 즉시 파드를 NotReady로 만든다. 관련 함정은 [[k8s-pod-death-5-reasons]]에 더 있다.
6. **Parameter Store와 Secrets Manager를 용도로 나눠라.** 로테이션·크로스계정 공유·바이너리가 필요하면 Secrets Manager(시크릿당 월 $0.40). 단순 설정값이나 로테이션이 필요 없는 값은 Parameter Store Standard(4KB, 무료). 단 Parameter Store는 기본 처리량 한도가 40 TPS라 부팅 시 수백 파드가 동시에 읽으면 스로틀이 난다 — 고성능 처리량 옵션을 켜거나 사이드카에서 캐싱한다.
7. **유출 대응 훈련의 지표를 바꿔라.** "시크릿이 어디 저장돼 있나"가 아니라 **"지금 이 키를 무효화하면 몇 분 만에 전 서비스가 새 키로 도는가"**를 측정한다. 이 숫자가 곧 사고 시 노출 창이다.

## 더 깊은 토끼굴

- [[mtls-zero-trust]] — secret zero를 신원으로 대체하는 같은 발상
- [[oauth2-grant-types]] — 토큰 수명과 refresh 설계가 겪는 동일한 문제
- [[rbac-abac-rebac]] — 시크릿을 누가 읽을 수 있는가는 별개의 축
- [[k8s-pod-death-5-reasons]] — 로테이션 실패가 헬스체크를 통과해 버리는 이유
- [[jwt-vs-session]] — 무상태 크레덴셜을 즉시 무효화할 수 없는 구조적 한계

**출처**
- HashiCorp Vault, Database Secrets Engine (동적 크레덴셜과 lease): https://developer.hashicorp.com/vault/docs/secrets/databases
- AWS Secrets Manager, Rotation staging labels (`AWSCURRENT`/`AWSPENDING`/`AWSPREVIOUS`): https://docs.aws.amazon.com/secretsmanager/latest/userguide/rotate-secrets_managed.html
- Kubernetes 공식 문서, Secrets — mounted Secret 갱신과 환경변수의 차이: https://kubernetes.io/docs/concepts/configuration/secret/#mounted-secrets-are-updated-automatically
