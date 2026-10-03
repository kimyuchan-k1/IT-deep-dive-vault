---
title: 락 순서를 맞췄는데 데드락이 났다. 서로를 막은 건 존재하지도 않는 행이었다
date: 2026-10-03
day: 103
category: db
tags: [deadlock, innodb, gap-lock, lock-ordering, postgresql]
related: ["[[mvcc-how]]", "[[phantom-read-isolation]]", "[[upsert-idempotency]]", "[[retry-exponential-backoff-jitter]]", "[[connection-pool-sizing]]"]
difficulty: 3
short_text: |
  🔥 [Day 103] 락 순서 맞췄는데 데드락

  오해: 순서만 지키면 안 난다
  실제: 없는 행 FOR UPDATE→갭락 공유→둘 다 INSERT→서로 대기

  📖 https://github.com/kimyuchan-k1/IT-deep-dive-vault/blob/main/posts/2026-10-03-deadlock-detection.md
---

# 락 순서를 맞췄는데 데드락이 났다. 서로를 막은 건 존재하지도 않는 행이었다

## 흔한 오해

> "데드락은 A→B, B→A처럼 락을 반대 순서로 잡을 때 난다. 순서만 통일하면 끝이고, 혹시 나도 DB가 알아서 하나 죽여 준다."

교과서의 데드락 그림은 항상 행 두 개와 화살표 두 개다. 그래서 대책도 "같은 순서로 잡아라" 한 줄로 끝난다.

절반만 맞다. 순서 규칙은 **실제로 존재하는 행**에 거는 락에만 통한다. InnoDB의 REPEATABLE READ에서는 없는 행을 조회해도 락이 잡힌다. 행과 행 사이의 **간격(gap)** 에 거는 락이다. 그리고 이 갭 락은 같은 쿼리를 두 번 실행하기만 해도 데드락을 만든다. 순서를 뒤집을 것도 없이.

## 실제 원리

### 1. 탐지는 wait-for graph 순회다

트랜잭션 T1이 T2가 가진 락을 기다리면 그래프에 `T1 → T2` 간선이 생긴다. 사이클이 생기면 데드락이다.

```
T1 ──기다림──▶ T2
 ▲              │
 └───기다림─────┘     사이클 = 데드락 → 희생자 1개 롤백
```

InnoDB는 락 대기가 **발생하는 순간** 이 그래프를 탐색한다(`innodb_deadlock_detect` 기본 ON). 희생자는 비용이 작은 쪽이다. 정확히는 insert·update·delete한 행 수가 적은 트랜잭션을 고른다. 클라이언트는 `ERROR 1213 (40001): Deadlock found when trying to get lock`을 받는다.

탐색에는 상한이 있다. 대기 목록이 트랜잭션 200개를 넘거나 검사해야 할 락이 100만 개를 넘으면, InnoDB는 사이클을 끝까지 찾지 않고 **데드락으로 간주**해 검사하던 트랜잭션을 롤백한다. 핫 로우 하나에 수백 개가 줄 선 상황에서는 진짜 데드락이 아닌데도 1213이 뜬다.

### 2. PostgreSQL은 기다렸다가 찾는다

PostgreSQL은 반대 전략이다. 락 대기가 `deadlock_timeout`(기본 1s)을 넘겨야 그때 탐지를 돌린다. 대부분의 대기는 1초 안에 풀리니까 비싼 검사를 아끼는 설계다. 희생자는 비용이 아니라 **검사를 돌린 쪽**이다. 에러는 `40P01 deadlock detected`. 같은 "데드락"이라도 InnoDB는 즉시·작은 쪽, PostgreSQL은 1초 뒤·검사한 쪽이 죽는다.

### 3. 갭 락끼리는 충돌하지 않는다. 그게 함정이다

InnoDB REPEATABLE READ에서 `SELECT ... FOR UPDATE`가 행을 못 찾으면, 그 값이 들어갈 자리의 간격에 갭 락을 건다. 팬텀을 막기 위한 장치다([[phantom-read-isolation]]).

MySQL 문서의 정의가 핵심이다. 갭 락은 "순수하게 억제용"이라 **서로 다른 트랜잭션의 갭 락은 충돌하지 않는다.** 두 트랜잭션이 같은 간격에 동시에 갭 락을 가질 수 있다. 반면 INSERT는 들어가기 전에 **insert intention lock**을 요청하고, 이건 다른 트랜잭션의 갭 락과 충돌한다.

```
T1: SELECT ... WHERE k=7 FOR UPDATE   → 없음, gap(5,9) 획득
T2: SELECT ... WHERE k=7 FOR UPDATE   → 없음, gap(5,9) 획득 (충돌 안 함)
T1: INSERT k=7  → insert intention, T2의 gap 대기
T2: INSERT k=7  → insert intention, T1의 gap 대기   → 사이클
```

락 순서는 완벽히 같다. 둘 다 같은 쿼리를 같은 순서로 실행했다. 문제는 "확인 후 생성" 패턴 자체에 있다. [[mvcc-how]]의 일관된 읽기가 아니라 잠금 읽기를 썼는데도 생긴다.

## 현장 시나리오

쿠폰 서비스, MySQL 8.0, REPEATABLE READ. 발급 API는 이렇게 생겼다.

```sql
BEGIN;
SELECT id FROM user_coupon WHERE user_id=? AND coupon_id=? FOR UPDATE;
-- 없으면
INSERT INTO user_coupon(user_id, coupon_id, ...) VALUES (?, ?, ...);
COMMIT;
```

`(user_id, coupon_id)`에 유니크 인덱스가 있으니 중복은 막힌다고 봤다. 그런데 앱 푸시로 선착순 이벤트를 열자 이렇게 흘렀다.

1. 사용자가 "받기" 버튼을 연타 → 같은 키로 요청 2개가 수 ms 간격 도착
2. 둘 다 `FOR UPDATE`에서 행을 못 찾음 → 둘 다 같은 간격에 갭 락 획득
3. 둘 다 INSERT → insert intention이 서로의 갭 락에 막힘 → 사이클
4. InnoDB가 하나를 롤백, 1213 반환 → 앱에 재시도 로직이 없어 그대로 500
5. 피크 10분간 발급 요청의 수 %가 500. 클라이언트가 실패 화면에서 또 연타 → 같은 쌍이 다시 충돌

로그에 남은 데드락은 하나뿐이었다. `SHOW ENGINE INNODB STATUS`는 **가장 최근 데드락 1건**만 보여 주기 때문이다. 원인 쿼리를 특정하는 데 하루가 걸렸다.

## 실무 적용 포인트

1. **"조회 후 없으면 INSERT"를 잠금 읽기로 하지 않는다.** 유니크 인덱스에 바로 `INSERT ... ON DUPLICATE KEY UPDATE`나 `INSERT IGNORE`를 던진다. 판단을 DB 유니크 제약에 맡기는 게 [[upsert-idempotency]]의 정석이다.

2. **1213 / 40001 / 40P01은 재시도 대상으로 분류한다.** 트랜잭션 전체를 다시 실행하고 횟수는 3회, 간격은 지터 포함 10~50ms에서 시작([[retry-exponential-backoff-jitter]]). 데드락은 "버그"가 아니라 동시성 제어의 정상 결과다.

3. **`innodb_print_all_deadlocks=ON`을 켠다.** 모든 데드락이 에러 로그에 남는다. 기본 OFF라서 켜지 않으면 최근 1건밖에 못 본다.

4. **존재하는 행끼리는 PK 오름차순으로 잠근다.** 다건 갱신은 `WHERE id IN (...) ORDER BY id FOR UPDATE`. 애플리케이션에서 정렬해 루프를 돌리는 것도 같다.

5. **트랜잭션을 짧게 유지한다.** 락 안에서 외부 API를 부르면 대기 사슬이 길어지고 `innodb_lock_wait_timeout`(기본 50s) 동안 커넥션이 묶인다. 결제처럼 길어질 수밖에 없는 구간은 이 값을 세션 단위로 5s 이하로 내린다([[connection-pool-sizing]]).

6. **핫 로우 경합이 극단적이면 탐지 자체가 병목이다.** MySQL 문서는 이 경우 `innodb_deadlock_detect=OFF` 후 `innodb_lock_wait_timeout`에 의존하는 선택지를 명시한다. 끄기 전에 카운터 분산(행 N개로 쪼개기)부터 검토한다.

## 더 깊은 토끼굴

- [[phantom-read-isolation]] — 갭 락이 존재하는 이유. READ COMMITTED로 내리면 갭 락 대부분이 사라진다.
- [[mvcc-how]] — 일관된 읽기(락 없음)와 잠금 읽기(`FOR UPDATE`)가 다른 스냅샷을 보는 구조.
- [[upsert-idempotency]] — 확인 후 생성 대신 유니크 제약으로 경쟁을 끝내는 방법.
- [[retry-exponential-backoff-jitter]] — 데드락 재시도가 같은 충돌을 반복하지 않게 하는 간격 설계.
- [[connection-pool-sizing]] — 락 대기가 풀 고갈로 번지는 경로.

**1차 출처**
- MySQL 8.0 Reference Manual — Deadlock Detection (200 트랜잭션 / 100만 락 상한, `innodb_deadlock_detect`): https://dev.mysql.com/doc/refman/8.0/en/innodb-deadlock-detection.html
- MySQL 8.0 Reference Manual — InnoDB Locking (gap lock, insert intention lock): https://dev.mysql.com/doc/refman/8.0/en/innodb-locking.html
- MySQL 8.0 Reference Manual — How to Minimize and Handle Deadlocks: https://dev.mysql.com/doc/refman/8.0/en/innodb-deadlocks-handling.html
- PostgreSQL Docs — Lock Management (`deadlock_timeout`): https://www.postgresql.org/docs/current/runtime-config-locks.html
