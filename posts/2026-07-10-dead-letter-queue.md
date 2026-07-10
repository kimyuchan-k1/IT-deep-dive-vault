---
title: 실패하면 재시도하면 된다고 믿었는데, 메시지 한 건이 큐 전체를 멈춰 세웠다
date: 2026-07-10
day: 37
category: kafka
tags: [messaging, dead-letter-queue, poison-message, retry, backpressure]
related: ["[[at-least-once-vs-at-most-once]]", "[[retry-exponential-backoff-jitter]]", "[[kafka-partition-math]]", "[[idempotency-key]]", "[[backpressure-patterns]]"]
difficulty: 2
short_text: |
  🔥 [Day 37] 메시지 1건이 큐 전체를 멈췄다
  오해: 실패하면 재시도로 해결
  실제: poison message→무한 재시도→뒤 메시지 정체
  "1건이 파티션을 막았다"
  📖 https://github.com/kimyuchan-k1/IT-deep-dive-vault/blob/main/posts/2026-07-10-dead-letter-queue.md
---

# 실패하면 재시도하면 된다고 믿었는데, 메시지 한 건이 큐 전체를 멈춰 세웠다

## 흔한 오해

메시지 큐에 컨슈머를 붙이고 나면 실패 처리를 이렇게 생각한다.

> "메시지 처리가 실패하면 ack를 안 보내면 되잖아. 그럼 브로커가 다시 보내주고, 될 때까지 재시도하면 언젠가는 성공하겠지."

그래서 실패 = 재시도, 재시도 = 만능이라 믿는다. 재시도 횟수를 늘리고 백오프를 붙이면 안정성이 올라간다고 여긴다.

**틀린 건 아닌데, "실패"를 한 종류로 뭉뚱그린 게 문제다.** 실패엔 두 종류가 있다. 잠깐 뒤에 다시 하면 되는 **일시적 실패**(DB 커넥션 끊김, 다운스트림 타임아웃)와, 몇 번을 다시 해도 영원히 실패하는 **영구적 실패**(깨진 JSON, 없는 스키마 필드, 삭제된 참조). 재시도는 앞엣것만 낫게 하고, 뒤엣것에겐 독이 된다. 영원히 실패할 메시지를 무한 재시도하면 그 뒤에 줄 선 멀쩡한 메시지들이 전부 발이 묶인다. 이 "독이 든 메시지(poison message)"를 격리하는 우회로가 바로 Dead Letter Queue(DLQ)다.

## 실제 원리

### ① Poison message가 파이프라인을 막는 구조

메시지 큐, 특히 Kafka 같은 파티션 기반 큐는 **파티션 안에서 순서를 보장**한다. 순서를 지키려면 컨슈머는 offset을 앞에서부터 순서대로 커밋해야 한다. 여기서 함정이 생긴다.

`offset 5001`이 영구적으로 실패하는 메시지라고 하자. 컨슈머는 이걸 처리 못 해 커밋하지 못한다. 커밋을 못 하니 `5002`, `5003`으로 넘어갈 수도 없다 — 넘어가면 순서가 깨지고, 재기동 시 `5001`을 건너뛰어 유실이 된다. 그래서 컨슈머는 `5001`을 붙잡고 재시도, 재시도, 재시도… 그동안 **뒤에 쌓인 수백만 건이 통째로 정체된다.** 한 건이 파티션 전체를 인질로 잡는 것.

이게 [[at-least-once-vs-at-most-once]]에서 "무한 재시도는 poison message 하나로 파티션 전체를 멈춘다"고 짚었던 바로 그 상황이다.

### ② DLQ — "이건 못 하겠다"를 인정하는 별도 창고

DLQ의 아이디어는 단순하다. **N번 재시도해도 실패하면, 그 메시지를 원래 큐에서 빼서 별도의 큐(dead letter queue)로 옮기고, 원래 컨슈머는 다음 메시지로 넘어간다.**

```
정상 큐:  [5001(독)] [5002] [5003] ...
                │ 3회 실패
                ▼
DLQ:      [5001]              ← 격리, 나중에 사람이 조사
정상 큐:            [5002] [5003] ... ← 파이프라인 정상 흐름
```

핵심은 **"버리는 게 아니라 옮기는 것"**이다. 그냥 버리면(at-most-once) 유실이고, 무한 재시도하면 정체다. DLQ는 제3의 길 — 실패한 메시지를 잃지 않되 메인 흐름에서 치워, 유실도 정체도 막는다. 격리된 메시지는 나중에 원인을 고친 뒤 다시 메인 큐로 돌려보낼 수 있다(재처리/redrive).

### ③ "몇 번"과 "어디로"의 경계

DLQ 설계의 두 결정은 **재시도 임계값**과 **DLQ의 트리거 조건**이다.

- **재시도 횟수**: SQS는 `maxReceiveCount`(예: 5)를 넘기면 자동으로 DLQ로 보낸다. 이 값이 너무 크면 일시적 실패도 오래 정체시키고, 너무 작으면 잠깐의 장애에도 멀쩡한 메시지가 DLQ로 샌다.
- **어떤 실패를 DLQ로 보낼까**: 영구적 실패(파싱 에러, 검증 실패)는 **즉시** DLQ로 보내는 게 낫다. 재시도해봐야 시간 낭비이기 때문. 반대로 일시적 실패(네트워크)는 백오프를 두고 몇 번 재시도한 뒤에만 DLQ로. 그래서 성숙한 시스템은 재시도용 큐(retry queue)와 DLQ를 분리하고, 예외 타입으로 둘을 라우팅한다.

Kafka엔 SQS 같은 내장 DLQ가 없다. 그래서 Kafka Connect의 `errors.tolerance`/`errors.deadletterqueue.topic.name` 설정이나, 컨슈머 코드에서 실패 메시지를 별도 토픽(`orders.DLT`)으로 직접 프로듀스하는 방식을 쓴다. Spring Kafka의 `DeadLetterPublishingRecoverer`가 후자를 자동화한 것.

## 현장 시나리오

한 이커머스의 주문 이벤트 컨슈머. `orders` 토픽을 받아 배송 시스템에 등록한다. 어느 날 배송 API 스펙이 바뀌어 일부 주문 메시지에 `null`인 새 필드가 들어왔고, 컨슈머는 이걸 역직렬화하다 `NullPointerException`을 던졌다.

인과 사슬은 이렇게 번졌다.

- `offset 812340` 메시지가 역직렬화 실패 → 컨슈머가 예외, 커밋 안 함
- 컨슈머는 이 메시지를 **다시 poll → 또 같은 예외** (영구적 실패라 100% 반복)
- 재시도 로직이 무한 루프. `812341` 이후 정상 주문 **12만 건이 처리 대기**로 정체
- 배송 등록 지연 → 고객 "주문했는데 배송이 안 잡혀요" CS 폭주
- 대시보드의 consumer lag이 수직 상승, 온콜 알람

운영팀은 처음에 "컨슈머가 죽었나" 하고 재기동을 반복했다. 하지만 재기동해도 컨슈머는 매번 같은 `812340`부터 다시 읽어 같은 예외를 반복했다 — **재기동이 문제를 리셋하는 게 아니라 poison message 앞에서 계속 부딪힌 것**. 근본 원인은 영구 실패 메시지에 재시도 상한과 DLQ가 없었다는 것. 3회 실패 후 `orders.DLT`로 보내는 로직을 넣자, 문제의 1건은 DLQ로 격리되고 나머지 12만 건이 **몇 분 만에 소진**됐다. 격리된 1건은 스키마를 고친 뒤 다음 날 redrive로 재처리했다. **한 건을 버리지 않으면서도, 한 건 때문에 전체가 멈추지 않게 하는 것** — 그게 DLQ가 있는 유일한 이유였다.

## 실무 적용 포인트

1. **재시도 상한을 반드시 정하라 — 무한 재시도는 장애다.** SQS `maxReceiveCount`는 3~5로 시작. Kafka 컨슈머는 애플리케이션 레벨에서 재시도 카운트를 헤더에 실어 추적하고, 임계 초과 시 DLT로 보낸다. "될 때까지"는 정책이 아니라 사고다.
2. **영구 실패와 일시 실패를 예외 타입으로 갈라라.** 파싱·검증 실패(`DeserializationException`)는 재시도 없이 **즉시** DLQ. 네트워크·타임아웃은 백오프 재시도 후 DLQ. 둘을 섞으면 깨진 메시지가 재시도 큐를 점거한다([[retry-exponential-backoff-jitter]]).
3. **DLQ를 만들었으면 알람을 걸어라.** DLQ에 메시지가 쌓이는 건 "문제가 격리됐다"인 동시에 "문제가 있다"는 신호다. `ApproximateNumberOfMessagesVisible > 0`이면 알람. 알람 없는 DLQ는 메시지가 조용히 죽는 무덤이 된다.
4. **원본 메시지 + 실패 메타데이터를 함께 실어라.** DLQ 메시지엔 원본 payload뿐 아니라 **예외 스택, 실패 시각, 원 토픽/offset**을 헤더로 남겨라. 없으면 나중에 왜 실패했는지 재현 불가.
5. **재처리(redrive) 경로를 미리 설계하라.** 원인을 고친 뒤 DLQ → 원래 큐로 되돌리는 버튼이 있어야 한다. SQS는 콘솔의 redrive 기능, Kafka는 DLT를 다시 읽어 원 토픽으로 재프로듀스하는 잡. 재처리 시 [[idempotency-key]]로 중복 처리를 막는 건 필수.
6. **DLQ 자체의 poison message도 대비하라.** DLQ 컨슈머(알림·저장)도 실패할 수 있다. DLQ에 다시 DLQ를 두거나, 최소한 재시도 없이 로그만 남기고 넘겨 2차 정체를 막아라.

## 더 깊은 토끼굴

- AWS SQS 공식 문서 — [Dead-letter queues](https://docs.aws.amazon.com/AWSSimpleQueueService/latest/SQSDeveloperGuide/sqs-dead-letter-queues.html): `maxReceiveCount`, redrive policy의 정확한 동작 (검증된 1차)
- Confluent 공식 블로그 — [Kafka Connect Deep Dive: Error Handling and Dead Letter Queues](https://www.confluent.io/blog/kafka-connect-deep-dive-error-handling-dead-letter-queues/): `errors.tolerance`와 DLQ 토픽 설정
- [[at-least-once-vs-at-most-once]]: DLQ가 왜 "유실도 정체도 아닌 제3의 길"인지의 뿌리
- [[retry-exponential-backoff-jitter]]: DLQ로 보내기 전 재시도를 어떻게 하는가
- [[kafka-partition-math]]: 한 파티션의 정체가 왜 컨슈머 그룹 전체 처리량을 떨어뜨리나
- [[backpressure-patterns]]: 정체를 큐 밖에서 흡수하는 다른 방법들
