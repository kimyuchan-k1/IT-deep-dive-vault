---
title: Spot은 2분 전에 알려준다. 그래서 60대가 동시에 죽었을 때 아무 소용이 없었다
date: 2026-08-22
day: 75
category: cloud
tags: [spot-instance, ec2, capacity-pool, karpenter, node-drain, autoscaling]
related: ["[[hpa-internals]]", "[[liveness-readiness-startup]]", "[[blue-green-canary-rolling]]", "[[k8s-pod-death-5-reasons]]", "[[chaos-engineering-intro]]"]
difficulty: 3
short_text: |
  ⚠️ [Day 75] Spot 2분 경고, 60대 동시에 죽으면 소용없다

  오해: 2분이면 드레인 충분
  실제: 회수는 풀 단위 동시 발생→대체 노드 3분→그 사이 용량 붕괴

  📖 https://github.com/kimyuchan-k1/IT-deep-dive-vault/blob/main/posts/2026-08-22-spot-instance-safe.md
---

# Spot은 2분 전에 알려준다. 그래서 60대가 동시에 죽었을 때 아무 소용이 없었다

## 흔한 오해

> "Spot은 언제든 회수될 수 있지만 2분 전에 알려준다. 그 2분 동안 드레인하고 새 노드 띄우면 되니까, 스테이트리스 워크로드면 안전하다."

가격표만 보면 반박할 이유가 없다. 온디맨드 대비 최대 90% 싸고, AWS는 회수 2분 전에 인스턴스 메타데이터로 통지한다. 그래서 "graceful shutdown만 잘 짜면 된다"는 결론이 나온다. 대부분의 Spot 가이드가 여기까지만 설명한다.

두 가지를 빠뜨렸다. 첫째, **2분은 한 대의 인스턴스에 주어지는 시간이지 클러스터에 주어지는 시간이 아니다.** 둘째, **회수는 인스턴스 단위로 무작위로 오지 않는다. 풀 단위로 한꺼번에 온다.** 이 둘이 겹치면 2분짜리 유예는 있으나 마나 한 값이 된다.

## 실제 원리

### 회수의 단위는 인스턴스가 아니라 capacity pool

Spot 용량은 **(인스턴스 타입 × 가용영역) 조합**, 즉 capacity pool 단위로 관리된다. `m5.large` × `ap-northeast-2a`는 하나의 풀이고, `m5.large` × `2c`는 다른 풀이다.

회수가 일어나는 이유는 그 풀의 여유 용량이 온디맨드 수요에 밀려났기 때문이다. 원인이 풀에 있으니 결과도 풀 전체에 걸린다. 같은 풀에 인스턴스 60대를 띄워놨다면, 회수 신호는 60대에 **거의 동시에** 도착한다. 한 대씩 순차적으로 빠지는 그림은 애초에 존재하지 않는다.

여기가 핵심이다. 대부분의 Spot 사고는 "Spot이 회수됐다"가 아니라 **"한 풀에 몰빵했다"**가 원인이다.

### 2분 안에 실제로 끝나야 하는 일들

2분(120초)을 쪼개보면 여유가 없다는 게 보인다. 쿠버네티스 노드 한 대 기준으로:

```
t=0    interruption notice 도착 (IMDS /latest/meta-data/spot/instance-action)
t=0~5  핸들러가 폴링으로 감지 (기본 폴링 간격만큼 늦어짐)
t=5    노드 cordon + drain 시작
t=5~   파드별 preStop hook → SIGTERM → terminationGracePeriodSeconds(기본 30초)
       + LB deregistration delay(ALB 타깃그룹 기본 300초)
t=120  인스턴스 강제 종료. 남은 건 전부 끊김
```

ALB 타깃그룹의 `deregistration_delay.timeout_seconds` 기본값이 300초라는 점만으로도 2분 예산은 이미 초과다. 드레인이 끝나기 전에 인스턴스가 사라지면, 그 노드로 향하던 인플라이트 요청은 502로 돌아온다.

### 대체 용량은 2분 안에 오지 않는다

빠진 파드는 어딘가에 다시 스케줄돼야 한다. 여유 노드가 없으면 새 노드를 띄워야 하고, 그 경로는 이렇다: 오토스케일러가 pending 파드 감지(수십 초) → 인스턴스 부팅 → kubelet Ready → 이미지 pull → readiness probe 통과. 이미지가 크면 **3분 이상**도 흔하다.

즉 타임라인은 이렇게 어긋난다.

```
용량 빠짐:  ▓▓ (2분 안에 완료)
용량 복구:  ......▓▓▓▓ (3분 이상 뒤 시작)
            └─ 이 갭 동안 남은 노드가 전부를 받는다
```

갭 구간에서 남은 노드들이 평소의 몇 배 트래픽을 받는다. 여기서 [[hpa-internals]]나 커넥션 풀 한계에 먼저 부딪히면, Spot 회수가 아니라 **잔여 노드의 연쇄 과부하**가 실제 장애 원인이 된다.

### 알림은 사실 두 종류다

- **Rebalance recommendation**: 회수 위험이 높아졌다는 조기 신호. 2분 통지보다 먼저 올 수 있지만 **리드타임 보장이 없고**, 왔다고 반드시 회수되는 것도 아니다.
- **Interruption notice**: 확정 통보. 2분.

앞의 것을 구독해 미리 대체 노드를 띄우는 게 유일한 시간 벌기다. 뒤의 것만 보고 움직이면 항상 늦는다.

## 현장 시나리오

이커머스 API를 EKS에서 굴리던 팀이 비용 절감 과제를 받았다. 노드 그룹을 Spot 100%로 바꾸고, "검증된 조합"이라며 인스턴스 타입을 `c5.2xlarge` 하나로 고정했다. AZ는 두 개였지만 트래픽 지연 때문에 `ap-northeast-2a`에 스케줄링이 쏠려 있었다. 그 풀에 60대.

평일 오후 2시 12분, `c5.2xlarge` × `2a` 풀에 온디맨드 수요가 몰렸다. 그 풀의 Spot 인스턴스에 회수 통지가 일제히 나갔다. 60대 중 47대.

- **14:12:00** — interruption notice 47대 동시 수신
- **14:12:04** — Node Termination Handler가 47대 cordon, drain 시작
- **14:12:06** — 파드 축출 시도. `minAvailable: 100%`로 설정된 PDB가 결제 서비스 축출을 거부. 드레인이 그 자리에서 멈춤
- **14:12:30** — 살아남은 13대로 전체 트래픽 유입. 커넥션 풀 포화, p99가 180ms에서 4초로
- **14:14:00** — 47대 강제 종료. 드레인 못 끝낸 파드는 그냥 사라짐. ALB는 아직 디레지스터 안 된 타깃으로 계속 라우팅 → 502 급증
- **14:15:20** — 오토스케일러가 온디맨드로 대체 시도. 그런데 같은 시각 다른 팀들도 같은 풀에서 밀려나 온디맨드로 몰림. 인스턴스 확보에 추가 지연
- **14:17:40** — 새 노드 readiness 통과. 정상화

**5분 28초, 5xx 비율 최대 31%.** 사후에 남은 한 줄: Spot이 아니라 **풀 하나에 47대를 올려둔 것**이 원인이었다. 인스턴스 타입 3개 × AZ 3개로 흩어놨다면 한 번에 빠지는 건 전체의 1/9였다.

## 실무 적용 포인트

1. **capacity pool을 최소 6~10개로 분산한다.** 인스턴스 타입 3종 이상 × AZ 3개. ASG는 `MixedInstancesPolicy` + allocation strategy를 `price-capacity-optimized`로 (가장 싼 풀이 아니라 **여유 용량이 많은 풀**을 고른다). Karpenter면 NodePool의 `requirements`에서 인스턴스 타입을 좁게 고정하지 말고 family/size 범위로 열어둔다.

2. **온디맨드 베이스를 남긴다.** `OnDemandBaseCapacity`로 정상 트래픽의 30~40%는 온디맨드에 고정. Spot이 통째로 빠져도 서비스가 죽지 않는 하한선이다. 비용 절감분이 90%에서 60%로 줄지만, 그게 Spot의 실제 가격이다.

3. **2분 예산을 역산해서 타임아웃을 맞춘다.** ALB 타깃그룹 `deregistration_delay.timeout_seconds`를 300 → **30초**로, 파드 `terminationGracePeriodSeconds`는 **60~90초**로, preStop에 `sleep 5~10`을 넣어 디레지스터가 SIGTERM보다 먼저 반영되게 한다. 합이 120초를 넘으면 그 초과분은 그대로 502다.

4. **rebalance recommendation을 구독한다.** EventBridge → SQS로 받아 Karpenter interruption queue나 AWS Node Termination Handler에 연결한다. IMDS 폴링만 쓸 거면 간격을 **2초 이하**로. 5초 폴링은 2분 예산의 4%를 그냥 버린다.

5. **PDB에 `minAvailable: 100%`를 쓰지 않는다.** 드레인을 영구 블록해서, 결국 유예시간을 다 쓰고 강제 종료(=graceful 아님)로 끝난다. `maxUnavailable: 1`처럼 진행 가능한 값으로 두고, 레플리카는 3개 이상.

6. **여유 노드를 미리 만들어둔다.** priorityClass를 음수(-1)로 준 pause 파드를 띄워 노드를 선점해두면, 회수 시 실제 파드가 그 자리를 즉시 뺏는다. 노드 부팅 3분이 스케줄링 수 초로 바뀐다.

7. **배치 작업은 체크포인트 간격을 2분보다 짧게.** 2분 안에 저장 못 하면 그 작업은 처음부터 다시다. 진행률이 아니라 **재개 가능 지점**을 기준으로 잡는다.

8. **주기적으로 실제로 죽여본다.** [[chaos-engineering-intro]] 방식으로 노드를 임의 종료해보지 않았다면, 위 설정이 맞는지 확인된 적이 없는 것이다.

## 더 깊은 토끼굴

- [[hpa-internals]] — 용량이 급감했을 때 HPA가 왜 제때 못 따라오는가
- [[liveness-readiness-startup]] — readiness 통과 시점이 대체 용량 복구 시간을 결정한다
- [[k8s-pod-death-5-reasons]] — 드레인 실패로 죽는 파드의 종료 코드 읽는 법
- [[blue-green-canary-rolling]] — 배포 중 Spot 회수가 겹칠 때의 조합 폭발
- [[chaos-engineering-intro]] — 회수 시나리오를 훈련으로 검증하는 방법

**출처**
- AWS EC2 User Guide, Spot Instance interruptions: https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/spot-interruptions.html
- AWS EC2 User Guide, EC2 instance rebalance recommendations: https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/rebalance-recommendations.html
- AWS Node Termination Handler: https://github.com/aws/aws-node-termination-handler
- Karpenter, Disruption / interruption handling: https://karpenter.sh/docs/concepts/disruption/
