---
title: 변경분을 잡으려 5초마다 SELECT를 돌렸는데, CDC는 이미 끝난 트랜잭션 로그를 읽고 있었다
date: 2026-06-12
day: 11
category: distributed
tags: [cdc, debezium, binlog, wal, kafka, replication]
related: ["[[outbox-pattern]]", "[[wal-pitr]]", "[[kafka-connect-schema-registry]]", "[[replication-lag]]", "[[event-sourcing-intro]]", "[[cqrs-when-needed]]"]
difficulty: 3
short_text: |
  💡 [Day 11] CDC는 폴링이 아니라 로그를 읽는다
  오해: 변경 감지=주기 폴링/트리거
  실제: binlog/WAL tail→부하0→순서보장→이벤트
  "폴링을 지웠더니 부하가 사라졌다"
  📖 https://github.com/kimyuchan-k1/IT-deep-dive-vault/blob/main/posts/2026-06-12-cdc-debezium.md
---

# 변경분을 잡으려 5초마다 SELECT를 돌렸는데, CDC는 이미 끝난 트랜잭션 로그를 읽고 있었다

## 흔한 오해

"DB가 바뀐 걸 다른 시스템에 알리려면, 결국 주기적으로 테이블을 조회해서 `updated_at`이 바뀐 행을 긁어오는 거 아닌가? 아니면 트리거를 걸어서 변경될 때마다 별도 테이블에 기록하든가. CDC(Change Data Capture)라는 것도 그냥 이걸 좀 자동화해 주는 도구겠지."

변경 전파를 처음 만들 때 거의 다 폴링 아니면 트리거로 시작한다. 그래서 입문 자료도 "5초마다 `WHERE updated_at > ?`로 긁어오세요" 정도로 가르치고 넘어간다.

**둘 다 동작은 하는데, 둘 다 DB를 또 때린다.** 폴링은 변경이 없어도 계속 쿼리를 날려 부하를 만들고, 트리거는 원래 트랜잭션 안에서 같이 돌아 쓰기 지연을 늘린다. Debezium 같은 로그 기반 CDC는 *아예 테이블을 안 본다*. 이미 커밋되어 디스크에 적힌 **트랜잭션 로그를 뒤에서 따라 읽을** 뿐이다.

## 실제 원리

핵심은 하나다 — **모든 변경은 이미 한 번 기록되어 있다.** DB는 내구성(durability)을 위해 데이터 파일을 고치기 전에 변경을 먼저 로그에 적는다. MySQL의 `binlog`, PostgreSQL의 `WAL`(Write-Ahead Log)이 그것이다. 이 로그는 [[wal-pitr]]이나 복제(replication)를 위해 어차피 존재한다.

### CDC는 복제 슬레이브인 척한다

여기가 핵심이다. Debezium은 DB에 대해 **자기를 복제 팔로워(replica)처럼 등록**한다. MySQL이면 `binlog`를 받아가는 가짜 슬레이브로, PostgreSQL이면 **logical replication slot**을 잡고 WAL 스트림을 받아간다. DB 입장에선 "복제본 하나 더 붙었네" 수준이라, 테이블에 추가 쿼리가 0건이다.

그래서 폴링·트리거와 결정적으로 다르다:

- **DB 부하**: 폴링은 변경이 없어도 N초마다 쿼리. CDC는 변경이 있을 때만 로그가 흐르고, 그 로그는 이미 쓰여 있던 것이라 추가 읽기 비용이 사실상 0.
- **순서 보장**: 로그는 커밋된 **트랜잭션 순서 그대로** 적혀 있다. 폴링은 `updated_at`이 같은 밀리초에 겹치면 순서가 깨지고, 폴링 주기 사이에 두 번 바뀌면 **중간 상태를 통째로 놓친다**(예: A→B→C 중 B를 못 봄).
- **삭제 감지**: 폴링으로는 사라진 행을 알 수 없다(없는 걸 SELECT할 수 없으니). 로그엔 `DELETE`가 명시적으로 찍힌다.

### offset이 진짜 어려운 부분

CDC가 읽은 위치는 **offset**(binlog 파일+포지션, 또는 WAL의 LSN)으로 관리된다. Debezium은 이 offset을 Kafka 토픽에 주기적으로 커밋한다. 문제는 "메시지를 보냈다"와 "offset을 커밋했다" 사이가 원자적이지 않다는 점이다. 그 사이 죽으면 재시작 후 같은 변경을 다시 읽는다 — 그래서 CDC는 본질적으로 **at-least-once**고, 같은 이벤트가 두 번 올 수 있다. 받는 쪽이 멱등([[outbox-pattern]]의 그 멱등)해야 하는 이유다.

또 하나의 함정은 **logical slot이 안 빠지면 WAL이 안 지워진다**는 것. 컨슈머가 멈춰 있으면 PostgreSQL은 "아직 이 slot이 못 읽은 WAL"이라 판단해 계속 쌓고, 디스크가 찬다.

## 현장 시나리오

한 커머스의 검색 인덱스 동기화. 주문/상품 테이블이 바뀌면 Elasticsearch에 반영해야 했다. 처음엔 배치 워커가 **5초마다 `WHERE updated_at > last_run`**으로 긁었다. 인과 사슬은 이랬다:

- 트래픽이 늘자 폴링 쿼리 자체가 **상품 테이블 풀스캔에 가까워졌다**. `updated_at` 인덱스가 있어도 변경 없는 5초마다 DB를 때리는 건 그대로였다
- 더 큰 문제: 한 상품이 5초 안에 **재고 변경→가격 변경→품절** 3번 바뀌면, 폴링은 마지막 상태만 봤다. 중간의 "가격 변경" 이벤트를 흘려서, 가격 알림이 안 나갔다
- 세일 시작 순간엔 수만 행이 동시에 바뀌었고, 폴링 워커가 한 번에 다 못 긁어 **랙이 분 단위로 벌어졌다**. 검색 결과의 가격이 실제와 달라 CS가 터졌다

팀은 Debezium 기반 CDC로 바꿨다. PostgreSQL에 logical replication slot을 만들고, WAL을 Kafka로 흘려 [[kafka-connect-schema-registry]]로 스키마를 관리했다. 결과:

- 폴링 쿼리가 **완전히 사라져** DB CPU가 눈에 띄게 내려갔다
- 모든 중간 변경이 **커밋 순서대로** 토픽에 찍혀, "가격 변경" 이벤트를 더는 안 놓쳤다
- 세일 순간의 대량 변경도 로그 스트림이라 **순차적으로 흘러** 랙이 초 단위로 안정됐다

원인은 폴링이 게을러서가 아니라, **이미 DB가 트랜잭션 로그에 순서대로 다 적어둔 정보를, 굳이 테이블을 다시 긁어 재구성하려 한 것**이었다. 답은 만드는 게 아니라 이미 있던 로그를 읽는 것이었다.

## 실무 적용 포인트

1. **로그 기반 CDC를 우선 검토하라**: 변경 전파가 필요하면 폴링/트리거 전에 `binlog`(MySQL `binlog_format=ROW`) 또는 PostgreSQL `wal_level=logical`을 먼저 본다. 테이블 추가 쿼리 0건이 가장 큰 이점이다.
2. **컨슈머는 무조건 멱등하게**: CDC는 at-least-once다. 같은 이벤트 중복 도착을 가정하고, 이벤트의 PK+LSN/binlog-position을 **dedup 키**로 써라. [[event-sourcing-intro]]나 upsert로 받으면 안전하다.
3. **logical slot 모니터링 필수**: PostgreSQL은 `pg_replication_slots`의 `restart_lsn`과 현재 LSN 차이로 **밀린 WAL 양**을 본다. 컨슈머가 죽으면 WAL이 무한정 쌓여 디스크가 찬다 — 임계 알림을 걸어라.
4. **초기 스냅샷 비용을 계획하라**: Debezium은 첫 기동 시 기존 테이블 전체를 **snapshot**한다. 대형 테이블이면 이 단계가 오래 걸리고 락/부하를 준다. `incremental snapshot`(시그널 테이블) 옵션으로 쪼개라.
5. **dual-write 대신 Outbox+CDC**: "DB도 쓰고 Kafka도 쏘는" dual-write는 둘 중 하나만 성공하면 깨진다. 대신 같은 트랜잭션에 outbox 행을 쓰고 그 테이블을 CDC로 흘리면 원자성이 보장된다 — [[outbox-pattern]] 참고.
6. **스키마 변경(DDL)을 의식하라**: 컬럼 추가/삭제가 binlog/WAL에도 흐른다. 컨슈머가 옛 스키마를 가정하면 깨지므로, Schema Registry의 **호환성 모드(backward)**로 막고 [[replication-lag]]처럼 전파 지연도 같이 본다.

## 더 깊은 토끼굴

- Debezium 공식: [Debezium Architecture](https://debezium.io/documentation/reference/stable/architecture.html) — connector가 로그를 읽어 Kafka로 흘리는 전체 구조
- PostgreSQL 공식: [Logical Decoding Concepts](https://www.postgresql.org/docs/current/logicaldecoding-explanation.html) — replication slot과 LSN의 동작
- MySQL 공식: [The Binary Log](https://dev.mysql.com/doc/refman/8.0/en/binary-log.html) — `ROW` 포맷이 CDC에 필요한 이유
- [[outbox-pattern]]: dual-write 문제를 CDC로 푸는 정석
- [[wal-pitr]]: CDC가 빌려 쓰는 바로 그 로그의 원래 용도
- [[event-sourcing-intro]]: 변경 로그를 1급 데이터로 다루는 사고의 확장
