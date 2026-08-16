---
title: CloudWatch를 버리고 월 4,880달러를 아꼈고, 블랙프라이데이에 메트릭이 통째로 사라졌다
date: 2026-08-16
day: 70
category: cloud
tags: [observability, cloudwatch, prometheus, datadog, cardinality, metrics]
related: ["[[distributed-tracing-otel]]", "[[structured-logging]]", "[[percentile-p99]]", "[[sli-slo-sla]]", "[[error-budget]]"]
difficulty: 2
short_text: |
  💡 [Day 70] 태그 하나가 210만 시리즈 만들었다
  오해: 셋 다 그래프 툴
  실제: CloudWatch=메트릭 수, Datadog=태그 조합, Prometheus=RAM
  📖 https://github.com/kimyuchan-k1/IT-deep-dive-vault/blob/main/posts/2026-08-16-observability-stack.md
---

# CloudWatch를 버리고 월 4,880달러를 아꼈고, 블랙프라이데이에 메트릭이 통째로 사라졌다

## 흔한 오해

> "CloudWatch, Datadog, Grafana — 결국 다 그래프 그려주는 도구 아닌가? 예산이랑 UI 취향으로 고르면 되지."

비교 글들이 대개 그렇게 쓰여 있으니 자연스러운 결론이다. 가격표, 대시보드 스크린샷, 통합 개수를 나란히 놓고 표로 만든다. 그래서 도입 회의도 "월 얼마"와 "우리 팀이 쓰기 편한가"에서 끝난다.

그런데 셋을 실제로 갈아타 본 팀은 같은 지점에서 다친다. 셋은 **메트릭을 저장하는 단위가 서로 다르고**, 그래서 같은 코드 한 줄이 어디서는 요금으로, 어디서는 프로세스 사망으로 나타난다. 고른 건 UI였는데 바뀐 건 데이터 모델이다.

## 실제 원리

### 저장 단위: 무엇이 "메트릭 하나"인가

**CloudWatch**에서 메트릭 하나는 `네임스페이스 + 메트릭 이름 + 차원(dimension) 집합`이다. 차원 집합이 다르면 이름이 같아도 완전히 다른 메트릭이다. 여기서 핵심은 AWS 문서에 명시된 제약이다 — **커스텀 메트릭은 차원을 가로질러 집계되지 않는다.** `Service=checkout, Endpoint=/pay`로 넣어놓고 `Service=checkout`만으로 조회하면 데이터가 없다. 서비스 단위 합계를 보고 싶으면 차원 집합을 하나 더 만들어 `PutMetricData`를 한 번 더 호출해야 하고, 그건 메트릭이 하나 더 생겼다는 뜻이며, 곧 요금이 하나 더 붙었다는 뜻이다.

**Datadog**에서 커스텀 메트릭 하나는 `메트릭 이름 + 태그 값의 조합`이다(호스트 태그 포함). 집계는 조회 시점에 한다. 그래서 태그를 붙여두면 나중에 마음대로 쪼갤 수 있다 — 대신 고유 조합 개수가 그대로 과금 단위가 된다.

**Prometheus**에서는 `메트릭 이름 + 라벨 집합` 조합 하나가 시계열 하나다. 공식 문서가 직접 경고한다: 고유한 키-값 라벨 조합 하나하나가 새 시계열이라고. 집계는 역시 조회 시점이고, 자체 호스팅이라 요금은 없다. 대신 **활성 시계열이 전부 head 블록으로 메모리에 상주한다.**

### 집계 시점 하나가 나머지를 다 결정한다

CloudWatch는 쓰기 시점 집계(write-time aggregation)다. 보낼 때 이미 잘라놓은 축으로만 볼 수 있다. 유연성이 없는 대신 시계열이 폭발하지 않는다.

Datadog과 Prometheus는 읽기 시점 집계(read-time aggregation)다. 원본 조합을 전부 들고 있어야 나중에 자를 수 있다. 그래서 "나중에 필요할지 모르니 태그 하나 더 붙여두자"는 습관이 CloudWatch에서는 애초에 불가능한 사치이고, 나머지 둘에서는 조용히 누적되는 부채가 된다.

### 퍼센타일은 사전 집계와 상극이다

`PutMetricData`는 원시 값 배열 대신 `StatisticSet`(min/max/sum/count)을 받을 수 있다. 호출 수를 줄이는 정석 최적화인데, 이걸 쓰면 그 메트릭에서 퍼센타일을 못 얻는다. 합과 개수만으로는 분포를 복원할 수 없기 때문이다. p99를 원하면 원시 값을 보내야 하고, 그럼 요청 수가 다시 늘어난다.

Prometheus 히스토그램은 여기서 다르게 푼다. 버킷별 누적 카운터라 **가법적(additive)** 이고, 그래서 인스턴스 여러 대의 버킷을 더한 뒤 `histogram_quantile()`을 돌리면 전체 분포의 근사 분위수가 나온다. 인스턴스별 p99를 평균 내는 짓([[percentile-p99]]에서 다룰 고전적 오류)을 안 해도 된다. 대가는 카디널리티다 — `le` 라벨 때문에 버킷 12개짜리 히스토그램 하나는 시계열 14개(버킷 12 + `_sum` + `_count`)를 만든다. 라벨 조합에 그대로 곱해진다.

### 보존과 해상도

CloudWatch는 자동 롤업이 있다. 1초 해상도는 3시간, 1분은 15일, 5분은 63일, 1시간은 455일 보관되고 오래된 구간은 알아서 거칠어진다. Prometheus 로컬 스토리지는 롤업이 없고 기본 보존이 15일이다(`--storage.tsdb.retention.time`). 디스크는 문서 기준 샘플당 1~2바이트로 싸지만, 장기 보관은 remote write로 Thanos/Mimir를 붙이는 별도 숙제가 된다.

## 현장 시나리오

국내 이커머스, EKS 위 서비스 40개. 관측성은 CloudWatch 커스텀 메트릭이었다.

차원 설계가 `Service(40) × Endpoint(30) × StatusCode(6)`였고 메트릭 이름이 4종이라 고유 메트릭이 28,800개. us-east-1 요금표로 첫 1만 개는 개당 월 $0.30, 그다음 구간은 개당 $0.10이라 월 $4,880이 나왔다. 게다가 서비스 단위 합계를 보려고 차원 집합을 하나 더 넣어 이중으로 쏘고 있었다 — 차원을 가로질러 집계해주지 않으니 다른 방법이 없었다.

그래서 Prometheus + Grafana 자체 호스팅으로 옮겼다. 노드 하나(16GiB 리밋)에 Prometheus, 대시보드는 Grafana. 요금은 0이 됐고 3개월간 잘 돌았다.

11월, 결제 팀에 합류한 개발자가 실패 카운터에 `order_id` 라벨을 추가했다. 실패한 주문을 바로 짚기 위해서였다. 이전 직장이 Datadog이었고 거기서 태그를 넉넉히 붙이는 건 나쁜 습관이 아니었다 — 청구서가 조금 늘 뿐이었다. PR은 "관측성 개선"으로 승인됐다.

블랙프라이데이. 주문이 몰리면서 `order_id`마다 새 시계열이 생겼고 head 시계열이 210만 개까지 올라갔다. Prometheus RSS가 14GB를 넘으면서 OOMKilled. 재시작하면 WAL을 리플레이하고 다시 스크레이프를 시작하는데, 그 순간 같은 카디널리티가 그대로 다시 쌓여서 또 죽었다. 크래시 루프.

문제는 그래프가 끊긴 게 아니었다. **알림 룰을 평가하는 주체가 죽은 Prometheus 본인**이었다. 결제 5xx가 3%에서 19%로 올라가는 동안 아무 알림도 발화하지 않았다. Grafana 대시보드는 CloudWatch 데이터소스로 폴백돼 CPU와 ALB 지표는 멀쩡히 보여줬고, 그래서 당직자는 22분 동안 "인프라는 정상"이라고 판단했다. 앱 에러율은 이관하면서 CloudWatch에서 뺀 지표였다.

원인 한 줄: **카디널리티는 사라지지 않는다. 청구 방식만 바뀐다.**

## 실무 적용 포인트

1. **무한 값 필드는 라벨/차원에 넣지 마라.** `order_id`, `user_id`, `request_id`, 원본 URL 경로, 이메일 — 전부 금지. 경로는 `/orders/{id}`로 정규화한다. 개별 식별자로 하나를 찾아내는 건 메트릭의 일이 아니라 [[structured-logging]]과 [[distributed-tracing-otel]]의 일이다. 메트릭은 "몇 건인가", 로그·트레이스는 "어느 건인가"다.

2. **카디널리티에 알람을 걸어라.** Prometheus는 `prometheus_tsdb_head_series` 게이지를 그대로 그리고, `topk(10, count by (__name__)({__name__=~".+"}))`로 범인 메트릭을 찾는다. 임계값은 노드 메모리에서 역산해라 — 시리즈당 메모리는 라벨 길이와 스크레이프 주기에 따라 수 KB 범위로 달라지니, 자기 클러스터의 `head_series` 대비 실제 RSS로 계수를 먼저 구하고 리밋의 60%를 임계로 잡는다.

3. **알림 평가를 관측 대상과 같은 프로세스에 두지 마라.** 위 사고의 본질은 메트릭 유실이 아니라 알림 유실이다. Alertmanager를 별도 파드로 띄우고, 데드맨 스위치(항상 발화하는 룰 하나 + 그게 안 오면 울리는 외부 감시)를 둬라. [[sli-slo-sla]]의 SLO 알림도 같은 프로세스에 얹혀 있으면 같이 죽는다.

4. **CloudWatch에서는 보고 싶은 축을 미리 다 쏴라.** 차원을 가로질러 집계해주지 않으므로 `[Service]`, `[Service, Endpoint]`처럼 차원 집합을 여러 개 명시적으로 `PutMetricData`한다. 그만큼 메트릭 개수가 늘고 요금이 는다(us-east-1 기준 첫 1만 개는 개당 월 $0.30). 설계 단계에서 축 개수를 곱해보고 예산을 먼저 계산해라.

5. **퍼센타일이 필요하면 `StatisticSet`을 쓰지 마라.** min/max/sum/count로는 분포가 복원되지 않는다. CloudWatch는 원시 값, Prometheus는 히스토그램을 쓴다. 단 버킷 수가 시계열 배수라는 걸 감안해 8~12개로 제한하고, SLO 임계값 주변에 버킷을 몰아 배치한다([[error-budget]]에서 쓰는 그 임계값이다).

6. **셋 중 하나를 고르지 말고 계층을 나눠라.** AWS 인프라 지표는 어차피 CloudWatch에 쌓이니 그대로 두고, 앱 지표만 Prometheus로, 대시보드는 Grafana에서 두 데이터소스를 함께 본다. 다만 Grafana의 CloudWatch 데이터소스는 `GetMetricData`를 호출하고 이건 요청 메트릭 1,000개당 $0.01로 과금된다 — 30초마다 갱신되는 대형 대시보드 하나가 조용히 청구서를 만든다. 새로고침을 5분으로 늘리면 열 배가 줄어든다.

## 더 깊은 토끼굴

- [[percentile-p99]] — 인스턴스별 p99를 평균 내면 왜 틀리나
- [[distributed-tracing-otel]] — 개별 요청 추적은 메트릭이 아니라 여기서
- [[structured-logging]] — 고유 식별자가 있어야 할 자리
- [[sli-slo-sla]] — 어떤 지표에 알림을 걸 것인가
- [[error-budget]] — 히스토그램 버킷을 어디에 배치할지 결정하는 기준

**출처**
- AWS 공식 문서, "Amazon CloudWatch concepts" — https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/cloudwatch_concepts.html (메트릭 = 네임스페이스+이름+차원 집합, 커스텀 메트릭은 차원을 가로질러 집계되지 않음, 해상도별 보존 기간)
- AWS, CloudWatch 요금 — https://aws.amazon.com/cloudwatch/pricing/ (커스텀 메트릭 구간별 단가, `GetMetricData` 요청 과금)
- Prometheus 공식 문서, "Metric and label naming" — https://prometheus.io/docs/practices/naming/ (고유 라벨 조합 하나가 새 시계열)
- Prometheus 공식 문서, "Storage" — https://prometheus.io/docs/prometheus/latest/storage/ (샘플당 1~2바이트, 기본 보존 15일, head 블록 구조)
- Datadog 공식 문서, "Custom Metrics" — https://docs.datadoghq.com/metrics/custom_metrics/ (커스텀 메트릭 = 메트릭 이름 + 태그 값 조합)
