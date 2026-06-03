---
title: Kafka Exactly-Once는 "정확히 한 번 전송"이 아니다
date: 2026-06-03
day: 4
category: kafka
tags: [kafka, exactly-once, idempotent-producer, transactions, eos]
related: ["[[at-least-once-vs-at-most-once]]", "[[outbox-pattern]]", "[[idempotency-key]]"]
difficulty: 4
short_text: |
  💡 [Day4] Kafka Exactly-Once의 거짓말
  오해: 메시지가 네트워크로 한 번만 간다
  실제: 여러 번 가도 PID+seq로 중복제거→처리만 1회
  📖 https://github.com/kimyuchan-k1/IT-deep-dive-vault/blob/main/posts/2026-06-03-kafka-exactly-once.md
---

## 흔한 오해

> "Kafka에서 `enable.idempotence=true` 켜고 `transactional.id` 주면, 메시지가 네트워크로 정확히 한 번만 나가는 거 아닌가? 이름이 exactly-once니까."

그래서 "재시도해도 브로커엔 딱 한 번 도착한다", "중복은 이제 신경 안 써도 된다"로 받아들인다. 입문 글들이 "정확히 한 번 전달"로 번역해버린 탓도 크다.

틀린 건 아닌데, 단어 하나를 잘못 짚었다. exactly-once의 "once"는 **전송(delivery)**이 아니라 **처리(processing)**다. 네트워크 위로는 여전히 메시지가 여러 번 흐른다. 정확히 한 번이 되는 건 "로그에 기록되고 다운스트림이 보는" 최종 결과뿐이다.

## 실제 원리

Kafka의 Exactly-Once Semantics(EOS)는 마법이 아니라 **세 개의 기계장치가 겹친 것**이다. 각각이 무엇을 보장하는지 정확히 나눠야 한다.

### ① Idempotent Producer — 중복은 브로커가 버린다

프로듀서가 켜지면 브로커에서 **PID(Producer ID, 64bit)**를 받는다. 모든 레코드 배치에는 `(PID, 파티션, sequence number)`가 붙는다. sequence는 파티션별로 1씩 단조 증가한다.

여기가 핵심이다. 프로듀서가 배치를 보내고 ACK를 못 받아 **재시도**하면, 같은 배치가 브로커에 두 번 도착한다. 하지만 브로커는 그 파티션에서 마지막으로 받은 seq를 기억한다. 이미 본 seq면 그냥 버리고 ACK만 다시 준다. 즉 네트워크는 at-least-once인데, **로그에는 한 번만** 들어간다.

그래서 idempotence를 켜면 Kafka가 `acks=all`, `max.in.flight.requests.per.connection ≤ 5`, `retries > 0`을 강제한다. in-flight가 5를 넘으면 seq 순서가 꼬여 중복 판정이 깨지기 때문이다.

### ② Transactions — 여러 파티션 쓰기를 원자로 묶는다

idempotence는 "한 파티션, 한 프로듀서 세션" 안에서만 중복을 막는다. 진짜 문제는 **consume → transform → produce** 파이프라인이다. 여러 파티션에 쓰고, 동시에 "여기까지 읽었다"는 컨슈머 offset도 커밋해야 하는데, 이게 따로 놀면 한쪽만 반영되고 죽었을 때 중복이 샌다.

그래서 **Transaction Coordinator**와 내부 토픽 `__transaction_state`가 등장한다. 프로듀서는 `transactional.id`로 자신을 등록하고, `beginTransaction → 여러 produce + sendOffsetsToTransaction → commitTransaction` 흐름을 탄다. 코디네이터는 2단계 커밋과 유사하게 동작한다: 모든 파티션에 **transaction marker(COMMIT/ABORT control batch)**를 기록한 뒤에야 트랜잭션이 확정된다.

`transactional.id`는 **epoch fencing**도 한다. 같은 id로 새 프로듀서가 등록하면 epoch가 +1 되고, 옛 프로듀서(좀비)는 낮은 epoch라 거부당한다. 네트워크 단절 후 되살아난 유령 인스턴스의 중복 쓰기를 원천 차단한다.

### ③ Read-Committed — 컨슈머가 abort된 걸 안 본다

마지막 한 겹. 컨슈머가 `isolation.level=read_committed`면, **LSO(Last Stable Offset)** 너머의 아직-안-확정된 메시지는 안 읽는다. ABORT 마커가 붙은 트랜잭션의 메시지는 로그에 물리적으로 남아있어도 컨슈머에게 **건너뛰어진다**. 기본값은 `read_uncommitted`라, 이걸 안 켜면 EOS의 마지막 단추가 풀려 abort된 메시지까지 다 읽힌다.

## 현장 시나리오

결제 정산 파이프라인. `payments` 토픽을 읽어 수수료를 계산하고 `settlements` 토픽에 쓰는 컨슈머가 있다. 처리 직후 offset을 커밋하는 구조.

장애의 순간: 인스턴스가 `settlements`에 produce는 **성공**했는데, offset 커밋 **직전에** OOM으로 죽었다. 재시작하면 offset이 안 올라갔으니 같은 결제 메시지를 **다시 읽는다** → 다시 정산 → **이중 출금**. 한 시간에 이런 케이스 12건이 쌓였다.

EOS를 적용하면 인과가 바뀐다. produce와 offset 커밋을 하나의 트랜잭션으로 묶는다(`sendOffsetsToTransaction`). 인스턴스가 커밋 전에 죽으면 → 코디네이터가 `transaction.timeout.ms`(기본 60초) 후 트랜잭션을 **abort** → 모든 파티션에 ABORT 마커 → `read_committed` 다운스트림은 그 정산 결과를 **아예 못 봄** → 재시작한 인스턴스가 같은 메시지를 새 트랜잭션으로 다시 처리. 결과적으로 이중 정산 **12건 → 0건**.

여기서 메시지는 네트워크로 두 번 갔다. 그런데 정산은 한 번만 일어났다.

## 실무 적용 포인트

1. **`enable.idempotence=true`** — Kafka 3.0+는 기본값. 켜면 `acks=all`, `max.in.flight ≤ 5`, `retries>0`이 자동 강제된다. 이걸 직접 5보다 크게 만지면 중복 보장이 깨진다.
2. **`transactional.id`는 인스턴스마다 안정적·고유하게** 부여하라. 매번 랜덤/라운드로빈으로 주면 epoch fencing이 작동 안 해 좀비 프로듀서를 못 막는다. 보통 `앱이름-파티션그룹` 같이 결정적으로.
3. **컨슈머 `isolation.level=read_committed`를 명시**하라. 기본 `read_uncommitted`면 EOS가 절반만 작동해 abort된 메시지까지 읽힌다.
4. **Kafka Streams면 `processing.guarantee=exactly_once_v2` 한 줄**이면 끝. v2(2.5+)는 v1과 달리 태스크마다 프로듀서를 따로 안 만들어 프로듀서 수가 확 줄고 확장성이 좋다.
5. **`transaction.timeout.ms`는 `max.poll.interval.ms`보다 크게** 잡아라. 처리가 길어 poll 간격이 timeout을 넘기면 트랜잭션이 조기 abort돼 무한 재처리에 빠진다.
6. **EOS는 Kafka↔Kafka 안에서만 원자적**이다. 트랜잭션 안에서 외부 DB에 직접 INSERT하는 건 그 보장 밖이다. DB까지 묶으려면 [[outbox-pattern]]으로 "DB 쓰기 = 메시지 발행"을 한 로컬 트랜잭션에 넣어야 한다.

## 더 깊은 토끼굴

EOS는 결국 "[[at-least-once-vs-at-most-once]] 전송 + 멱등 + 원자 커밋"의 합성이다. 멱등 키 설계가 왜 분산 시스템 곳곳에서 반복되는지는 [[idempotency-key]]에서, 외부 시스템까지 정합성을 끌고 가는 패턴은 [[outbox-pattern]]에서 이어진다. 코디네이터의 2단계 커밋이 진짜 2PC와 어떻게 다른지는 [[saga-vs-2pc]]와 같이 보면 선명해진다. 파티션·컨슈머 수가 EOS 확장성에 미치는 영향은 [[kafka-partition-math]]로.

정확히 한 번 **처리**됐을 뿐, 메시지는 네트워크로 여러 번 갔다. 그 둘을 분리해서 보는 순간 EOS는 마법에서 엔지니어링이 된다.

**출처**
- Confluent Engineering Blog, "Exactly-Once Semantics Are Possible: Here's How Kafka Does It" — https://www.confluent.io/blog/exactly-once-semantics-are-possible-heres-how-apache-kafka-does-it/
- Apache Kafka KIP-98: Exactly Once Delivery and Transactional Messaging — https://cwiki.apache.org/confluence/display/KAFKA/KIP-98+-+Exactly+Once+Delivery+and+Transactional+Messaging
