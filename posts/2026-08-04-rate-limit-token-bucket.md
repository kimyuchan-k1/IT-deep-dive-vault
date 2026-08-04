---
title: 1000 RPS로 제한을 걸었는데 백엔드에는 2000 RPS가 들어왔다
date: 2026-08-04
day: 58
category: security
tags: [rate-limiting, token-bucket, leaky-bucket, gcra, redis, api-gateway]
related: ["[[retry-exponential-backoff-jitter]]", "[[circuit-breaker]]", "[[bulkhead-pattern]]", "[[backpressure-patterns]]", "[[cache-stampede]]"]
difficulty: 3
short_text: |
  ⚠️ [Day 58] 1000 RPS 제한에 2000이 통과

  오해: 1초마다 카운터 리셋
  실제: 창 경계 앞뒤 몰림→2배

  "6000건이 200ms에 왔다"

  📖 https://github.com/kimyuchan-k1/IT-deep-dive-vault/blob/main/posts/2026-08-04-rate-limit-token-bucket.md
---

# 1000 RPS로 제한을 걸었는데 백엔드에는 2000 RPS가 들어왔다

한도를 넘긴 요청은 한 건도 없었다. 모니터링의 분당 카운트도 정확히 한도 안이었다. 그런데 뒷단은 무너졌다.

## 흔한 오해

> "초당 1000개로 막으려면 카운터 하나 두고 1초마다 0으로 되돌리면 되잖아. Redis에 `INCR` 하고 `EXPIRE 1` 두 줄이면 끝인데."

거의 모든 튜토리얼의 첫 예제가 이 코드다. 원자적이고, 사용자당 상태가 정수 하나고, 명령 두 개면 끝난다.

틀린 건 아니다. 이 방식은 **"어떤 창 안에서도 1000개를 넘지 않는다"를 정확히 보장한다.** 문제는 우리가 원했던 게 그게 아니라는 것이다. 우리가 원한 건 "임의의 1초 구간에서 1000개"였고, 고정 창은 그걸 보장하지 않는다.

## 실제 원리

### 고정 창은 경계에서 두 배를 흘린다

키가 `rl:u123:1754280000`처럼 초 단위 epoch를 담고 있다고 하자. 0.999초 시점에 1000개가 들어와 창이 꽉 찬다. 1.000초에 키가 바뀌고 카운터는 0에서 다시 시작한다. 1.001초에 또 1000개가 통과한다.

임의의 1초 구간 `[0.999, 1.999]`를 잘라보면 그 안에 **2000개**가 지나갔다. 한도의 정확히 두 배다. 이건 구현 버그가 아니라 고정 창 알고리즘이 구조적으로 허용하는 최악값이다.

여기가 핵심이다. **이 최악값은 우연히 발생하지 않는다.** 429를 받은 클라이언트는 보통 "다음 창까지 기다렸다 재시도"로 짜인다. 그러면 거부당한 요청들이 전부 다음 창의 첫 밀리초에 정렬된다. 제한기가 스스로 동기화된 버스트를 만들어내는 구조다.

### 토큰 버킷과 리키 버킷은 반대 방향의 도구다

둘 다 "평균 속도를 r로 제한한다"고 설명되지만, 버스트를 다루는 방향이 정반대다.

**Token bucket**: 용량 C짜리 버킷에 초당 r개 토큰이 채워진다. 요청 하나가 토큰 하나를 소비하고, 토큰이 없으면 거부된다. 버킷이 가득 찬 채로 놀고 있었다면 순간 C개까지 한 번에 통과한다. 평균은 r로 수렴하지만 **순간 최대는 C까지 허용**된다. 버스트를 의도적으로 허가하는 도구다.

**Leaky bucket(큐 형태)**: 요청이 큐에 쌓이고, 출력이 정확히 초당 r개씩 새어나간다. 순간 최대도 r이다. **버스트를 평탄화**한다. 대신 통과하기까지 큐에서 기다리므로 지연이 생기고, 큐가 넘치면 드롭된다.

공개 API처럼 클라이언트가 가끔 몰아치는 걸 허용해야 하면 token bucket, 뒷단이 처리율 고정인 워커나 과금되는 외부 벤더 API를 감싸는 자리라면 leaky bucket이다.

구현에서 한 가지가 중요하다. **토큰을 타이머로 채우면 안 된다.** 사용자 1000만 명이면 타이머 1000만 개다. 요청이 도착했을 때 그 자리에서 계산하는 lazy refill을 쓴다.

```
tokens = min(C, tokens + (now - last_refill) * r)
if tokens >= 1: tokens -= 1; allow
else: deny
last_refill = now
```

상태는 `(tokens, last_refill)` 두 개면 된다.

### GCRA — 상태 하나로 같은 일을 한다

GCRA(Generic Cell Rate Algorithm)는 ATM 네트워크의 트래픽 셰이핑에서 온 leaky bucket의 meter 형태 구현이다. 저장하는 상태가 TAT(theoretical arrival time) **하나**다.

`T = 1/r` (요청 사이 최소 간격), `τ = 허용 버스트 개수 × T`로 두면:

```
if now < TAT - τ:  거부, 대기 시간 = TAT - τ - now
else:              TAT = max(now, TAT) + T, 통과
```

필드가 하나라 원자적 갱신이 쉽고, 거부할 때 **"정확히 몇 초 뒤에 다시 오라"를 계산해서 줄 수 있다.** 고정 창은 이 값을 모른다. "다음 창이 시작될 때까지" 말고는 답이 없고, 그 답이 앞서 말한 정렬 버스트를 만든다.

### 분산 환경에서 실제로 무너지는 지점

인스턴스 8대에 각각 로컬 카운터를 두면 실효 한도는 8000이 된다. 그래서 상태를 Redis로 옮기는데, 여기서 고정 창과 token bucket이 갈린다.

`INCR`은 단일 명령이라 원자적이다. 반면 token bucket은 필드 두 개를 읽고, 경과 시간으로 계산하고, 다시 쓰는 read-modify-write다. `GET` → 계산 → `SET` 사이에 다른 인스턴스의 요청이 끼어들면 같은 토큰을 두 번 쓴다. 동시성이 높을수록 새는 양이 늘어난다. 해법은 세 연산을 Lua 스크립트 한 덩어리로 묶는 것이다. Redis는 스크립트를 다른 명령과 섞이지 않게 실행한다.

## 현장 시나리오

공개 API 게이트웨이. 파트너사 400곳, 정책은 "API 키당 분당 6000회". 구현은 `INCR rl:{key}:{yyyyMMddHHmm}` + `EXPIRE 60`이었고 6개월간 문제가 없었다.

파트너 한 곳이 실시간 호출을 배치 연동으로 바꾸면서 깨졌다. 그쪽 cron이 `* * * * *`, 매 분 00초였다. 몇 달에 걸쳐 같은 패턴의 파트너가 24곳까지 늘었다.

인과는 이랬다. 매 분 00초에 게이트웨이의 카운터 키가 바뀐다 → 배치 클라이언트들의 cron도 00초에 정렬돼 있다 → 앞 분에 429로 거부된 요청들까지 "다음 분에 재시도"로 같은 지점에 합류한다 → **분당 6000이라는 한도는 정직하게 지켜지는데, 그 6000건이 첫 200ms 안에 도착한다.**

게이트웨이는 전부 통과시켰다. 한도를 넘긴 적이 없으니 429도 거의 안 찍혔다. 무너진 건 뒷단이었다. 커넥션 풀 100개가 즉시 고갈되고 대기 큐가 쌓이면서 p99가 380ms에서 4.2초로 뛰었다. 헬스체크가 타임아웃되어 인스턴스 2대가 LB에서 빠졌고, 남은 6대에 같은 트래픽이 다시 몰렸다. 5xx 비율 3.2%.

GCRA로 바꾸고 `T = 10ms`(초당 100건), `τ = 20T = 200ms`(버스트 20건)로 잡았다. 같은 배치가 그대로 들어와도 파트너당 10ms 간격으로 갈린다. 00초 구간의 순간 피크는 6000건에서 480건(24곳 × 버스트 20)으로 떨어졌고 p99는 400ms대로 돌아왔다.

## 실무 적용 포인트

1. **고정 창을 유지해야 한다면 최소한 sliding window counter로 바꿔라.** 이전 창 카운트에 겹치는 비율을 곱해 더한다: `이전창 × (1 - 현재창_경과비율) + 현재창`. 상태는 정수 두 개, 오차는 실측에서 몇 % 수준이고 경계 2배 문제가 사라진다. Cloudflare가 이 방식을 쓴다.

2. **token bucket이나 GCRA는 반드시 Lua 스크립트로 원자화하라.** `EVALSHA`로 호출하고 `KEYS`를 하나만 써서 Redis Cluster에서 슬롯이 갈리지 않게 한다. 라이브러리의 "분산 rate limiter"를 쓸 때도 내부가 `GET`/`SET` 두 번인지 스크립트 한 번인지부터 확인해야 한다.

3. **거부할 때 `Retry-After`를 정확한 값으로 채워라.** 429는 RFC 6585, `Retry-After`는 RFC 9110 §10.2.3이다. GCRA는 `TAT - τ - now`로 초 단위 값을 계산할 수 있다. 그리고 클라이언트는 그 값에 반드시 jitter를 더해 재시도해야 한다 — [[retry-exponential-backoff-jitter]]. jitter가 없으면 정확한 `Retry-After`가 오히려 완벽한 동기화 버스트를 만든다.

4. **rate와 burst를 분리해서 문서화하라.** "분당 6000회"라고만 공지하면 파트너는 6000건을 한 번에 쏴도 된다고 읽는다. "초당 100건, 버스트 20건"으로 쓰면 클라이언트 구현 자체가 달라진다. 위 사고에서 실제로 바꾼 것은 알고리즘과 이 문장 두 개였다.

5. **Redis 장애 시 동작을 코드에 미리 박아라.** 판정이 요청 경로에 들어가므로 같은 AZ 왕복 1ms 미만이어도 p99에는 보인다. Redis가 죽었을 때 fail-open(전부 통과, 뒷단이 위험)인지 fail-closed(전부 429, 가용성 포기)인지 결정해두어야 한다.

6. **키를 IP로 잡지 마라.** CGNAT과 회사 프록시 뒤에서는 수천 명이 IP 하나를 공유한다. 인증된 요청은 API 키나 사용자 ID로, 미인증 트래픽만 IP로 잡는다. 그리고 rate limit은 부하 방어의 마지막 줄이 아니다. 뒷단 격리는 [[bulkhead-pattern]]과 [[circuit-breaker]]가 맡는 몫이다.

## 더 깊은 토끼굴

- [[retry-exponential-backoff-jitter]] — `Retry-After`가 만드는 동기화를 깨는 방법
- [[circuit-breaker]] — 한도 안의 트래픽이 뒷단을 무너뜨릴 때 끊는 쪽
- [[bulkhead-pattern]] — 커넥션 풀 고갈이 전체로 번지지 않게 나누기
- [[backpressure-patterns]] — 거부 대신 밀어내기를 택하는 경우
- [[cache-stampede]] — 같은 순간 정렬이 캐시 계층에서 나타나는 형태

**출처**

- RFC 6585 §4, 429 Too Many Requests — https://www.rfc-editor.org/rfc/rfc6585.html
- RFC 9110 §10.2.3, Retry-After — https://www.rfc-editor.org/rfc/rfc9110.html
- Stripe Engineering, Scaling your API with rate limiters — https://stripe.com/blog/rate-limiters
- Cloudflare Blog, How we built rate limiting capable of scaling to millions of domains — https://blog.cloudflare.com/counting-things-a-lot-of-different-things/
- Brandur Leach, Rate limiting, cells, and GCRA — https://brandur.org/rate-limiting
- Redis Docs, Scripting with Lua — https://redis.io/docs/latest/develop/interact/programmability/eval-intro/

정리하면, 이 사고의 원인은 제한기가 느슨해서가 아니라 **제한기가 측정하는 창과 뒷단이 아파하는 창이 서로 달랐다는 것**이다. 한도는 6000건이었고 실제로 6000건이 들어왔다. 다만 그 6000건이 60초에 걸쳐 오는지 200ms에 몰려 오는지를 아무도 세고 있지 않았다.
