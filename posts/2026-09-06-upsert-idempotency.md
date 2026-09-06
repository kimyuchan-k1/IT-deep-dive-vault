---
title: UPSERT를 걸어놨는데 재시도 한 번에 잔액이 두 배가 됐다
date: 2026-09-06
day: 88
category: db
tags: [postgres, mysql, upsert, idempotency, transaction]
related: ["[[idempotency-key]]", "[[mvcc-how]]", "[[postgres-vacuum-bloat]]", "[[outbox-pattern]]"]
difficulty: 3
short_text: |
  🔥 [Day 88] UPSERT 걸어놨는데 잔액이 두 배가 됐다

  오해: UPSERT는 멱등하다
  실제: DO UPDATE SET x = x+n → 재시도마다 누적

  📖 https://github.com/kimyuchan-k1/IT-deep-dive-vault/blob/main/posts/2026-09-06-upsert-idempotency.md
---

# UPSERT를 걸어놨는데 재시도 한 번에 잔액이 두 배가 됐다

## 흔한 오해

> "`INSERT ... ON CONFLICT DO UPDATE`는 한 문장이고 원자적이니까, 재시도해도 결과가 같은 거 아닌가?"

그래서 웹훅 수신부나 컨슈머에 UPSERT 한 줄을 박아놓고 "멱등 처리 끝났다"고 말한다. 튜토리얼들이 UPSERT를 "중복 삽입 방지 패턴"으로 소개하니 자연스러운 결론이다.

원자성은 맞다. 멱등성은 아니다. **원자적이라는 말은 "중간 상태가 보이지 않는다"는 뜻이지, "몇 번 실행해도 결과가 같다"는 뜻이 아니다.** 이 둘을 붙여 쓰는 순간 돈이 새기 시작한다.

## 실제 원리

### 1. UPSERT는 삽입 실패를 감지해서 갈아타는 구조다

Postgres의 `ON CONFLICT`는 "먼저 확인하고 나중에 넣는" 방식이 아니다. speculative insertion — 일단 튜플을 투기적으로 넣고, arbiter 인덱스에 유니크 충돌이 걸리면 그 튜플을 죽인 뒤(`super-deleted`) 충돌한 기존 행으로 방향을 튼다.

여기서 두 가지가 따라온다. 하나, 충돌해서 UPDATE로 갔어도 **죽은 튜플은 남는다**. UPSERT를 초당 수천 번 때리는 테이블은 [[postgres-vacuum-bloat]]가 UPDATE 전용 테이블과 똑같이 쌓인다. 둘, `SERIAL`/`IDENTITY`의 `nextval()`은 충돌 판정 **이전에** 호출된다. 충돌해서 INSERT가 취소돼도 시퀀스는 소비된다. ID가 듬성듬성 비는 건 버그가 아니라 설계다.

### 2. arbiter 인덱스는 하나만 고른다

`ON CONFLICT (email) DO UPDATE`는 email 유니크 인덱스 하나만 감시한다. 같은 테이블에 `phone` 유니크 인덱스가 또 있으면, phone 충돌은 `ON CONFLICT`가 잡아주지 않고 그대로 `unique_violation`(SQLSTATE 23505)으로 튀어나온다. "UPSERT 걸었으니 중복 예외는 안 난다"는 가정이 여기서 깨진다.

### 3. 핵심 — DO UPDATE의 우변이 멱등성을 결정한다

여기가 갈림길이다.

```sql
-- 멱등: 절대값 대입. 100번 돌려도 결과 동일
ON CONFLICT (order_id) DO UPDATE SET status = EXCLUDED.status;

-- 비멱등: 상대값 누적. 재시도 횟수만큼 틀어진다
ON CONFLICT (user_id) DO UPDATE SET balance = accounts.balance + EXCLUDED.amount;
```

UPSERT 자체에는 멱등성이 없다. 멱등성은 **UPDATE 절이 대입(assignment)이냐 누적(accumulation)이냐**에서 나온다. 그리고 실무에서 UPSERT를 쓰고 싶어지는 경우의 상당수가 카운터·잔액·재고 — 전부 누적이다.

### 4. 다중 행 UPSERT는 데드락을 만든다

`ON CONFLICT DO UPDATE`는 충돌한 행에 `FOR UPDATE` 급 row lock을 건다. 트랜잭션 A가 `VALUES (1),(2)` 순서로, B가 `(2),(1)` 순서로 배치 UPSERT를 날리면 서로의 락을 기다린다. Postgres가 40P01로 한쪽을 죽인다. 배치 크기가 커질수록 확률이 올라간다.

MySQL의 `INSERT ... ON DUPLICATE KEY UPDATE`는 여기에 하나 더 얹는다. REPEATABLE READ에서 유니크 인덱스 탐색이 gap lock을 잡아서, 충돌이 없는 삽입끼리도 데드락이 난다. 게다가 `affected_rows`가 삽입이면 1, 갱신이면 2를 돌려줘서 "몇 건 처리했나"를 이 값으로 세면 숫자가 부풀어 오른다.

### 5. MERGE는 대안이 아니다

Postgres 15부터 `MERGE`가 생겼지만, `MERGE`는 `ON CONFLICT`와 달리 동시성 안전하지 않다. 공식 문서가 명시한다 — 동시 실행 시 `unique_violation`이 날 수 있고, 재시도는 애플리케이션 몫이다. 표준 문법이라고 갈아타면 없던 예외가 생긴다.

## 현장 시나리오

정산 시스템. PG사 결제 웹훅을 받아 `wallet` 테이블에 잔액을 더한다. 코드는 한 줄이었다.

```sql
INSERT INTO wallet (user_id, balance) VALUES ($1, $2)
ON CONFLICT (user_id) DO UPDATE SET balance = wallet.balance + EXCLUDED.balance;
```

밤 11시, 커밋은 성공했는데 응답이 나가기 직전 ALB 유휴 타임아웃 60초에 걸려 커넥션이 끊긴다. PG사는 200을 못 받았으니 **정상 재시도** 정책대로 같은 `payment_id`를 다시 보낸다. 서버는 다시 UPSERT를 실행한다. 충돌 → `balance + 50000` 한 번 더.

체인이 이렇게 이어졌다. 응답 유실 → 웹훅 재전송 → UPSERT 재실행 → 잔액 2배 → 사용자 출금 → 원장(ledger) 합계와 wallet 잔액 불일치 → 다음 날 아침 정산 배치가 12,400원이 아니라 470만원 차이를 뱉음.

원인은 UPSERT가 아니었다. `payment_id`가 어디에도 유니크 제약으로 없었다는 것이다. 충돌 기준이 `user_id`였으니, 같은 결제를 두 번 받았다는 사실 자체를 DB가 알 방법이 없었다.

## 실무 적용 포인트

1. **누적 연산에는 UPSERT를 쓰지 마라.** `SET x = x + n` 형태면 멱등이 아니다. 대신 이벤트 ID를 PK로 갖는 `ledger` 테이블에 `INSERT ... ON CONFLICT (payment_id) DO NOTHING`으로 넣고, 잔액은 그 테이블의 `SUM()` 또는 트리거로 파생시킨다. 중복은 삽입 단계에서 죽는다.
2. **충돌 기준을 "비즈니스 중복 키"로 잡아라.** `user_id`가 아니라 `payment_id`, `message_id`, `Idempotency-Key` 헤더값이다. [[idempotency-key]]와 [[http-idempotency]]가 이 얘기다.
3. **`DO NOTHING`으로 처리 결과를 판정하지 마라.** 충돌 시 `RETURNING`이 빈 결과를 준다. "0행 반환 = 중복이었음"을 알고 싶으면 `WITH ins AS (INSERT ... RETURNING *) SELECT ... UNION ALL SELECT ... FROM t WHERE key = $1` 패턴으로 기존 행을 다시 읽어야 한다.
4. **배치 UPSERT는 정렬해서 보내라.** `ORDER BY` 후 `VALUES` 목록을 구성하면 모든 트랜잭션이 같은 락 순서를 갖는다. 데드락 확률이 사실상 0으로 떨어진다. 배치 크기는 500~1000행 이하 권장 — 커질수록 락 보유 시간이 선형으로 늘어난다.
5. **`ON CONFLICT`는 유니크/exclusion 제약이 있어야만 동작한다.** 부분 인덱스(`WHERE deleted_at IS NULL`)를 arbiter로 쓰려면 `ON CONFLICT (email) WHERE deleted_at IS NULL`처럼 조건을 그대로 반복해줘야 인덱스가 매칭된다. 안 그러면 42P10 에러다.
6. **UPSERT 테이블은 autovacuum을 따로 튜닝해라.** `ALTER TABLE wallet SET (autovacuum_vacuum_scale_factor = 0.02)` 정도로 기본 0.2에서 내린다. 죽은 튜플이 삽입 실패분까지 쌓이므로 일반 테이블 기준으로는 늦다. [[mvcc-how]] 참고.

정산 팀이 최종적으로 고친 건 SQL 한 줄이 아니었다. `payment_id UNIQUE` 컬럼 하나를 추가하고, 잔액 갱신을 [[outbox-pattern]]으로 원장 삽입 뒤에 붙인 것이다. 멱등성은 쿼리 문법이 아니라 **어떤 키로 "같은 요청"을 정의했느냐**에서 나온다.

## 더 깊은 토끼굴

- [[idempotency-key]] — 클라이언트가 보내는 멱등 키의 수명과 저장 위치
- [[http-idempotency]] — HTTP 메서드별 멱등성 정의와 재시도 규약
- [[mvcc-how]] — 죽은 튜플이 왜 생기고 언제 회수되는가
- [[postgres-vacuum-bloat]] — UPSERT 폭주 테이블의 블로트 관리
- [[outbox-pattern]] — DB 커밋과 메시지 발행을 한 트랜잭션으로 묶기
- [[phantom-read-isolation]] — gap lock과 격리 수준이 데드락에 미치는 영향

**출처**
- PostgreSQL 공식 문서, INSERT — ON CONFLICT Clause: https://www.postgresql.org/docs/current/sql-insert.html
- PostgreSQL 공식 문서, MERGE (동시성 주의사항 포함): https://www.postgresql.org/docs/current/sql-merge.html
- MySQL 8.0 Reference Manual, INSERT ... ON DUPLICATE KEY UPDATE: https://dev.mysql.com/doc/refman/8.0/en/insert-on-duplicate.html
