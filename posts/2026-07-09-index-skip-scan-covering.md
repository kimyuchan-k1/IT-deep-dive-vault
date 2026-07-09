---
title: 인덱스를 탔는데 왜 여전히 느린가 — 진짜 병목은 테이블 재방문이었다
date: 2026-07-09
day: 36
category: db
tags: [database, index, covering-index, skip-scan, random-io]
related: ["[[btree-index-internals]]", "[[hash-merge-nested-loop-join]]", "[[innodb-buffer-pool]]", "[[connection-pool-sizing]]"]
difficulty: 3
short_text: |
  💡 [Day 36] 인덱스 탔는데도 느린 이유
  오해: 인덱스=끝
  실제: 인덱스→테이블 재방문 랜덤I/O가 진짜 병목
  "커버링으로 재방문 0, 12초→30ms"
  📖 https://github.com/kimyuchan-k1/IT-deep-dive-vault/blob/main/posts/2026-07-09-index-skip-scan-covering.md
---

# 인덱스를 탔는데 왜 여전히 느린가 — 진짜 병목은 테이블 재방문이었다

## 흔한 오해

"`EXPLAIN` 찍어보니 인덱스를 타긴 타는데(`type: ref`), 그런데도 쿼리가 느리다. 인덱스 걸었으면 끝난 거 아닌가? 인덱스를 탔다는데 대체 뭘 더 하란 말인가."

대부분 여기서 막힌다. 입문서는 "인덱스를 타느냐 풀스캔이냐"라는 이분법만 가르친다. 그래서 `EXPLAIN`에 인덱스 이름이 찍히면 최적화가 끝났다고 안심한다.

**틀린 건 아닌데, "인덱스를 탄다"와 "인덱스만으로 끝난다"는 완전히 다른 얘기라는 걸 빼먹었다.** 대부분의 인덱스 조회는 인덱스에서 위치만 찾고, **실제 데이터를 읽으러 테이블(힙)로 다시 돌아간다.** 이 재방문이 랜덤 I/O 폭탄이고, 여기가 진짜 병목이다. 커버링 인덱스와 스킵 스캔은 바로 이 재방문을 없애거나 우회하는 기술이다.

## 실제 원리

### ① 인덱스 조회는 2단계다 — 탐색 + 재방문

[[btree-index-internals]]에서 봤듯 B+Tree 인덱스는 `(키, 행 위치)` 쌍만 정렬해 갖고 있다. 인덱스에 없는 컬럼을 `SELECT`하면 무슨 일이 벌어지나:

1. 인덱스를 타고 내려가 조건에 맞는 행들의 **위치(row id / PK)**를 찾는다
2. 그 위치 하나하나마다 **테이블 본체로 가서 나머지 컬럼을 읽는다** — 이게 랜덤 I/O

문제는 2번이다. 조건에 맞는 행이 10만 개면 **테이블로 10만 번 랜덤 점프**한다. 인덱스 스캔 자체는 순차적이라 빠른데, 재방문이 디스크 헤드를 사방으로 튀게 만든다. 그래서 옵티마이저는 "결과가 전체의 5~10% 넘으면 차라리 풀스캔"이라 판단하고 인덱스를 버리기도 한다 — 재방문 비용이 순차 스캔보다 비싸지는 지점이다.

### ② Covering Index — 재방문을 아예 없앤다

**쿼리가 필요로 하는 모든 컬럼이 인덱스 안에 이미 있으면, 테이블로 돌아갈 이유가 없다.** 인덱스만 읽고 끝. 이걸 커버링 인덱스(covering index) 혹은 index-only scan이라 한다.

```sql
-- 이 쿼리를 위해
SELECT user_id, amount FROM orders WHERE status = 'PAID';

-- status로만 인덱스 걸면: 인덱스 탐색 후 amount/user_id 읽으러 재방문
CREATE INDEX idx1 ON orders(status);

-- status에 필요한 컬럼을 얹으면: 인덱스만으로 완결
CREATE INDEX idx2 ON orders(status, user_id, amount);
```

`idx2`는 `status`로 위치를 찾고, 같은 인덱스 엔트리 안에 `user_id`, `amount`가 이미 들어 있어 **테이블 재방문이 0**이다. PostgreSQL은 `EXPLAIN`에 `Index Only Scan`으로, MySQL은 `Extra: Using index`로 찍힌다.

핵심 구분: `WHERE`에 쓰는 컬럼은 인덱스 **앞쪽(key)**, `SELECT`만 하는 컬럼은 **뒤에 얹기만** 하면 된다. PostgreSQL은 `INCLUDE (amount)` 구문으로 "탐색엔 안 쓰지만 실어만 두는" 컬럼을 명시할 수 있다. MySQL(InnoDB)은 [[innodb-buffer-pool]]의 클러스터드 인덱스 특성상 PK가 세컨더리 인덱스에 항상 딸려온다.

### ③ Index Skip Scan — leftmost 규칙을 우회한다

복합 인덱스 `(a, b)`는 `WHERE a = ?` 또는 `WHERE a = ? AND b = ?`엔 쓰지만, **`WHERE b = ?`만 있으면 못 쓴다**는 게 leftmost prefix 규칙이다. 정렬 기준이 `a` 먼저라 `b`만으로는 범위를 좁힐 수 없기 때문이다.

스킵 스캔은 여기에 트릭을 쓴다. **`a`의 distinct 값이 적으면**(예: `status`가 3종류), 옵티마이저가 `a`의 각 값마다 `b` 조건을 반복 탐색한다. 논리적으로 `a IN (모든 값) AND b = ?`를 인덱스 안에서 자동으로 돌리는 것. `a`의 카디널리티가 낮을 때만 이득이라, `a`가 수백만 종류면 스킵 스캔은 오히려 손해다. Oracle은 오래전부터, MySQL은 8.0부터, PostgreSQL은 18부터 지원한다.

## 현장 시나리오

대시보드의 "최근 결제 목록" API가 어느 날부터 **12초**가 걸렸다. 데이터가 늘면서 서서히 악화됐다.

원인 사슬은 이랬다:

- 쿼리는 `SELECT order_id, amount, created_at FROM payments WHERE merchant_id = ? AND status = 'PAID'`
- 인덱스는 `(merchant_id, status)`만 있었다 — 조건은 다 커버하니 `EXPLAIN`엔 당당히 인덱스가 찍혔다
- 문제의 상점은 `PAID` 주문이 **500만 건**이었다
- 인덱스로 500만 행의 위치를 찾은 뒤, `amount`와 `created_at`을 읽으러 **테이블로 500만 번 랜덤 재방문**
- 랜덤 I/O가 [[innodb-buffer-pool]] 버퍼풀을 휩쓸어 다른 쿼리 캐시까지 밀려남 → 12초

수정은 인덱스에 두 컬럼을 얹은 것뿐이었다:

```sql
CREATE INDEX idx_cover ON payments(merchant_id, status)
  INCLUDE (order_id, amount, created_at);
```

이제 인덱스 하나로 완결(Index Only Scan), **테이블 재방문 0회**. 12초 → **30ms**. **인덱스를 "탄" 건 처음부터 맞았다. 문제는 인덱스를 타고도 500만 번 테이블로 돌아온 것이었다.**

## 실무 적용 포인트

1. **`EXPLAIN`에서 "인덱스를 탔다"에 안심하지 말고 재방문 여부를 봐라**: PostgreSQL `Index Only Scan`(재방문 없음) vs `Index Scan`(있음), MySQL `Using index`(없음) vs 그냥 `Using where`(있음)를 구분한다. 후자면 커버링 여지가 있다.
2. **`SELECT *`를 좁혀라**: 커버링은 "쿼리가 원하는 컬럼 전부가 인덱스에 있을 때"만 성립한다. 필요 없는 컬럼을 긁으면 재방문이 강제된다. 뜨거운 쿼리일수록 컬럼을 명시하라.
3. **`WHERE`용 컬럼은 key, `SELECT`용 컬럼은 `INCLUDE`**: PostgreSQL `INCLUDE`, SQL Server `INCLUDE`를 쓰면 인덱스 키가 불필요하게 커지지 않아 트리 높이·쓰기 비용을 아낀다.
4. **스킵 스캔은 선두 컬럼 카디널리티가 낮을 때만**: `status`(3종), `gender`(2종)처럼 distinct가 적으면 이득. distinct가 수천 넘으면 인덱스를 다시 설계하는 게 낫다. `EXPLAIN`에 `Skip Scan`/`skip scan` 표기 확인.
5. **커버링 인덱스도 공짜가 아니다**: 컬럼을 얹을수록 인덱스가 커지고 `INSERT`/`UPDATE` 시 인덱스 갱신 비용이 는다. 읽기 5% 이득 vs 쓰기 상시 비용을 저울질하라. 모든 쿼리에 커버링을 만들면 쓰기가 죽는다.
6. **결과가 테이블의 큰 비율이면 인덱스 자체를 포기당한다**: 조건이 전체의 20%를 뽑으면 옵티마이저는 재방문 비용 때문에 풀스캔을 고른다([[hash-merge-nested-loop-join]]의 cardinality 판단과 같은 원리). 이땐 커버링으로 재방문을 없애면 인덱스가 다시 채택되기도 한다.

## 더 깊은 토끼굴

- PostgreSQL 공식: [Index-Only Scans and Covering Indexes](https://www.postgresql.org/docs/current/indexes-index-only-scans.html) — visibility map과 재방문 회피 조건, 검증된 1차 자료
- Use The Index, Luke!: [Clustering Data / Covering Index](https://use-the-index-luke.com/sql/clustering/index-only-scan-covering-index) — 커버링 인덱스의 트레이드오프
- [[btree-index-internals]]: 왜 인덱스가 `(키, 위치)`만 갖고 데이터 본체는 따로인지 — 재방문의 근본 원인
- [[innodb-buffer-pool]]: 랜덤 재방문이 버퍼풀을 어떻게 오염시키나
- [[hash-merge-nested-loop-join]]: 커버링 인덱스로 조인 후 재방문(랜덤 I/O)까지 없애는 이야기
