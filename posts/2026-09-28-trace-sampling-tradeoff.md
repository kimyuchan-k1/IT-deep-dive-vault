---
title: 1% 샘플링을 켰다. 장애 30분 동안 에러 트레이스가 한 건도 없었다
date: 2026-09-28
day: 99
category: observability
tags: [tracing, sampling, opentelemetry, tail-sampling, trace-context]
related: ["[[distributed-tracing-otel]]", "[[cardinality-explosion]]", "[[percentile-p99]]", "[[structured-logging]]", "[[sli-slo-sla]]"]
difficulty: 3
short_text: |
  ⚠️ [Day 99] 1% 샘플링. 장애 30분간 에러 트레이스 0건

  오해: 샘플링은 비율 문제
  실제: 루트에서 먼저 결정→에러는 뒤에 발생→희귀한 것만 사라짐

  📖 https://github.com/kimyuchan-k1/IT-deep-dive-vault/blob/main/posts/2026-09-28-trace-sampling-tradeoff.md
---

# 1% 샘플링을 켰다. 장애 30분 동안 에러 트레이스가 한 건도 없었다

## 흔한 오해

> "트레이스 비용이 크면 샘플링 비율을 낮추면 된다. 1%만 남겨도 통계적 대표성은 유지되니 p99도 그대로 보이고, 비용은 100분의 1이 된다."

절반은 맞다. 초당 5,000건이면 1%도 초당 50건이고, 이 정도면 지연 분포의 모양은 흔들리지 않는다.

문제는 트레이스를 보는 이유가 분포가 아니라는 것이다. 트레이스는 **이상한 한 건**을 열어보려고 켠다. 비용을 100분의 1로 줄이면 그 한 건이 잡힐 확률도 100분의 1이다. 희귀하다는 건 그게 바로 장애라는 뜻이다.

오해가 하나 더 있다. "비율만 정하면 된다"는 말은 **언제 결정하느냐**를 빼먹었다. 샘플링 설계의 거의 모든 어려움이 그 시점에 있다.

## 실제 원리

### 1. head-based 샘플링은 아무것도 모르는 시점에 결정한다

기본 설정은 head-based다. 루트 스팬이 시작될 때, 즉 요청이 첫 서비스에 도착한 순간 "이 트레이스를 남길지"를 던진다.

알 수 있는 것은 진입 경로와 헤더뿐이다. 이 요청이 500을 뱉을지, 8초 걸릴지, DB 커넥션을 못 받고 대기할지는 전부 모른다. **판단 근거가 생기기 전에 판단을 끝낸다.** 에러율 0.3% 엔드포인트에 1% head 샘플링이면 에러 트레이스 기대값은 요청 10만 건당 3건이다. 10분 장애 동안 요청 3만 건이면 기대 수집량이 1건 미만이다. 0건이 정상 동작이다.

### 2. 결정은 전파된다. 그래서 되돌릴 수 없다

한번 내린 결정은 요청과 함께 흘러간다. W3C Trace Context의 `traceparent` 헤더가 통로다.

```
traceparent: 00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01
             ^버전  ^trace-id(16B)                 ^parent-id(8B)   ^flags
                                                        flags 하위 비트 = sampled (01/00)
```

하위 서비스의 기본 샘플러는 `parentbased_traceidratio` 계열이다. 부모가 `01`이면 남기고 `00`이면 버린다. 이게 있어야 트레이스가 조각나지 않는다. 대가도 붙는다. **결제 서비스에서만 샘플링을 올리는 일이 불가능해진다.**

그래서 consistent probability sampling이 나왔다. 부모 플래그를 믿는 대신 trace ID에서 유도한 난수값을 임계값과 비교한다. trace ID는 모든 서비스에서 같으니 서로 통신하지 않아도 같은 결론에 도달한다. OpenTelemetry는 이 임계값을 `tracestate`의 `ot` 항목(`th:`)에 실어 전파하도록 규정한다. 기록되면 백엔드가 "몇 분의 1로 걸러진 것"인지 알게 된다.

### 3. 샘플링된 스팬으로 만든 지표는 보정이 필요하다

스팬에서 RED 메트릭을 뽑으면(span metrics) 그 숫자는 실제 요청 수가 아니라 살아남은 요청 수다. 1%면 100을 곱해야 원래 값이고, 이 보정 계수가 adjusted count다. 샘플링률이 엔드포인트별로 다르거나 컬렉터에서 한 번 더 걸러지면 **곱해야 할 수가 스팬마다 달라지고**, 그 값이 스팬에 없으면 복원이 불가능하다. 확률을 데이터에 실어 보내는 것이 규격에 들어간 이유가 이거다. 총량과 에러율은 샘플링 없는 메트릭에서 읽는 편이 안전하다([[cardinality-explosion]]).

### 4. tail-based 샘플링은 결정을 미루고 대신 메모리를 쓴다

결과를 보고 결정하려면 트레이스가 끝날 때까지 스팬을 들고 있어야 한다. Collector의 `tail_sampling` 프로세서가 그 일을 한다. `decision_wait`(기본 30초) 동안 trace ID별로 스팬을 모으고, `status_code`·`latency`·`numeric_attribute` 조합의 정책을 평가한 뒤 트레이스를 통째로 남기거나 버린다. 제약이 셋 붙는다.

- **메모리**: 버퍼는 대략 초당 스팬 수 × `decision_wait`다. 초당 4만 스팬에 10초면 40만 스팬이다. `num_traces` 상한을 넘으면 오래된 트레이스가 먼저 밀려난다.
- **스팬 라우팅**: 한 트레이스의 모든 스팬이 **같은 컬렉터 인스턴스**에 도착해야 한다. 앞에 일반 로드밸런서를 놓으면 스팬이 흩어지고 각 인스턴스가 조각난 트레이스를 보고 판단한다.
- **지연**: 트레이스가 백엔드에 뜨는 시각이 `decision_wait`만큼 늦다. 그보다 오래 걸리는 요청은 평가 시점에 안 끝나 있어서 불완전한 데이터로 판단된다.

## 현장 시나리오

커머스 백엔드. 서비스 14개, 피크 초당 5,000 요청, head 샘플링 1%. 벤더 청구서를 보고 3개월 전에 10%에서 1%로 내렸다. 금요일 저녁 결제 승인 실패율이 0.3%에서 2.1%로 올랐다. 온콜이 실패 주문의 trace ID로 Jaeger를 검색했다.

```
주문 ID로 trace ID 확보 → 조회 결과 "not found"
  → 1% head 샘플링, 루트에서 이미 버려진 트레이스
  → 30분간 검색한 실패 요청 12건 전부 미수집
  → p99 그래프는 정상 (성공 요청이 압도적이라 분포가 안 흔들림)
  → 로그로 우회, 서비스 14개 수동 대조에 40분
  → 원인은 결제 게이트웨이 커넥션 풀 고갈 (스팬 하나면 즉시 보였을 것)
  → MTTR 71분, 그중 52분이 "어느 구간인지 찾는 시간"
```

월요일에 tail sampling으로 전환했다. 에러와 2초 초과는 100%, 나머지는 1%. 컬렉터 파드 3대, 앞은 Kubernetes Service 기본 라운드로빈. 이틀 만에 두 번째 문제가 터졌다.

```
라운드로빈으로 스팬이 3대에 분산 → 한 트레이스가 3등분
  → 각 컬렉터는 스팬 2~3개만 보고 정책 평가
  → 에러 스팬을 받은 인스턴스만 남기기로 결정
  → 백엔드에 스팬 3개짜리 "트레이스" 저장. 루트도 없음
  → decision_wait 30초 × 초당 4만 스팬 버퍼로 RSS 6GB, OOMKilled 2회
```

고친 것은 둘이다. 1계층 컬렉터에 `loadbalancing` 익스포터를 넣어 trace ID로 해싱, `decision_wait`를 8초로 줄이고 `num_traces` 상한을 명시.

원인 한 줄. 첫 번째는 결과를 보기 전에 결정했고, 두 번째는 결과를 다 모으기 전에 결정했다. 샘플링 사고는 늘 **판단 시점과 정보 도착 시점의 어긋남**이다.

## 실무 적용 포인트

1. **에러와 느린 요청은 샘플링에서 제외하라.** tail sampling이면 `status_code: ERROR`와 `latency threshold_ms: 2000`은 `always_sample`, 정상 요청만 1~5%로 내린다. 트레이스 양의 대부분이 정상 요청이라 절감 효과는 거의 그대로다. 기준선은 "에러 예산을 태우는 이벤트는 100% 수집"이다([[sli-slo-sla]]).

2. **tail sampling을 켜면 컬렉터를 2계층으로 나눠라.** 1계층은 `loadbalancing` 익스포터에 `routing_key: traceID`만 걸고, 2계층에서 `tail_sampling`을 돌린다. 이 구성 없이 수평 확장하면 조각난 트레이스가 저장되고, 저장된 뒤에는 복구할 방법이 없다.

3. **`decision_wait`는 p99 요청 시간보다 조금 크게, 그 이상은 주지 마라.** p99가 3초면 5~8초. 기본값 30초를 쓰면 버퍼가 30초치로 부풀어 OOM으로 간다. `num_traces`(기본 5만)와 `expected_new_traces_per_sec`도 실측값으로 채운다.

4. **head 샘플링이면 확률을 데이터에 남겨라.** OTel SDK 환경변수는 `OTEL_TRACES_SAMPLER=parentbased_traceidratio`, `OTEL_TRACES_SAMPLER_ARG=0.01`이다. 확률이 `tracestate`에 기록되지 않으면 adjusted count로 총량을 복원할 수 없다.

5. **총량·에러율을 스팬에서 뽑아야 한다면 `spanmetrics` 커넥터를 샘플링 프로세서보다 앞에 둬라.** 전수 기준으로 집계된다. 순서가 뒤바뀌면 에러율이 샘플링률만큼 왜곡된다.

6. **버려진 트레이스의 빈자리는 로그와 exemplar로 메운다.** 모든 로그 라인에 `trace_id`를 넣어두면 트레이스가 없어도 로그를 trace ID로 묶어 볼 수 있다([[structured-logging]]). 히스토그램 exemplar로 p99 버킷에서 살아남은 트레이스로 들어가는 경로도 같이 깐다([[percentile-p99]]).

## 더 깊은 토끼굴

- [[distributed-tracing-otel]] — 컨텍스트 전파와 스팬 구조. 샘플링 플래그가 어디에 실려 다니는지.
- [[cardinality-explosion]] — 메트릭 쪽 비용 폭발. 트레이스로 옮기라고 권한 데이터가 여기서 샘플링에 지워진다.
- [[percentile-p99]] — 분포는 샘플링에 견디고 꼬리 한 건은 못 견디는 이유.
- [[structured-logging]] — 트레이스가 없을 때 로그를 trace ID로 묶는 폴백 경로.
- [[sli-slo-sla]] — 무엇을 100% 수집할지 정하는 기준선.
- [[error-budget]] — 수집 정책을 예산 소진 이벤트에 맞추기.

**1차 출처**
- W3C Trace Context (`traceparent` flags, `tracestate`): https://www.w3.org/TR/trace-context/
- OpenTelemetry — Sampling: https://opentelemetry.io/docs/concepts/sampling/
- Collector Contrib — Tail Sampling Processor (`decision_wait`, `num_traces`): https://github.com/open-telemetry/opentelemetry-collector-contrib/tree/main/processor/tailsamplingprocessor
- Collector Contrib — Load Balancing Exporter (`routing_key: traceID`): https://github.com/open-telemetry/opentelemetry-collector-contrib/tree/main/exporter/loadbalancingexporter
- OTel Spec — Probability Sampling with TraceState (`ot=th:`): https://opentelemetry.io/docs/specs/otel/trace/tracestate-probability-sampling/
