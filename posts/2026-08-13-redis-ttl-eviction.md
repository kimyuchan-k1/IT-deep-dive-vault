---
title: 만료 시각이 지났는데 키가 남아 있었고, 정작 세션이 축출됐다
date: 2026-08-13
day: 67
category: redis
tags: [ttl, expiration, eviction, lru, lfu, maxmemory]
related: ["[[cache-aside-vs-write-through]]", "[[cache-stampede]]", "[[lazy-freeing]]", "[[redis-bigkey]]", "[[redis-rdb-vs-aof]]"]
difficulty: 2
short_text: |
  ⚠️ [Day 67] 만료 시각이 지나도 키가 남는다

  오해: 만료=즉시 삭제
  실제: 접근 때+100ms 20개 샘플→25% 남음

  "TTL 없는 키에 세션이 밀려났다"

  📖 https://github.com/kimyuchan-k1/IT-deep-dive-vault/blob/main/posts/2026-08-13-redis-ttl-eviction.md
---

# 만료 시각이 지났는데 키가 남아 있었고, 정작 세션이 축출됐다

## 흔한 오해

> "TTL을 걸면 그 시각에 키가 지워진다. 메모리가 부족하면 LRU가 알아서 가장 오래된 키부터 밀어낸다. 그러니 `maxmemory-policy`만 LRU로 해두면 캐시 메모리는 관리된다."

`EXPIRE key 3600`이라고 쓰면 한 시간 뒤에 예약된 삭제가 실행될 것 같다. 그리고 LRU는 자료구조 수업에서 배운 그 LRU — 접근 순서대로 연결 리스트를 관리하는 정확한 알고리즘 — 이라고 가정한다.

둘 다 틀렸다. Redis는 만료 시각에 어떤 타이머도 걸지 않고, LRU도 정확한 LRU가 아니다. 만료는 **샘플링으로 따라잡는 근사**고, 축출도 **샘플링으로 고르는 근사**다. 그리고 이 둘이 겹치는 지점에서 사고가 난다.

## 실제 원리

### 만료는 두 경로로만 일어난다

첫째, **수동(lazy) 만료**. 그 키에 접근이 들어올 때 만료 여부를 검사하고, 지났으면 그때 지운다. 아무도 안 읽는 키는 만료 시각이 한참 지나도 메모리에 그대로 있다.

둘째, **능동(active) 만료 사이클**. TTL이 걸린 키만 모아둔 `expires` 사전에서 무작위로 뽑아 검사한다. 공식 문서가 적어둔 절차 그대로다: 초당 10회, TTL 있는 키 20개를 무작위로 뽑고, 만료된 것을 지우고, **뽑은 것 중 25%가 넘게 만료였으면 즉시 같은 절차를 반복**한다.

여기가 핵심이다. 전수 검사가 아니라 확률 수렴이다. 그래서 문서는 보장을 확률로 말한다 — 이미 만료됐지만 아직 메모리를 차지하고 있는 키는 통계적으로 전체의 25%를 넘지 않는다. 만료가 초당 수만 건씩 몰리면 사이클이 뒤처지고, `used_memory`는 만료 시각보다 늦게 떨어진다. `DBSIZE`는 논리적으로 이미 없는 키까지 세고 있다.

사이클은 CPU를 무한정 쓰지 않는다. 이벤트 루프 앞에서 도는 빠른 사이클은 1ms 수준, `serverCron`에서 도는 느린 사이클은 CPU 25%를 상한으로 스스로 멈춘다. 그러니 `hz`를 10에서 올리면 사이클이 자주 돌지만, 그 CPU는 [[redis-bigkey]]에서 본 그 싱글 스레드에서 나온다.

### 복제본은 스스로 만료시키지 않는다

레플리카는 만료 시각이 지나도 자기 키를 지우지 않는다. 마스터가 만료를 확정하고 보내주는 `DEL`을 기다린다. 대신 읽기 요청에는 논리적으로 만료된 키를 없는 것처럼 응답한다. 삭제 시점을 마스터 하나로 몰아둔 설계이고, 그 결과 레플리카의 `used_memory`가 마스터보다 큰 상태가 정상적으로 나타난다.

### LRU도 LFU도 샘플이다

`maxmemory`에 닿으면 Redis는 축출을 시작한다. 정확한 LRU 리스트를 유지하려면 키마다 앞뒤 포인터가 필요하고, 그 메모리가 캐시 자체보다 아깝다. 그래서 객체 헤더의 24비트 필드에 마지막 접근 시각(초 단위)만 적어두고, 축출할 때 `maxmemory-samples`개(기본 5)를 무작위로 뽑아 그중 가장 오래된 것을 버린다. Redis 3.0부터는 후보 16개짜리 풀을 유지해서 진짜 LRU에 훨씬 가까워졌지만, 여전히 근사다. 샘플을 10으로 올리면 CPU를 조금 더 쓰고 정확도가 올라간다.

LFU는 같은 24비트를 8비트 로그 카운터와 16비트 감쇠 시각으로 쪼개 쓴다. 카운터는 로그 스케일이라 255에서 포화하고, `lfu-decay-time` 분마다 줄어든다. 한 번 훑고 지나가는 배치 트래픽이 LRU를 오염시키는 환경에서는 LFU가 유리하다.

### `volatile-*`의 함정

정책 이름의 접두사가 후보 집합을 정한다. `allkeys-*`는 모든 키가 후보고, `volatile-*`는 **TTL이 걸린 키만** 후보다. TTL 없는 키가 메모리를 채우면 `volatile-lru`는 그 키들을 건드리지 못하고, 남은 후보인 TTL 있는 키만 계속 밀어낸다. 후보가 아예 없으면 쓰기는 `OOM command not allowed when used memory > 'maxmemory'` 에러를 받는다. 기본값인 `noeviction`과 같은 결말이다.

그리고 TTL이 사라지는 경로가 하나 더 있다. `SET key value`는 `KEEPTTL` 없이 실행되면 **기존 TTL을 제거한다**. 캐시 갱신 코드가 `EX`를 빼먹으면 그 키는 그 순간부터 불멸이 되고, `volatile-*` 정책에서는 축출 후보에서도 빠진다. [[cache-aside-vs-write-through]]에서 본 "TTL은 정합성 안전망"이라는 말이 메모리 축에서도 그대로 반복된다.

## 현장 시나리오

세션 스토어와 추천 캐시를 한 Redis 인스턴스에 같이 올린 커머스. `maxmemory 12gb`, `maxmemory-policy volatile-lru`. 세션 키는 `SET sess:{id} ... EX 1800`, 약 400만 개. 반년 동안 `used_memory`는 8GB 언저리에서 평평했다.

신규 추천 배치가 붙었다. 상품별 추천 목록 260만 건을 `SET reco:{pid} {json}`으로 적재했는데, `EX`가 빠져 있었다. 건당 1.4KB, 총 3.6GB. 배치는 성공했고 알림도 없었다.

12GB에 닿는 순간 축출이 시작됐다. 정책이 `volatile-lru`라 후보는 TTL이 걸린 키, 즉 세션뿐이었다. `reco:*` 3.6GB는 후보 목록에 아예 들어가지 않았다. `INFO stats`의 `evicted_keys`가 초당 9,000씩 올라갔고, 그 9,000개는 전부 살아 있는 세션이었다.

사용자 화면에서는 로그인이 무작위로 풀렸다. 재로그인 요청이 몰리자 인증 API QPS가 120에서 4,300으로 뛰었고, 새로 만든 세션이 다시 메모리를 밀어 올려 또 축출됐다. 자기 자신을 먹이는 순환이었다. 대시보드의 `used_memory`는 12GB에 딱 붙어 평평했기 때문에 아무도 메모리를 의심하지 않았다. 조사 첫 30분은 인증 서버 배포 이력을 뒤졌다.

`TTL reco:1001`이 `-1`을 반환한 것을 확인하는 데 40분이 걸렸다. 원인 한 줄: **`EX` 하나가 빠지자, 축출은 그 키를 빼고 남은 전부를 대상으로 정상 동작했다.**

## 실무 적용 포인트

1. **`maxmemory`와 정책을 명시적으로 박아라.** 기본 `maxmemory-policy`는 `noeviction`이다. 캐시 용도면 `maxmemory 12gb` + `maxmemory-policy allkeys-lru`가 출발점이다. 세션처럼 잃으면 안 되는 데이터는 애초에 캐시 인스턴스와 분리한다.

2. **`volatile-*`를 쓰려면 "모든 키에 TTL"이 불변식이다.** 코드 경로 하나만 TTL을 빼먹어도 그 키는 불멸이 되고 축출 후보에서도 빠진다. 배포 전 `redis-cli --scan --pattern 'reco:*'` 표본에 `TTL`을 돌려 `-1`이 나오는지 확인해라.

3. **`SET`은 TTL을 지운다.** 갱신 경로는 `SET k v KEEPTTL`이나 `SET k v EX 3600`으로 고정한다. 해시 필드 갱신(`HSET`)은 키 TTL을 건드리지 않으니 두 경로가 섞이면 더 헷갈린다. `TTL key`가 `-1`이면 불멸, `-2`면 없는 키다.

4. **축출 정확도가 필요하면 `maxmemory-samples`를 5에서 10으로 올려라.** CPU를 조금 더 쓰고 진짜 LRU에 가까워진다. 스캔성 배치가 LRU를 오염시키는 환경이면 `allkeys-lfu`로 바꾸고 `lfu-log-factor 10`, `lfu-decay-time 1`에서 시작해 `OBJECT FREQ key`로 카운터 분포를 본다.

5. **`expired_keys`와 `evicted_keys`를 분리해서 그래프에 올려라.** `INFO stats` 두 값의 초당 증가율이 다른 이야기를 한다. `expired_keys`만 오르면 정상 만료, `evicted_keys`가 0이 아닌 상태로 지속되면 이미 메모리 상한에서 데이터가 버려지고 있다는 뜻이다. `used_memory`가 상한에 붙어 평평한 그래프는 안정이 아니라 포화 신호다.

6. **만료가 몰리는 워크로드는 `hz`와 lazy free를 같이 본다.** 능동 사이클이 뒤처지면 `used_memory`가 만료보다 늦게 떨어진다. `hz`를 10에서 올리면 사이클이 자주 돌지만 CPU를 그만큼 쓴다. 만료 대상이 큰 컬렉션이면 `lazyfree-lazy-expire yes`로 해제를 백그라운드 스레드에 넘겨라 — [[lazy-freeing]] 그대로다.

7. **TTL에 지터를 넣어라.** 같은 배치가 채운 키들은 만료 시각도 같아서 한꺼번에 사라진다. `EX 3600` 대신 `EX 3300~3900` 범위로 흩어라. 동시 만료는 그대로 miss 폭풍이고, 그 뒤는 [[cache-stampede]] 이야기다.

8. **`maxmemory`는 단편화를 포함하지 않는다.** 상한은 `used_memory` 기준이라 프로세스 RSS는 그보다 크다. `INFO memory`의 `mem_fragmentation_ratio`가 1.5를 넘으면 컨테이너 메모리 리밋을 다시 계산해라. RDB/AOF의 fork 시점 메모리까지 겹치면 더 벌어진다 — [[redis-rdb-vs-aof]].

## 더 깊은 토끼굴

- [[cache-aside-vs-write-through]] — TTL을 정합성 안전망으로 쓰는 쪽 이야기
- [[cache-stampede]] — TTL이 동시에 만료됐을 때 벌어지는 일
- [[lazy-freeing]] — 만료·축출된 큰 값을 실제로 해제하는 비용
- [[redis-bigkey]] — 능동 만료 사이클이 쓰는 CPU가 어디서 나오는가
- [[redis-rdb-vs-aof]] — 영속화 fork와 메모리 상한의 상호작용

**출처**
- Redis 공식 문서, `EXPIRE` — https://redis.io/docs/latest/commands/expire/ ("How Redis expires keys" 절: 초당 10회, 20개 샘플, 25% 재시도 알고리즘)
- Redis 공식 문서, "Key eviction" — https://redis.io/docs/latest/develop/reference/eviction/ (정책 목록, 근사 LRU 샘플링과 `maxmemory-samples` 정확도 비교 그래프, LFU 카운터)
- Redis 공식 문서, "Replication" — https://redis.io/docs/latest/operate/oss_and_stack/management/replication/ (레플리카는 만료를 스스로 수행하지 않고 마스터의 `DEL`을 기다린다)
