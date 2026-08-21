---
title: 쓰기가 빠르다고 RocksDB를 골랐는데, 새벽 3시에 쓰기가 통째로 멈췄다
date: 2026-08-21
day: 74
category: db
tags: [lsm-tree, rocksdb, compaction, write-stall, write-amplification, tombstone]
related: ["[[btree-index-internals]]", "[[wal-pitr]]", "[[backpressure-patterns]]", "[[postgres-vacuum-bloat]]", "[[innodb-buffer-pool]]"]
difficulty: 4
short_text: |
  🔥 [Day 74] 쓰기 빠른 DB가 새벽에 쓰기를 멈췄다

  오해: LSM=쓰기 빠름
  실제: 빠른 게 아니라 외상. compaction 밀림→L0 36개→정지

  📖 https://github.com/kimyuchan-k1/IT-deep-dive-vault/blob/main/posts/2026-08-21-lsm-tree-rocksdb.md
---

# 쓰기가 빠르다고 RocksDB를 골랐는데, 새벽 3시에 쓰기가 통째로 멈췄다

## 흔한 오해

> "LSM Tree는 쓰기가 빠르고 읽기가 느리다. B+Tree는 그 반대고. 그러니까 쓰기가 많으면 LSM, 읽기가 많으면 B+Tree."

어느 비교표에나 이렇게 적혀 있다. 순차 append라 디스크 랜덤 쓰기가 없고, 대신 읽을 때 여러 레벨을 뒤져야 하니 느리다 — 논리도 매끄럽다. 그래서 시계열·로그·이벤트 적재에는 반사적으로 LSM 계열을 고른다.

방향은 맞는데 **비용의 위치를 잘못 짚었다.** LSM은 쓰기를 싸게 만들지 않는다. **쓰기 비용을 나중으로 미룰 뿐이다.** 사용자 요청 경로에서 빠져나간 그 작업은 사라지지 않고 백그라운드 compaction에 외상으로 쌓인다. 그리고 외상은 갚아야 한다. 갚는 속도가 쌓이는 속도를 못 따라가는 순간, 느려지는 건 읽기가 아니라 **쓰기**다.

## 실제 원리

### 쓰기 경로에는 정말로 디스크 탐색이 없다

`Put(key, value)`가 하는 일은 두 가지뿐이다. WAL에 append하고, 메모리의 memtable(기본 skiplist)에 넣는다. 정렬은 메모리에서 일어나고 디스크는 순차 쓰기만 한다. B+Tree가 매 삽입마다 리프 페이지를 찾아 읽고 고쳐 쓰는 것과 대비되는 지점이다. memtable이 `write_buffer_size`(RocksDB 기본 64MB)를 채우면 immutable로 전환되고, 백그라운드 스레드가 정렬된 파일 하나로 떨군다. 이게 L0의 SST 파일이다. 여기까지는 실제로 싸다.

### L0만 규칙이 다르다. 그리고 사고는 항상 L0에서 난다

L1 이하 레벨의 SST들은 **키 범위가 서로 겹치지 않는다.** 한 레벨에서 특정 키가 있을 수 있는 파일은 최대 하나다. L0만 예외다. memtable을 순서대로 떨군 결과물이라 파일마다 키 범위가 통째로 겹친다. 그래서 읽기는 L0의 **모든** 파일을 확인해야 하고, RocksDB는 L0 파일 수에 하드 리밋을 건다.

- `level0_slowdown_writes_trigger` (기본 20): 넘으면 `delayed_write_rate`로 쓰기를 인위적으로 느리게 한다
- `level0_stop_writes_trigger` (기본 36): 넘으면 쓰기를 **완전히 막는다**

이게 write stall이다. 버그가 아니라 설계된 방어 기제다. compaction이 밀리는데 유입을 그대로 받으면 읽기 성능이 무한히 나빠지므로, DB가 스스로 상류를 막는다. [[backpressure-patterns]]가 스토리지 엔진 안에 내장된 형태다.

### 증폭 세 개, 그리고 셋 다는 못 가진다

```
                유입 1GB
                   │
   L0  [파일들 키 범위 겹침] ← flush, 여기까지 1배
   L1  [10GB, 겹침 없음]     ← 병합 재작성
   L2  [100GB]               ← 또 재작성
   L3  [1TB]                 ← 또 재작성
```

leveled compaction은 레벨마다 크기가 fanout 배(기본 10)씩 커진다. 키 하나가 L0에서 최하위까지 내려가려면 각 레벨에서 한 번씩 다시 쓰여야 한다. 그래서 **write amplification은 대략 fanout × 레벨 수**, 실측으로 10~30배가 흔하다. 사용자가 1GB를 넣으면 디스크에는 10~30GB가 쓰인다. "순차 쓰기라 싸다"는 말은 **총량이 적다는 뜻이 아니다.**

read amplification은 반대로 생각만큼 크지 않다. 레벨마다 bloom filter가 붙어 "이 파일에 이 키 없음"을 대부분 즉시 판정한다. 키당 10비트면 false positive가 약 1%고, 없는 키 조회의 실제 디스크 접근은 거의 최하위 레벨 한 번으로 수렴한다. 단 bloom filter는 point lookup에만 듣는다. **range scan은 전 레벨을 다 열어야 한다.**

space amplification은 leveled에서 1.1배 수준이다. 최하위 레벨이 전체의 90%를 차지하고 거기는 중복이 없다. 반대로 tiered(RocksDB의 universal) compaction은 write amp를 크게 줄이는 대신 space amp가 2배까지 간다. 셋 중 둘만 고를 수 있다는 게 RUM conjecture고, compaction 전략 선택은 그 삼각형 위에 점을 찍는 일이다.

### 삭제가 공간을 늘린다

LSM에는 in-place 수정이 없으므로 `Delete`도 append다. "이 키는 죽었다"는 tombstone 레코드를 새로 쓴다. 실제 공간은 그 tombstone이 최하위 레벨까지 내려가 원본과 만나야 회수된다. 그 전까지는 **삭제할수록 데이터가 늘어난다.**

더 아픈 건 스캔이다. 100만 건을 지운 구간을 스캔하면 이터레이터가 tombstone 100만 개를 하나씩 밟고 지나간다. 반환 행은 0개인데 응답은 수 초가 걸린다. Cassandra가 `tombstone_failure_threshold` 기본 10만 개에서 쿼리를 아예 실패시키는 이유가 이것이다.

## 현장 시나리오

IoT 센서 수집 파이프라인. Kafka 컨슈머가 메시지를 받아 RocksDB에 쓰는 구조였고, 평시 유입은 초당 12만 건, 값 크기 평균 400바이트였다. 노드는 NVMe SSD에 16코어, `max_background_jobs`는 기본값 2로 둔 채 반년을 무사히 돌았다. 대시보드에는 p99 write latency와 디스크 사용률만 있었다.

새벽 2시 40분, 펌웨어 자동 업데이트로 40만 대 디바이스가 재부팅됐다. 로컬 버퍼에 쌓아둔 측정값을 한꺼번에 밀어 올리면서 유입이 초당 38만 건으로 뛰었다. 3배다.

memtable flush는 따라갔다. 메모리 쓰기와 순차 파일 쓰기뿐이니까. 문제는 그 뒤였다. compaction 스레드 2개가 L0→L1 병합을 처리하는데, 이 작업은 **압축 해제·병합·재압축이 전부 CPU 작업**이라 NVMe가 아무리 빨라도 소용이 없었다. 디스크 사용률은 31%에서 움직이지 않았고, compaction 스레드만 100%였다.

L0 파일이 쌓이기 시작했다. 2시 51분에 20개를 넘었고, RocksDB가 `delayed_write_rate`로 쓰기를 스로틀하기 시작했다. 컨슈머 스레드가 `Put`에서 블로킹되면서 Kafka 오프셋 커밋이 늦어지고 컨슈머 랙이 올라갔다. 랙이 보이자 당직자가 취한 조치는 **컨슈머 인스턴스 증설**이었다. 유입은 더 늘었다.

2시 58분, L0 파일이 36개를 찍었다. 쓰기가 완전히 멈췄다. 4분 12초 동안 `Put`이 하나도 통과하지 못했다.

에러 로그는 조용했다. 예외가 던져진 게 아니라 스레드가 대기했을 뿐이다. 디스크는 한가했고, CPU는 사용자 요청이 아니라 반년치 미뤄둔 정리 작업을 하고 있었다. 청구서가 하필 그날 새벽에 도착했다.

## 실무 적용 포인트

1. **대시보드에 compaction 부채를 올린다.** 디스크 사용률로는 절대 안 보인다. `rocksdb.num-files-at-level0`, `rocksdb.estimate-pending-compaction-bytes`, 그리고 누적 stall 시간인 `rocksdb.stall-micros` 셋을 본다. L0 파일 수가 slowdown 트리거의 절반(기본값 기준 10개)을 넘어 유지되면 그 시점이 경보 지점이다. 완전 정지 전에 최소 몇 분의 여유가 생긴다.
2. **`max_background_jobs`를 기본값 2로 두지 않는다.** 코어 수의 절반(16코어면 8)에서 시작해 stall 지표를 보며 조정한다. compaction은 IO보다 CPU 병목인 경우가 많다 — 압축 알고리즘이 CPU를 먹기 때문이다. 동시에 `NewGenericRateLimiter`로 compaction IO 상한(예: 200MB/s)을 걸어 전경 읽기의 IO를 통째로 빼앗기지 않게 한다. 단 상한이 너무 낮으면 부채가 안 갚아져 stall로 돌아오므로, 평시 flush 처리량의 10배 이상에서 시작한다.
3. **버퍼를 키워 버스트를 흡수한다.** `write_buffer_size`를 64MB → 256MB, `max_write_buffer_number`를 2 → 4로 올리면 flush 대기로 인한 stall 구간이 사라진다. 대신 memtable이 차지하는 메모리가 `256MB × 4 × CF 수`로 늘어나므로 block cache와의 배분을 함께 계산한다.
4. **읽기 대책은 레벨 수 줄이기가 아니라 bloom filter다.** point lookup이 많으면 키당 10비트(`BloomFilter(10, false)`)를 기본으로 깔고, prefix 스캔이 많으면 `prefix_extractor` + prefix bloom을 붙인다. 레벨 수를 줄이려고 fanout을 키우면 write amp가 그대로 올라간다.
5. **압축은 레벨별로 다르게 준다.** L0~L1은 압축 없음 또는 LZ4로 두어 flush/초기 compaction의 CPU를 아끼고, `bottommost_compression`에만 ZSTD를 건다. 전체의 90%가 최하위 레벨이므로 공간 절감의 대부분은 거기서 나온다.
6. **삭제는 개별 `Delete` 대신 `DeleteRange`를 쓰고, tombstone 회수 기한을 강제한다.** 대량 삭제 뒤 스캔이 느려지면 원인은 거의 tombstone이다. `periodic_compaction_seconds`(예: 30일)나 `ttl`을 설정해 트래픽이 적은 레벨도 주기적으로 재작성되게 만든다. 그렇지 않으면 최하위 레벨의 tombstone은 몇 달이고 남는다.

이 사고에서 고장 난 부품은 없었다. **LSM Tree가 미뤄준 비용을 아무도 장부에 적어두지 않았을 뿐이다.**

## 더 깊은 토끼굴

- [[btree-index-internals]] — 반대편 설계. 매 쓰기마다 갚기 때문에 밀릴 부채가 없다
- [[wal-pitr]] — LSM의 WAL과 같은 물건. memtable이 날아가도 복구되는 근거
- [[backpressure-patterns]] — write stall은 스토리지 엔진에 내장된 백프레셔다
- [[postgres-vacuum-bloat]] — tombstone과 dead tuple, 지연된 공간 회수라는 같은 문제
- [[innodb-buffer-pool]] — 같은 메모리를 memtable과 block cache가 나눠 갖는 구조와 비교
- [[percentile-p99]] — compaction stall은 평균에는 안 잡히고 p99.9에만 나타난다

**1차 출처**

- Patrick O'Neil et al., *The Log-Structured Merge-Tree (LSM-Tree)*, Acta Informatica, 1996: https://www.cs.umb.edu/~poneil/lsmtree.pdf
- RocksDB Wiki, *Write Stalls* — slowdown/stop 트리거 조건: https://github.com/facebook/rocksdb/wiki/Write-Stalls
- RocksDB Wiki, *Leveled Compaction* — fanout과 write amplification: https://github.com/facebook/rocksdb/wiki/Leveled-Compaction
- Manos Athanassoulis et al., *Designing Access Methods: The RUM Conjecture*, EDBT 2016: https://stratos.seas.harvard.edu/files/stratos/files/rum.pdf
- Siying Dong et al., *Optimizing Space Amplification in RocksDB*, CIDR 2017: https://www.cidrdb.org/cidr2017/papers/p82-dong-cidr17.pdf
