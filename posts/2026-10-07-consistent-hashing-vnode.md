---
title: 캐시 노드 1대가 죽자 바로 옆 노드도 죽었다. 해시 링이 균등하다는 건 가상 노드가 있을 때 얘기였다
date: 2026-10-07
day: 105
category: distributed
tags: [consistent-hashing, virtual-node, sharding, memcached, cassandra]
related: ["[[sharding-strategies]]", "[[redis-cluster-slot]]", "[[cache-stampede]]", "[[redis-hotkey]]", "[[quorum-rw-n]]", "[[gossip-protocol-antientropy]]"]
difficulty: 3
short_text: |
  🔥 [Day 105] 캐시 노드 1대 죽자 옆 노드도 죽었다

  오해: 해시 링은 키를 균등 분배
  실제: 노드당 점 1개→호 편차→죽은 노드 몫 전부 이웃 1대로

  📖 https://github.com/kimyuchan-k1/IT-deep-dive-vault/blob/main/posts/2026-10-07-consistent-hashing-vnode.md
---

# 캐시 노드 1대가 죽자 바로 옆 노드도 죽었다. 해시 링이 균등하다는 건 가상 노드가 있을 때 얘기였다

## 흔한 오해

> "`hash(key) % N`은 노드가 바뀌면 키가 전부 재배치된다. 그래서 Consistent Hashing을 쓴다. 링 위에 노드를 올리면 키가 균등하게 나뉘고, 노드가 빠져도 1/N만 움직인다."

절반은 맞다. 노드 변경 시 **움직이는 키의 양**이 평균 1/N이라는 건 사실이다. 틀린 건 두 가지다. 하나, 노드당 점 하나만 찍은 링은 **균등하지 않다**. 둘, 빠진 노드의 키는 1/N만큼 움직이지만 **한 노드로 몰린다**. 면접 답안과 입문 글은 "1/N만 이동"에서 멈추고, 그 1/N이 어디로 가는지는 말하지 않는다.

## 실제 원리

### 1. 링의 규칙

해시 공간 `[0, 2^32)`를 원으로 만든다. 노드 식별자를 해시해 원 위에 점을 찍는다. 키도 해시해 원 위에 놓고, **시계 방향으로 처음 만나는 노드**가 주인이다. 노드가 추가되면 그 점과 직전 점 사이 구간만 새 노드로 옮긴다. 노드가 빠지면 그 구간이 다음 노드에게 넘어간다. Karger 등이 1997년 웹 캐시 분산을 위해 제안한 구조다.

`% N`과 비교하면 차이가 크다. 노드 10대에서 11대로 늘리면 `% N`은 키의 약 91%가 다른 노드로 간다. 링은 약 1/11만 간다.

### 2. 점 하나짜리 링은 왜 불균등한가

노드 N개를 원 위에 무작위로 찍으면 원은 N개의 호로 쪼개진다. 각 노드가 맡는 키 비율은 **자기 호의 길이**다. 무작위 점이 만드는 호 길이는 고르게 나오지 않는다. 가장 긴 호의 기댓값은 평균의 약 `ln N`배 수준이다. N=12면 평균 호는 8.3%인데 가장 긴 호는 20%를 넘기기 쉽다.

```
         node-A
       ╱        ╲
 node-D          ·     ← A~B 사이가 텅 빔
   │              ·
   │               node-B   (B가 원의 40%를 맡음)
 node-C ─────────╱
```

### 3. 노드가 빠질 때가 더 위험하다

노드 X가 죽으면 X의 호 전체가 **시계 방향 다음 노드 하나**에 붙는다. 다른 노드들은 아무것도 받지 않는다. 다음 노드의 부하는 "자기 몫 + X의 몫"이 된다. X가 하필 큰 호를 가진 노드였다면 이웃 노드의 부하는 두세 배로 뛴다. 그 이웃이 버티지 못하고 죽으면 두 노드 몫이 다시 그다음 노드로 넘어간다. 링에서는 장애가 **시계 방향으로 전파**된다.

### 4. 가상 노드: 점을 많이 찍는다

해법은 물리 노드 하나를 링 위 여러 점으로 쪼개는 것이다. `node-A#0`, `node-A#1`, … `node-A#159`처럼 이름을 붙여 각각 해시한다. 효과는 두 개다.

- **분산 평탄화**: 노드 하나의 부하는 V개 작은 호의 합이 된다. 상대 표준편차는 대략 `1/√V`로 준다. V=100이면 약 10%, V=256이면 약 6%다.
- **장애 분산**: 노드가 빠지면 V개 호가 **각각 다른 이웃**에게 넘어간다. 한 노드가 떠안던 짐이 클러스터 전체로 흩어진다.

가중치도 자연스럽게 생긴다. 메모리 두 배인 노드에 가상 노드를 두 배 주면 된다. libmemcached의 ketama 계열 클라이언트는 서버당 160개 점을 쓴다. Amazon Dynamo도 같은 이유로 가상 노드를 도입했다고 논문에 적었다.

### 5. 가상 노드도 공짜는 아니다

점이 많아지면 노드마다 맡는 **토큰 범위 수**가 늘어난다. Cassandra처럼 범위 단위로 복제·repair·스트리밍하는 시스템에서는 관리 단위가 그만큼 쪼개진다. 더 미묘한 문제도 있다. 가상 노드가 많으면 임의의 두 노드가 **어떤 범위의 복제본을 함께** 갖고 있을 확률이 1에 가까워진다. RF=3 클러스터에서 아무 노드 2대만 동시에 죽어도 일부 범위는 쿼럼([[quorum-rw-n]])을 잃는다. Cassandra 4.0이 `num_tokens` 기본값을 256에서 16으로 낮추고, 토큰 배치를 무작위 대신 할당 알고리즘(`allocate_tokens_for_local_replication_factor`)에 맡긴 이유다. 적은 점으로도 균등하게 만들고, 장애 동시 영향 범위는 줄였다.

## 현장 시나리오

커머스 상품 상세 캐시. memcached 12대, 자체 Go 클라이언트가 Consistent Hashing으로 샤딩한다. 클라이언트는 `host:port` 문자열을 해시해 노드당 **점 1개**만 찍고 있었다. 누구도 노드별 키 비율을 재 본 적이 없었다.

1. 링 배치가 우연히 치우침 → `cache-07`이 키의 19%, 시계 방향 다음인 `cache-08`이 14%를 맡음(평균 8.3%)
2. 블프 전날 `cache-07` 호스트가 커널 패닉으로 다운
3. 클라이언트가 `cache-07`을 링에서 제거 → 19%가 통째로 `cache-08`로 이동, `cache-08` 담당 비율 33%
4. `cache-08` 메모리 초과로 eviction 폭증 + NIC 대역폭 포화 → 응답 지연으로 클라이언트 타임아웃
5. 타임아웃 난 노드를 클라이언트가 다시 제거 → 33%가 `cache-09`로 → 같은 패턴 반복
6. 캐시 미스가 몰린 키에서 동시 재조회 발생([[cache-stampede]]) → 상품 DB QPS 평시의 4배 → 상세 페이지 p99 3초

죽은 건 1대였지만 3대가 연달아 무너졌다. 수정은 클라이언트 한 줄이었다. 노드당 점을 160개로 늘리자 시뮬레이션에서 최대 노드 비율이 9.6%로 내려갔다. 같은 노드를 빼도 가장 많이 받는 이웃이 1.2%p만 더 받았다.

## 실무 적용 포인트

1. **노드당 가상 노드 100~200개.** 클라이언트 측 링(memcached, 자체 샤딩)은 ketama 기본값 160이 무난한 출발점이다. 노드 수가 수백 대면 링 크기(노드×V)와 조회 비용(`O(log(N·V))` 이진 탐색)을 같이 본다.

2. **배포 전에 분포를 계산한다.** 실제 노드 목록으로 링을 만들고 각 노드 호 비율의 `max / avg`를 출력하는 스크립트를 CI에 둔다. 1.15를 넘으면 V를 늘린다. "노드 하나 빼고 다시 계산"도 같이 돌려 최대 이웃 증가분을 본다.

3. **노드 식별자는 IP가 아니라 고정 이름으로.** Kubernetes에서 `pod IP`를 해시하면 재시작마다 점 위치가 바뀌어 키가 재배치된다. StatefulSet의 `cache-0`, `cache-1` 같은 안정 이름을 해시 입력으로 쓴다.

4. **핫스팟이 남으면 bounded load.** 가상 노드는 **키 개수**를 고르게 할 뿐 **요청량**은 못 고친다([[redis-hotkey]]). HAProxy의 `hash-type consistent` + `hash-balance-factor 125`는 노드 부하가 평균의 125%를 넘으면 다음 노드로 넘긴다(Consistent Hashing with Bounded Loads).

5. **Cassandra는 4.0 이상에서 `num_tokens: 16` + `allocate_tokens_for_local_replication_factor: 3`.** 기존 256 토큰 클러스터를 바꾸려면 노드 단위 교체가 아니라 새 데이터센터를 띄워 옮기는 방식이 필요하다. 토큰 수는 노드 부트스트랩 후 바꿀 수 없다.

6. **노드를 번호로만 늘리고 줄인다면 Jump Hash.** 메모리 없이 `O(ln N)`으로 버킷을 정하고 분포 편차가 거의 없다. 대신 중간 번호 노드를 임의로 뺄 수 없다. 스토리지 샤드처럼 끝에서만 늘리는 구조에 맞다. Redis Cluster는 링 대신 고정 16384 슬롯을 쓴다([[redis-cluster-slot]]).

## 더 깊은 토끼굴

- [[sharding-strategies]] — range·hash·directory 샤딩 중 Consistent Hashing이 차지하는 위치.
- [[redis-cluster-slot]] — 링 대신 고정 슬롯 테이블을 고른 설계와 리샤딩 방식.
- [[cache-stampede]] — 재배치된 키가 한꺼번에 미스 날 때 DB로 번지는 경로.
- [[quorum-rw-n]] — 가상 노드 수가 쿼럼 가용성에 주는 영향.
- [[gossip-protocol-antientropy]] — 링 멤버십과 토큰 정보를 노드끼리 퍼뜨리는 방법.

**1차 출처**
- Karger et al., "Consistent Hashing and Random Trees" (STOC 1997): https://dl.acm.org/doi/10.1145/258533.258660
- DeCandia et al., "Dynamo: Amazon's Highly Available Key-value Store" (SOSP 2007) — 가상 노드 도입 배경: https://www.allthingsdistributed.com/files/amazon-dynamo-sosp2007.pdf
- Mirrokni, Thorup, Zadimoghaddam, "Consistent Hashing with Bounded Loads": https://arxiv.org/abs/1608.01350
- Lamping, Veach, "A Fast, Minimal Memory, Consistent Hash Algorithm" (Jump Hash): https://arxiv.org/abs/1406.2294
- Apache Cassandra 문서 — `num_tokens`, 토큰 할당: https://cassandra.apache.org/doc/latest/
