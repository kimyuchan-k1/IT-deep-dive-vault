---
title: 캐시를 지우는 데 성공했는데 옛 값이 3시간 동안 박혀 있었다
date: 2026-08-11
day: 65
category: redis
tags: [cache-aside, write-through, write-behind, invalidation, lease, stale-read]
related: ["[[cache-stampede]]", "[[redis-ttl-eviction]]", "[[read-your-writes]]", "[[lazy-freeing]]", "[[eventual-vs-strong-consistency]]"]
difficulty: 3
short_text: |
  ⚠️ [Day 65] 캐시만 3시간 옛 값

  오해: DB 쓰고 캐시 DEL
  실제: DEL은 성공, 늦은 채움이 옛 값 고정

  "TTL 없으면 영구다"

  📖 https://github.com/kimyuchan-k1/IT-deep-dive-vault/blob/main/posts/2026-08-11-cache-aside-vs-write-through.md
---

# 캐시를 지우는 데 성공했는데 옛 값이 3시간 동안 박혀 있었다

## 흔한 오해

> "캐시 갱신은 간단하잖아. 읽을 때 없으면 DB에서 읽어서 채우고, 쓸 때는 DB 쓰고 캐시 지운다. 정합성이 더 중요하면 write-through로 바꾸면 캐시랑 DB가 항상 같아진다."

입문 자료 대부분이 cache-aside를 네 줄짜리 의사코드로 가르친다. `get` → miss면 `SELECT` → `set` → 반환. 쓰기는 `UPDATE` 후 `DEL`. 네 줄이니 버그가 들어갈 자리가 없어 보인다.

네 줄 자체는 맞다. 빠진 건 그 네 줄이 **하나의 원자 연산이 아니라는 것**, 그리고 초당 수천 개의 요청이 그 줄 사이 틈으로 서로 끼어든다는 것이다. 캐시 정합성 사고의 대부분은 무효화가 실패해서 생기지 않는다. 무효화는 성공한다. 무효화보다 **늦게 도착한 채움**이 문제다.

## 실제 원리

### 세 전략은 정합성 축이 아니라 비용 축으로 갈린다

**cache-aside(lazy loading)**는 애플리케이션이 캐시와 DB를 각각 호출한다. 캐시가 죽어도 DB 경로로 서비스가 산다. 대신 모든 데이터의 첫 요청은 miss고, 무효화 책임이 전부 애플리케이션 코드에 흩어진다.

**write-through**는 쓰기가 캐시를 통과해 DB로 간다. 쓰기 지연이 캐시 쓰기 + DB 쓰기 합으로 늘어난다. 다시 안 읽힐 데이터까지 캐시 메모리를 먹으므로 [[redis-ttl-eviction]]의 eviction 압력이 올라간다.

**write-behind(write-back)**는 캐시에만 쓰고 일정 간격으로 DB에 flush한다. 쓰기가 압도적으로 빠른 대신, flush 안 된 창만큼은 캐시가 유일한 원본이다. 캐시 노드가 죽으면 그 창이 그대로 데이터 유실이다. 지속성 요구가 DB에서 캐시로 옮겨 앉는다는 뜻이고, 그래서 write-behind를 쓰려면 캐시에 복제와 AOF를 켜야 한다. 그 시점에 캐시는 이미 캐시가 아니다.

### 여기가 핵심 — 무효화의 순서와 경쟁 창

쓰기 경로에서 순서를 어떻게 잡느냐가 창의 크기를 결정한다.

**나쁜 순서 (캐시 먼저 지우고 DB 쓰기)**

```
W1: DEL product:42
R1: GET product:42 → miss
R1: SELECT → 89,000 (아직 옛 값)
R1: SET product:42 = 89,000
W1: UPDATE ... = 79,000  (커밋)
```

캐시에 89,000이 고정된다. 경쟁 창이 **DB 쓰기 전체 구간**이라 수십 ms 단위로 넓다. 그래서 기본은 "DB 커밋 먼저, 캐시 DEL 나중"이다.

**좋은 순서에도 남는 창 (DB 쓰기 후 캐시 지우기)**

```
R1: GET product:42 → miss
R1: SELECT → 89,000
                        W1: UPDATE = 79,000 (커밋)
                        W1: DEL product:42  → 키 없음, no-op
R1: SET product:42 = 89,000   ← 여기
```

`DEL`은 정상 실행됐다. 지울 키가 아직 없었을 뿐이다. 그 직후 R1이 자기가 들고 있던 옛 값을 캐시에 쓴다. 창은 좁지만 0이 아니고, R1의 `SELECT` 응답과 `SET` 사이에 GC pause나 TCP 재전송이 끼면 수백 ms까지 벌어진다. 그리고 이 키에 TTL이 없으면 불일치는 **영구**다.

이 문제는 오래된 문제이고 해법도 정리돼 있다. Facebook memcache는 miss마다 **lease** 토큰을 발급하고, 그 사이에 무효화가 지나갔으면 뒤늦은 `set`을 거절한다. Redis 단독으로는 lease가 없으므로 채움 락([[cache-stampede]]에서 쓰는 그 락)이 사실상 같은 역할을 한다.

### write-through도 정합성을 보장하지 않는다

캐시 쓰기와 DB 쓰기는 서로 다른 두 시스템에 대한 두 개의 연산이다. 둘을 묶는 트랜잭션이 없다. 캐시는 성공하고 DB가 롤백되면 캐시에 커밋되지 않은 값이 남고, 반대면 캐시가 뒤처진다. write-through의 실이익은 "캐시와 DB가 같다"가 아니라 **"쓰기 직후 읽기가 miss를 안 낸다"**다. 정합성 보장으로 오해하는 순간 TTL도 안 걸고 검증도 안 하게 된다.

쓰기 때 캐시를 새 값으로 `SET`하는 변형에도 같은 구멍이 있다. 두 쓰기의 `SET`이 순서 역전되면 옛 값이 최종 상태로 남는다. `DEL`은 최종 상태가 "없음"이라 다음 읽기가 원본에서 다시 채운다. 캐시를 하나의 복제본으로 보면 [[eventual-vs-strong-consistency]]의 마지막-쓰기-승리 문제 그대로다.

## 현장 시나리오

상품 5백만 건 커머스. 상품 상세 캐시는 Redis, 정책은 cache-aside. 무효화를 신뢰해서 상품 키에 TTL을 걸지 않았다. hit rate 97%, p99 8ms. 반년 동안 문제가 없었다.

프로모션 시작일 새벽, 배치가 3만 건의 가격을 내렸다. 89,000 → 79,000. 배치는 건당 `UPDATE` 커밋 후 `DEL`을 정확히 호출했고, 실패 로그는 0건이었다.

같은 시각 프로모션 트래픽이 들어왔다. 3만 건이 한꺼번에 무효화됐으니 그 키들에 read miss가 동시에 몰렸다. 어떤 상품 하나에서, 채움 중이던 요청이 `SELECT`로 89,000을 읽은 직후 JVM full GC로 340ms 멈췄다. 그 340ms 안에 배치의 `UPDATE`와 `DEL`이 지나갔다. GC가 끝나고 그 요청은 89,000을 캐시에 썼다.

TTL이 없으니 그 값은 만료되지 않았다. 목록과 상세 화면은 89,000, 결제 계산은 DB를 직접 읽어 79,000. 3시간 동안 "장바구니 금액이 다르다"는 문의가 쌓였다. 배치 로그는 깨끗해서 아무도 캐시를 의심하지 않았고, 결국 상품 캐시 전체를 flush했다. 그 flush가 다시 DB에 miss 폭풍을 만들어 p99가 2초까지 튀었다.

원인 한 줄: **무효화는 성공했다. 무효화보다 늦게 도착한 채움을 아무도 막지 않았다.**

## 실무 적용 포인트

1. **순서를 코드 규약으로 고정한다: DB 커밋 → 캐시 `DEL`.** 캐시를 먼저 지우면 경쟁 창이 DB 쓰기 시간 전체로 벌어진다. 트랜잭션 안에서 `DEL`하지도 마라 — 롤백되면 멀쩡한 캐시만 날린 셈이다.

2. **갱신은 `SET`이 아니라 `DEL`을 기본으로 한다.** 쓰기 순서 역전 시 `SET`은 옛 값을 최종 상태로 남긴다. 굳이 `SET`으로 캐시를 채워야 한다면 값에 단조 증가 `version`을 넣고 Lua로 `현재 version < 새 version`일 때만 덮어써라.

3. **TTL은 성능 장치가 아니라 정합성 안전망이다.** 무효화가 완벽하다는 가정으로 TTL을 빼면 버그 하나가 영구 버그가 된다. 상품/가격처럼 무효화가 있는 키도 `EXPIRE 3600` 수준을 걸어라. 최악의 불일치 지속 시간을 TTL로 상한을 두는 것이다.

4. **채움 경로에 단일 채움 락을 건다.** miss 시 `SET fill:product:42 1 NX PX 200`으로 락을 잡은 요청만 DB를 읽고 캐시를 채우게 한다. 이건 [[cache-stampede]] 대책과 같은 도구인데, 부수 효과로 늦은 채움 경쟁도 좁혀준다. 락 획득 실패한 요청은 짧게 재시도하거나 DB를 직접 읽는다.

5. **delayed double delete로 남은 창을 덮는다.** 커밋 후 `DEL`, 그리고 500ms~1s 뒤 한 번 더 `DEL`(비동기 큐로). 정확한 해법이 아니라 확률을 낮추는 장치다. 지연값은 채움 경로 p99보다 크게 잡아야 의미가 있다. `DEL` 자체가 실패할 수 있으니 무효화 이벤트는 재시도 큐에 남겨라.

6. **write-behind는 유실 예산을 먼저 숫자로 적는다.** flush 간격 1초는 최악의 경우 1초분 쓰기 유실이다. 조회수·좋아요 카운터라면 받아들일 수 있고, 잔액·주문이라면 안 된다. 큰 컬렉션을 캐시에 쌓아두다 지울 때 블로킹이 걸리는 문제는 [[lazy-freeing]]으로 이어진다.

7. **쓰기 직후 그 값을 반드시 봐야 하는 화면은 캐시를 우회한다.** 주문 완료 직후 상세 조회 같은 경로는 원본을 직접 읽어라. [[read-your-writes]]가 필요한 지점은 트래픽의 1% 수준이고, 그 1%를 캐시에서 빼는 비용이 정합성 사고보다 싸다.

## 더 깊은 토끼굴

- [[cache-stampede]] — 무효화 직후 몰리는 miss 폭풍을 막는 쪽 이야기
- [[redis-ttl-eviction]] — TTL을 걸어도 언제 실제로 지워지는가
- [[read-your-writes]] — 캐시 우회 경로를 어디까지 열어둘 것인가
- [[lazy-freeing]] — 캐시에 쌓인 큰 값을 지울 때 생기는 블로킹
- [[eventual-vs-strong-consistency]] — 캐시를 사실상 하나의 복제본으로 볼 때의 모델

**출처**
- Nishtala et al., "Scaling Memcache at Facebook", USENIX NSDI 2013 — https://www.usenix.org/system/files/conference/nsdi13/nsdi13-final170.pdf (§3.2.1 Leases: 늦은 `set`을 거절하는 메커니즘)
- AWS ElastiCache 개발자 가이드, "Caching strategies" — https://docs.aws.amazon.com/AmazonElastiCache/latest/red-ug/Strategies.html (lazy loading / write-through의 장단점 표)
- Redis 공식 문서, "Client-side caching in Redis" — https://redis.io/docs/latest/develop/clients/client-side-caching/ (무효화 메시지를 서버가 밀어주는 tracking 모드)
