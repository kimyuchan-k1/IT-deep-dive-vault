---
title: autovacuum은 매시간 돌았는데 테이블은 3배가 됐다
date: 2026-08-06
day: 60
category: db
tags: [postgresql, vacuum, mvcc, bloat, autovacuum, xmin-horizon]
related: ["[[mvcc-how]]", "[[btree-index-internals]]", "[[phantom-read-isolation]]", "[[lsm-tree-rocksdb]]", "[[wal-pitr]]"]
difficulty: 4
short_text: |
  🔥 [Day 60] VACUUM 돌려도 디스크는 그대로

  오해: VACUUM=공간 반환
  실제: 트랜잭션 1개가 xmin 고정→삭제 불가

  "리포트 커넥션 6시간"

  📖 https://github.com/kimyuchan-k1/IT-deep-dive-vault/blob/main/posts/2026-08-06-postgres-vacuum-bloat.md
---

# autovacuum은 매시간 돌았는데 테이블은 3배가 됐다

로그에는 `automatic vacuum of table "public.orders"`가 시간마다 찍혀 있었다. 실패한 적도, 건너뛴 적도 없다. 그 시간 동안 테이블은 62GB에서 145GB가 됐다.

## 흔한 오해

> "dead tuple이야 VACUUM이 치우는 거고, autovacuum은 기본으로 켜져 있잖아. 그럼 신경 쓸 게 없는 거 아냐?"

공식 문서가 "autovacuum은 기본 활성화되어 있고 대개 별도 조치가 필요 없다"고 말하니 자연스러운 통념이고, 열에 아홉은 맞다.

틀린 건 **VACUUM이 무엇을 지울지 VACUUM이 정한다는 가정**이다. 범위를 정하는 건 데이터베이스에서 **가장 오래된 스냅숏**이다. VACUUM은 그 경계 바깥만 건드릴 수 있고, 경계가 안 움직이면 매시간 돌면서 0건을 지운다.

## 실제 원리

### UPDATE는 갱신이 아니라 복사다

PostgreSQL의 MVCC에서 각 행 버전은 자기를 만든 트랜잭션 ID(`xmin`)와 자기를 죽인 ID(`xmax`)를 헤더에 달고 있다. `UPDATE`는 기존 버전을 고치지 않는다. **새 버전을 힙에 추가하고 옛 버전의 `xmax`에 자기 XID를 적는다.** `DELETE`는 `xmax`만 찍는다.

그래서 8천만 행 테이블에 초당 1,200번 상태 변경이 일어나면 하루에 1억 개의 죽은 행 버전이 물리적으로 쌓인다. 논리적 행 수는 8천만 그대로다. 이건 낭비가 아니라 설계다 — 읽는 쪽이 락 없이 과거를 볼 수 있는 근거가 이 사본이다. [[mvcc-how]]가 락을 줄이는 원리 그대로다.

### VACUUM은 공간을 OS에 돌려주지 않는다

VACUUM이 dead tuple을 정리하면 그 자리는 **Free Space Map에 등록돼 다음 INSERT/UPDATE가 재사용**할 수 있게 된다. 파일 크기는 줄지 않는다. 예외는 파일 **끝쪽**이 통째로 비었을 때의 truncate뿐이다.

정상 상태의 테이블은 dead tuple이 생기는 속도와 재사용되는 속도가 균형을 이루면서 크기가 고원에 머문다. 디스크가 안 줄었다는 사실 자체는 신호가 아니다. **고원이 계속 올라가는 것**이 신호다.

### 삭제 가능 여부를 정하는 건 xmin horizon

죽은 행 버전이라도 그걸 볼 수 있는 스냅숏이 하나라도 남아 있으면 지울 수 없다. VACUUM은 시작할 때 그 경계값을 계산하고, 경계를 끌어내리는 건 넷뿐이다. 실행 중인 가장 오래된 트랜잭션의 `xmin`, 복제 슬롯의 `xmin`/`catalog_xmin`, `hot_standby_feedback = on`인 스탠바이가 올려보낸 xmin, `PREPARE TRANSACTION`으로 매달린 2PC 트랜잭션.

여기가 핵심이다. `BEGIN` 후 쿼리 한 줄 던지고 커밋도 롤백도 안 한 채 `idle in transaction`으로 앉아 있는 세션 하나면 경계가 고정된다. **아무 일도 하지 않는 트랜잭션이 가장 강하게 붙잡는다.**

그동안 autovacuum은 실패하지 않고 정상 종료한다. `VACUUM (VERBOSE)`을 걸어야 진실이 보인다.

```
tuples: 0 removed, 26431905 remain, 26,398,112 are dead but not yet removable
oldest xmin: 894417213
```

`0 removed`와 `dead but not yet removable`. 이 두 숫자를 안 보면 "autovacuum 돌고 있음"과 "autovacuum이 아무것도 못 하고 있음"은 로그에서 구분되지 않는다.

### 기본 임계값은 큰 테이블에서 늦게 발동한다

autovacuum이 테이블을 집는 조건은 `dead tuple 수 > autovacuum_vacuum_threshold + autovacuum_vacuum_scale_factor × 행 수`다. 기본값은 각각 `50`과 `0.2`. 1억 행 테이블이면 **dead tuple이 2천만 개 쌓여야 처음 발동한다.**

속도에도 브레이크가 있다. `autovacuum_vacuum_cost_delay` 2ms에 cost limit 200이면 초당 10만 cost, 페이지를 더럽히는 비용이 페이지당 20이니 **초당 5,000페이지 = 약 40MB**다. NVMe가 초당 수 GB를 쓰는 동안 VACUUM은 40MB/s로 기어간다. 회전 디스크 시절에 정해진 값이다.

### 인덱스는 별도로 부푼다

힙의 dead tuple을 지우려면 그 행을 가리키는 인덱스 엔트리를 먼저 지워야 한다. 그래서 VACUUM은 인덱스를 전부 스캔하고, 인덱스가 6개면 비용도 6배다. 빈 B+Tree 페이지는 FSM에 들어가도 같은 키 범위로만 재사용돼 힙보다 훨씬 잘 안 줄어든다. [[btree-index-internals]]의 팬아웃이 실전에서 무너지는 방식이다 — 높이는 그대로인데 페이지당 유효 엔트리가 절반이면 같은 탐색에 읽는 페이지가 두 배다.

빠져나갈 길은 **HOT(Heap-Only Tuple) 업데이트** 하나뿐이다. 바뀐 컬럼에 인덱스가 없고 새 버전이 **같은 페이지 안에** 들어갈 자리가 있으면 인덱스를 건드리지 않고 페이지 내부 포인터로 연결한다.

## 현장 시나리오

B2B 물류 SaaS의 `orders` 테이블. 8천만 행, 힙 62GB, 인덱스 6개 합쳐 30GB. 주문 상태가 초당 1,200번 바뀌는데 `status`에 인덱스가 걸려 있어 HOT은 애초에 불가능했고 `fillfactor`도 기본 100이었다. 그래도 2년간 크기는 65GB 근처에서 평평했다.

깨진 건 분석팀이 야간 리포트 배치를 업무 시간으로 옮긴 날이다. 배치는 `BEGIN` 후 `REPEATABLE READ`로 집계 쿼리 12개를 돌렸고, 11번째에서 애플리케이션이 예외를 먹고 커넥션을 풀에 반납했다. 트랜잭션은 커밋도 롤백도 되지 않은 채 남았다. `idle in transaction` 6시간.

인과는 이랬다. 트랜잭션이 열린 채 유지된다 → oldest xmin이 894417213에 고정된다 → autovacuum은 시간마다 정상 실행되고 매번 0건을 지운다 → `n_dead_tup`이 2,600만까지 쌓인다 → 재사용할 공간이 없으니 새 행 버전마다 페이지가 새로 붙어 힙 145GB, 인덱스 71GB가 된다 → `shared_buffers` 32GB로 덮이던 워킹셋이 안 들어가 버퍼 히트율이 99.4%에서 91%로 떨어진다 → visibility map도 갱신되지 않아 index-only scan이 전부 heap fetch로 되돌아간다 → 주문 조회 p99가 40ms에서 900ms가 된다.

먼저 울린 알람은 쿼리 지연이 아니라 디스크 사용률 85%였다. 트랜잭션을 `pg_terminate_backend`로 끊자 다음 autovacuum이 2,600만 건을 정리했고 `n_dead_tup`은 0이 됐다. **그리고 디스크는 1바이트도 줄지 않았다.** 145GB 파일 안에 구멍 83GB가 생겼을 뿐이고, 실제로 되찾은 건 `pg_repack`을 돌린 다음 새벽이었다 — 145GB → 63GB.

이 사고에서 VACUUM은 한 번도 실패하지 않았다. 6시간 동안 아무 일도 하지 않던 트랜잭션 하나가 8천만 행의 과거 전부를 붙잡고 있었을 뿐이다.

## 실무 적용 포인트

1. **`n_dead_tup`보다 xmin horizon을 먼저 봐라.** dead tuple이 쌓이는 건 결과고, horizon 고정이 원인이다. 확인할 곳은 셋뿐이다.
   ```sql
   SELECT pid, state, age(backend_xmin) FROM pg_stat_activity
    WHERE backend_xmin IS NOT NULL ORDER BY 3 DESC LIMIT 5;
   SELECT slot_name, xmin, catalog_xmin FROM pg_replication_slots;
   SELECT gid, prepared FROM pg_prepared_xacts;
   ```
   그리고 `idle_in_transaction_session_timeout = '5min'`을 걸어라. 이 사고는 이 한 줄로 6시간이 5분이 된다.

2. **큰 테이블은 scale_factor를 테이블 단위로 낮춰라.** 전역 0.2는 100만 행짜리 기준이다. `ALTER TABLE orders SET (autovacuum_vacuum_scale_factor = 0.01, autovacuum_vacuum_threshold = 10000)`이면 1억 행에서 발동 시점이 dead tuple 2천만 개에서 100만 개로 내려온다. 자주 짧게 도는 쪽이 가끔 오래 도는 쪽보다 항상 싸다.

3. **속도 제한을 SSD 기준으로 풀어라.** `vacuum_cost_limit` 200 → 1000~2000이면 처리량도 그만큼 배가 된다. `autovacuum_max_workers`는 기본 3이라 큰 테이블 몇 개가 워커를 다 물면 나머지가 굶는다. `autovacuum_work_mem`은 1GB 이상 — 단 **PG 17 미만은 dead tuple 배열이 1GB 상한**(TID 6바이트 × 약 1억 7천만 개)이라 넘으면 인덱스를 여러 번 스캔한다.

4. **`VACUUM FULL`은 운영 중에 쓰지 마라.** `ACCESS EXCLUSIVE`로 읽기까지 막고, 통째로 새로 쓰므로 원본 크기만큼 여유 디스크를 더 먹는다. 온라인 대안은 `pg_repack` — 짧은 락 두 번만 잡고 그 사이 변경분은 트리거로 따라잡는다.

5. **HOT이 되도록 스키마를 설계해라.** 초당 수백 번 바뀌는 컬럼에 인덱스를 걸지 않는 게 첫째, `ALTER TABLE orders SET (fillfactor = 90)`이 둘째다. fillfactor는 이후 쓰이는 페이지부터 적용되므로 기존 테이블은 repack을 한 번 해야 효과가 난다.

6. **wraparound도 같이 봐라.** bloat 실측은 `pgstattuple_approx('orders')`, XID 소진은 `age(datfrozenxid)`다. `autovacuum_freeze_max_age` 기본이 2억이니 1억에서 알람을 걸어라. wraparound 방지용 autovacuum은 **다른 세션의 락 요청에 자리를 비켜주지 않는다.**

## 더 깊은 토끼굴

- [[mvcc-how]] — 이 사본들이 애초에 왜 필요한지
- [[btree-index-internals]] — 인덱스가 부풀면 팬아웃이 무너지는 방식
- [[phantom-read-isolation]] — `REPEATABLE READ`가 스냅숏을 트랜잭션 내내 붙잡는 이유
- [[lsm-tree-rocksdb]] — 같은 문제를 compaction으로 푸는 반대편 설계
- [[wal-pitr]] — 복제 슬롯이 horizon과 WAL을 동시에 붙잡는 지점

**출처**

- PostgreSQL Documentation, *Routine Vacuuming* — https://www.postgresql.org/docs/current/routine-vacuuming.html
- PostgreSQL Documentation, *Heap-Only Tuples (HOT)* — https://www.postgresql.org/docs/current/storage-hot.html
- PostgreSQL Documentation, *Automatic Vacuuming 파라미터* — https://www.postgresql.org/docs/current/runtime-config-autovacuum.html
- pg_repack — https://reorg.github.io/pg_repack/

정리하면, bloat는 VACUUM이 게을러서 생기지 않는다. **지워도 된다고 허락하는 주체가 따로 있고**, 그건 지금 이 순간 가장 오래 열려 있는 트랜잭션이다. 대시보드에 `n_dead_tup`은 있는데 `max(age(backend_xmin))`이 없다면 원인이 아니라 결과만 보고 있는 것이다.
