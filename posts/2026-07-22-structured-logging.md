---
title: 로그는 다 찍혔는데, 장애 원인은 grep으로 끝내 못 찾았다 — Structured Logging
date: 2026-07-22
day: 46
category: observability
tags: [logging, observability, json-log, correlation-id]
related: ["[[distributed-tracing-otel]]", "[[percentile-p99]]", "[[sli-slo-sla]]", "[[error-budget]]"]
difficulty: 2
short_text: |
  💡 [Day 46] 로그 다 찍혔는데 grep으론 못 찾았다
  오해: 로그=사람이 읽는 문장
  실제: 문자열은 필드가 없어 집계 불가
  구조화=필드+trace_id로 즉시 쿼리
  📖 https://github.com/kimyuchan-k1/IT-deep-dive-vault/blob/main/posts/2026-07-22-structured-logging.md
---

# 로그는 다 찍혔는데, 장애 원인은 grep으로 끝내 못 찾았다 — Structured Logging

새벽에 p99가 튀었다. 로그는 초당 수만 줄씩 잘 쌓이고 있었다. 그런데 `grep 500 app.log | grep timeout`을 아무리 조합해도 "어느 사용자의, 어느 요청이, 어디서 막혔는지"가 나오지 않았다. 로그가 없어서가 아니다. 로그의 **모양**이 문제였다.

## 흔한 오해

"로그는 사람이 읽는 거니까 문장으로 예쁘게 찍으면 되지. 나중에 `grep`으로 찾으면 되잖아?"

대부분 그렇게 시작한다. 그래서 이런 줄이 쌓인다:

```
2026-07-22 03:14:22 ERROR Payment failed for user 8123 after 3 retries (timeout 5000ms)
```

읽기엔 완벽하다. 그리고 로그 수집기(ELK, Loki)만 붙이면 관측성이 끝났다고 생각한다. **여기까지는 절반만 맞다.** 사람 1명이 로그 100줄을 읽을 때는 문장이 최고다. 기계가 로그 1억 줄을 읽어야 할 때는 문장이 최악이다.

## 실제 원리

### grep은 "필드"를 모른다

위 로그에서 "재시도 3회 이상 실패한 요청"을 세고 싶다고 하자. `retries (\d+)`를 정규식으로 파싱해야 한다. 그런데 다음 주에 누가 메시지를 `Payment failed (retry count: 3)`로 바꾸면 정규식이 조용히 깨진다. **로그 문장은 스키마가 없다.** 필드가 문장 속에 녹아 있어서, 검색할 때마다 사람이 매번 파싱 규칙을 다시 만들어야 한다.

Structured logging은 이걸 뒤집는다. 로그를 문장이 아니라 **key-value 이벤트**로 찍는다:

```json
{"ts":"2026-07-22T03:14:22Z","level":"error","event":"payment_failed",
 "user_id":8123,"retries":3,"timeout_ms":5000,"trace_id":"a1b2c3","service":"checkout"}
```

이제 `retries`는 문자열 속 숫자가 아니라 **정수 필드**다. "retries >= 3 인 이벤트를 service별로 집계"가 정규식이 아니라 쿼리 한 줄이 된다. 여기가 핵심이다: 구조화의 목적은 "예쁨"이 아니라 **로그를 쿼리 가능한 데이터로 만드는 것**이다.

### grep(O(n) 스캔) vs 인덱스(역색인)

문자열 로그에서 특정 조건을 찾으면 전체 파일을 훑어야 한다 — O(n). 구조화 로그는 각 필드가 색인된다. `user_id:8123`은 역색인에서 바로 해당 문서 목록으로 점프한다. 1억 줄에서 `grep`은 수십 초, 색인 조회는 수십 ms. 이 차이가 [[percentile-p99]] 조사 같은 급한 상황에서 갈린다.

### 진짜 무기는 correlation ID

구조화의 최대 이득은 필드 하나에서 나온다: 요청 진입점에서 발급해 모든 로그에 심는 `trace_id`(correlation ID). 한 요청이 게이트웨이→인증→결제→DB를 거치며 5개 서비스에 로그를 남겨도, `trace_id:a1b2c3` 한 번으로 **그 요청의 전체 여정**이 시간순으로 재구성된다. 이게 [[distributed-tracing-otel]]로 확장되는 출발점이다. 문자열 로그로는 이 상관관계를 사람이 눈으로 이어붙여야 한다.

## 현장 시나리오

한 이커머스의 결제 서비스. 평소 p99 120ms가 새벽 3시에 4초로 튀었다. 로그는 멀쩡히 쌓였지만 전부 `"Payment processing..."` 같은 문장이라, 온콜은 30분간 `grep | awk`로 씨름했다.

원인 사슬은 이랬다: 외부 PG사 응답이 느려짐 → 결제 서비스가 재시도 3회(각 5초 타임아웃) → 커넥션 풀 점유 → 다른 정상 결제까지 대기 → p99 폭발. 문장 로그에는 `retries`, `timeout_ms`, `upstream` 필드가 없어서 "재시도가 폭증했다"를 집계로 볼 수 없었다.

사후에 구조화 로그로 바꾸자, 같은 장애를 `event:payment_retry` 카운트를 `upstream`별로 그린 대시보드가 **10초 만에** 짚어냈다. 근본 원인은 같았다. 바뀐 건 로그의 모양뿐이었다.

## 실무 적용 포인트

1. **메시지가 아니라 이벤트를 찍어라.** 문자열 보간(`f"user {id} failed"`) 대신 `logger.error("payment_failed", user_id=id, retries=n)` 형태. 메시지는 상수로 두고 변수는 전부 필드로 뺀다.

2. **필드 이름을 표준화하라.** `user_id` / `userId` / `uid`가 섞이면 쿼리가 지옥이 된다. Elastic Common Schema(ECS)나 OpenTelemetry semantic conventions의 필드명을 그대로 채택하는 게 제일 싸다.

3. **correlation ID를 진입점에서 1회 발급**하고 로깅 컨텍스트(MDC, contextvars)에 넣어 모든 로그에 자동 첨부. 서비스 경계를 넘을 땐 HTTP 헤더(`traceparent`)로 전파.

4. **로그 레벨을 이벤트 타입과 분리**하라. `level`은 심각도, `event`는 무슨 일인지. `level:error AND event:payment_failed`로 필터되게.

5. **카디널리티 폭탄 주의.** `user_id` 같은 고카디널리티 필드를 인덱스 태그로 남발하면 색인 비용이 터진다. Loki 기준 라벨은 저카디널리티만, 나머지는 로그 본문 필드로.

6. **사람용 출력은 로컬에서만.** 개발 환경은 pretty-print, 운영은 JSON 한 줄(NDJSON). [The Twelve-Factor App](https://12factor.net/logs) 원칙대로 로그는 stdout 이벤트 스트림으로 흘리고 수집은 인프라에 맡긴다.

## 더 깊은 토끼굴

- 구조화 로그 → [[distributed-tracing-otel]]: correlation ID가 span으로 진화하는 지점
- [[percentile-p99]]: 로그 집계로 지연 분포를 볼 때 평균이 아니라 왜 p99를 봐야 하나
- [[sli-slo-sla]]: 구조화 이벤트가 SLI 측정의 원천 데이터가 되는 방식
- [[error-budget]]: error 이벤트율을 배포 결정에 연결하기

**출처**:
- OpenTelemetry Logs Data Model (공식): https://opentelemetry.io/docs/specs/otel/logs/data-model/
- The Twelve-Factor App — Logs: https://12factor.net/logs
- Elastic Common Schema (ECS) reference: https://www.elastic.co/guide/en/ecs/current/index.html
