---
title: 버퍼풀을 메모리 80%로 키웠더니 DB가 더 느려졌다
date: 2026-06-04
day: 5
category: db
tags: [mysql, innodb, buffer-pool, memory-tuning, lru]
related: ["[[btree-index-internals]]", "[[connection-pool-sizing]]", "[[mvcc-how]]", "[[lsm-tree-rocksdb]]"]
difficulty: 4
short_text: |
  🔥 [Day 5] 버퍼풀 키울수록 빠르다는 착각
  오해: 클수록 캐시히트 ↑
  실제: 커넥션버퍼+OS 무시→RSS>RAM→스왑→히트가 디스크I/O로
  "p99 5ms→800ms"
  📖 https://github.com/kimyuchan-k1/IT-deep-dive-vault/blob/main/posts/2026-06-04-innodb-buffer-pool.md
---

# 버퍼풀을 메모리 80%로 키웠더니 DB가 더 느려졌다

## 흔한 오해

"InnoDB 버퍼풀은 데이터를 메모리에 캐싱하는 곳이니까, 클수록 캐시 히트율이 올라가서 빨라진다. 그러니 물리 메모리의 70~80%를 주면 된다."

거의 모든 튜닝 글이 그렇게 가르친다. `innodb_buffer_pool_size`를 RAM의 80%로 잡으라는 조언은 사실상 업계 밈이 됐다. 그래서 32GB 서버면 25GB쯤 떼어주고 "이제 디스크 안 타니까 빠르겠지"라고 안심한다.

**절반만 맞다.** 버퍼풀이 워킹셋을 담을 만큼 커야 한다는 건 맞다. 하지만 "메모리의 80%"라는 숫자는 두 가지를 빼먹었다. 첫째, MySQL은 버퍼풀 말고도 **커넥션마다 따로 메모리를 더 쓴다.** 둘째, 버퍼풀이 크다고 공짜가 아니다 — 내부 LRU 관리와 더티 페이지 flush 비용이 같이 커진다. 80%가 임계를 넘는 순간 DB는 더 빨라지는 게 아니라 **스왑에 빠져 죽는다.**

## 실제 원리

### 버퍼풀은 단순 캐시가 아니라 관리되는 자료구조다

버퍼풀은 16KB 페이지들의 풀이고, 그 안에서 세 개의 리스트로 관리된다. **Free list**(빈 페이지), **LRU list**(데이터가 올라온 페이지), **Flush list**(수정됐지만 아직 디스크에 안 쓴 더티 페이지). 핵심은 LRU list가 그냥 "오래된 것부터 버리는" 단순 LRU가 아니라는 점이다.

InnoDB의 LRU는 **young 서브리스트(앞 5/8)와 old 서브리스트(뒤 3/8)로 쪼개져** 있다. 새로 읽은 페이지는 리스트 맨 앞이 아니라 **둘의 경계(midpoint)에 끼워 넣는다.** 이걸 midpoint insertion이라 부르고, 경계 위치는 `innodb_old_blocks_pct`(기본 37)로 정한다.

왜 이렇게 하나? **풀스캔이나 read-ahead가 한 번 훑고 지나가는 페이지가 핫 데이터를 밀어내지 못하게** 하려는 거다. old 영역에 들어온 페이지는 `innodb_old_blocks_time`(기본 1000ms) 안에 다시 접근되지 않으면 young으로 승격되지 못하고 그대로 밀려 나간다. [[btree-index-internals]]에서 본 "풀스캔이 버퍼풀을 휩쓴다"는 시나리오를 InnoDB는 이 구조로 일부 방어한다.

### 버퍼풀 바깥의 메모리 — 여기가 함정

버퍼풀은 한 번 잡으면 끝인 **전역(global) 메모리**다. 문제는 MySQL이 쓰는 메모리가 그게 다가 아니라는 것. 쿼리마다 정렬·조인·임시 결과를 위해 **커넥션(스레드)별로 따로** `sort_buffer_size`, `join_buffer_size`, `read_rnd_buffer_size`, `tmp_table_size` 등을 할당한다.

여기가 핵심이다. 이 per-connection 버퍼는 **동시 커넥션 수만큼 곱해진다.** `sort_buffer_size`가 4MB인데 정렬 쿼리를 도는 커넥션이 500개면 순간적으로 2GB가 버퍼풀 **바깥에서** 추가로 잡힐 수 있다. 즉 MySQL 프로세스의 실제 메모리 사용량(RSS)은:

```
RSS ≈ 버퍼풀 + (per-connection 버퍼 합 × 활성 커넥션 수) + redo/log 버퍼 + 코드/스택
```

버퍼풀을 RAM의 80%로 잡으면, 커넥션이 몰리는 순간 이 합계가 **물리 메모리를 초과**한다. 그러면 OS가 버퍼풀의 일부 페이지를 디스크 스왑 영역으로 내쫓는다. 결과는 잔혹하다 — 버퍼풀에 "있다"고 믿었던 페이지가 사실은 스왑에 있어서, **캐시 히트가 곧 디스크 읽기**가 된다. 캐시의 존재 이유가 통째로 무너진다.

### 큰 버퍼풀의 또 다른 비용

버퍼풀이 크면 더티 페이지도 더 많이 쌓인다. InnoDB는 이걸 page cleaner 스레드로 백그라운드 flush하는데, 한 번에 너무 많이 쌓이면(checkpoint age가 redo 로그 한계에 가까워지면) **furious flushing**에 들어가 쓰기 폭주가 일어난다. `innodb_io_capacity`를 스토리지 실제 IOPS와 안 맞춰두면 이 구간에서 쓰기 지연이 튄다.

## 현장 시나리오

32GB RAM의 MySQL 서버. DBA가 "캐시 히트율을 올리자"며 `innodb_buffer_pool_size`를 12GB에서 **26GB(약 80%)**로 올렸다. 며칠은 멀쩡했다. 평소 커넥션이 50~80개라 per-connection 버퍼 합이 작았기 때문이다.

문제는 월말 정산 배치가 도는 밤이었다. 인과 사슬은 이랬다:

- 정산 배치 + 평소 트래픽이 겹치며 활성 커넥션이 **순간 500개 이상**으로 치솟음
- 각 커넥션이 정렬·조인 쿼리를 돌며 per-connection 버퍼를 할당 → 버퍼풀 바깥 메모리가 수 GB 추가로 점유
- 프로세스 RSS가 26GB 버퍼풀 + α로 **물리 32GB를 초과** → Linux가 스왑 시작
- 스왑된 버퍼풀 페이지를 다시 읽을 때마다 디스크 I/O 발생 → 메모리에 "있던" 데이터가 사실상 콜드
- 쿼리 p99 지연이 **5ms → 800ms**로 폭증 → 쿼리가 안 끝나니 커넥션이 반환 안 됨 → `max_connections` 한계 도달 → 앱에서 "Too many connections" 에러

원인은 26GB 버퍼풀 자체가 아니라, **버퍼풀 바깥에서 커넥션당 추가로 쓰는 메모리를 계산에 안 넣은 것**이었다. 수정은 버퍼풀을 **20GB로 낮추고** `max_connections`를 현실적으로 제한한 것. 캐시 히트율은 99.9%에서 99.7%로 약간 떨어졌지만, p99는 다시 5ms로 돌아왔다. **버퍼풀은 크게가 아니라 "스왑이 안 일어나는 가장 큰 값"으로 잡는 거였다.**

## 실무 적용 포인트

1. **버퍼풀 크기는 80%가 아니라 역산으로 정한다**: `RAM − OS여유(1~2GB) − (per-connection버퍼 합 × 예상 최대 활성 커넥션) − redo/log버퍼`. 전용 DB 서버라도 보통 **50~75%** 선이 안전하다. 커넥션 수를 [[connection-pool-sizing]]로 통제하면 더 크게 잡을 수 있다.
2. **스왑을 끄거나 거의 안 쓰게**: 리눅스 `vm.swappiness=1`로 두고, 가능하면 `innodb_buffer_pool_size`를 스왑이 절대 안 날 값으로 잡는다. 스왑 인 발생 시 모든 "캐시 히트"가 디스크 읽기가 된다.
3. **히트율을 직접 본다**: `SHOW ENGINE INNODB STATUS`의 BUFFER POOL 섹션에서 `Buffer pool hit rate 1000 / 1000`이 목표(=99.9%+). `Free buffers`가 0에 가깝고 evict가 잦으면 워킹셋이 안 들어가는 것. 단순히 키우기 전에 무엇이 evict되는지부터 본다.
4. **풀스캔 오염 방어**: 배치·분석 쿼리가 핫 페이지를 밀어낸다면 `innodb_old_blocks_pct`(기본 37)와 `innodb_old_blocks_time`(기본 1000ms)을 유지·상향해 old 서브리스트 체류 시간을 늘린다.
5. **버퍼풀 인스턴스로 mutex 경합 완화**: 1GB 넘는 버퍼풀은 `innodb_buffer_pool_instances`로 쪼개 인스턴스당 ≥1GB가 되게 한다(`innodb_buffer_pool_chunk_size` 기본 128MB 배수로 정렬됨). 고동시성에서 LRU mutex 경합이 준다.
6. **재시작 콜드 스타트 방지**: `innodb_buffer_pool_dump_at_shutdown=ON` + `innodb_buffer_pool_load_at_startup=ON`으로 LRU 상태를 덤프/복원한다. 안 그러면 재시작 후 버퍼풀이 빌 때까지 한동안 모든 쿼리가 디스크를 탄다.

## 더 깊은 토끼굴

- MySQL 공식: [InnoDB Buffer Pool](https://dev.mysql.com/doc/refman/8.0/en/innodb-buffer-pool.html) — 구성과 사이징 가이드
- MySQL 공식: [Buffer Pool LRU Algorithm](https://dev.mysql.com/doc/refman/8.0/en/innodb-buffer-pool.html#innodb-buffer-pool-lru) — midpoint insertion과 old/young 서브리스트
- [[btree-index-internals]]: 풀스캔이 버퍼풀을 휩쓰는 그 시나리오 — InnoDB가 LRU로 방어하는 대상
- [[connection-pool-sizing]]: 활성 커넥션을 통제해야 per-connection 메모리가 폭주하지 않는다
- [[mvcc-how]]: 버퍼풀 안 페이지도 undo 버전을 들고 있다 — 메모리 압박의 또 다른 축
- [[lsm-tree-rocksdb]]: 쓰기 폭주를 메모리 버퍼(memtable)로 흡수하는 정반대 설계
