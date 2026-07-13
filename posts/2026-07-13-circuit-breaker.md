---
title: 재시도를 촘촘히 넣었는데 장애가 더 커졌다 — Circuit Breaker
date: 2026-07-13
day: 40
category: observability
tags: [resilience, circuit-breaker, cascading-failure, timeout]
related: ["[[bulkhead-pattern]]", "[[retry-exponential-backoff-jitter]]", "[[dead-letter-queue]]", "[[percentile-p99]]"]
difficulty: 2
short_text: |
  🔥 [Day 40] 재시도를 깔았더니 장애가 더 커졌다

  오해: 그냥 실패 카운터
  실제: 죽은 곳에 스레드 쌓임→풀 고갈→내 서버 사망

  "부가 API 1곳 느려져 전체가 죽었다"

  📖 https://github.com/kimyuchan-k1/IT-deep-dive-vault/blob/main/posts/2026-07-13-circuit-breaker.md
---

# 재시도를 촘촘히 넣었는데 장애가 더 커졌다 — Circuit Breaker

## 흔한 오해

"Circuit Breaker? 실패 횟수 세다가 임계 넘으면 요청 막는 거 아닌가. `if (failCount > 5) throw` 같은 거."

대부분 그렇게 안다. 그래서 실패 카운터 하나 두고 "차단기 붙였다"고 말한다. 그리고 안정성을 높이려고 **재시도(retry)를 촘촘히** 깐다 — 실패하면 3번 재시도, 타임아웃이면 또 재시도.

**틀린 건 아닌데, 핵심을 거꾸로 잡았다.** Circuit Breaker가 막는 건 "실패"가 아니라 **"실패하는 의존성에 요청을 계속 밀어넣어서 내 서버까지 같이 죽는 것"**이다. 그리고 순진한 재시도는 이 상황을 완화하는 게 아니라 **증폭**시킨다.

## 실제 원리

### 진짜 적은 실패가 아니라 "느린 실패"다

다운스트림 서비스가 완전히 죽으면 오히려 낫다. 커넥션이 즉시 거절(`connection refused`)되니 스레드가 바로 풀려난다.

진짜 위험한 건 **느려지는 것**이다. 결제 API가 평소 50ms인데 갑자기 5초씩 걸린다고 하자. 내 서버의 요청 스레드는 그 5초 동안 **그 커넥션을 붙잡고 대기**한다. 초당 200요청이 들어오면 5초 안에 1,000개 스레드가 결제 응답을 기다리며 묶인다. 스레드 풀은 보통 200~400개. **풀이 고갈되는 순간, 결제와 무관한 상품 조회·로그인 요청도 스레드를 못 얻어 같이 죽는다.**

이게 [[bulkhead-pattern]]이 격벽으로 막으려는 것과 같은 뿌리의 문제다. Circuit Breaker는 다른 각도에서 푼다: **애초에 죽은 의존성으로 요청을 보내지 않는다.**

### 3가지 상태 (여기가 핵심)

Circuit Breaker는 단순 카운터가 아니라 **상태 기계**다. 상태가 3개다.

- **Closed(닫힘)**: 정상. 요청이 그대로 통과한다. 대신 결과(성공/실패/지연)를 **슬라이딩 윈도우**에 계속 기록한다.
- **Open(열림)**: 차단. 실패율이 임계를 넘으면 열린다. 이 상태에서는 다운스트림을 **호출하지도 않고 즉시 실패**시킨다(fail-fast). 스레드가 5초 묶이는 대신 1ms 만에 fallback으로 떨어진다.
- **Half-Open(반열림)**: 정찰. Open 상태로 일정 시간(`waitDurationInOpenState`, 예: 30초)이 지나면 자동 전환. **소수의 요청만** 통과시켜 다운스트림이 회복됐는지 떠본다. 성공하면 Closed로, 또 실패하면 다시 Open으로.

"실패 카운터"와의 결정적 차이는 **Open 상태에서 호출 자체를 건너뛴다**는 것. 카운터만 있으면 실패는 세지만 여전히 매 요청 5초씩 기다린다. 상태 기계라야 fail-fast가 된다.

### 임계는 "횟수"가 아니라 "비율 × 윈도우"

성숙한 구현(Resilience4j 등)은 `failCount > 5`처럼 절대 횟수로 열지 않는다. 그러면 트래픽 100배 차이 나는 서비스에 같은 숫자를 쓸 수 없다. 대신:

- **failure rate threshold**: 예) 최근 윈도우의 50% 이상이 실패하면 Open.
- **slow call rate threshold**: 실패가 아니어도 **응답이 느린 비율**(예: 2초 초과가 80%)로도 연다. 느린 실패가 진짜 적이기 때문.
- **sliding window**: 최근 N개 요청(count-based) 또는 최근 T초(time-based) 기준. 오래된 결과는 윈도우에서 빠진다.
- **minimum number of calls**: 표본이 너무 적으면(예: 10건 미만) 판단 보류. 3건 중 2건 실패했다고 여는 건 노이즈다.

## 현장 시나리오

이커머스 상품 상세 API가 있었다. 이 API는 내부에서 리뷰 서비스를 동기 호출해 평점을 붙였다. 리뷰 서비스는 부가 정보라 없어도 그만인데, 호출은 동기였다.

어느 날 리뷰 서비스 DB에 슬로우 쿼리가 껴서 응답이 50ms → 8초로 늘었다. 그러자:

1. 상품 API의 요청 스레드가 리뷰 응답을 기다리며 8초씩 묶임
2. 초당 300요청 × 8초 → 순식간에 스레드 풀(300개) 고갈
3. **리뷰와 무관한 "장바구니 담기", "가격 조회"까지 스레드를 못 얻어 실패**
4. 여기에 "실패하면 3회 재시도" 로직이 있어서 죽어가는 리뷰 서비스에 **트래픽 3배**를 더 퍼부음 → 회복 불가
5. 상품 도메인 전체 5분간 다운

사후 조치는 리뷰 호출에 Circuit Breaker를 감는 것이었다. `slowCallDurationThreshold=1s`, `slowCallRateThreshold=50%`. 리뷰가 느려지면 3초 안에 회로가 Open → 이후 리뷰 호출은 즉시 "평점 없음"으로 fallback → **상품 API는 리뷰만 빠진 채 정상 응답**. 원인 한 줄: **"부가 기능 하나가 동기 호출이었다."**

## 실무 적용 포인트

1. **횟수 말고 비율로 열어라**: `failureRateThreshold=50%` + `minimumNumberOfCalls=20`. 표본 적을 땐 판단 보류해 노이즈로 여는 걸 막는다.
2. **slow call도 실패로 취급**: `slowCallDurationThreshold=1~2s`, `slowCallRateThreshold=80%`. 느린 실패가 풀 고갈의 진짜 원인이다.
3. **Circuit Breaker 안쪽에 반드시 timeout**: 회로가 열리기 전 첫 요청들은 여전히 대기한다. 호출 자체에 `readTimeout`(예: 2s)을 걸어 스레드가 무한정 묶이지 않게 한다. 둘은 세트다.
4. **재시도는 회로 바깥에**: Open 상태에서 재시도하면 fail-fast 결과만 반복한다. 재시도는 Closed일 때만, 그것도 [[retry-exponential-backoff-jitter]]로 간격을 벌려라. 순진한 즉시 재시도는 장애를 3배로 키운다.
5. **Half-Open 정찰 수를 작게**: `permittedNumberOfCallsInHalfOpenState=3~5`. 회복 안 된 서비스에 대량 요청을 다시 쏘면 재차 무너진다.
6. **fallback을 미리 설계**: 캐시된 값, 기본값, "일부 정보 없음" 응답. fallback 없는 Circuit Breaker는 그냥 빠른 에러일 뿐이다.

## 더 깊은 토끼굴

- Martin Fowler, [CircuitBreaker](https://martinfowler.com/bliki/CircuitBreaker.html) — 패턴의 정본 설명 (상태 기계 다이어그램 포함)
- Resilience4j 공식 문서: [CircuitBreaker](https://resilience4j.readme.io/docs/circuitbreaker) — 슬라이딩 윈도우/임계 설정의 실제 파라미터
- [[bulkhead-pattern]]: 같은 "풀 고갈"을 격벽으로 막는 형제 패턴 — 둘을 함께 써야 완성된다
- [[retry-exponential-backoff-jitter]]: 회로 바깥에서 재시도를 어떻게 안전하게 하나
- [[percentile-p99]]: slow call 임계를 정할 때 봐야 하는 지표
