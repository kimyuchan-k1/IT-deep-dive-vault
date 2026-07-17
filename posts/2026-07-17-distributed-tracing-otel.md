---
title: 로그를 다 모았는데 왜 느린지는 끝내 못 찾았다
date: 2026-07-17
day: 42
category: observability
tags: [opentelemetry, tracing, context-propagation, sampling]
related: ["[[structured-logging]]", "[[slo-error-budget]]", "[[p99-latency-tail]]", "[[circuit-breaker]]", "[[replication-lag]]"]
difficulty: 3
short_text: |
  ⚠️ [Day 42] 로그 다 모아도 원인은 안 나온다

  오해: 트레이싱=로그+ID
  실제: context 전파 누락→트레이스 단절

  "3초의 범인은 그래프 밖"

  📖 https://github.com/kimyuchan-k1/IT-deep-dive-vault/blob/main/posts/2026-07-17-distributed-tracing-otel.md
---

# 로그를 다 모았는데 왜 느린지는 끝내 못 찾았다

## 흔한 오해

"분산 트레이싱? 그거 결국 로그에 요청 ID 하나 박아서 나중에 그 ID로 grep하는 거 아닌가. 우리는 이미 `X-Request-ID`를 헤더로 넘기고 로그에 찍고 있는데."

많은 팀이 그렇게 안다. 그래서 트레이싱 도입을 "로그 파이프라인 개선"의 한 갈래로 취급한다. ELK든 Loki든 중앙에 로그를 다 모아두고, 상관관계 ID로 조인하면 같은 효과라고 믿는다. OpenTelemetry SDK를 붙이는 건 그 위에 얹는 장식 정도로 본다.

**절반은 맞다.** 요청 ID로 로그를 묶으면 "이 요청이 어떤 서비스들을 지나갔는지"는 나온다. 그런데 그게 트레이싱이 파는 물건이 아니다. 트레이싱이 파는 건 **인과 구조와 시간의 분배**다. 요청 ID는 그 둘을 못 준다.

## 실제 원리

### 로그는 점, 트레이스는 트리

로그 한 줄은 "시각 T에 서비스 A에서 이런 일이 있었다"는 **점**이다. 요청 ID로 묶어봐야 점들의 **평평한 목록**이 나온다. 여기서 "A가 B를 부르는 동안 C도 병렬로 돌았는가", "B의 200ms 중 150ms가 D를 기다린 시간인가"는 복원되지 않는다. 로그는 부모-자식 관계를 기록하지 않기 때문이다.

트레이스는 **span의 트리**다. 각 span은 최소 이 네 개를 갖는다: `trace_id`(요청 전체의 ID), `span_id`(이 작업의 ID), `parent_span_id`(나를 부른 작업의 ID), 그리고 시작/종료 시각. `parent_span_id` — 이 한 필드가 목록을 트리로 바꾼다. 트리가 있으면 "총 3초 중 어느 가지가 2.8초를 먹었나"가 뺄셈으로 나온다. 로그로는 그 뺄셈이 불가능하다.

### 진짜 어려운 건 수집이 아니라 전파

여기가 핵심이다. 트레이싱 도입이 실패하는 지점은 백엔드(Jaeger/Tempo)도, SDK 설치도 아니다. **context propagation**이다.

서비스 A가 B를 HTTP로 부를 때, A는 자기 `trace_id`와 현재 `span_id`를 요청 헤더에 실어 보내야 한다. B는 그걸 꺼내서 자기 span의 부모로 삼아야 한다. 이 규격이 W3C의 `traceparent` 헤더다. 형식은 고정되어 있다:

```
traceparent: 00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01
             │  └── trace-id (16바이트)     └── parent-id  └── flags
             └── version
```

이 헤더가 한 홉이라도 끊기면 그 뒤는 **새로운 트레이스로 시작**한다. 원래 트리에서 그 가지는 통째로 사라진다. 그래서 트레이스는 조용히 잘린다 — 에러 로그 하나 없이.

끊기는 지점은 정해져 있다. **HTTP 클라이언트가 계측 안 된 라이브러리**일 때, **메시지 큐를 지날 때**(Kafka 메시지 헤더에 `traceparent`를 직접 넣지 않으면 컨슈머 쪽은 고아 트레이스가 된다), **스레드 풀이나 async 경계를 넘을 때**(context가 thread-local에 있으면 다른 스레드로 안 넘어간다). 이게 트레이싱을 붙였는데도 그림이 반쪽인 이유의 대부분이다.

### 샘플링이 통계가 아니라 트리를 자른다

100% 저장은 비용이 감당 안 된다. 그래서 샘플링을 하는데, 여기에 함정이 있다. **head-based sampling**은 트레이스 시작 시점에 주사위를 굴려 1%만 남긴다. 문제는 그 시점엔 이 요청이 느릴지 터질지 모른다는 것. 정작 보고 싶은 p99 요청이 99% 확률로 버려진다.

**tail-based sampling**은 트레이스가 끝난 뒤 판단한다 — "에러났으면 저장, 2초 넘었으면 저장, 나머지는 1%". 대신 collector가 트레이스 전체를 메모리에 모아두고 기다려야 하고, 같은 `trace_id`의 span이 **같은 collector 인스턴스로 모여야** 한다. 그래서 collector를 `trace_id` 기준으로 라우팅하는 계층이 필요해진다. 공짜가 아니다.

## 현장 시나리오

주문 API의 p99가 3초를 찍었다. 평균은 120ms. 로그는 다 있었다. `X-Request-ID`로 묶어서 느린 요청 하나를 골라 타임라인을 그렸다. 게이트웨이 진입 09:14:02.100, 주문 서비스 응답 09:14:05.050. **그 사이 2.95초 동안 아무 로그도 없었다.**

인과 사슬은 이렇게 흘렀다. 주문 서비스가 결제 서비스를 gRPC로 부른다 → 결제 서비스는 재고 서비스를 부른다 → 재고 서비스가 리플리카 DB를 조회한다 → 그 리플리카에 배치 잡이 겹쳐 쿼리가 2.8초 걸린다. 하지만 로그에는 이 사슬이 **안 보였다**. 재고 서비스의 gRPC 클라이언트가 인터셉터 없이 붙어 있어 `traceparent`를 안 실었고, 재고 서비스의 로그에는 그 요청 ID가 아예 없었기 때문이다. 팀은 "게이트웨이랑 주문 서비스 사이 네트워크 문제"로 3일을 태웠다.

OpenTelemetry gRPC 인터셉터를 붙인 다음 날, 트레이스 하나로 끝났다. 총 2,950ms 중 `inventory.query` span이 2,810ms. 범인은 처음부터 그래프에 있었지만, 그래프에 **선이 안 그어져 있었다**. 이건 [[replication-lag]]이 만든 문제였는데, lag 지표를 아무리 봐도 "주문 API가 느리다"와 연결이 안 됐던 것.

느린 이유를 못 찾은 게 아니다. 시간이 어디로 갔는지 **묻는 방법 자체가 없었던 것**이다.

## 실무 적용 포인트

1. **W3C `traceparent`를 표준으로 못 박아라.** 사내 커스텀 헤더(`X-Trace-Id` 같은)를 쓰면 서드파티 SDK·서비스 메시·클라우드 로드밸런서가 전파를 못 이어받는다. OTel 기본값이 W3C Trace Context다. 그대로 써라.

2. **전파 단절을 알람으로 잡아라.** `parent_span_id`가 없는데 루트가 아닌 span, 즉 **고아 span의 비율**을 지표로 뽑는다. 이 비율이 튀면 계측 빠진 홉이 생겼다는 뜻이다. 배포 후 이 값부터 확인한다.

3. **메시지 큐 경계는 수동으로 심어라.** Kafka 프로듀서는 메시지 헤더에 `traceparent`를 넣고(`inject`), 컨슈머는 꺼내서(`extract`) 링크한다. 비동기 처리라 부모-자식이 아니라 **span link**로 거는 게 정석이다. 자동 계측만 믿으면 큐 뒤가 통째로 사라진다.

4. **p99를 보려면 tail-based sampling으로 가라.** head-based 1%로는 느린 요청이 안 잡힌다. OTel Collector의 `tail_sampling` 프로세서에 `latency > 1s` 또는 `status = ERROR` 정책을 걸고 나머지는 1~5%로 낮춘다. 대신 collector 메모리(`decision_wait`만큼 span을 들고 있음)와 `trace_id` 기반 로드밸런싱 계층을 같이 계산해라.

5. **span 개수와 attribute를 예산으로 관리하라.** 요청당 span 200개, span당 attribute 수십 개를 무심코 찍으면 collector 네트워크 비용이 애플리케이션보다 커진다. OTel의 `span_limits`로 attribute 개수·길이 상한을 명시한다. 카디널리티 높은 값(사용자 ID, UUID)은 attribute로는 되지만 **메트릭 라벨로는 절대 금지**다.

6. **로그와 트레이스를 `trace_id`로 꿰라.** 로그 포맷에 `trace_id`/`span_id`를 필드로 박아두면 트레이스 → 로그, 로그 → 트레이스 양방향 점프가 된다. 트레이싱이 로그를 대체하는 게 아니다. 트레이스가 **어디를 볼지** 알려주고, 로그가 **거기서 무슨 일이 있었는지** 알려준다. 구조화 로깅이 전제인 이유는 [[structured-logging]]에서 판다.

## 더 깊은 토끼굴

- [[structured-logging]] — 트레이스와 로그를 꿰는 `trace_id` 필드가 가능하려면
- [[p99-latency-tail]] — 평균은 멀쩡한데 p99만 터지는 구조적 이유
- [[slo-error-budget]] — 트레이스로 잡은 지연을 무엇을 기준으로 "문제"라 부를 것인가
- [[circuit-breaker]] — 트레이스에서 재시도 폭풍이 어떻게 보이는가
- [[replication-lag]] — 이 글의 시나리오에서 범인이었던 그 지연

**1차 출처**:
- W3C 표준 — Trace Context (`traceparent` 헤더 규격): https://www.w3.org/TR/trace-context/
- OpenTelemetry 공식 문서 — Context Propagation: https://opentelemetry.io/docs/concepts/context-propagation/
- OpenTelemetry Collector — Tail Sampling Processor: https://github.com/open-telemetry/opentelemetry-collector-contrib/tree/main/processor/tailsamplingprocessor
