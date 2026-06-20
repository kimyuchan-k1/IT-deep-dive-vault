---
title: HPA는 CPU만 보고 즉시 늘려주는 게 아니다
date: 2026-06-20
day: 18
category: cloud
tags: [kubernetes, hpa, autoscaling, metrics-server]
related: ["[[liveness-readiness-startup]]", "[[k8s-pod-death-5-reasons]]", "[[percentile-p99]]"]
difficulty: 3
short_text: |
  ⚠️ [Day 18] HPA는 CPU 넘어도 즉시 못 늘린다

  오해: 부하2배=파드2배 즉시
  실제: 스크랩+sync+풀+readiness=2~3분 지연

  "그새 p99는 이미 터졌다"

  📖 https://github.com/kimyuchan-k1/IT-deep-dive-vault/blob/main/posts/2026-06-20-hpa-internals.md
---

# HPA는 CPU만 보고 즉시 늘려주는 게 아니다

## 흔한 오해

"HorizontalPodAutoscaler는 CPU가 목표치를 넘으면 자동으로 파드를 늘려준다. 트래픽이 2배가 되면 파드도 곧 2배가 되니까, 스파이크는 HPA가 알아서 흡수한다."

그래서 입문 가이드들은 `kubectl autoscale deployment web --cpu-percent=50 --min=2 --max=10` 한 줄을 보여주고 "끝났다"고 말한다. 마치 HPA가 트래픽을 실시간으로 따라가는 자동 온도조절기처럼.

틀린 건 아닌데, **언제 반응하는지**를 통째로 빼먹었다. HPA는 실시간이 아니다. 여러 개의 지연이 직렬로 쌓인 끝에야 새 파드가 트래픽을 받는다.

## 실제 원리

### HPA는 폴링 루프다

HPA 컨트롤러는 `--horizontal-pod-autoscaler-sync-period` 주기로 도는 루프다. 기본값 **15초**. 즉 HPA는 최대 15초에 한 번만 "지금 몇 개가 필요한가"를 계산한다. 그 사이에 트래픽이 어떻게 출렁이든 보지 않는다.

계산식은 단순한 비율이다:

```
desiredReplicas = ceil( currentReplicas × (currentMetricValue / desiredMetricValue) )
```

목표 CPU 50%인데 현재 평균이 100%면 `ceil(4 × (100/50))` = 8개. 비율로 한 번에 점프하는 구조라, 두 배 부하면 두 배를 요청하긴 한다. 문제는 `currentMetricValue`가 **이미 늦은 값**이라는 데 있다.

### 메트릭 자체가 과거다

HPA는 CPU를 직접 재지 않는다. `metrics-server`가 kubelet에서 긁어온 값을 읽는다. metrics-server의 기본 스크랩 주기는 `--metric-resolution` **15초**. 그래서 HPA가 읽는 CPU는 최악의 경우 15초 전 스냅샷이다.

여기에 더해 metrics-server는 보통 60초 가까운 평활(averaging) 윈도우 위에서 동작하므로, 30초짜리 짧은 스파이크는 평균에 묻혀 목표치를 안 넘기도 한다. 정리하면 HPA가 보는 부하는 **현재가 아니라 한참 전의 평균**이다.

### tolerance와 안정화 윈도우

비율이 1에 가까우면 HPA는 움직이지 않는다. 기본 `tolerance` **0.1**. 즉 목표 대비 ±10% 안이면 무시한다. 50% 목표에 53%가 떠도 스케일하지 않는다. 잦은 출렁임(flapping)을 막으려는 설계다.

스케일다운에는 안정화 윈도우가 붙는다. `behavior.scaleDown.stabilizationWindowSeconds` 기본 **300초**. 최근 5분간의 최대 추천치를 보고 내리기 때문에, 한 번 늘어난 파드는 5분간 안 줄어든다. 스케일업 쪽 안정화 윈도우는 기본 0초라 올리는 건 빠르지만, 그 "빠름"도 위의 폴링·메트릭 지연 뒤에서나 시작된다.

### 그래서 실제 반응 시간

스파이크가 와서 새 파드가 트래픽을 받기까지의 사슬은 이렇다:

```
메트릭 스크랩(≤15s) → HPA sync(≤15s) → 스케줄링 →
이미지 풀 → 컨테이너 기동 → readiness 통과 → 엔드포인트 등록
```

앞의 두 단계만으로도 최악 30초. 뒤에 [[liveness-readiness-startup]]의 readiness 게이트와 이미지 풀이 더해지면 새 파드가 실제로 응답하기까지 흔히 2~3분이 걸린다. HPA는 빠른 게 아니라 **여러 번 늦는다**.

## 현장 시나리오

한 커머스 서비스가 타임세일을 열었다. `web` 디플로이먼트는 `targetCPUUtilization: 50`, `min=4 max=20`. 평소 CPU 30%, 잘 돌았다.

세일 시작 14:00:00, 30초 만에 RPS가 4배로 뛰었다. 그런데:

- 14:00:00 트래픽 급증, 파드 CPU 즉시 95%로 포화
- 14:00:12 metrics-server 다음 스크랩 — 평활 윈도우 탓에 평균은 아직 68%
- 14:00:15 HPA sync — `ceil(4 × 68/50)` = 6개로 추천, 2개만 추가
- 14:00:30 CPU 평균이 마침내 95%에 도달, 다음 sync에서 8개 추천
- 14:00:45~14:02:30 새 파드들 스케줄→이미지풀→readiness 통과

결과: 트래픽이 4배가 된 첫 **2분 30초** 동안 기존 4개 파드가 모든 부하를 받았다. CPU 스로틀링으로 p99 응답이 200ms → 9초로 치솟고, readiness가 흔들리며 멀쩡한 파드까지 엔드포인트에서 빠졌다([[k8s-pod-death-5-reasons]]의 그 패턴). 정작 파드가 8개로 다 뜬 14:03에는 스파이크 피크가 이미 지나 있었다. HPA는 **지나간 불을 끄러 도착했다**.

## 실무 적용 포인트

1. **스파이크성 트래픽엔 CPU 말고 RPS를 타깃으로.** CPU는 본질적으로 후행 지표다. `Pods`/`External` 메트릭으로 파드당 초당 요청수(예: `targetAverageValue: 30`)를 직접 잡으면 부하 자체를 본다.
2. **이벤트 기반엔 KEDA를 얹어라.** Kafka lag, SQS 큐 길이처럼 "쌓인 일"을 직접 보고 0→N으로 스케일한다. CPU가 오르기를 기다리지 않는다.
3. **`scaleUp.policies`로 점프 폭을 키워라.** 기본은 15초마다 100% 증가지만, `type: Pods value: 8 periodSeconds: 15`처럼 한 번에 큰 폭을 허용하면 폴링 지연을 일부 상쇄한다.
4. **scaleDown은 보수적으로.** `scaleDown.stabilizationWindowSeconds: 300`을 유지하거나 늘려라. 스파이크 직후 성급히 줄이면 다음 파동에 또 늦는다.
5. **resource requests를 정확히.** HPA의 utilization은 `usage / request`다. request를 너무 낮게 잡으면 항상 100% 위라 max까지 튀고, 너무 높으면 영영 안 늘어난다.
6. **여유 파드(headroom)를 미리 깔아라.** `min`을 평소보다 한두 개 높게, 또는 예측 가능한 세일엔 cron으로 사전 스케일. HPA의 반응 지연은 설정으로 줄일 뿐 0으로 못 만든다.

## 더 깊은 토끼굴

HPA는 결국 "늦게, 평균값으로, 비율만큼" 움직이는 폴링 컨트롤러다. 트래픽을 실시간으로 따라간다고 믿는 순간 첫 2분을 잃는다.

- [[liveness-readiness-startup]] — 새 파드가 "응답 가능"으로 등록되는 마지막 게이트
- [[k8s-pod-death-5-reasons]] — 스케일아웃 도중 멀쩡한 파드가 빠지는 이유
- [[percentile-p99]] — 스케일 지연이 어디서 드러나는지(평균 말고 꼬리)
- [[retry-exponential-backoff-jitter]] — 파드가 뜨는 동안 클라이언트가 버티는 법
- [[circuit-breaker]] — 백엔드 포화 시 캐스케이드 차단

**1차 출처**: Kubernetes 공식 — Horizontal Pod Autoscaling (https://kubernetes.io/docs/tasks/run-application/horizontal-pod-autoscale/), HPA 알고리즘 세부 (https://kubernetes.io/docs/tasks/run-application/horizontal-pod-autoscale/#algorithm-details)
