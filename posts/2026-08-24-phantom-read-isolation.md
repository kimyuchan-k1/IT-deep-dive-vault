---
title: 격리 수준을 REPEATABLE READ로 올렸다. 정원 20명 강의에 23명이 등록됐다
date: 2026-08-24
day: 77
category: db
tags: [isolation-level, phantom-read, mvcc, write-skew, serializable, gap-lock]
related: ["[[mvcc-how]]", "[[postgres-vacuum-bloat]]", "[[innodb-buffer-pool]]", "[[btree-index-internals]]", "[[idempotency-key]]"]
difficulty: 3
short_text: |
  ⚠️ [Day 77] RR인데 정원 20명 강의에 23명 등록

  오해: REPEATABLE READ면 팬텀 차단
  실제: 스냅샷은 읽기만 지킨다→write skew

  📖 https://github.com/kimyuchan-k1/IT-deep-dive-vault/blob/main/posts/2026-08-24-phantom-read-isolation.md
---

# 격리 수준을 REPEATABLE READ로 올렸다. 정원 20명 강의에 23명이 등록됐다

## 흔한 오해

> "격리 수준 표는 외웠다. REPEATABLE READ는 팬텀만 허용하고 SERIALIZABLE은 다 막는다. 팬텀이 걱정되면 한 칸 올리면 된다."

이 표는 거의 모든 DB 교재와 면접 질문에 나온다. 출처도 명확하다. ANSI SQL-92가 격리 수준을 **세 가지 이상현상(dirty read, non-repeatable read, phantom)이 나느냐 안 나느냐**로 정의했고, 그 3×4 표가 30년 넘게 복제됐다.

문제는 이 표가 실제 사고를 예측하지 못한다는 것이다. 위 사고에서 트랜잭션은 전부 REPEATABLE READ였고 팬텀 리드는 **한 건도 발생하지 않았다.** 그런데도 정원이 깨졌다.

## 실제 원리

### ANSI 표는 락 기반 구현을 전제로 쓰였다

1995년 Berenson 등이 쓴 "A Critique of ANSI SQL Isolation Levels"가 이 지점을 지적한다. ANSI의 이상현상 정의는 넓게 읽으면 락 기반 2PL 구현과 대응되고, 좁게 읽으면 아무것도 못 막는다. 결정적으로 **스냅샷 격리(SI)는 이 표의 어느 칸에도 들어맞지 않는다.** 그런데 오늘날 PostgreSQL, MySQL InnoDB, Oracle이 전부 MVCC다. 읽기가 락을 잡지 않고 스냅샷을 보니 표의 전제 자체가 다르다.

### MVCC에서 팬텀 리드는 이미 안 난다

PostgreSQL의 REPEATABLE READ는 트랜잭션 첫 쿼리 시점에 스냅샷을 뜨고 끝날 때까지 그것만 본다. 같은 `SELECT count(*) FROM enroll WHERE course_id=7`을 100번 실행해도 값이 안 변한다. 다른 트랜잭션이 커밋한 새 행은 스냅샷보다 나중 XID라 아예 안 보인다. 즉 **ANSI 표가 "RR에서는 허용된다"고 적어둔 팬텀 리드가 PostgreSQL RR에서는 원천적으로 발생하지 않는다.** 표보다 강하다.

MySQL InnoDB RR도 plain `SELECT`는 consistent read라 같다. 여기에 갭 락(gap lock)까지 있어서 `SELECT ... FOR UPDATE`처럼 락을 잡는 읽기는 인덱스의 **행 사이 빈 구간**까지 잠근다. 새 행이 끼어드는 것 자체를 막는다.

그래서 팬텀 리드로 나는 사고는 실무에서 오히려 드물다. 진짜 사고는 다른 이름을 갖고 있다.

### 진짜 범인은 write skew — 그리고 이건 ANSI 표에 없다

패턴은 이렇게 생겼다.

```
T1: SELECT count(*) FROM enroll WHERE course_id=7;  -- 19
T2: SELECT count(*) FROM enroll WHERE course_id=7;  -- 19  (각자 스냅샷)
T1: INSERT INTO enroll VALUES (7, 'alice');         -- 20이라 판단
T2: INSERT INTO enroll VALUES (7, 'bob');           -- 20이라 판단
T1: COMMIT;  T2: COMMIT;                            -- 실제 21
```

여기가 핵심이다. **두 트랜잭션이 같은 행을 건드리지 않았다.** MVCC의 쓰기 충돌 감지는 "같은 행을 두 트랜잭션이 갱신했나"를 보는데 겹치는 행이 없으니 충돌이 아니다. 갭 락도 안 걸린다 — plain `SELECT`는 락을 잡지 않으니까.

락이 걸려야 할 대상은 행이 아니라 **판단의 근거였던 조건(premise)** 이다. "course_id=7인 행이 19개다"라는 사실에 락을 걸 방법이 표준 SQL에는 없다. 이게 write skew이고 ANSI SQL-92의 세 이상현상 어디에도 없다.

### SERIALIZABLE이 이걸 잡는 방식은 DB마다 다르다

PostgreSQL의 SERIALIZABLE은 SSI(Serializable Snapshot Isolation)다. 락을 더 거는 게 아니라 각 트랜잭션이 **무엇을 읽었는지**를 술어 락(predicate lock)으로 추적하고, rw-의존성이 특정 패턴(dangerous structure)을 이루면 한쪽을 죽인다. 실패는 커밋 시점에 `40001 could not serialize access due to read/write dependencies` 로 온다.

그래서 PostgreSQL SERIALIZABLE은 **재시도 로직이 없으면 쓸 수 없다.** 옵션이 아니라 그 격리 수준의 계약이다.

MySQL InnoDB의 SERIALIZABLE은 정반대다. 모든 plain `SELECT`를 암묵적으로 `SELECT ... LOCK IN SHARE MODE`로 바꾼다. 읽기가 공유 락을 잡으니 위 시나리오에서 T1과 T2가 서로를 막고 데드락 또는 대기가 난다. 정확하지만 읽기 처리량이 크게 떨어진다. 같은 단어가 한쪽에서는 "abort를 각오해라", 다른 쪽에서는 "읽기가 느려진다"를 뜻한다.

## 현장 시나리오

교육 플랫폼의 인기 강의 수강신청. 정원 20명, 오픈 시각 정각.

등록 로직은 단순했다. 트랜잭션 열고 → `SELECT count(*) FROM enroll WHERE course_id=7` → 20 미만이면 `INSERT` → 커밋. PostgreSQL, 커넥션 풀 30, 격리 수준은 팀이 "안전하게" REPEATABLE READ로 올려둔 상태였다.

- **오픈 +0.0초**: 대기 요청이 한꺼번에 들어옴. 초당 약 400건. 풀 30개가 전부 동시에 트랜잭션을 연다.
- **+0.02초**: 등록 19명 시점. 동시에 열린 트랜잭션 중 네 개가 각자 스냅샷을 뜨고 전부 `19`를 읽음. 넷 다 "자리가 있다"고 판단.
- **+0.03초**: 네 건의 `INSERT`가 각각 **다른 행**을 만든다. 겹치는 행이 없으니 PostgreSQL은 쓰기 충돌로 보지 않는다. 락 대기도, 에러도 없다.
- **+0.04초**: 네 건 전부 커밋 성공. 등록 인원 **23명**. 로그에는 경고 한 줄 없다.
- **+3일**: 강의실 좌석이 20석이라 CS가 들어오고 3명 환불 + 사과 메일.

원인 한 줄: 정원 체크는 스냅샷을 읽었는데, 그 스냅샷이 맞다는 걸 커밋 시점까지 보장해주는 장치가 아무것도 없었다.

## 실무 적용 포인트

1. **집계 조건으로 판단할 거면 판단의 실체를 잠가라.** `SELECT ... FROM course WHERE id=7 FOR UPDATE`로 부모 행을 먼저 잠그고 카운트한다. 조건에 락을 걸 수 없으니 조건을 대표하는 행에 락을 거는 방식이다(materializing the conflict). 이 케이스에선 이게 가장 싸고 확실하다.
2. **가능하면 DB 제약으로 밀어라.** `UNIQUE(course_id, seat_no)`로 좌석 번호를 실체화하면 초과 등록이 물리적으로 불가능하다. 유니크 인덱스는 격리 수준과 무관하게 항상 동작한다.
3. **PostgreSQL SERIALIZABLE을 쓸 거면 `40001` 재시도를 먼저 붙여라.** 지수 백오프로 3~5회, 재시도 시 부작용 중복을 막으려면 [[idempotency-key]]가 같이 필요하다. 그리고 `max_pred_locks_per_transaction`(기본 64)을 넘기면 술어 락이 페이지·테이블 단위로 승격돼 무관한 트랜잭션까지 abort된다 — 롱 트랜잭션이 있는 워크로드면 256~512로 올려두고 `pg_stat_database.xact_rollback` 추이를 본다.
4. **MySQL RR에서 `FOR UPDATE`를 쓸 거면 인덱스부터 확인해라.** 갭 락은 인덱스 레코드 사이에 걸린다. 조건 컬럼에 인덱스가 없으면 InnoDB가 훑은 모든 행에 넥스트 키 락을 걸어 사실상 테이블 전체가 잠기고 곧 데드락이 난다. `EXPLAIN`에서 `type: ALL`이면 그 `FOR UPDATE`는 쓰면 안 된다.
5. **`SKIP LOCKED`를 팬텀 방지용으로 착각하지 마라.** 잠긴 행을 건너뛰고 다음 걸 가져오는 큐 소비 패턴용이다. 정원 체크에 쓰면 조건을 아예 무시하고 통과한다.
6. **RR로 올리기 전에 롱 트랜잭션 비용을 계산해라.** RR은 시작 시점 스냅샷을 끝까지 유지하므로 그동안 `VACUUM`이 그 XID보다 나중 버전의 죽은 튜플을 회수하지 못한다. 배치가 RR로 10분 돌면 10분치 블로트가 쌓인다 — [[postgres-vacuum-bloat]] 참고.
7. **격리 수준으로 못 막는 것부터 적어라.** write skew, 이중 제출, 외부 API 호출과 커밋 사이의 간극. 셋 다 격리 수준이 아니라 제약·락·멱등키가 푸는 문제다. "SERIALIZABLE로 올렸으니 안전하다"가 가장 위험한 문장이다.

## 더 깊은 토끼굴

- [[mvcc-how]] — 스냅샷이 어떻게 만들어지고 어느 버전을 보는가
- [[postgres-vacuum-bloat]] — 롱 트랜잭션이 스냅샷을 붙잡아 생기는 비용
- [[btree-index-internals]] — 넥스트 키 락 범위가 인덱스 구조로 결정되는 이유
- [[innodb-buffer-pool]] — 갭 락이 걸리는 인덱스 페이지가 사는 곳
- [[idempotency-key]] — `40001` 재시도가 부작용을 두 번 일으키지 않게 하는 장치

**출처**
- PostgreSQL 공식 문서, Transaction Isolation: https://www.postgresql.org/docs/current/transaction-iso.html
- MySQL 공식 문서, InnoDB Locking (gap / next-key lock): https://dev.mysql.com/doc/refman/8.0/en/innodb-locking.html
- Berenson et al., "A Critique of ANSI SQL Isolation Levels" (1995): https://arxiv.org/abs/cs/0701157
- Ports & Grittner, "Serializable Snapshot Isolation in PostgreSQL" (VLDB 2012): https://arxiv.org/abs/1208.4179
