---
title: 파티션을 12개에서 200개로 늘렸다. 처리량은 그대로였고 p99만 3배가 됐다
date: 2026-09-15
day: 96
category: kafka
tags: [kafka, partition, consumer-group, throughput, rebalance]
related: ["[[rabbitmq-vs-kafka]]", "[[kafka-exactly-once]]", "[[sharding-strategies]]", "[[connection-pool-sizing]]", "[[backpressure-patterns]]"]
difficulty: 3
short_text: |
  ⚠️ [Day 96] 파티션 12→200. 처리량 그대로, p99만 3배

  오해: 파티션 늘리면 처리량이 는다
  실제: 배치 축소→요청 폭증→브로커 CPU 포화

  📖 https://github.com/kimyuchan-k1/IT-deep-dive-vault/blob/main/posts/2026-09-15-kafka-partition-math.md
---

# 파티션을 12개에서 200개로 늘렸다. 처리량은 그대로였고 p99만 3배가 됐다

## 흔한 오해

> "파티션이 병렬성 단위니까, 처리량이 부족하면 파티션을 늘리면 된다. 컨슈머도 그만큼 붙이면 선형으로 올라간다. 넉넉하게 잡아두면 나중에 편하다."

절반은 맞다. 한 컨슈머 그룹에서 동시에 일할 수 있는 컨슈머 수의 상한은 파티션 수다. 파티션 12개에 컨슈머 40대를 붙이면 28대는 논다. 빠진 게 둘이다. 파티션을 늘리면 **처리량이 커지는 게 아니라 잘게 쪼개진다**는 것, 그리고 **파티션 자체가 공짜가 아니라는 것**. "넉넉하게 잡아두면 편하다"는 조언은 방향이 반대다.

## 실제 원리

### 1. 공식은 나눗셈 두 개의 max다

Confluent의 Jun Rao가 정리한 계산식이 기준선이다.

```
필요 파티션 수 = max( T / P_p , T / C_p )

T   = 목표 처리량 (msg/s)
P_p = 파티션 1개에 프로듀서가 밀어 넣는 처리량
C_p = 파티션 1개를 컨슈머 1대가 처리하는 처리량
```

병목은 거의 항상 오른쪽 항이다. 컨슈머가 1건마다 DB에 쓰고 20ms가 걸리면 `C_p`는 초당 50건, 목표 24,000 msg/s면 480개가 나온다.

여기가 함정이다. 이 숫자를 보고 파티션을 480개로 만드는 게 아니라 **`C_p`를 키워야 한다**. 건별 쓰기를 500건 배치 upsert로 바꾸면 `C_p`가 수십 배 뛰고 필요 파티션 수도 그만큼 줄어든다. 분모를 키우는 게 분자를 쪼개는 것보다 항상 싸다.

### 2. 파티션을 늘리면 배치가 작아진다

프로듀서는 파티션별로 별도 배치 버퍼를 만들고, `batch.size`(기본 16KB)만큼 차거나 `linger.ms`(기본 0)이 지나면 보낸다. 24,000 msg/s를 파티션 12개에 흘리면 파티션당 2,000 msg/s라 배치가 금방 찬다. 같은 트래픽을 200개에 흘리면 파티션당 120 msg/s다. `linger.ms=0`이면 차기를 기다리지 않고 거의 건별로 나간다.

결과는 produce 요청 수 폭증이다. 브로커 비용은 메시지 크기가 아니라 **요청 개수**에 붙는다. 요청 1건마다 네트워크 스레드, 요청 큐, I/O 스레드, 복제 경로를 탄다. 압축도 배치 단위라, 배치가 7건짜리로 쪼개지면 lz4 압축률이 무너져 네트워크 바이트가 오히려 늘어난다. 처리량은 그대로인데 브로커 CPU와 p99만 올라가는 구간이 정확히 여기다.

### 3. 파티션 1개의 고정 비용

파티션은 논리 개념이 아니라 브로커 디스크의 디렉터리다. 레플리카 1개당 활성 세그먼트마다 최소 `.log` / `.index` / `.timeindex`가 열려 있다. `replication.factor=3`이면 파티션 200개는 클러스터 전체에서 레플리카 600개다.

- **복제 팬아웃**: 브로커당 레플리카가 많아지면 팔로워 fetch와 ISR 관리 부하가 는다. 메시지는 high watermark를 넘겨야 컨슈머에게 보이니 이 지연이 곧 end-to-end 지연이다.
- **장애 복구 시간**: 브로커가 죽으면 그 브로커가 리더였던 **모든 파티션**에 리더 선출이 돌아야 한다. 복구 시간이 파티션 수에 비례한다. 컨트롤러가 전체 파티션 메타데이터를 ZooKeeper에서 읽어 오던 구조를 KRaft가 메타데이터 로그로 바꾼 이유가 이거다(KIP-500).

### 4. 키가 쏠리면 파티션 수는 의미가 없다

프로듀서의 기본 파티셔너는 키가 있을 때 `murmur2(key) % numPartitions`다. 결정론적이라 같은 키는 항상 같은 파티션으로 간다. 순서 보장이 여기서 나온다. 

대가는 이거다. 상위 키 3개가 트래픽의 40%를 먹으면, 파티션을 200개로 늘려도 그 40%는 여전히 파티션 3개로만 간다. 위 공식은 키 분포가 균등하다는 가정 위에서만 성립하는데 실제 키 공간은 거의 항상 지프 분포다. [[sharding-strategies]]의 핫 샤드와 같은 뿌리다.

게다가 파티션 수를 바꾸면 `% numPartitions`의 결과가 전부 바뀌어 기존 키의 순서 보장이 깨진다. 늘릴 수만 있고 줄일 수 없다는 제약까지 겹친다.

## 현장 시나리오

주문 이벤트를 처리하는 커머스 팀. 토픽 `orders.events`, 파티션 12개, 키 `customer_id`, 컨슈머 12대. 피크에 lag이 40만 건까지 쌓였다. 진단은 "파티션이 병목"이었고 파티션 200개 · 컨슈머 200대로 올렸다. 다음 날 지표는 이랬다.

- 처리량: 23,800 → 24,100 msg/s (변화 없음)
- produce p99: 18ms → 54ms
- 브로커 CPU: 41% → 88%, lag: 그대로

```
파티션 12 → 200
  → 파티션당 유입 2,000/s → 120/s
  → linger.ms=0 이라 배치가 안 참 (평균 120건 → 7건)
  → produce 요청 수 급증 + lz4 압축률 붕괴
  → 브로커 요청 큐 적체 → produce p99 3배
  → 컨슈머 200대가 커넥션 30개짜리 DB 풀에 동시 접근
  → 풀 대기가 건당 처리 시간을 늘림 → C_p가 오히려 감소
  → lag 그대로
```

여기에 키 쏠림이 겹쳤다. 상위 고객 3곳이 전체 이벤트의 38%였다. `kafka-consumer-groups.sh --describe`로 뽑으니 200개 중 3개가 lag의 대부분을 들고 있었고 나머지 197대는 거의 놀았다.

배포도 비싸졌다. 200대 롤링 재시작은 리밸런스 200번이고 기본 `RangeAssignor`는 리밸런스마다 그룹 전체를 멈춘다. 배포가 4분에서 20분이 됐다.

되돌린 뒤 한 일은 셋이다. 파티션 24개, 컨슈머가 500건씩 모아 배치 upsert, DB 풀 30 → 80. 처리량은 91,000 msg/s가 됐고 파티션은 처음의 2배였다.

원인 한 줄. 병목은 파티션이 아니라 `C_p`였는데, 공식의 분모를 안 보고 분자를 쪼갰다.

## 실무 적용 포인트

1. **파티션을 정하기 전에 `C_p`를 측정하고 먼저 키워라.** 컨슈머 1대가 파티션 1개를 초당 몇 건 처리하는지 실측한 뒤 `max(T/P_p, T/C_p)`를 계산한다. 건별 외부 호출을 배치로 묶는 것만으로 보통 10~50배가 나온다. 컨슈머 200대가 커넥션 30개짜리 풀을 두고 경쟁하면 `C_p`는 오히려 떨어지니 [[connection-pool-sizing]]이 파티션 수보다 먼저다.

2. **파티션 수는 목표 컨슈머 수의 2~3배까지만.** `kafka-topics.sh --alter --partitions`는 증가만 허용한다. 줄이려면 새 토픽을 만들어 마이그레이션해야 한다. "넉넉하게"가 되돌릴 수 없다는 뜻이다.

3. **파티션을 늘렸으면 `linger.ms`를 같이 올려라.** 기본 0은 배치를 포기한다는 뜻이다. 처리량형 파이프라인은 `linger.ms` 5~20ms, `batch.size` 32~64KB, `compression.type=lz4`가 출발점이다.

4. **파티션별 lag을 봐라. 합계 lag은 키 쏠림을 숨긴다.** `kafka-consumer-groups.sh --describe --group <G>`로 상위 몇 개 파티션이 전체 LAG의 몇 %인지 확인한다. 특정 파티션만 쌓이면 문제는 파티션 수가 아니라 키 설계다. 순서 보장이 필요 없으면 키를 없애 라운드로빈으로 흘린다.

5. **컨슈머 파드 수 상한을 파티션 수로 고정하라.** HPA `maxReplicas`가 파티션 수보다 크면 초과분은 파티션을 못 받고 논다. [[hpa-internals]]의 스케일 기준도 CPU가 아니라 컨슈머 lag으로 잡는다.

6. **리밸런스 비용을 줄여라.** `partition.assignment.strategy`를 `CooperativeStickyAssignor`로 바꾸면 영향받는 파티션만 멈춘다(KIP-429). 롤링 재배포가 잦으면 `group.instance.id`로 정적 멤버십을 켜고 `session.timeout.ms`를 재시작 시간 위로 잡아 리밸런스를 건너뛴다.

## 더 깊은 토끼굴

- [[rabbitmq-vs-kafka]] — 병렬성 단위가 파티션이 아닌 쪽은 이 계산을 안 한다. 대신 다른 대가를 낸다.
- [[kafka-exactly-once]] — 파티션 수가 트랜잭션 코디네이터와 오프셋 커밋 비용에 붙는 지점.
- [[sharding-strategies]] — 키 해시 분배와 핫 샤드. 파티션 쏠림과 같은 뿌리.
- [[connection-pool-sizing]] — `C_p`를 실제로 결정하는 건 대개 이쪽이다.
- [[backpressure-patterns]] — 파티션을 늘려도 안 풀리는 적체를 어디서 흡수하나.

**1차 출처**
- Confluent, Jun Rao — "How to Choose the Number of Topics/Partitions in a Kafka Cluster?": https://www.confluent.io/blog/how-choose-number-topics-partitions-kafka-cluster/
- Apache Kafka Docs — Producer Configs (`batch.size`, `linger.ms`): https://kafka.apache.org/documentation/#producerconfigs
- Apache Kafka Docs — Consumer Configs (`partition.assignment.strategy`, `group.instance.id`): https://kafka.apache.org/documentation/#consumerconfigs
- KIP-429: Kafka Consumer Incremental Rebalance Protocol: https://cwiki.apache.org/confluence/display/KAFKA/KIP-429%3A+Kafka+Consumer+Incremental+Rebalance+Protocol
- KIP-500: Replace ZooKeeper with a Self-Managed Metadata Quorum: https://cwiki.apache.org/confluence/display/KAFKA/KIP-500
