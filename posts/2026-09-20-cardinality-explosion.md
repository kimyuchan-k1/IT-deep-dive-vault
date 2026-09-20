---
title: 라벨 하나 추가했다. Prometheus가 OOM으로 죽었고 그동안 알림이 꺼져 있었다
date: 2026-09-20
day: 97
category: observability
tags: [prometheus, cardinality, tsdb, metrics, relabeling]
related: ["[[percentile-p99]]", "[[distributed-tracing-otel]]", "[[structured-logging]]", "[[observability-stack]]", "[[k8s-pod-death-5-reasons]]"]
difficulty: 3
short_text: |
  🔥 [Day 97] 라벨 하나 추가하고 Prometheus가 OOM으로 죽었다

  오해: 라벨은 공짜다
  실제: 라벨 곱→시리즈 폭증→OOM→재시작 9분간 알림 실명

  📖 https://github.com/kimyuchan-k1/IT-deep-dive-vault/blob/main/posts/2026-09-20-cardinality-explosion.md
---

# 라벨 하나 추가했다. Prometheus가 OOM으로 죽었고 그동안 알림이 꺼져 있었다

## 흔한 오해

> "라벨은 그냥 태그 아닌가? 나중에 필터링하려면 많이 붙여두는 게 이득이지. 로그에 필드 추가하는 거랑 비슷한 비용일 텐데."

로그는 실제로 그 정도다. 줄마다 바이트가 조금 늘고 끝이다. 메트릭은 저장 모델이 다르다. Prometheus에서 **라벨 값 하나가 바뀌면 같은 메트릭의 다른 값이 아니라 완전히 다른 시계열**이다. 저장 단위가 메트릭이 아니라 시계열이고, 시계열 수는 라벨 값 개수의 **곱**으로 늘어난다.

"필터링용으로 넉넉히 붙여두자"는 판단은 덧셈을 예상하고 곱셈을 사는 것이다.

## 실제 원리

### 1. 시계열의 신원은 라벨 집합 전체다

Prometheus TSDB에서 시계열을 식별하는 키는 메트릭 이름과 모든 라벨 키·값 쌍의 집합이다. `__name__`도 사실 라벨이다.

```
http_requests_total{method="GET", status="200", pod="api-7d4f"}   ← 시리즈 A
http_requests_total{method="GET", status="200", pod="api-9b21"}   ← 시리즈 B (완전히 별개)
```

`pod` 값이 하나 늘면 시계열이 하나 느는 게 아니라 나머지 라벨 조합 수만큼 늘어난다. 라벨 4개가 각각 값 10개면 상한은 10⁴ = 10,000 시계열이다. 실제 카디널리티는 관측된 조합 수라 보통 이보다 작지만 **라벨 추가의 비용은 곱셈**이라는 성질은 그대로다.

여기가 핵심이다. 라벨 값이 유한하고 작은 집합이면 곱은 관리 가능하다. 값 집합이 무한하면 — 사용자 ID, 주문 ID, 요청 URL 원본, 에러 메시지 문자열 — 곱은 시간이 지날수록 발산한다.

### 2. 비용이 붙는 곳은 디스크가 아니라 head 인덱스다

Prometheus는 최근 구간(기본 2시간)을 head 블록으로 **메모리에** 들고 있다. 샘플은 압축이 잘 먹어 샘플당 수 바이트다. 비싼 건 시계열마다 따라붙는 고정 비용이다.

- 활성 시계열마다 열려 있는 청크와 메타데이터
- 라벨 쌍 → 시리즈 ID 목록 역색인(postings). 쿼리는 이 목록들의 교집합으로 풀린다
- 라벨 값 문자열 사전

경험칙으로 활성 시계열 100만 개면 RSS가 수 GB 단위로 잡힌다. 정확한 값은 라벨 길이와 쿼리 패턴에 따라 흔들리지만 **메모리가 시계열 수에 거의 선형으로 붙는다**는 방향은 변하지 않는다. 샘플 주기를 15초에서 30초로 늘리는 최적화가 카디널리티에 거의 효과가 없는 이유가 이거다. 줄어드는 건 샘플이지 시계열이 아니다.

### 3. 히스토그램은 곱을 한 번 더 곱한다

히스토그램 하나는 시계열 하나가 아니다. 버킷 12개(`+Inf` 포함)짜리 히스토그램은 라벨 조합 1개당 `_bucket` 12개 + `_sum` + `_count` = **14 시계열**이다.

`endpoint` 40 × `method` 4 × `status` 5 × `pod` 120 조합에 히스토그램을 붙이면 상한이 96,000 × 14 = 134만 시계열이다. [[percentile-p99]]를 제대로 보려고 버킷을 촘촘히 깐 팀이 정확히 이 지점에서 무너진다.

### 4. 사라진 시계열도 바로 사라지지 않는다 (churn)

배포할 때마다 파드 이름이 바뀌면 `pod` 라벨 값이 전부 새것이 된다. 이전 시계열은 스크레이프가 끊겨도 head 보존 구간 동안 메모리에 남는다. 하루에 30번 배포하는 서비스는 카디널리티 상한이 아니라 **생성 속도**가 문제다.

## 현장 시나리오

결제 API를 운영하는 팀. Prometheus 1대, 활성 시계열 약 120만, RSS 14GB, 노드 메모리 32GB. 평온했다.

장애 조사 중 "어떤 주문이 느린지 보고 싶다"는 요구가 나왔다. 한 개발자가 HTTP 미들웨어에서 히스토그램 라벨 `path`를 라우트 템플릿(`/orders/:id`)이 아니라 **요청 URL 원본**으로 바꿨다. 코드 한 줄, 리뷰도 통과했다.

```
path 라벨 = 원본 URL (/orders/9a3f-b21c-...)
  → 주문 ID마다 새 시계열, 히스토그램이라 조합당 14개
  → 30분 만에 활성 시계열 120만 → 810만
  → head 인덱스·청크가 메모리를 밀어올림, RSS 14GB → 31GB
  → 컨테이너 메모리 limit 초과, OOMKilled
  → 재시작하며 비대해진 WAL을 replay, 9분간 기동 불가
  → 그동안 스크레이프도, 알림 룰 평가도 멈춤
  → 같은 시각 결제 승인 실패율이 4%로 올라갔는데 아무도 호출받지 못함
  → 고객 문의로 15분 뒤에 인지
```

복구 후 `promtool tsdb analyze`를 돌리니 메트릭 1개가 전체 시계열의 83%를 먹고 있었다. 되돌린 조치는 셋이다. `path`를 라우트 템플릿으로 정규화, `sample_limit` 부여, 개별 주문 추적은 트레이스로 이동([[distributed-tracing-otel]]).

원인 한 줄. 메트릭 라벨에 무한 집합을 넣었고, 그 대가를 관측 시스템 자신이 먼저 치렀다.

진짜 비용은 Prometheus가 죽은 게 아니다. **죽은 동안 다른 장애를 못 봤다는 것**이다. 관측 시스템의 장애는 언제나 두 번 계산된다.

## 실무 적용 포인트

1. **라벨 값 집합이 유한한지부터 확인하라.** 사용자 ID, 주문 ID, 세션 ID, 원본 URL, 에러 메시지, 타임스탬프는 라벨에 넣지 않는다. Prometheus 공식 계측 가이드는 라벨 하나의 값 개수를 10 미만으로 유지하고, 그걸 넘는 라벨은 시스템 전체에 몇 개만 두라고 권고한다. 경로는 반드시 라우트 템플릿(`/orders/:id`)으로 정규화한다.

2. **스크레이프 단계에 하드 가드를 걸어라.** `scrape_config`의 `sample_limit`으로 타깃당 샘플 상한을, `label_limit` / `label_value_length_limit`(Prometheus 2.27+)로 라벨 개수·길이 상한을 건다. 한 타깃이 폭발하면 그 타깃만 실패하고 나머지는 산다. 상한이 없으면 실패가 전체로 번진다.

3. **이미 나간 라벨은 `metric_relabel_configs`로 잘라라.** 코드 배포를 기다릴 필요가 없다.
```yaml
metric_relabel_configs:
  - source_labels: [__name__]
    regex: 'http_server_requests_seconds_bucket'
    action: drop
  - regex: 'customer_id|request_id'
    action: labeldrop
```
`labeldrop`은 라벨을 지워 조합을 합치고 `drop`은 시계열을 통째로 버린다. 적용 시점은 스크레이프 직후·저장 직전이다.

4. **히스토그램 버킷 수를 세어라.** 조합당 `버킷 수 + 2` 시계열이다. 버킷 20개짜리를 라벨 조합 5,000개에 붙이면 11만 시계열이다. SLO 임계값 근처만 촘촘하게 깔고 나머지는 듬성하게 간다. 버킷 문제를 구조적으로 없애려면 native histogram(Prometheus 2.40에 실험적 기능으로 도입, `--enable-feature=native-histograms`)을 검토한다. 조합당 시계열이 1개로 떨어진다.

5. **개별 식별자는 트레이스·로그로 보낸다.** "이 주문이 왜 느린가"는 메트릭의 질문이 아니다. 메트릭은 집계된 분포를 보고, 이상 구간에서 exemplar로 트레이스 ID를 타고 들어간다. 식별자 단위 조회는 [[structured-logging]]의 영역이다.

6. **카디널리티를 상시 지표로 감시하라.** `prometheus_tsdb_head_series`로 총량을, `rate(prometheus_tsdb_head_series_created_total[5m])`로 생성 속도(churn)를 본다. 범인 찾기는 `topk(10, count by (__name__)({__name__=~".+"}))`, `/tsdb-status` 페이지, 오프라인이면 `promtool tsdb analyze`다. 총량 증가율에 알림을 걸면 OOM 전에 잡힌다([[observability-stack]]).

## 더 깊은 토끼굴

- [[percentile-p99]] — 히스토그램 버킷 설계. 정확도를 높이려는 선택이 카디널리티 청구서로 돌아오는 지점.
- [[distributed-tracing-otel]] — 고유 식별자가 원래 살아야 할 곳. exemplar로 메트릭과 잇는다.
- [[structured-logging]] — 라벨에 넣으면 안 되는 필드를 받아주는 저장소.
- [[observability-stack]] — 백엔드마다 카디널리티 과금 모델이 다르다.
- [[k8s-pod-death-5-reasons]] — OOMKilled가 관측 스택 자신에게 일어났을 때의 특수성.

**1차 출처**
- Prometheus Docs — Instrumentation best practices (Do not overuse labels): https://prometheus.io/docs/practices/instrumentation/#do-not-overuse-labels
- Prometheus Docs — Storage (TSDB head, WAL): https://prometheus.io/docs/prometheus/latest/storage/
- Prometheus Docs — Configuration (`scrape_config`, `metric_relabel_configs`, `sample_limit`, `label_limit`): https://prometheus.io/docs/prometheus/latest/configuration/configuration/#scrape_config
- Prometheus Docs — Feature flags (native histograms): https://prometheus.io/docs/prometheus/latest/feature_flags/
