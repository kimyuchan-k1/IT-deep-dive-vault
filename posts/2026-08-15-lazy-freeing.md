---
title: UNLINK로 바꿨더니 삭제는 즉시 끝났고, 41초 뒤 프로세스가 죽었다
date: 2026-08-15
day: 69
category: redis
tags: [unlink, lazyfree, del, memory, bio-thread, jemalloc]
related: ["[[redis-bigkey]]", "[[redis-ttl-eviction]]", "[[redis-scan-vs-keys]]", "[[redis-hotkey]]", "[[redis-rdb-vs-aof]]"]
difficulty: 3
short_text: |
  ⚠️ [Day 69] UNLINK 했는데 메모리 안 줄었다
  오해: UNLINK=항상 비동기
  실제: 할당 64개 미만은 동기, 회수는 스레드 1개
  "삭제는 성공, 회수는 아직이었다"
  📖 https://github.com/kimyuchan-k1/IT-deep-dive-vault/blob/main/posts/2026-08-15-lazy-freeing.md
---

# UNLINK로 바꿨더니 삭제는 즉시 끝났고, 41초 뒤 프로세스가 죽었다

## 흔한 오해

> "`DEL`이 큰 키에서 블로킹된다길래 전부 `UNLINK`로 바꿨다. UNLINK는 비동기니까 이제 몇 GB짜리 키를 지워도 안전하다."

[[redis-bigkey]]를 읽고 나면 자연스럽게 나오는 결론이고, Redis 4.0 릴리스 노트도 UNLINK를 "non-blocking DEL"이라고 소개한다. 그래서 팀 위키에는 대개 "DEL 금지, UNLINK 사용" 한 줄만 남는다.

틀린 건 아닌데, 두 가지를 빼먹었다. UNLINK는 **조건부로만** 비동기다. 그리고 비동기일 때조차 옮겨진 건 '해제 작업'이지 '해제 비용'이 아니다. 명령이 `OK`를 반환한 시점과 메모리가 실제로 돌아온 시점은 다르고, 그 사이 간격에서 사고가 난다.

## 실제 원리

### 삭제 비용은 바이트가 아니라 할당 개수에 비례한다

`DEL`이 느린 이유를 "값이 커서"라고 뭉뚱그리면 판단이 틀어진다. 정확히는 **해제해야 할 메모리 할당(allocation)의 개수**에 비례한다.

512MB짜리 String 하나를 지우는 건 `free()` 한 번이다. jemalloc이 huge 영역을 `munmap`하고 끝나므로 마이크로초 단위다. 반면 필드 20만 개짜리 hashtable 인코딩 Hash는 dict 엔트리 20만 개 + 키 sds 20만 개 + 값 sds 20만 개를 각각 해제해야 한다. 크기는 전자가 훨씬 큰데, 싱글 스레드를 잡아먹는 쪽은 후자다. "BigKey"의 위험도는 `MEMORY USAGE`가 아니라 원소 개수로 재야 한다.

### UNLINK는 조건부 비동기다

`UNLINK`가 하는 일은 두 단계다. 먼저 keyspace 사전에서 엔트리를 떼어낸다(unlink) — 이건 무조건 즉시, O(1)이다. 그래서 명령 반환 직후 `EXISTS`는 0을 준다. 그다음 떼어낸 객체를 **버릴지 넘길지**를 판단한다.

Redis는 해제에 드는 "품"을 먼저 센다. `lazyfree.c`의 `lazyfreeGetFreeEffort()`가 그 함수고, 반환값은 대략 해제할 할당의 개수다 — hashtable 인코딩 Hash/Set은 dict의 원소 수, skiplist 인코딩 ZSet은 노드 수, quicklist 인코딩 List는 **원소 수가 아니라 노드 수**다. 그 외에는 그냥 1이다.

이 값이 임계치 **64를 넘고** 동시에 객체의 `refcount`가 1일 때만 백그라운드 워커로 넘어간다. 둘 중 하나라도 어긋나면 그 자리에서 동기로 해제한다.

listpack(구 ziplist)으로 인코딩된 Hash는 통째로 **할당 하나**다. 필드가 100개여도 effort는 1이고, `hash-max-listpack-entries 128`을 넘어 hashtable로 승격된 뒤에야 lazy free 대상이 된다. 512MB String도, 공유 정수처럼 `refcount > 1`인 값도 마찬가지로 동기 경로다. `OBJECT ENCODING key`가 `listpack`을 반환하면 그 키에 UNLINK를 쓰든 DEL을 쓰든 실행되는 코드는 동일하다.

### 반환 시점과 회수 시점은 다르다

백그라운드로 넘어간 객체는 bio 잡 큐에 쌓이고, **lazy free 전용 워커 하나**가 순서대로 해제한다. 워커는 하나다. UNLINK를 수천 번 날려도 병렬로 늘어나지 않는다.

그동안 `used_memory`는 떨어지지 않는다. Redis의 메모리 카운터는 zmalloc 계층에서 실제 `free()` 호출 시점에 감소하기 때문이다. 즉 UNLINK가 `OK`를 반환한 순간의 상태는 "지웠다"가 아니라 "지우기로 예약했고 아직 그 메모리를 쓰고 있다"에 가깝다.

밀린 양은 `INFO memory`에서 볼 수 있다. `lazyfree_pending_objects`는 아직 해제되지 않고 큐에 남은 객체 수, `lazyfreed_objects`는 누적 해제 개수다. 전자가 0이 아닌 상태가 지속되면 워커가 뒤처지고 있다는 뜻이다.

### 켤 스위치는 UNLINK 하나가 아니다

애플리케이션이 보내는 삭제만 lazy free 대상인 게 아니다. Redis 내부에서 값을 버리는 경로가 여럿 있고, 각각 별도 설정이다. redis.conf 기준 **기본값은 전부 `no`** — 즉 명시적으로 켜지 않으면 전부 동기 해제다.

```
lazyfree-lazy-eviction    no   # maxmemory 축출로 버릴 때
lazyfree-lazy-expire      no   # TTL 만료로 버릴 때
lazyfree-lazy-server-del  no   # RENAME 등이 덮어쓰는 기존 값
replica-lazy-flush        no   # 풀 동기화 직전 레플리카가 DB를 비울 때
lazyfree-lazy-user-del    no   # DEL을 UNLINK처럼 동작시킴 (6.0+)
lazyfree-lazy-user-flush  no   # FLUSHALL/FLUSHDB 기본 ASYNC (6.2+)
```

코드를 UNLINK로 다 바꿔놓고도 [[redis-ttl-eviction]]에서 본 만료·축출 경로가 큰 컬렉션을 동기로 해제하고 있으면, 지연 스파이크의 원인은 여전히 남아 있다.

## 현장 시나리오

리타게팅 광고 플랫폼. Redis 7.0 단일 인스턴스, 쿠버네티스 파드 메모리 리밋 16Gi. `maxmemory`는 설정하지 않았다(데이터를 잃으면 안 되는 세그먼트라 축출을 원하지 않았다). 평상시 `used_memory` 9.5GB, RSS 10.8GB.

세그먼트는 날짜별 ZSet이다. `seg:d20260814:{code}` 512개, 각 18만 멤버, 합쳐서 6.2GB. 매일 새벽 3시 배치가 전날 세그먼트를 지우고 당일치를 적재한다. 6개월 전 `DEL` 루프가 12초씩 블로킹한 사고가 있어서 그때 전부 `UNLINK`로 바꿨고, 그 뒤로 이 배치는 조용했다. 이번 배포에서 한 줄이 더 빠졌다 — 삭제 완료를 기다리던 sleep 30초를 "UNLINK는 즉시 반환하니 불필요"라며 제거했다.

새벽 3시. UNLINK 512개가 9ms 만에 전부 반환했고 배치는 곧바로 `seg:d20260815:*` 적재를 시작했다. 그런데 이전 6.2GB는 아직 살아 있었다 — 워커 하나가 512개 객체, 합계 9,200만 개 할당을 해제하는 중이었고 그 작업은 41초가 걸린다.

그 41초 동안 신규 세그먼트 6.4GB가 밀려 들어왔다. `used_memory`는 9.5GB에서 15.1GB까지 올라갔고, 단편화를 포함한 RSS는 16Gi를 넘었다. `maxmemory`를 안 걸어뒀으니 Redis는 스스로 멈추지 않았다. 멈춘 건 커널이었다 — OOMKilled.

파드가 재시작하면서 6.2GB RDB를 로드하는 데 2분 50초. 그동안 입찰 요청은 전부 캐시 미스로 원본 DB에 직행했고, 커넥션 풀이 마르면서 입찰 응답이 타임아웃됐다. 사후에 이전 날짜 메트릭을 뒤져보니 `lazyfree_pending_objects`는 반년 내내 매일 새벽 3시마다 400 언저리까지 치솟았다 내려가고 있었다. sleep 30초가 그 구간을 덮고 있었을 뿐이다.

원인 한 줄: **UNLINK는 성공했다. 성공한 것은 삭제였고, 회수가 아니었다.**

## 실무 적용 포인트

1. **대량 삭제 후 재적재 사이에 게이트를 넣어라.** `INFO memory`의 `lazyfree_pending_objects`가 0이 될 때까지 폴링한 뒤 다음 단계로 넘어간다. 고정 sleep보다 정확하고 데이터가 커져도 자동으로 늘어난다(상한 타임아웃은 걸어둔다). 이 값은 상시 그래프에도 올려라 — 0이 아닌 구간의 길이가 곧 "삭제했다고 믿는데 메모리는 아직 안 돌아온" 구간이다.

2. **`maxmemory`를 반드시 설정해라.** 미설정(`0`)은 "무제한"이고, 컨테이너 환경에서 그건 "커널이 결정한다"는 뜻이다. 파드 리밋의 60~70%를 `maxmemory`로 잡아 단편화와 복제 버퍼 여유를 남긴다. OOMKill은 축출보다 항상 나쁘다.

3. **내부 lazy free 스위치 6개를 다 켜라.** `lazyfree-lazy-eviction`, `lazyfree-lazy-expire`, `lazyfree-lazy-server-del`, `replica-lazy-flush`, 그리고 6.0+ `lazyfree-lazy-user-del`, 6.2+ `lazyfree-lazy-user-flush`. 기본값은 전부 `no`다. `lazyfree-lazy-user-del yes`를 켜면 애플리케이션 코드를 안 고치고도 `DEL`이 UNLINK 경로를 탄다.

4. **UNLINK가 실제로 비동기인지 확인해라.** 판단 기준은 크기가 아니라 effort > 64와 `refcount == 1`이다. `OBJECT ENCODING key`가 `listpack`/`intset`이면 통짜 할당 하나라 동기 해제고, UNLINK를 써도 얻는 게 없다. `hash-max-listpack-entries 128`, `zset-max-listpack-entries 128`을 넘겨 승격된 키가 진짜 대상이다.

5. **삭제를 시간에 분산해라.** lazy free 워커는 하나뿐이라 UNLINK 수천 개는 그 하나에 직렬로 쌓인다. [[redis-scan-vs-keys]]처럼 `SCAN`으로 커서를 돌리며 배치당 50~100개씩 UNLINK하고 사이에 짧은 간격을 둔다. 한 번에 던지고 반환값만 보고 안심하는 게 가장 위험하다.

6. **DB를 통째로 비울 땐 `FLUSHALL ASYNC`/`FLUSHDB ASYNC`.** 객체를 하나씩 큐에 넣는 게 아니라 사전 전체를 백그라운드로 넘기므로 반환이 사실상 즉시다. 다만 회수 시점은 여전히 나중이라 1번 게이트는 똑같이 필요하고, `used_memory`·RSS를 같이 그려야 [[redis-rdb-vs-aof]]의 fork 시점 메모리와 겹치는 구간이 보인다.

## 더 깊은 토끼굴

- [[redis-bigkey]] — 왜 삭제 하나가 클러스터 전체로 번지는가
- [[redis-ttl-eviction]] — 만료·축출 경로도 같은 해제 비용을 낸다
- [[redis-scan-vs-keys]] — 대량 키를 나눠서 훑는 방법
- [[redis-hotkey]] — 원소가 많은 컬렉션이 만드는 다른 종류의 편중
- [[redis-rdb-vs-aof]] — fork 시점 메모리와 회수 지연이 겹칠 때

**출처**
- Redis 공식 문서, `UNLINK` — https://redis.io/docs/latest/commands/unlink/ (keyspace에서 즉시 분리하고 실제 회수는 다른 스레드가 수행)
- Redis 공식 문서, `DEL` — https://redis.io/docs/latest/commands/del/ (복잡도가 삭제되는 원소 수 O(N)에 비례, String은 O(1))
- Redis 소스, `src/lazyfree.c` — https://github.com/redis/redis/blob/unstable/src/lazyfree.c (`LAZYFREE_THRESHOLD` 64, `lazyfreeGetFreeEffort()`, `refcount == 1` 조건)
- antirez, "Lazy Redis is better Redis" — http://antirez.com/news/93 (lazy free 도입 배경과 설계 결정)
