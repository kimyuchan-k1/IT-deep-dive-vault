---
title: DB 커밋도 성공, 메시지 발행도 성공, 그런데 주문 37건이 사라졌다
date: 2026-08-02
day: 56
category: distributed
tags: [outbox, dual-write, cdc, kafka, transaction, debezium]
related: ["[[saga-vs-2pc]]", "[[cdc-debezium]]", "[[idempotency-key]]", "[[at-least-once-vs-at-most-once]]", "[[dead-letter-queue]]"]
difficulty: 3
short_text: |
  🔥 [Day 56] 커밋도 발행도 성공, 주문은 사라졌다

  오해: 트랜잭션 안에서 발행하면 안전
  실제: 커밋 2번 사이 크래시→메시지 유실

  "37건이 창고에 안 갔다"

  📖 https://github.com/kimyuchan-k1/IT-deep-dive-vault/blob/main/posts/2026-08-02-outbox-pattern.md
---

# DB 커밋도 성공, 메시지 발행도 성공, 그런데 주문 37건이 사라졌다

로그를 보면 아무 문제가 없다. `INSERT INTO orders` 커밋 성공, `producer.send()` 콜백에 예외 없음. 그런데 창고 시스템에는 그 주문이 없다. 두 번의 성공 사이에 있는 틈이 문제였다.

## 흔한 오해

> "트랜잭션 안에서 DB 저장하고 메시지 발행하면 되잖아. 발행 실패하면 롤백되니까 둘 다 없던 일이 되고."

이 코드가 거의 모든 서비스에 한 번씩은 들어있다.

```java
@Transactional
public void createOrder(Order order) {
    orderRepository.save(order);          // DB 트랜잭션
    kafkaProducer.send("order-created", order);  // 브로커로 나가는 별개 통신
}
```

`@Transactional`이 둘 다 감싸고 있으니 원자적이라고 읽힌다. 스프링 트랜잭션을 먼저 배우고 메시징을 나중에 배우면 자연스럽게 이렇게 생각하게 된다.

틀린 건 아닌데, 애노테이션이 관리하는 범위를 착각했다. `@Transactional`은 DB 커넥션 하나의 커밋/롤백만 관리한다. Kafka는 그 트랜잭션 밖에 있는 완전히 다른 프로세스다.

## 실제 원리

### 커밋이 두 번이면 원자성은 없다

문제의 이름은 **dual write**다. 상태 변경을 두 개의 독립된 저장소에 각각 커밋해야 하는 상황. 공유된 커밋 프로토콜이 없으므로 순서를 어떻게 잡아도 깨진다.

**발행 먼저, 커밋 나중**이면 유령 메시지가 생긴다. Kafka에 `OrderCreated`가 나갔는데 DB 커밋이 제약조건 위반으로 롤백된다. 창고 시스템은 존재하지 않는 주문을 처리한다.

**커밋 먼저, 발행 나중**이면 메시지가 유실된다. `orders` 행은 커밋됐는데 그 직후 프로세스가 죽거나 브로커가 리더 선출 중이면 이벤트가 영영 나가지 않는다. DB만 보면 주문은 정상이라 아무도 모른다.

여기가 핵심이다. 어느 쪽이든 **커밋 사이의 구간에서 프로세스가 죽으면 두 시스템의 상태가 갈라진다**. 재시도 로직을 아무리 촘촘하게 넣어도 재시도를 실행할 프로세스 자체가 죽는 경우는 못 막는다.

XA 2PC로 묶는 방법이 있지만 코디네이터 장애 시 참가자가 락을 쥔 채 멈추고, Kafka는 애초에 XA 리소스 매니저가 아니다. 이 트레이드오프는 [[saga-vs-2pc]]에서 다뤘다.

### 그래서 커밋을 하나로 만든다

Outbox 패턴의 발상은 단순하다. **이벤트를 브로커가 아니라 같은 DB의 테이블에 쓴다.**

```sql
BEGIN;
INSERT INTO orders (id, user_id, amount) VALUES (...);
INSERT INTO outbox (aggregate_id, event_type, payload)
  VALUES ('order-9f3a', 'OrderCreated', '{"orderId":"order-9f3a",...}');
COMMIT;
```

커밋이 한 번이다. 주문 행과 이벤트 행은 같은 WAL 레코드에 들어가므로 둘 다 남거나 둘 다 없다. 원자성 문제가 DB 안으로 들어와서 사라진다.

브로커로 실제 전달하는 건 별개의 **relay**가 맡는다. outbox 테이블에서 아직 안 보낸 행을 읽어 발행하고 표시한다. relay가 발행 직후 표시 전에 죽으면 같은 이벤트를 두 번 보낸다. 즉 outbox는 exactly-once를 만들어주지 않는다. **유실을 중복으로 바꾸는 것**이고, 중복은 소비자 쪽 [[idempotency-key]]로 흡수한다. [[at-least-once-vs-at-most-once]]의 선택을 명시적으로 한 쪽으로 고정하는 셈이다.

### relay를 어떻게 돌리나

두 가지다.

**폴링**: relay가 주기적으로 `SELECT ... WHERE published_at IS NULL`을 돈다. 구현이 단순한 대신 폴링 주기만큼 지연이 붙고 빈 쿼리가 계속 나간다.

**트랜잭션 로그 테일링**: Debezium 같은 CDC가 WAL을 직접 읽어 outbox INSERT를 감지한다. 폴링 부하가 0이고 WAL 순서가 그대로 이벤트 순서가 된다. 대신 replication slot이라는 새 운영 대상이 생긴다. 자세한 건 [[cdc-debezium]].

## 현장 시나리오

주문 12만 건/일 규모의 이커머스. 주문 서비스는 `@Transactional` 안에서 `orderRepository.save()` 후 `kafkaProducer.send()`를 호출하고 있었다. `acks=all`, 재시도까지 설정해뒀으니 안전하다고 봤다.

새벽 3시 12분, Kafka 브로커 한 대가 디스크 압박으로 GC 스톨에 빠졌다. 해당 파티션 리더 선출이 시작되고 produce 요청이 밀린다. 프로듀서는 `delivery.timeout.ms` 120초 동안 내부 버퍼에 이벤트를 쥐고 재시도한다.

그 사이 애플리케이션 파드는 이미 DB 커밋을 끝낸 상태다. 응답을 기다리던 요청들이 쌓이면서 힙이 차고, 3시 14분에 파드 2대가 OOM으로 kill된다. **버퍼 안에서 재시도 대기 중이던 이벤트가 프로세스와 함께 증발했다.** 롤백할 것도 없다. DB 커밋은 이미 성공했으니까.

결과: `orders` 테이블에는 있는데 `OrderCreated`가 나가지 않은 주문 37건. 창고 시스템은 존재를 몰랐고 결제는 이미 승인됐다. 오전 11시 반, 고객 문의 세 건이 들어오고 나서야 CS가 알아챘다. 브로커 장애 자체는 4분 만에 자동 복구됐고 알람은 울리지도 않았다.

outbox로 바꾼 뒤 같은 상황을 재현했을 때는 outbox 테이블에 미발행 행이 쌓였다가 브로커 복구 후 11초 만에 전부 흘러나갔다. 잃어버린 건 4분간의 지연이지 주문이 아니었다.

## 실무 적용 포인트

1. **outbox 스키마와 부분 인덱스**. `id BIGSERIAL, aggregate_id TEXT, event_type TEXT, payload JSONB, created_at, published_at`. 폴링 방식이면 `CREATE INDEX ON outbox (id) WHERE published_at IS NULL` 부분 인덱스가 필수다. 전체 인덱스를 쓰면 테이블이 커질수록 스캔 비용이 같이 커진다.

2. **relay 워커는 `FOR UPDATE SKIP LOCKED`로 뽑는다**. `SELECT ... WHERE published_at IS NULL ORDER BY id LIMIT 100 FOR UPDATE SKIP LOCKED` — 워커를 여러 개 띄워도 서로 같은 행을 기다리지 않는다. 폴링 주기는 200ms~1s가 실용적이다. 그보다 짧게 잡으면 빈 쿼리가 커넥션을 갉아먹는다.

3. **CDC를 쓰면 slot lag를 반드시 감시한다**. Debezium의 Outbox Event Router SMT는 outbox 행을 토픽/키/페이로드로 자동 변환해준다. 대신 커넥터가 멈추면 Postgres가 WAL을 못 지워 디스크가 찬다. `max_slot_wal_keep_size`를 설정하고 `pg_replication_slots`의 지연을 알람 대상으로 잡아라.

4. **파티션 키는 aggregate_id로 고정한다**. Kafka는 파티션 단위로만 순서를 보장한다. `order-9f3a`의 `Created`/`Paid`/`Shipped`가 다른 파티션으로 흩어지면 소비자가 뒤집힌 순서를 본다. 같은 aggregate는 같은 파티션으로 보내라.

5. **outbox 테이블은 반드시 비운다**. 하루 12만 건이면 한 달에 360만 행이다. 발행된 행을 배치로 `DELETE`하면 autovacuum이 밀려 블로트가 생긴다. `PARTITION BY RANGE (created_at)`으로 일 단위 파티션을 만들고 오래된 파티션을 `DROP`하는 쪽이 비용이 일정하다.

6. **미발행 지연을 SLI로 잡는다**. `SELECT now() - min(created_at) FROM outbox WHERE published_at IS NULL` — 30초를 넘으면 relay가 죽었거나 브로커가 막힌 것이다. relay 실패는 조용하게 진행되므로 이 지표가 없으면 outbox를 쓰는 의미가 절반으로 줄어든다. 반복 실패하는 행은 [[dead-letter-queue]]로 빼서 relay가 한 행에 막히지 않게 한다.

## 더 깊은 토끼굴

- [[saga-vs-2pc]] — outbox가 대체하는 2PC 쪽 트레이드오프
- [[cdc-debezium]] — relay를 WAL 테일링으로 돌리는 방법
- [[idempotency-key]] — at-least-once를 소비자에서 흡수하기
- [[at-least-once-vs-at-most-once]] — 유실과 중복 중 무엇을 고를 것인가
- [[kafka-exactly-once]] — 브로커 트랜잭션이 outbox를 대신할 수 있는 범위

**출처**

- Chris Richardson, Transactional Outbox pattern — https://microservices.io/patterns/data/transactional-outbox.html
- Debezium, Outbox Event Router — https://debezium.io/documentation/reference/stable/transformations/outbox-event-router.html
- PostgreSQL 공식 문서, `SELECT ... FOR UPDATE SKIP LOCKED` — https://www.postgresql.org/docs/current/sql-select.html#SQL-FOR-UPDATE-SHARE

정리하면, 두 시스템에 각각 커밋하는 구조를 한 시스템에 한 번 커밋하는 구조로 접었다. 사라진 37건의 원인은 Kafka 장애가 아니라, 커밋 두 개 사이에 아무도 지키지 않는 구간이 있었다는 것이다.
