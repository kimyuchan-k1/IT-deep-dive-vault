---
title: Push가 더 빠르다고 컨슈머에 직접 밀어 넣었더니, 트래픽이 튀자 컨슈머가 줄줄이 OOM으로 터졌다
date: 2026-06-21
day: 19
category: kafka
tags: [pull, push, backpressure, kafka, rabbitmq, messaging]
related: ["[[backpressure-patterns]]", "[[sqs-vs-sns-vs-eventbridge]]", "[[at-least-once-vs-at-most-once]]", "[[idempotency-key]]", "[[dead-letter-queue]]", "[[kafka-exactly-once]]"]
difficulty: 2
short_text: |
  ⚠️ [Day 19] Push로 직접 밀다 컨슈머가 줄줄이 터졌다
  오해: Push가 빠르니 더 낫다
  실제: 백프레셔 없음→버퍼 폭주→OOM
  "초당 5만 건에 컨슈머가 깔렸다"
  📖 https://github.com/kimyuchan-k1/IT-deep-dive-vault/blob/main/posts/2026-06-21-pull-vs-push-model.md
---

# Push가 더 빠르다고 컨슈머에 직접 밀어 넣었더니, 트래픽이 튀자 컨슈머가 줄줄이 OOM으로 터졌다

## 흔한 오해

"메시지를 보낼 때 컨슈머가 일일이 가지러 오는(pull) 건 비효율 아닌가? 브로커가 새 메시지가 생기는 즉시 컨슈머한테 밀어주면(push) 지연도 짧고, 컨슈머가 빈 폴링으로 헛걸음할 일도 없잖아. 그러니 실시간이 중요하면 push, 그게 더 빠른 거지."

처음 메시지 큐를 붙이면 거의 이렇게 생각한다. push가 "도착 즉시 전달"이라 직관적으로 빨라 보이고, pull은 "또 물어보고 또 물어보는" 낭비처럼 보인다. 그래서 Kafka가 굳이 pull을 쓰는 걸 두고 "왜 굳이 느린 방식을?"이라고 의아해한다.

**갈리는 건 속도가 아니라 "처리 속도의 결정권이 누구에게 있느냐"다.** push는 보내는 쪽(브로커)이 속도를 정하고, pull은 받는 쪽(컨슈머)이 정한다. 이 결정권이 어디 있느냐가 트래픽이 튀는 순간 **컨슈머가 버티느냐 터지느냐**를 가른다. push가 빠른 게 아니라, push는 컨슈머의 사정을 안 봐준다.

## 실제 원리

핵심 질문은 하나다. **컨슈머가 처리할 수 있는 속도보다 메시지가 더 빨리 들어오면 누가 멈추나.**

### Push — 브로커가 속도를 정한다

push 모델에서 브로커는 메시지가 생기는 즉시 컨슈머 소켓으로 밀어 넣는다. 컨슈머가 지금 바쁜지, 큐가 얼마나 찼는지 **애초에 모른다.** 컨슈머의 처리 속도가 초당 1만 건인데 초당 5만 건이 들어오면, 남는 4만 건은 컨슈머 쪽 **수신 버퍼와 힙에 쌓인다.** 쌓이다 못해 터지는 게 OOM이다.

이걸 막으려면 push 시스템은 **역방향 신호(flow control)**를 따로 만들어야 한다. RabbitMQ가 `basic.qos`의 **prefetch count**로 "아직 ack 안 한 메시지가 N개를 넘으면 더 보내지 마라"를 거는 게 그것이다. prefetch를 안 걸면(=무제한) 브로커는 컨슈머가 받든 말든 계속 민다. 즉 push는 **백프레셔를 기본 제공하지 않고, 옵션으로 덧대는 구조**다([[backpressure-patterns]]).

### Pull — 컨슈머가 속도를 정한다

pull 모델에서 컨슈머는 자기가 **준비됐을 때만** `poll()`로 가지러 간다. 처리에 허덕이면 다음 poll을 늦추면 그만이다. 들어오는 양이 폭주해도 안 가져간 메시지는 **브로커(디스크)에 그대로 남는다.** 컨슈머 힙이 아니라 브로커의 로그에 쌓이니, 컨슈머는 절대 자기 용량 이상을 떠안지 않는다. **백프레셔가 모델 자체에 내장**돼 있다.

Kafka가 pull을 택한 이유가 정확히 이것이다. 컨슈머가 `max.poll.records`(기본 500)로 "한 번에 최대 몇 건"을 스스로 정하고, 처리한 만큼만 다음에 또 가져간다. 폭주는 브로커의 **컨슈머 랙(lag)**, 즉 "안 가져간 메시지 수"라는 깔끔한 지표로 드러난다. 밀린 일감이 눈에 보인다.

### 그럼 pull은 느린가 — 롱 폴링

"가지러 갈 때까지 기다리니 pull이 느리다"는 절반만 맞다. 순진한 pull은 메시지가 없을 때 빈 응답을 받고 곧장 또 물어, 트래픽이 없을 때 **빈 폴링으로 CPU·네트워크를 태운다.** 그래서 Kafka는 `fetch.min.bytes`와 `fetch.max.wait.ms`로 **롱 폴링**을 한다. "이만큼 쌓이거나 이 시간이 지날 때까지 응답을 붙들어 둬라" — 메시지가 오면 즉시 깨어 응답하니 지연은 push에 근접하고, 없으면 헛걸음을 안 한다. SQS의 `WaitTimeSeconds`(최대 20초)도 같은 장치다([[sqs-vs-sns-vs-eventbridge]]).

정리하면 — push는 저지연이지만 컨슈머를 보호 안 하고, pull은 백프레셔가 공짜지만 롱 폴링으로 지연을 메운다. **빠르냐 느리냐가 아니라, 폭주의 책임을 누가 지느냐의 문제다.**

## 현장 시나리오

한 핀테크의 알림 서비스가 RabbitMQ를 쓰면서, 컨슈머 워커들이 `basic.qos`의 **prefetch를 0(무제한)으로 두고** 있었다. 평소 초당 8천 건은 워커 20대가 무리 없이 처리했다. 인과 사슬은 이랬다:

- 마케팅이 전체 사용자 대상 푸시 캠페인을 돌리면서 큐에 **순간 초당 5만 건**이 쏟아졌다
- prefetch 무제한이라 브로커는 각 워커에게 받든 말든 **수천 건씩 한꺼번에 밀어 넣었다.** 워커 힙에 미처리 메시지가 수만 건씩 쌓였다
- 워커들이 차례로 **OOM으로 죽었다.** 죽은 워커가 잡고 있던(ack 안 한) 메시지는 큐로 되돌아가, **살아남은 워커에게 다시 몰렸다.** 그 워커도 같은 이유로 터졌다
- 죽고 → 재배분 → 또 죽는 **연쇄 붕괴**가 돌았다. RabbitMQ는 메모리 워터마크를 넘기자 **퍼블리셔까지 블로킹**했고, 알림과 무관한 결제 이벤트 발행마저 멈췄다
- 복구는 큐를 비우고 워커를 띄운 뒤에야 됐다. 캠페인 알림은 대부분 유실됐다

수정은 두 줄이었다. **`prefetch_count=50`**으로 워커가 한 번에 안고 있는 미처리 메시지를 제한하고, 처리량을 넘는 초과분은 큐(디스크)에 쌓이게 뒀다. 이제 폭주가 와도 워커는 자기 용량인 50건씩만 안고, 나머지는 브로커에 백로그로 남아 **큐 깊이 알람**이 울렸다. 워커는 죽지 않았고, 밀린 알림은 몇 분 뒤 따라잡혔다. 원인은 트래픽이 아니었다 — **컨슈머에게 속도 결정권을 안 준 push 설정**이었다. push를 쓰려면 백프레셔를 직접 채워 넣어야 한다.

## 실무 적용 포인트

1. **push(RabbitMQ)를 쓰면 prefetch를 반드시 유한값으로**: `basic.qos`의 `prefetch_count`를 워커 처리 능력에 맞춰 **10~100** 범위로 잡아라. 0(무제한)은 백프레셔를 꺼버리는 것이라 폭주 시 컨슈머 OOM 직행이다.
2. **폭주가 잦으면 모델 자체가 pull인 Kafka/SQS를 고려**: 백프레셔를 옵션으로 덧대는 대신, 안 가져간 메시지가 **브로커에 쌓이는** 구조를 택하면 컨슈머는 구조적으로 자기 용량을 안 넘는다.
3. **Kafka 컨슈머는 `max.poll.records`와 `max.poll.interval.ms`를 같이 본다**: 한 번에 가져온 건수(기본 500)를 `max.poll.interval.ms`(기본 5분) 안에 처리 못 하면 **죽은 걸로 보고 리밸런싱**된다. 처리시간이 길면 records를 줄여라.
4. **롱 폴링으로 빈 폴링 비용을 죽여라**: Kafka는 `fetch.min.bytes`·`fetch.max.wait.ms`, SQS는 `WaitTimeSeconds=20`. 0이면 트래픽 없을 때도 빈 응답에 CPU·과금을 태운다.
5. **백로그를 지표로 띄워라**: pull이면 **컨슈머 랙(lag)**, push면 **큐 깊이/미처리 ack 수**를 알람에 걸어라. push 직결은 백로그가 안 보여 장애를 늦게 안다([[dead-letter-queue]]).
6. **재처리 대비 멱등성은 필수**: prefetch든 리밸런싱이든 죽은 컨슈머의 미완료 메시지는 **재전달**된다. 소비자는 같은 메시지를 두 번 받아도 안전해야 한다([[idempotency-key]], [[at-least-once-vs-at-most-once]]).

## 더 깊은 토끼굴

- Apache Kafka 공식 — [Design: Push vs. pull](https://kafka.apache.org/documentation/#design_pull): Kafka가 왜 pull을 택했는지, 백프레셔와 롱 폴링에 대한 1차 설명
- RabbitMQ 공식 — [Consumer Prefetch](https://www.rabbitmq.com/docs/consumer-prefetch): `basic.qos` prefetch로 push에 백프레셔를 거는 방법
- RabbitMQ 공식 — [Flow Control & Memory Alarms](https://www.rabbitmq.com/docs/flow-control): 메모리 워터마크 초과 시 퍼블리셔 블로킹의 출처
- [[backpressure-patterns]]: 생산이 소비보다 빠를 때 멈추는 신호를 어디에 거나
- [[sqs-vs-sns-vs-eventbridge]]: SQS(pull) vs SNS(push)가 같은 갈림길의 AWS 버전
- [[kafka-exactly-once]]: 재전달을 전제로 한 컨슈머가 중복을 어떻게 흡수하나
