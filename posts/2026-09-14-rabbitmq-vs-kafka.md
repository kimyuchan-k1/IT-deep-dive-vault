---
title: RabbitMQ에선 한 건만 다시 처리됐다. Kafka로 옮기자 뒤의 8만 건이 멈췄다
date: 2026-09-14
day: 95
category: kafka
tags: [rabbitmq, kafka, amqp, partition, ack]
related: ["[[kafka-partition-math]]", "[[dead-letter-queue]]", "[[at-least-once-vs-at-most-once]]", "[[pull-vs-push-model]]", "[[backpressure-patterns]]"]
difficulty: 3
short_text: |
  ⚠️ [Day 95] 한 건 재시도가 뒤의 8만 건을 멈춰 세웠다

  오해: Kafka는 더 빠른 RabbitMQ
  실제: 오프셋 1개→개별 ack 불가→파티션 정지

  📖 https://github.com/kimyuchan-k1/IT-deep-dive-vault/blob/main/posts/2026-09-14-rabbitmq-vs-kafka.md
---

# RabbitMQ에선 한 건만 다시 처리됐다. Kafka로 옮기자 뒤의 8만 건이 멈췄다

## 흔한 오해

> "둘 다 메시지 큐 아닌가. Kafka가 처리량이 훨씬 높고 로그를 보관하니까 상위 호환이다. RabbitMQ는 레거시고, 새로 짓는다면 Kafka로 통일하는 게 맞다."

벤치마크만 보면 그렇게 읽힌다. Kafka는 초당 수십만 건을 쓰고 RabbitMQ는 수만 건대에서 꺾인다. 그래서 "규모가 커지면 Kafka" 라는 결정 트리가 나온다.

처리량은 원인이 아니라 결과다. 두 시스템은 **브로커가 메시지 한 건당 무엇을 기억하는가**를 정반대로 정했고, 처리량 차이도 재시도 동작도 전부 그 하나에서 파생된다. 그래서 축을 처리량으로 잡으면 틀린 쪽을 고른다.

## 실제 원리

### 1. 브로커가 기억하는 것의 크기가 다르다

RabbitMQ 브로커는 메시지 한 건마다 상태를 갖는다. 큐에 들어온 순간부터 어떤 채널에 전달됐는지(unacked), ack가 왔는지, 몇 번 재전달됐는지를 브로커가 들고 있다. 컨슈머가 `basic.ack`를 보내야 그 메시지가 큐에서 사라진다. `basic.nack`에 `requeue=false`면 dead letter exchange로 빠진다.

Kafka 브로커가 컨슈머 그룹에 대해 기억하는 건 파티션당 **정수 하나**다. `__consumer_offsets`에 저장된 "이 그룹은 이 파티션을 여기까지 읽었다"는 오프셋. 메시지는 ack와 무관하게 retention 기간 동안 로그에 그대로 남는다.

이 설계 차이가 성능 차이의 정체다. Kafka는 파티션을 append-only 파일로 쓰고, 컨슈머에게 내보낼 때 페이지 캐시에서 소켓으로 `sendfile`을 써 유저 공간 복사를 건너뛴다. 브로커가 건별 상태를 안 만들기 때문에 가능한 일이다. RabbitMQ는 건별 상태 관리 자체가 일이라 같은 방식으로 못 민다.

### 2. 그래서 병렬성의 단위가 다르다

RabbitMQ에서 큐 하나에 컨슈머 40대를 붙이면 40대가 각자 다음 메시지를 가져간다. 처리 시간이 제각각이어도 상관없다. 브로커가 prefetch(`basic.qos`) 만큼만 나눠주고, 끝낸 놈부터 다음 걸 받는다. 워커 수를 늘리는 데 상한이 없다.

Kafka에서 한 그룹의 유효 컨슈머 수 상한은 **파티션 수**다. 파티션은 순서 보장 단위이자 할당 단위다. 파티션 12개짜리 토픽에 컨슈머 40대를 붙이면 28대는 아무것도 배정받지 못하고 논다. 자세한 계산은 [[kafka-partition-math]]에 있다.

### 3. 재시도가 갈라지는 지점

여기가 핵심이다. Kafka 컨슈머가 오프셋 1000번 메시지 처리에 실패했다고 하자. 선택지는 둘뿐이다.

- 커밋하지 않고 재시도한다 → 1001번 이후는 **손도 못 댄다**. 커밋 위치는 정수 하나라 "1000만 빼고 1001부터"를 표현할 방법이 없다.
- 그냥 커밋하고 넘어간다 → 1000번은 유실된다.

RabbitMQ에는 이 선택이 없다. 1000번만 nack해서 DLX로 보내고 1001번은 계속 흐른다. 건별 상태를 브로커가 들고 있으니 건별로 결정할 수 있다.

Kafka 쪽 우회책이 재시도 토픽 패턴이다. 실패한 건을 `orders.retry.5m` 같은 별도 토픽으로 produce하고 원본 오프셋은 커밋해 흐름을 푼다. Uber가 이 구조를 공개하면서 사실상 표준이 됐다. 대가는 순서 보장 포기다. 재시도된 건은 원래 자리로 못 돌아온다.

정리하면 결정 트리의 축은 처리량이 아니라 이쪽이다.

```
건별 ack / 건별 재시도 / 워커 수 자유 확장이 필요한 작업 큐
    → RabbitMQ (또는 SQS)

키 단위 순서 / 재생 / 여러 소비자가 같은 스트림을 독립적으로 읽기
    → Kafka
```

## 현장 시나리오

정산 파이프라인을 RabbitMQ에서 Kafka로 옮긴 커머스 팀이 있었다. 이유는 "피크에 큐가 밀린다"였다. 토픽 `settlement.orders`, 파티션 12개, 키는 `seller_id`. 셀러별 순서를 지키려는 합리적인 선택이었다.

컨슈머는 건마다 외부 PG 정산 API를 호출한다. 어느 날 PG 한 곳이 특정 셀러 계정에서만 `504`를 뱉기 시작했다.

컨슈머 코드는 실패 시 3회 재시도, 백오프 30초였다. 한 건에 90초 이상. 그 셀러의 `seller_id`는 파티션 7번으로 해시됐다.

```
PG 504 → 컨슈머 재시도 90초 → 오프셋 커밋 못 함
  → 파티션 7 진행 정지 → 뒤의 8만 건 대기 (lag 8만)
  → 처리 시간이 max.poll.interval.ms 300초 초과
  → 코디네이터가 이 컨슈머를 죽은 걸로 판정 → 리밸런스 트리거
  → 리밸런스 동안 그룹 전체 12개 파티션 정지
  → 재할당 후 파티션 7을 받은 새 컨슈머가 같은 메시지부터 다시 시작
  → 다시 90초 → 다시 리밸런스
```

리밸런스 루프에 들어가면서 문제 없던 셀러 11개 파티션까지 몇 분씩 멈췄다. 정산 지연 알림이 전 셀러에게 나갔다.

RabbitMQ 시절 같은 장애가 났을 때 결과는 이랬다. 그 셀러 메시지만 3회 재시도 후 `x-delivery-limit` 초과로 DLQ에 떨어졌고, 큐의 나머지는 그대로 흘렀다. 아침에 DLQ에 쌓인 240건을 확인한 게 대응의 전부였다.

원인 한 줄. 브로커가 건별 상태를 안 가진다는 설계 특성을, 처리량 숫자만 보느라 도입 결정에서 뺐다.

## 실무 적용 포인트

1. **작업 큐인지 이벤트 로그인지 먼저 답하라.** "실패한 한 건만 따로 처리해야 하나?"가 Yes면 Kafka는 기본 도구가 아니다. 작업 단위 처리 시간이 제각각이고 워커를 자유롭게 늘리고 싶어도 마찬가지다.

2. **Kafka를 쓸 거면 재시도 토픽을 처음부터 설계에 넣어라.** 컨슈머 안에서 `Thread.sleep` 백오프를 도는 순간 파티션 전체가 인질이 된다. 실패 건은 즉시 `*.retry` 토픽으로 produce하고 원본은 커밋한다. [[dead-letter-queue]] 패턴과 같은 뿌리다.

3. **`max.poll.interval.ms`를 실제 최악 처리 시간 위로 잡아라.** 기본값 300000ms다. 한 배치 처리에 그보다 오래 걸리면 리밸런스가 돈다. 동시에 `max.poll.records`를 줄여 한 배치가 짧게 끝나게 만드는 쪽이 더 안전하다.

4. **RabbitMQ에선 prefetch를 반드시 명시하라.** `basic.qos(prefetch_count)` 기본값 무제한이면 빠른 컨슈머 하나가 큐를 전부 빨아들여 나머지가 논다. 처리 시간이 긴 작업은 `1`, 짧고 균일하면 `100~300`선.

5. **RabbitMQ 재시도는 `x-delivery-limit`으로 끊어라.** quorum queue에 이 인자를 걸면 한도 초과 건이 자동으로 dead letter로 간다. 안 걸면 nack + requeue가 무한 루프를 돌며 같은 메시지가 큐 앞자리를 계속 차지한다.

6. **파티션 수는 늘리기만 되고 줄일 수 없다는 걸 계산에 넣어라.** 게다가 파티션을 늘리면 기존 키의 해시 분배가 바뀌어 [[at-least-once-vs-at-most-once]] 경계에서 순서가 깨진다. 목표 컨슈머 수의 2~3배로 잡고 시작하는 게 실무 관행이다.

7. **둘 다 쓰는 게 정답인 경우가 많다.** 이벤트 스트림은 Kafka, 그 스트림을 소비해 만든 작업은 RabbitMQ/SQS 작업 큐. 하나로 통일하려는 욕구가 위 시나리오의 출발점이었다.

## 더 깊은 토끼굴

- [[kafka-partition-math]] — 파티션 수와 컨슈머 수, 처리량을 같이 푸는 법.
- [[dead-letter-queue]] — 실패 건을 격리하는 패턴과 재처리 루프 설계.
- [[pull-vs-push-model]] — RabbitMQ는 push, Kafka는 pull. 이 차이가 백프레셔를 가른다.
- [[backpressure-patterns]] — 컨슈머가 못 따라갈 때 두 시스템이 다르게 무너지는 지점.
- [[at-least-once-vs-at-most-once]] — ack 모델이 전달 보장에 어떻게 직결되나.

**1차 출처**
- Apache Kafka Documentation — Design (persistence, zero-copy, consumer position): https://kafka.apache.org/documentation/#design
- RabbitMQ Docs — Consumer Acknowledgements and Publisher Confirms: https://www.rabbitmq.com/docs/confirms
- RabbitMQ Docs — Quorum Queues (`x-delivery-limit`, poison message handling): https://www.rabbitmq.com/docs/quorum-queues
- Uber Engineering, "Building Reliable Reprocessing and Dead Letter Queues with Apache Kafka": https://www.uber.com/blog/reliable-reprocessing/
- KIP-932: Queues for Kafka — Kafka에 건별 ack(share group)을 넣으려는 진행 중 제안: https://cwiki.apache.org/confluence/display/KAFKA/KIP-932%3A+Queues+for+Kafka
