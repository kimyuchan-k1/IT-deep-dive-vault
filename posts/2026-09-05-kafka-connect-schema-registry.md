---
title: 스키마 호환성을 BACKWARD로 켜뒀다. 컨슈머가 전부 죽었다
date: 2026-09-05
day: 87
category: kafka
tags: [kafka, schema-registry, avro, kafka-connect, schema-evolution]
related: ["[[kafka-exactly-once]]", "[[dead-letter-queue]]", "[[event-sourcing-intro]]", "[[kafka-partition-math]]"]
difficulty: 3
short_text: |
  🔥 [Day 87] BACKWARD 호환인데 컨슈머가 다 죽었다

  오해: 호환성 켜두면 안전
  실제: BACKWARD는 직전 1개만 검사→v1↔v3 무방비

  📖 https://github.com/kimyuchan-k1/IT-deep-dive-vault/blob/main/posts/2026-09-05-kafka-connect-schema-registry.md
---

# 스키마 호환성을 BACKWARD로 켜뒀다. 컨슈머가 전부 죽었다

## 흔한 오해

> "Schema Registry에 BACKWARD 호환성을 켜뒀다. 깨지는 스키마는 등록 자체가 거부되니까, 등록에 성공했다는 건 안전하다는 뜻이다."

근거가 있는 믿음이다. Confluent Schema Registry의 기본 compatibility가 실제로 `BACKWARD`고, 호환되지 않는 스키마를 올리면 HTTP 409로 튕겨낸다. 게이트가 실재하니 게이트를 믿게 된다.

문제는 그 게이트가 **무엇과 무엇을 비교하는지**에 있다. 대부분은 "새 스키마 vs 지금까지의 모든 데이터"를 비교한다고 생각한다. 아니다. `BACKWARD`는 **새 스키마와 직전 버전 하나만** 비교한다. 그리고 토픽에 남아 있는 데이터는 직전 버전만이 아니다.

## 실제 원리

### 메시지 안에는 스키마가 아니라 숫자 4바이트가 들어 있다

Confluent 직렬화 포맷은 이렇게 생겼다.

```
[0x00][schema id: 4 bytes, big-endian][Avro binary payload]
```

맨 앞 1바이트는 매직 바이트, 그다음 4바이트가 Registry에 등록된 전역 스키마 ID다. 페이로드에는 필드 이름도 타입도 없다. Avro 바이너리는 **스키마를 알아야만 해석되는** 포맷이라, 컨슈머는 ID로 Registry를 조회해야 바이트를 뜯을 수 있다.

여기서 스키마가 두 개 등장한다. 데이터를 쓸 때 쓰인 **writer schema**(ID로 조회), 애플리케이션이 기대하는 **reader schema**(내 코드에 컴파일된 클래스). 디코딩은 writer로 하고, 그 결과를 reader 모양으로 투영한다. 이 투영 규칙이 Avro 명세의 schema resolution이다.

- reader에만 있는 필드 → writer 데이터에 없으니 **default 값으로 채운다. default가 없으면 에러.**
- writer에만 있는 필드 → reader가 무시한다.
- 같은 이름인데 타입이 다르면 → 승격 가능한 조합만 통과한다. `int→long→float→double`, `string↔bytes`. **그 밖은 전부 실패.**

즉 호환성이란 Registry의 상태가 아니라 **writer/reader 스키마 쌍마다 매번 다시 판정되는 관계**다. Registry는 그 쌍 중 딱 한 쌍만 미리 검사해 준다.

### BACKWARD는 직전 한 버전만 본다

compatibility 타입은 방향과 범위, 두 축으로 나뉜다. 방향은 이름이 말해 준다. `BACKWARD`는 새 스키마 reader가 옛 데이터를 읽고, `FORWARD`는 옛 reader가 새 데이터를 읽고, `FULL`은 양쪽 다다. 범위는 접미사가 결정한다. 이 셋은 **직전 버전 1개**와만 비교하고, `_TRANSITIVE`가 붙은 셋만 **모든 이전 버전**과 비교한다.

`BACKWARD`가 기본이라는 건 **컨슈머를 먼저 배포하라**는 전제가 깔려 있다는 뜻이다. 새 reader가 옛 데이터를 읽을 수 있으니 컨슈머부터 올리고 프로듀서를 뒤따르게 한다. `FORWARD`는 정반대 순서다.

방향은 대체로 잘 지켜진다. 사고는 범위에서 난다. v1→v2가 통과하고 v2→v3가 통과했다고 해서 **v1→v3가 통과하는 건 아니다.** 호환성은 이행적(transitive)이지 않다. 삭제와 추가를 섞으면 특히 그렇다. 필드를 지웠다가(v2) 같은 이름을 다른 타입으로 되살리면(v3), 각 단계는 초록불인데 v1 데이터와 v3 reader 사이에는 승격 불가능한 타입 쌍이 남는다. `BACKWARD_TRANSITIVE`였다면 v3 등록 시점에 v1과도 비교돼 409로 막혔을 조합이다.

"옛 버전 데이터는 retention으로 사라지지 않나"가 다음 방어선인데, 두 군데서 뚫린다. `cleanup.policy=compact`인 토픽은 키별 최신 레코드를 **영구히** 보관한다. 갱신이 뜸한 키의 값은 몇 년 전 스키마 그대로 앉아 있다. [[event-sourcing-intro]]처럼 로그 자체가 원장인 구조는 애초에 지우지 않는다. 이런 토픽에서 비-transitive 호환성은 사실상 무방비다.

### Kafka Connect는 스키마를 조용히 등록한다

Connect의 컨버터(`io.confluent.connect.avro.AvroConverter`)도 같은 직렬화기를 쓰고, Confluent 직렬화기의 `auto.register.schemas` 기본값은 `true`다. 소스 커넥터가 컬럼이 바뀐 테이블을 읽으면 사람이 리뷰한 적 없는 스키마가 **런타임에 새 버전으로 등록된다.** 그 시점부터 토픽에는 아무도 합의하지 않은 v4가 섞인다.

Subject 이름 규칙도 함정이다. 기본 `TopicNameStrategy`는 subject를 `{topic}-value`로 잡는다. 한 토픽에 `OrderCreated`와 `OrderCancelled`를 같이 넣으면 두 타입이 **같은 subject의 연속된 버전으로** 취급돼, 호환성 검사가 서로 무관한 스키마끼리 비교한다. 이럴 땐 `value.subject.name.strategy`를 `TopicRecordNameStrategy`로 바꿔 타입별로 버전 히스토리를 분리해야 한다.

## 현장 시나리오

주문 이벤트를 Kafka로 흘리는 커머스. 토픽 `orders.v1`, 파티션 24개, 컨슈머 8대. `cleanup.policy=compact`로 주문 키별 최신 상태를 보관한다.

- 2024년 v1: `discount_rate: double`. 프로모션 팀이 쓰던 값이다.
- 2025년 3월 v2: 프로모션 로직이 별도 서비스로 빠지면서 `discount_rate` 삭제. BACKWARD 통과.
- 2026년 9월 v3: 새 쿠폰 정책. 할인을 `"10%"`, `"3000원"`처럼 써야 해서 `discount_rate: string`을 default `""`와 함께 추가. 등록 성공(직전 v2와만 비교됨). 정산 컨슈머 8대를 v3로 롤링 배포.
- 배포 14분 뒤 컨슈머 3대가 죽는다. 2024년에 마지막으로 갱신되고 compact를 그대로 살아남은 주문 키를 만난 파티션들이다.

인과 사슬은 이렇다. **v1 레코드 조우 → writer(double) vs reader(string) resolution 실패 → `SerializationException`이 `poll()` 밖으로 던져짐 → 컨슈머 종료 → 재시작 후 커밋 안 된 같은 오프셋 재소비 → 같은 자리에서 다시 사망.** 전형적인 poison pill이다.

두 번째 증폭이 붙는다. 8대 중 3대가 2~3분 주기로 재시작하니 그룹이 그때마다 리밸런스를 돌고, 멀쩡한 5대도 소비를 멈춘다. 40분간 정산 랙이 1,200만 건까지 올라갔다.

임시 조치는 스키마 롤백이 아니었다. 이미 발행된 v3 메시지가 있어 되돌릴 수 없었다. 역직렬화 예외를 [[dead-letter-queue]]로 보내는 핸들러를 붙여 진행부터 재개시키고, 필드 이름을 `discount_expr`로 바꾼 v4를 올려 정리했다.

## 실무 적용 포인트

1. **compacted 토픽과 이벤트 소싱 토픽은 `BACKWARD_TRANSITIVE`로 올린다.** subject 단위로 설정한다: `PUT /config/orders.v1-value` 바디 `{"compatibility": "BACKWARD_TRANSITIVE"}`. 전역은 `PUT /config`. 대가는 등록 시 검사가 버전 수만큼 도는 것뿐이다.
2. **삭제한 필드 이름은 재사용하지 않는다.** 이름은 무덤이다. 이름이 같으면 resolution이 두 타입을 잇겠다고 시도하다 실패한다. 새 의미는 `discount_expr` 같은 새 이름으로 붙인다.
3. **추가하는 필드에는 항상 default를 넣는다.** `["null", "string"]` 유니온에 `"default": null`이 가장 안전하다. Avro는 유니온 default가 **첫 번째 타입**을 따르므로 `"null"`을 앞에 둔다. default 없는 필드 추가는 BACKWARD 위반이다.
4. **런타임 등록을 막고 검사를 CI로 옮긴다.** 프로듀서/Connect는 `auto.register.schemas=false`, `use.latest.version=true`(Connect 워커에서는 `value.converter.` 접두사). 대신 배포 전에 `POST /compatibility/subjects/{subject}/versions/latest?verbose=true`를 부르거나 `kafka-schema-registry-maven-plugin`의 `test-compatibility` 골을 PR 검사로 돌린다.
5. **역직렬화 실패를 poison pill로 취급한다.** Connect 싱크 커넥터는 `errors.tolerance=all`, `errors.deadletterqueue.topic.name=dlq.orders`, `errors.deadletterqueue.context.headers.enable=true`로 원인 헤더까지 남긴다. 순수 컨슈머라면 Spring Kafka의 `ErrorHandlingDeserializer`처럼 예외를 값으로 바꿔 주는 래퍼를 써서 `poll()` 루프를 살려 둔다. 안 그러면 [[at-least-once-vs-at-most-once]] 재시도가 무한 루프가 된다.
6. **Registry 장애 반경을 알아둔다.** 직렬화기는 스키마↔ID 매핑을 로컬 캐시에 들고 있어 Registry가 죽어도 이미 본 스키마는 계속 처리된다. 처음 보는 ID 조회와 신규 등록만 실패한다. 새 배포와 Registry 장애가 겹치는 순간이 가장 위험하다. Registry의 저장소 자체가 compacted 토픽 `_schemas`라, 이 토픽의 `cleanup.policy`를 건드리면 스키마 히스토리가 통째로 날아간다.

Schema Registry는 계약을 **강제하는** 장치가 아니라 계약 위반을 **한 쌍에 대해서만 검사해 주는** 장치다. 어느 쌍을 검사하게 할지는 compatibility 설정으로 직접 정해야 한다. 기본값은 가장 싼 검사이지 가장 안전한 검사가 아니다.

## 더 깊은 토끼굴

- [[kafka-exactly-once]] — 트랜잭션이 보장하는 건 전달이지 해석이 아니다
- [[dead-letter-queue]] — poison pill 격리와 재처리 설계
- [[event-sourcing-intro]] — 로그가 원장일 때 스키마 진화가 왜 더 어려워지나
- [[at-least-once-vs-at-most-once]] — 재시도 시맨틱과 무한 루프의 경계
- [[kafka-partition-math]] — 리밸런스가 컨슈머 그룹 전체를 멈추는 구조

**출처**
- Confluent, Schema Evolution and Compatibility: https://docs.confluent.io/platform/current/schema-registry/fundamentals/schema-evolution.html
- Apache Avro 1.11 명세, Schema Resolution: https://avro.apache.org/docs/1.11.1/specification/#schema-resolution
- Confluent, Kafka Connect Deep Dive – Error Handling and Dead Letter Queues: https://www.confluent.io/blog/kafka-connect-deep-dive-error-handling-dead-letter-queues/
