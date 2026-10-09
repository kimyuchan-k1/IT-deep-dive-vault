---
title: maxSkew 1을 걸었는데 AZ 하나 장애로 파드 절반이 죽었다. 노드 없는 AZ는 계산에 없었다
date: 2026-10-09
day: 106
category: cloud
tags: [kubernetes, topology-spread, availability-zone, scheduler, cluster-autoscaler]
related: ["[[aws-vpc-design]]", "[[hpa-internals]]", "[[k8s-pod-death-5-reasons]]", "[[spot-instance-safe]]", "[[bulkhead-pattern]]", "[[chaos-engineering-intro]]"]
difficulty: 3
short_text: |
  ⚠️ [Day 106] AZ 3개에 펼쳤는데 절반이 죽었다

  오해: maxSkew 1이면 AZ마다 균등
  실제: 노드 없는 AZ는 도메인 아님→2개 AZ에만 분산

  📖 https://github.com/kimyuchan-k1/IT-deep-dive-vault/blob/main/posts/2026-10-09-pod-topology-spread.md
---

# maxSkew 1을 걸었는데 AZ 하나 장애로 파드 절반이 죽었다. 노드 없는 AZ는 계산에 없었다

## 흔한 오해

> "`topologySpreadConstraints`에 `topologyKey: topology.kubernetes.io/zone`, `maxSkew: 1`을 걸면 파드가 AZ 3개에 고르게 퍼진다. AZ 하나가 죽어도 2/3는 산다."

설정 예제가 거의 다 이 한 블록이다. 그래서 "걸어 두면 끝"이라고 믿기 쉽다. 틀린 지점은 세 개다. 하나, 스케줄러가 세는 AZ는 **지금 노드가 있는 AZ**뿐이다. 둘, 이 제약은 **스케줄링 순간에만** 검사된다. 셋, `ScheduleAnyway`는 제약이 아니라 점수 가산점이다. 셋 다 평소에는 안 보이고 AZ 장애가 나야 드러난다.

## 실제 원리

### 1. skew는 "도메인" 사이에서만 계산된다

스케줄러는 `topologyKey` 라벨 값마다 도메인을 만든다. 도메인별로 `labelSelector`에 맞는 파드 수를 센다. 새 파드를 어떤 도메인에 놓았을 때 `(그 도메인 파드 수 + 1) - (전체 도메인 중 최솟값)`이 `maxSkew` 이하면 통과다.

여기서 핵심은 **도메인 목록의 출처**다. 도메인은 파드가 갈 수 있는 노드(nodeAffinity·nodeSelector 통과)의 라벨에서 나온다. `ap-northeast-2c`에 노드가 0대면 2c는 도메인이 아니다. 최솟값 계산에도 안 들어간다.

```
노드 있음:   2a [■■■■■■]   2b [■■■■■■]   2c (노드 0대)
도메인:      2a, 2b        → skew = 6 - 6 = 0  ✅ 제약 만족
실제 분산:   AZ 2개
```

스케줄러 입장에서는 완벽하게 균등하다. 2c가 존재한다는 사실 자체를 모른다.

### 2. minDomains: "도메인이 모자라면 최솟값을 0으로"

이 구멍을 막는 필드가 `minDomains`다. 조건 맞는 도메인 수가 `minDomains`보다 적으면 스케줄러는 **전역 최솟값을 0으로 간주**한다. `minDomains: 3`, 도메인 2개, 각 AZ에 파드 1개씩이 이미 있다면 새 파드의 skew는 `(1+1) - 0 = 2`다. `maxSkew: 1`을 넘으니 파드는 `Pending`이 된다.

Pending 파드가 생기면 Cluster Autoscaler나 Karpenter가 반응한다. 2c 노드 그룹을 늘려야 이 파드가 뜬다는 걸 시뮬레이션으로 알아낸다. 즉 `minDomains`는 "빈 AZ에 노드를 강제로 띄우게 만드는 장치"다. `whenUnsatisfiable: DoNotSchedule`일 때만 동작한다.

### 3. ScheduleAnyway는 점수일 뿐이다

`whenUnsatisfiable: ScheduleAnyway`는 필터가 아니라 스코어링 단계에 들어간다. skew를 줄이는 노드에 점수를 더 줄 뿐이다. 리소스 여유, 이미지 로컬리티, affinity 점수와 합산된다. 한 AZ 노드에 CPU가 남아돌면 skew 점수를 이기고 그쪽으로 몰린다.

kube-scheduler에는 사용자가 아무것도 안 써도 붙는 **기본 제약**이 있다. hostname 기준 `maxSkew: 3`, zone 기준 `maxSkew: 5`, 둘 다 `ScheduleAnyway`다. "기본으로 AZ 분산된다"는 말은 이 약한 선호를 가리킨다. 보장이 아니다.

### 4. 한 번 놓이면 다시 안 본다

제약은 파드 배치 시점에만 검사된다. 이후 노드가 빠지거나 스케일 인이 일어나도 스케줄러는 재배치하지 않는다. ReplicaSet이 축소할 때 지울 파드를 고르는 기준에도 AZ 분산은 없다. 그래서 스케일 인 몇 번이면 skew가 조용히 벌어진다. 사후 교정은 descheduler의 `RemovePodsViolatingTopologySpreadConstraint` 같은 별도 컴포넌트 몫이다.

### 5. 롤링 업데이트 중 셈이 섞인다

`labelSelector: app=api`는 구버전 ReplicaSet 파드와 신버전 파드를 같이 센다. 롤링 중 새 파드는 "구버전 파드가 적은 AZ"로 간다. 구버전이 다 빠지고 나면 신버전만 한쪽에 쏠릴 수 있다. `matchLabelKeys: ["pod-template-hash"]`를 주면 같은 리비전 파드끼리만 센다.

## 현장 시나리오

결제 승인 API. EKS, 노드 그룹은 AZ별로 3개(`ng-2a`, `ng-2b`, `ng-2c`), 각각 `min 0`. 디플로이먼트에는 zone 기준 `maxSkew: 1`, `DoNotSchedule`이 걸려 있었다. `minDomains`는 없었다.

1. 새벽 트래픽 저점에 HPA가 replicas를 12 → 4로 축소 → ReplicaSet이 2c 파드부터 지움(AZ 고려 없음)
2. 2c 노드들이 비자 Cluster Autoscaler가 scale-down → `ng-2c` 노드 0대 → 2c 도메인 소멸
3. 오전 트래픽 증가, HPA가 4 → 12로 확장 → 스케줄러가 본 도메인은 2a·2b뿐 → 6:6으로 배치, skew 0으로 제약 만족
4. Pending 파드가 없으니 오토스케일러는 `ng-2c`를 늘릴 이유가 없음 → 2c는 하루 종일 0대
5. 14시, 2a에서 네트워크 장애 → 노드 `NotReady`, 파드 6개 트래픽 단절 → 기본 `tolerationSeconds: 300` 동안 축출도 안 됨
6. 남은 2b 파드 6개가 2배 트래픽 → CPU 90% → p99 승인 지연 4초 → PG사 타임아웃으로 결제 실패율 7%

대시보드상 "3 AZ 분산 + maxSkew 1"은 내내 초록불이었다. 수정은 `minDomains: 3` 한 줄과 PDB였다. 이후 2c 노드가 0대가 되면 파드가 Pending으로 떠서 오토스케일러가 2c를 다시 채웠다.

## 실무 적용 포인트

1. **zone 제약에는 `minDomains`를 AZ 수만큼.** `minDomains: 3` + `whenUnsatisfiable: DoNotSchedule` 조합이어야 의미가 있다. 리전 AZ 수보다 크게 잡으면 영원히 Pending이니 정확히 맞춘다.

2. **오토스케일러가 AZ를 알게 한다.** Cluster Autoscaler는 노드 그룹 하나가 여러 AZ에 걸치면 새 노드가 어느 AZ에 뜰지 모른다. AZ별 노드 그룹을 만들고 `--balance-similar-node-groups=true`를 켠다. Karpenter는 NodePool `requirements`에 `topology.kubernetes.io/zone` 3개를 모두 열어 둔다.

3. **두 겹으로 건다.** zone 제약 `maxSkew: 1, DoNotSchedule`과 hostname 제약 `maxSkew: 1, ScheduleAnyway`를 같이 쓴다. AZ 분산은 강제, 노드 분산은 선호로 둔다. 둘 다 DoNotSchedule이면 노드 부족 시 Pending이 쉽게 난다.

4. **`matchLabelKeys: ["pod-template-hash"]` 추가.** 롤링 업데이트마다 신·구 ReplicaSet이 섞여 계산되는 문제를 막는다. 분산 상태 확인은 `kubectl get pods -l app=api -o wide`에 노드별 zone을 조인해 AZ별 개수를 센다. 최대/최소 차이가 maxSkew보다 크면 descheduler를 돈다.

5. **AZ 하나를 잃어도 버틸 replicas.** AZ 3개면 정상 부하의 1.5배를 2개 AZ로 감당해야 한다. HPA `minReplicas`를 "피크 필요 파드 수 × 3/2" 이상으로 잡고, PDB `maxUnavailable: 33%`로 자발적 축출도 AZ 하나 분량 이내로 묶는다.

6. **EBS 볼륨을 쓰면 `volumeBindingMode: WaitForFirstConsumer`.** `Immediate`면 볼륨이 먼저 한 AZ에 만들어지고 파드가 그 AZ에 묶인다. 분산 제약이 볼륨 위치를 이기지 못한다.

## 더 깊은 토끼굴

- [[aws-vpc-design]] — 서브넷을 AZ별로 나누는 이유. 노드 그룹 AZ 분리의 전제.
- [[hpa-internals]] — 스케일 인/아웃이 분산 상태를 어떻게 흔드는지.
- [[k8s-pod-death-5-reasons]] — NotReady 노드의 파드가 5분간 남아 있는 축출 타이밍.
- [[spot-instance-safe]] — 스팟 회수가 한 AZ에 몰릴 때 분산 제약과의 관계.
- [[bulkhead-pattern]] — AZ를 장애 격벽으로 쓰는 설계.
- [[chaos-engineering-intro]] — AZ 하나를 일부러 끊어 보는 게임데이.

**1차 출처**
- Kubernetes 문서, "Pod Topology Spread Constraints" (minDomains, matchLabelKeys, 클러스터 기본 제약): https://kubernetes.io/docs/concepts/scheduling-eviction/topology-spread-constraints/
- KEP-3022, "minDomains in PodTopologySpread": https://github.com/kubernetes/enhancements/tree/master/keps/sig-scheduling/3022-min-domains-in-pod-topology-spread
- Cluster Autoscaler FAQ — 멀티 AZ 노드 그룹과 `--balance-similar-node-groups`: https://github.com/kubernetes/autoscaler/blob/master/cluster-autoscaler/FAQ.md
- kubernetes-sigs/descheduler — `RemovePodsViolatingTopologySpreadConstraint`: https://github.com/kubernetes-sigs/descheduler
