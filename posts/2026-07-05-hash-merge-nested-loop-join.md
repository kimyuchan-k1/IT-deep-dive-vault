---
title: 같은 JOIN인데 왜 어떤 건 0.1초, 어떤 건 40분 걸리나
date: 2026-07-05
day: 32
category: db
tags: [database, join, query-planner, nested-loop, hash-join]
related: ["[[btree-index-internals]]", "[[index-skip-scan-covering]]", "[[connection-pool-sizing]]", "[[mvcc-how]]"]
difficulty: 3
short_text: |
  🧠 [Day 32] JOIN 하나가 0.1초 vs 40분
  NL O(N×M) vs Hash O(N+M)
  "통계 10배 오차→Hash가 NL로 뒤집혀 40분"
  📖 https://github.com/kimyuchan-k1/IT-deep-dive-vault/blob/main/posts/2026-07-05-hash-merge-nested-loop-join.md
---

# 같은 JOIN인데 왜 어떤 건 0.1초, 어떤 건 40분 걸리나

## 흔한 오해

"`JOIN`은 그냥 `JOIN` 아닌가? 두 테이블 붙이는 거고, 인덱스만 걸려 있으면 빠르겠지. 느리면 옵티마이저가 알아서 최적화할 테고."

대부분 그렇게 안다. SQL은 "무엇을" 원하는지만 쓰고 "어떻게" 붙일지는 DB가 정하니, `JOIN`을 하나의 연산으로 뭉뚱그려 생각한다. 그래서 같은 쿼리가 데이터 양이 조금 늘었다고 100배 느려지면 "DB가 갑자기 왜 이러지" 하고 당황한다.

**틀린 건 아닌데, `JOIN`이라는 단어 하나에 완전히 다른 세 가지 알고리즘이 숨어 있다는 걸 빼먹었다.** 옵티마이저는 매 쿼리마다 Nested Loop / Hash / Sort-Merge 중 하나를 고른다. 그리고 이 선택이 틀리면 0.1초짜리가 40분짜리가 된다. 옵티마이저가 "알아서" 하는 건 맞지만, 그 판단의 재료인 **통계가 틀리면 알아서 지옥으로 간다.**

## 실제 원리

세 알고리즘은 각각 잘 드는 상황이 정반대다.

### ① Nested Loop Join — 작은 쪽 × 인덱스

가장 단순하다. 바깥 테이블(outer)을 한 행씩 돌면서, 각 행마다 안쪽 테이블(inner)을 찾는다.

```
for each row R in outer:
    inner에서 R.key와 매칭되는 행 탐색
```

핵심은 **inner 탐색이 인덱스를 타느냐**다. inner에 인덱스가 있으면 한 번 탐색이 [[btree-index-internals]]의 B+Tree 높이만큼, 즉 3~4번 I/O로 끝난다. 그러면 전체 비용은 대략 **O(N × log M)** (N = outer 행 수, M = inner 행 수).

outer가 작을 때(수십~수천 행) 압도적으로 빠르다. 반대로 outer가 크거나 inner에 인덱스가 없으면, inner를 매번 풀스캔하며 **O(N × M)**로 폭발한다. 50만 × 20만이면 1,000억 번이다.

### ② Hash Join — 큰 테이블 두 개, 등호 조인

여기가 대용량의 주력이다. 두 단계로 동작한다.

1. **Build**: 작은 쪽 테이블을 읽어 조인 키로 **해시 테이블을 메모리에 만든다.**
2. **Probe**: 큰 쪽 테이블을 한 행씩 읽으며 해시 테이블에서 O(1)로 매칭을 찾는다.

각 테이블을 딱 한 번씩만 읽으므로 비용은 **O(N + M)**. 인덱스가 아예 없어도 된다. 50만 + 20만 = 70만 번이면 끝난다 — Nested Loop의 1,000억과 비교해보라.

단, 대가가 있다. build 쪽이 메모리(`work_mem`/`hash_mem`)에 안 들어가면 디스크로 spill(batch 분할)되면서 급격히 느려진다. 그리고 **등호 조인(`=`)에만 쓸 수 있다.** 해시는 순서가 없어 `>`, `<`, `BETWEEN` 조인엔 못 쓴다.

### ③ Sort-Merge Join — 둘 다 정렬돼 있을 때

양쪽을 조인 키로 **정렬한 뒤 지퍼처럼 한 번에 맞물려 훑는다.** 이미 인덱스로 정렬돼 있으면 정렬 비용이 공짜라 매우 빠르고, 아니면 정렬 비용 **O(N log N + M log M)**가 붙는다. Hash와 달리 부등호 조인·범위 조인에도 쓸 수 있는 게 강점이다.

### 그래서 옵티마이저는 무엇으로 고르나

세 알고리즘의 비용 공식에 들어가는 변수는 결국 **"몇 행이 나오느냐(cardinality 추정치)"**다. 옵티마이저는 테이블 통계(히스토그램, distinct 값 수)를 보고 "이 조건이면 대략 N행"이라 추정하고 가장 싼 알고리즘을 고른다. **이 추정이 실제와 어긋나는 순간, 잘못된 알고리즘을 확신에 차서 선택한다.** 통계가 오래됐거나, 컬럼 간 상관관계를 모르거나, 함수로 감싼 조건이면 추정이 틀어진다.

## 현장 시나리오

정산 배치가 어느 날 갑자기 **40분** 넘게 돌다 타임아웃 났다. 어제까진 8초였다. 쿼리는 그대로였다.

원인 사슬은 이랬다:

- 문제 쿼리는 `orders`(50만 행) ⋈ `order_items`(2,000만 행) 조인이었다
- 평소 옵티마이저는 이걸 **Hash Join**으로 풀었다 — `orders`로 해시 빌드, `order_items` probe, 몇 초
- 그런데 전날 밤 대량 배치가 `orders`에 500만 행을 밀어넣었고, **`ANALYZE`가 안 돌아 통계는 여전히 "50만 행"**으로 남아 있었다
- 옵티마이저는 outer가 작다고 착각하고 **Nested Loop**로 계획을 바꿨다
- 실제로는 550만 × 2,000만 → inner 인덱스가 있어도 550만 번의 B+Tree 탐색 → 랜덤 I/O 폭주 → 버퍼풀 [[btree-index-internals]] 휩쓸림 → 40분

수정은 두 줄이었다. 먼저 통계를 갱신하고:

```sql
ANALYZE orders;
```

`EXPLAIN`으로 계획이 다시 Hash Join으로 돌아온 걸 확인했다. 8초로 복귀. **인덱스도 쿼리도 그대로였고, 바뀐 건 옵티마이저가 본 "숫자" 하나였다.** 행 추정이 10배 틀리자 알고리즘 선택이 통째로 뒤집혔다.

## 실무 적용 포인트

1. **`EXPLAIN ANALYZE`로 실제 vs 추정 행을 대조하라**: `rows=50000`(추정)인데 `actual rows=5000000`이면 통계가 썩은 것. 이 괴리가 100배 넘으면 알고리즘 선택을 의심한다. PostgreSQL은 `Nested Loop`/`Hash Join`/`Merge Join`이 노드 이름으로 그대로 찍힌다.
2. **대량 적재·삭제 후 `ANALYZE`를 명시적으로 돌려라**: autovacuum/auto-analyze는 임계치(기본 변경 행 10% + 50)를 넘어야 트리거된다. 배치 직후엔 수동 `ANALYZE table`이 안전하다.
3. **`work_mem`(hash_mem)을 조인 규모에 맞춰라**: Hash Join build가 메모리를 넘으면 디스크 spill로 느려진다. `EXPLAIN ANALYZE`에 `Batches: 8`처럼 1보다 크게 찍히면 메모리 부족 신호. 세션 단위로 `SET work_mem = '256MB'` 조정.
4. **작은 결과 → 큰 테이블은 Nested Loop + inner 인덱스가 정답**: OLTP의 "특정 유저의 주문 목록"처럼 outer가 수십 행이면 Hash보다 NL이 빠르다. 이때 **inner 조인 키에 인덱스가 반드시 있어야** 한다. 없으면 재앙.
5. **옵티마이저를 못 믿겠으면 힌트로 고정 (최후의 수단)**: Oracle `USE_HASH`, MySQL `HASH_JOIN`, PostgreSQL `SET enable_nestloop = off`(세션). 단 힌트는 데이터 분포가 바뀌면 독이 되니, 근본 원인(통계·인덱스)을 먼저 고친다.
6. **상관관계 있는 컬럼은 확장 통계로**: `WHERE city='서울' AND zipcode='06000'`처럼 두 조건이 사실상 같은 정보면 옵티마이저는 독립으로 가정해 행을 과소추정한다. PostgreSQL `CREATE STATISTICS`로 다변량 통계를 만들면 추정이 정확해진다.

## 더 깊은 토끼굴

- PostgreSQL 공식: [Join Methods (Planner/Optimizer)](https://www.postgresql.org/docs/current/planner-optimizer.html) — 세 조인 방식과 비용 모델
- Use The Index, Luke!: [Nested Loops](https://use-the-index-luke.com/sql/join/nested-loops-join) — 조인별 인덱스 전략, 검증된 1차 자료
- [[btree-index-internals]]: Nested Loop의 inner 탐색이 왜 3~4 I/O인지 — B+Tree 높이 이야기
- [[index-skip-scan-covering]]: 커버링 인덱스로 조인 후 테이블 재방문(랜덤 I/O) 없애기
- [[connection-pool-sizing]]: 조인 하나가 40분 잡으면 커넥션 풀이 어떻게 마르는지
