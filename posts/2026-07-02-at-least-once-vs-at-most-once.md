---
title: "메시지가 두 번 왔다고 브로커를 의심했는데, 범인은 ack를 늦게 보낸 내 컨슈머였다"
date: 2026-07-02
day: 29
category: kafka
tags: [messaging, delivery-semantics, at-least-once, idempotency, ack]
related: ["[[kafka-exactly-once]]", "[[idempotency-key]]", "[[dead-letter-queue]]", "[[pull-vs-push-model]]", "[[outbox-pattern]]"]
difficulty: 2
short_text: |
  ⚠️ [Day 29] 중복, 범인은 내 ack였다
  오해: 중복은 브로커가 두 번 보낸 탓
  실제: 처리 성공→ack 직전 크래시→재전달
  "결제 두 번 찍혔다"
  📖 https://github.com/kimyuchan-k1/IT-deep-dive-vault/blob/main/posts/2026-07-02-at-least-once-vs-at-most-once.md
---

# 메시지가 두 번 왔다고 브로커를 의심했는데, 범인은 ack를 늦게 보낸 내 컨슈머였다

## 흔한 오해

메시지 큐를 처음 붙일 때 배달 보장을 이렇게 이해한다.

> "at-least-once는 브로커가 실수로 같은 메시지를 두 번 보내는 거고, at-most-once는 안전하게 한 번만 보내는 거 아닌가? 그럼 당연히 at-most-once가 낫지."

그래서 중복 메시지를 보면 "브로커가 이상하다"며 큐 설정을 뒤진다. 반대로 유실이 무서우면 "at-most-once는 위험하니 쓰면 안 된다"고 단정한다.

둘 다 방향이 어긋났다. 배달 시맨틱은 **브로커의 성격**이 아니라 **ack(확인 응답)를 언제 보내느냐**로 결정되는 선택지다. 그리고 대부분의 실무 시스템은 일부러 at-least-once를 고른다.

## 실제 원리

핵심은 하나다. **메시지 처리와 ack 전송은 원자적이지 않다.** 그 둘 사이에 크래시가 끼어들 수 있고, 그 틈을 어느 쪽으로 메우느냐가 시맨틱을 가른다.

컨슈머가 하는 일을 셋으로 쪼개보면 이렇다.

1. 메시지 수신
2. 비즈니스 로직 처리 (DB 쓰기, 결제 호출 등)
3. 브로커에 ack (Kafka에선 offset commit)

이 순서를 바꾸면 시맨틱이 통째로 바뀐다.

**at-most-once — ack 먼저, 처리 나중:**
```
수신 → ack(commit) → 처리
```
받자마자 "처리했다 치고" offset을 커밋한다. 그 직후 2번 처리 중에 크래시가 나면? 브로커는 이미 커밋을 받았으니 재전달하지 않는다. **메시지는 영영 사라진다.** 대신 절대 중복은 없다. 유실은 감수, 중복은 0.

**at-least-once — 처리 먼저, ack 나중:**
```
수신 → 처리 → ack(commit)
```
처리를 끝낸 뒤에야 커밋한다. 처리는 성공했는데 커밋 직전에 크래시가 나면? 브로커는 ack를 못 받았으니 그 메시지를 **다시 보낸다.** 컨슈머가 재기동하면 같은 메시지를 또 처리한다. **유실은 0, 대신 중복 가능.**

여기가 핵심이다. at-least-once의 중복은 브로커의 버그가 아니라 **설계된 정상 동작**이다. "ack를 못 받았으면 상대가 못 받았다고 가정하고 다시 보낸다" — 이건 신뢰할 수 없는 채널에서 유실을 막는 유일한 방법이고, 그래서 [[two-generals]] 문제와 정확히 같은 뿌리를 가진다. ack의 유실과 메시지의 유실을 송신자는 구분할 수 없다.

그럼 세 번째, exactly-once는? 흔히 "브로커가 마법으로 정확히 한 번 보내준다"고 오해하지만, 실제로는 at-least-once로 **전달**하되 컨슈머 쪽에서 **중복을 무해하게 만드는** 조합이다. Kafka의 exactly-once조차 트랜잭션(produce+offset commit을 원자적으로)으로 컨슈머-프로듀서 파이프라인 안에서만 성립하고, 외부 시스템(결제 게이트웨이, 메일 발송)에 대한 부수효과까지 정확히 한 번 보장해주진 않는다. 그건 결국 **멱등성**([[idempotency-key]])으로 네가 직접 막아야 한다.

정리하면 선택지는 사실상 둘이다. 유실을 못 견디면 at-least-once + 멱등 처리. 유실은 괜찮고 중복이 치명적이면(그리고 그런 경우는 생각보다 드물다) at-most-once.

## 현장 시나리오

한 커머스의 결제 완료 이벤트 파이프라인. Kafka `payment.completed` 토픽을 컨슈머가 받아 포인트를 적립한다.

트래픽 급증으로 컨슈머의 한 배치 처리가 길어졌다. 기본값 그대로 둔 `max.poll.interval.ms`(기본 300초)를 처리 시간이 넘겼다. 브로커는 이 컨슈머가 죽었다고 판단하고 **리밸런싱**을 트리거, 파티션을 다른 컨슈머에 재할당했다. 그런데 원래 컨슈머는 죽지 않았다 — 처리를 끝내고 offset을 커밋하려던 참이었다.

인과 사슬은 이렇게 이어졌다.

- 컨슈머 A가 `offset 5001~5100` 처리 완료, 포인트 100건 적립
- 커밋 직전 리밸런싱 발생 → A의 커밋 거부(fenced)
- 파티션이 컨슈머 B로 이동, B는 마지막 커밋된 `offset 5000`부터 다시 읽음
- B가 `5001~5100`을 **다시 처리** → 포인트 100건 이중 적립
- 고객 CS: "적립금이 두 배로 찍혔어요"

운영팀은 처음에 "Kafka가 메시지를 중복 발행했다"며 브로커 로그를 팠다. 하지만 프로듀서는 각 메시지를 정확히 한 번 보냈다. 범인은 **처리 시간 > `max.poll.interval.ms`**로 인한 리밸런싱과, 적립 로직에 멱등 키가 없었다는 것. at-least-once 시스템에서 중복은 언제든 온다는 전제를 컨슈머가 안 지킨 게 진짜 원인이었다.

## 실무 적용 포인트

1. **컨슈머를 멱등하게 짜라 — 이게 8할이다.** 메시지에 안정적인 유니크 키(주문ID+이벤트타입)를 싣고, `INSERT ... ON CONFLICT DO NOTHING` 또는 `processed_events` 테이블에 키를 먼저 기록해 중복을 걸러라. at-least-once는 "중복이 온다"가 전제다.
2. **처리 성공 후에 커밋하라.** Kafka에서 `enable.auto.commit=false`로 두고, 비즈니스 처리가 끝난 뒤 `commitSync()`. auto-commit(기본 `auto.commit.interval.ms=5000`)은 처리와 무관하게 시간마다 커밋해 유실 창을 만든다.
3. **처리 시간을 리밸런싱 임계값 아래로 유지하라.** 배치가 길면 `max.poll.records`를 줄이거나(예: 500→50) `max.poll.interval.ms`를 늘려라. 이 초과가 "죽지도 않았는데 리밸런싱→중복"의 단골 원인이다.
4. **끝내 못 처리하는 메시지는 DLQ로 격리하라.** 재시도 N회(예: 3회) 후 [[dead-letter-queue]]로 보내 파이프라인 정체를 막아라. 무한 재시도는 poison message 하나로 파티션 전체를 멈춘다.
5. **at-most-once는 "유실 허용" 데이터에만.** 초당 수만 건의 메트릭·클릭 로그처럼 몇 건 사라져도 통계가 안 흔들리는 곳. 결제·주문·재고엔 절대 쓰지 마라.
6. **부수효과엔 외부 멱등성을 별도로 걸어라.** 결제 게이트웨이 호출은 `Idempotency-Key` 헤더, 메일 발송은 발송 이력 테이블. 브로커의 exactly-once는 외부 API까지 지켜주지 않는다.

## 더 깊은 토끼굴

at-least-once + 멱등이 사실상 업계 표준 조합인 이유, exactly-once가 왜 "전달"이 아니라 "처리 결과"의 개념인지는 [[kafka-exactly-once]]와 [[idempotency-key]]에서 이어진다. ack가 유실될 수 있다는 근본 한계는 [[two-generals]]로, DB 트랜잭션과 메시지 발행을 원자적으로 묶는 방법은 [[outbox-pattern]]으로 파고들면 된다. 컨슈머가 왜 pull 모델을 쓰는지는 [[pull-vs-push-model]] 참고.

1차 출처:
- Kafka 공식 문서 — Message Delivery Semantics: https://kafka.apache.org/documentation/#semantics
- Kafka 공식 문서 — Consumer Configs (`max.poll.interval.ms`, `enable.auto.commit`): https://kafka.apache.org/documentation/#consumerconfigs
