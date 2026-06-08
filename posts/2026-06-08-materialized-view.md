---
title: 대시보드를 Materialized View로 바꿨더니 빨라졌는데 숫자가 어제 것이었다
date: 2026-06-08
day: 9
category: db
tags: [postgresql, materialized-view, cache, refresh, locking]
related: ["[[btree-index-internals]]", "[[mvcc-how]]", "[[innodb-buffer-pool]]", "[[cache-aside-vs-write-through]]", "[[replication-lag]]", "[[cdc-debezium]]"]
difficulty: 3
short_text: |
  💡 [Day 9] Materialized View는 자동 갱신이 아님
  오해: 뷰니까 항상 최신
  실제: REFRESH 전까진 스냅샷→갱신은 풀 재계산
  "빨라졌는데 숫자가 어제 것"
  📖 https://github.com/kimyuchan-k1/IT-deep-dive-vault/blob/main/posts/2026-06-08-materialized-view.md
---

# 대시보드를 Materialized View로 바꿨더니 빨라졌는데 숫자가 어제 것이었다

## 흔한 오해

"Materialized View(MV)는 결국 뷰의 캐시 버전 아닌가? 일반 뷰는 매번 쿼리를 다시 도니까 느리고, MV는 결과를 저장해두니까 빠르고. 그러니 무거운 집계 쿼리를 MV로 감싸두면 **알아서 최신 상태를 유지하면서** 빨라지겠지."

집계 대시보드가 느릴 때 거의 모두가 먼저 떠올리는 답이다. `CREATE MATERIALIZED VIEW`로 감싸기만 하면 ORM도 그대로 쓰고, 인덱스도 걸 수 있으니 깔끔해 보인다.

**절반만 맞다.** MV가 결과를 물리적으로 저장해 조회를 빠르게 만든다는 건 맞다. 하지만 "알아서 최신"이 틀렸다. MV는 **갱신 시점에 찍은 스냅샷**이고, 누군가 `REFRESH`를 호출하기 전까지는 시간이 멈춰 있다. 그리고 그 `REFRESH`가 공짜가 아니다 — 기본 동작은 **테이블 전체를 잠그고 처음부터 다시 계산한다.** 이 둘을 모르고 도입하면 "빠른데 틀린 데이터"를 얻는다.

## 실제 원리

### 일반 뷰 vs MV: 가상 테이블 vs 굳은 스냅샷

일반 뷰는 **저장된 쿼리 텍스트**일 뿐이다. 조회할 때마다 옵티마이저가 뷰 정의를 펼쳐 원본 테이블에서 매번 다시 계산한다. 그래서 항상 최신이지만, 무거운 집계라면 매 조회가 무겁다.

MV는 정반대다. `CREATE MATERIALIZED VIEW`를 실행하는 순간 쿼리를 한 번 돌려 **그 결과를 실제 디스크 페이지에 테이블처럼 굳혀** 놓는다. 조회는 그냥 저장된 결과를 읽는 거라 빠르고, [[btree-index-internals]]처럼 MV 위에 인덱스도 걸 수 있다. 대신 원본이 바뀌어도 MV는 **모른다.** 여기가 핵심이다 — MV의 데이터는 "마지막 `REFRESH` 시점"에 박제돼 있다.

### REFRESH는 증분이 아니라 풀 재계산이다

PostgreSQL의 `REFRESH MATERIALIZED VIEW`는 **정의 쿼리를 처음부터 전부 다시 실행**한다. 바뀐 행만 골라 반영하는 증분 갱신(incremental refresh)이 아니다. 원본이 1억 행이면 매 갱신이 1억 행 재집계다. 즉 MV는 "한 번 비싸게 만들고 여러 번 싸게 읽는" 구조이지, 실시간 동기화 장치가 아니다.

더 아픈 건 잠금이다. 그냥 `REFRESH MATERIALIZED VIEW mv;`는 대상 MV에 **`ACCESS EXCLUSIVE` 락**을 잡는다. 갱신이 끝날 때까지 **그 MV에 대한 `SELECT`조차 블로킹**된다. 집계가 5분 걸리면 대시보드가 5분간 멈춘다.

이걸 피하라고 `REFRESH MATERIALIZED VIEW CONCURRENTLY mv;`가 있다. 갱신 중에도 읽기를 허용한다. 단 조건과 비용이 있다 — MV에 **`UNIQUE` 인덱스가 반드시 있어야 하고**, 내부적으로 새 결과를 임시로 만든 뒤 기존본과 **diff를 떠서 변경분만 반영**하느라 일반 `REFRESH`보다 **더 느리고 더 많은 I/O**를 쓴다. 빠른 조회를 얻는 대신 갱신은 더 무거워지는 트레이드오프다. [[mvcc-how]]가 그 동안의 읽기 일관성을 받쳐준다.

### 그래서 "캐시 테이블"과 갈리는 지점

캐시 테이블은 그냥 평범한 테이블에 집계 결과를 내가 직접 넣고 관리하는 것이다. 손은 더 가지만 자유롭다 — **바뀐 행만 골라 `UPSERT`**할 수 있고(증분), 트리거나 [[cdc-debezium]]로 원본 변경을 실시간에 가깝게 반영할 수 있고, 부분만 갱신할 수도 있다. MV는 정의 쿼리에 묶여 "전부 아니면 전무"인 갱신만 되지만, 캐시 테이블은 갱신 전략을 내가 설계한다. MV는 **편의(선언 한 줄)**, 캐시 테이블은 **제어(증분·실시간)** 를 준다.

## 현장 시나리오

한 이커머스의 매출 대시보드. 주문 5천만 행을 매장·일자별로 집계하는 쿼리가 매 조회 8초가 걸렸다. 개발자가 이걸 `CREATE MATERIALIZED VIEW sales_daily`로 감쌌다. 조회는 8초 → 40ms로 떨어졌다. 인과 사슬은 이랬다:

- 도입 직후엔 완벽했다. 하지만 MV는 자동 갱신이 안 되므로 **매출 숫자가 도입 시점에 멈춰** 있었다 — 다음 날 보니 어제 데이터
- 부랴부랴 `cron`으로 매시간 `REFRESH MATERIALIZED VIEW sales_daily`를 걸었다. 그런데 이 갱신은 `ACCESS EXCLUSIVE` 락 → **매시간 정각마다 대시보드가 5분간 "로딩 중"으로 멈춤**. 5천만 행 풀 재집계라 갈수록 느려져 7분까지 늘어남
- `CONCURRENTLY`로 바꾸려니 MV에 `UNIQUE` 인덱스가 없어 에러. (매장id, 일자) 복합 유니크 인덱스를 추가
- `CONCURRENTLY`로 멈춤은 사라졌지만, diff 방식이라 갱신이 7분 → **14분**으로 더 길어짐. 그 14분 동안 대시보드는 여전히 **직전 스냅샷(최대 1시간+α stale)** 을 보여줌

결국 팀은 MV를 걷어내고, **주문 INSERT 시 트리거로 `sales_daily` 캐시 테이블을 증분 `UPSERT`**하는 방식으로 바꿨다. 갱신 비용은 주문 한 건당 수 ms로 분산됐고, 대시보드는 거의 실시간이 됐다. 원인은 MV가 나빠서가 아니라, **"항상 최신"을 기대하고 실시간 동기화가 필요한 자리에 스냅샷 도구를 쓴 것**이었다. MV가 맞는 자리는 따로 있었다 — 하루 한 번이면 충분한 야간 리포트.

## 실무 적용 포인트

1. **`CONCURRENTLY`를 쓰려면 `UNIQUE` 인덱스를 먼저 만들어라**: `CREATE UNIQUE INDEX ON mv (key1, key2);`가 없으면 `REFRESH ... CONCURRENTLY`는 에러난다. 일반 `REFRESH`는 `ACCESS EXCLUSIVE` 락으로 조회까지 막으니, 운영 중 갱신은 사실상 `CONCURRENTLY`가 기본 선택.
2. **갱신은 스케줄러로 명시적으로 돌려라**: PostgreSQL은 자동 갱신이 없다. `pg_cron`이나 외부 스케줄러로 `REFRESH`를 걸고, **허용 가능한 stale 시간(예: 1시간)** 을 SLA로 합의한다. "최신처럼 보이지만 1시간 전"임을 팀이 알아야 한다.
3. **실시간이 필요하면 MV가 아니라 캐시 테이블 + 증분 갱신**: 원본 변경을 트리거 `UPSERT`나 [[cdc-debezium]]로 반영한다. MV는 전체 재계산뿐이라 실시간/대용량에 부적합.
4. **MV가 맞는 자리만 골라 써라**: 갱신 주기가 길고(야간 배치, 일/시간 단위 리포트), 원본 대비 결과가 작게 압축되며, stale을 용인할 수 있는 집계. 이때는 선언 한 줄의 편의가 크다.
5. **MV 상태를 모니터링하라**: PostgreSQL `pg_matviews`의 `ispopulated`로 갱신 여부를, 마지막 `REFRESH` 시각을 로깅해 추적한다. `WITH NO DATA`로 만든 MV는 채워지기 전까지 조회 시 에러난다.
6. **갱신 비용을 분산하려면 잘게 쪼개라**: 하나의 거대한 MV보다 파티션/기간별로 나눈 작은 MV 여러 개가 `REFRESH` 단위를 줄여 락·재계산 시간을 분산한다. 버퍼풀/캐시 압박은 [[innodb-buffer-pool]]의 워킹셋 논리와 같은 결.

## 더 깊은 토끼굴

- PostgreSQL 공식: [CREATE MATERIALIZED VIEW](https://www.postgresql.org/docs/current/sql-creatematerializedview.html) — 정의와 `WITH [NO] DATA` 옵션
- PostgreSQL 공식: [REFRESH MATERIALIZED VIEW](https://www.postgresql.org/docs/current/sql-refreshmaterializedview.html) — `CONCURRENTLY`의 `UNIQUE` 인덱스 요구와 락 동작
- [[cache-aside-vs-write-through]]: MV vs 캐시 테이블 = 갱신 책임을 누가 지느냐의 같은 질문
- [[cdc-debezium]]: 원본 변경을 증분으로 흘려 캐시 테이블을 실시간 유지하는 방법
- [[mvcc-how]]: `REFRESH CONCURRENTLY`가 갱신 중에도 일관된 읽기를 주는 토대
- [[replication-lag]]: "보이는 데이터가 얼마나 오래된 것인가"라는 stale 문제의 또 다른 얼굴
