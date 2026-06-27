---
title: AOF를 켰으니 데이터는 안전하다고 믿었는데, 마지막 1초가 사라졌다
date: 2026-06-27
day: 25
category: redis
tags: [redis, persistence, durability, aof, rdb]
related: ["[[redis-bigkey]]", "[[lazy-freeing]]", "[[wal-pitr]]", "[[redis-cluster-slot]]"]
difficulty: 2
short_text: |
  ⚠️ [Day 25] AOF 켰는데 마지막 1초가 사라졌다
  오해: AOF=즉시 디스크 보존
  실제: 버퍼→1초마다 fsync→크래시 때 1초 증발
  everysec가 기본값이었다
  📖 https://github.com/kimyuchan-k1/IT-deep-dive-vault/blob/main/posts/2026-06-27-redis-rdb-vs-aof.md
---

# AOF를 켰으니 데이터는 안전하다고 믿었는데, 마지막 1초가 사라졌다

## 흔한 오해

"RDB는 스냅샷이라 위험하고, AOF는 모든 쓰기를 기록하니까 데이터 손실이 없다."

그래서 입문서들이 그렇게 가르친다. RDB는 "주기적 백업"이라 마지막 스냅샷 이후가 날아가고, AOF는 "모든 명령을 append"하니 안전하다고. 그래서 중요한 데이터엔 AOF를 켜면 끝이라고 생각한다.

**틀린 건 아닌데, 가장 중요한 변수를 빼먹었다.** AOF가 "안전한" 정도는 켜고 끄는 토글이 아니라 `appendfsync` 정책이 결정한다. 그리고 기본값은 "즉시 디스크"가 아니다.

## 실제 원리

### AOF는 명령을 디스크에 바로 쓰지 않는다

AOF는 쓰기 명령(`SET`, `LPUSH` 등)을 실행 후 텍스트 프로토콜 형태로 기록한다. 여기서 핵심은 **기록이 두 단계**라는 것이다.

1. 명령을 **AOF 버퍼**(메모리)에 쌓는다 — 이건 즉시
2. 버퍼를 OS 페이지 캐시로 `write()` 한 뒤, 디스크로 `fsync()` 한다 — 이게 진짜 영속화

운영체제의 `write()`는 데이터를 페이지 캐시에 넘길 뿐, 디스크 플래터/SSD 셀에 실제로 박는 건 `fsync()`다. 전원이 나가면 페이지 캐시에만 있던 데이터는 증발한다. 그래서 "언제 `fsync` 하느냐"가 durability의 전부다.

### `appendfsync` 3가지 정책

`fsync` 호출 주기를 정하는 게 `appendfsync`다.

- `always`: 매 쓰기 명령마다 `fsync`. 거의 무손실이지만, 모든 쓰기가 디스크 I/O를 기다려 처리량이 수십 분의 일로 떨어진다.
- `everysec`: **1초에 한 번** `fsync` (백그라운드 스레드가). 크래시 시 **최대 1초어치 손실**. Redis의 기본값.
- `no`: Redis는 `fsync`를 호출하지 않고 OS에 맡긴다. 보통 30초 단위. 가장 빠르지만 손실 폭이 가장 크다.

여기가 핵심이다. AOF를 "켰다"는 건 대부분 `everysec`를 켰다는 뜻이고, 이건 무손실이 아니라 **손실 상한이 1초**라는 뜻이다.

### RDB는 손실이 아니라 다른 트레이드오프

RDB는 특정 시점 메모리 전체를 바이너리 스냅샷으로 저장한다. `fork()`로 자식 프로세스를 만들고, copy-on-write로 부모는 계속 서비스하면서 자식이 덤프를 쓴다. 그래서 RDB의 약점은 두 가지다.

- 마지막 스냅샷 이후 변경분 손실 (예: 5분 주기면 최대 5분)
- `fork()` 순간 메모리가 크면 [[redis-bigkey]]처럼 지연 스파이크 발생 (copy-on-write 페이지 복제 비용)

대신 RDB는 **복구가 빠르다**. 바이너리를 메모리에 로드만 하면 끝. AOF는 기록된 명령을 전부 재실행(replay)해야 해서 파일이 크면 부팅이 느리다.

## 현장 시나리오

결제 서비스에서 멱등성 키를 Redis에 저장했다. "중복 결제 막아야 하니까" AOF를 켰고, 팀은 "AOF니까 데이터 손실 없음"으로 문서에 적었다. 아무도 `appendfsync` 값을 확인하지 않았다 — 기본값 `everysec` 그대로.

어느 날 호스트 장비의 전원 유닛이 죽으며 노드가 즉시 다운됐다.

- 마지막 `fsync` 직후 약 0.8초 사이에 들어온 결제 멱등성 키 일부가 AOF 버퍼에만 있었다
- `fsync` 전이라 디스크 AOF엔 없었음 → 재시작 후 복구된 데이터에서 빠짐
- 그 키들이 사라지자 클라이언트 재시도가 **중복 결제로 통과**
- 소수지만 실제 이중 청구 발생 → CS 대응 + 환불

원인 보고서 한 줄: **"everysec가 기본값이었다."** AOF는 켜져 있었다. 다만 아무도 그게 "최대 1초 손실"이라는 뜻인 줄 몰랐을 뿐이다.

## 실무 적용 포인트

1. **`appendfsync` 명시 확인**: `CONFIG GET appendfsync`. 무손실이 필요하면 `always`, 일반적으로는 `everysec` 유지하되 "1초 손실 가능"을 설계에 반영.
2. **돈/멱등성 데이터는 Redis를 SoT로 쓰지 마라**: 1초 손실이 치명적이면 RDBMS([[wal-pitr]] 기반)를 진실의 원천으로 두고 Redis는 캐시/가속층으로.
3. **RDB + AOF 동시 사용**: Redis 7부터 권장 기본. `aof-use-rdb-preamble yes`로 AOF 파일 앞부분을 RDB 바이너리로 두면 복구 속도(RDB) + 최근 변경(AOF) 둘 다 챙긴다.
4. **AOF rewrite 모니터링**: `auto-aof-rewrite-percentage`(기본 100), `auto-aof-rewrite-min-size`(기본 64mb). rewrite도 `fork()`를 쓰므로 [[lazy-freeing]]·메모리 크기와 함께 본다.
5. **복구 시간(RTO) 측정**: AOF가 수 GB면 replay에 분 단위가 걸린다. 정기적으로 실제 재시작 시간을 재서 SLA에 반영.
6. **`always`의 비용 인지**: 디스크가 SSD라도 매 명령 `fsync`는 처리량을 크게 깎는다. 벤치마크 없이 "안전하니까 always"는 금물.

## 더 깊은 토끼굴

- Redis 공식: [Redis persistence](https://redis.io/docs/latest/operate/oss_and_stack/management/persistence/) — RDB/AOF/혼합 모드 공식 설명
- [[wal-pitr]]: DB의 WAL은 Redis AOF와 같은 고민을 어떻게 푸는가 (group commit, fsync 타이밍)
- [[redis-bigkey]]: `fork()` 순간 copy-on-write가 왜 지연을 만드나
- [[lazy-freeing]]: AOF rewrite/스냅샷 시 백그라운드 해제와의 관계
- [[redis-cluster-slot]]: 클러스터에서 각 노드의 영속화는 독립이라는 함정
