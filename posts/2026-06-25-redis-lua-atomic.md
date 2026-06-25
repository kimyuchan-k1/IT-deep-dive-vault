---
title: EVAL로 감싸면 원자성이 공짜로 따라온다고 믿었는데, 스크립트 하나가 인스턴스 전체를 얼려 SCRIPT KILL조차 안 먹혔다
date: 2026-06-25
day: 23
category: redis
tags: [redis, lua, atomicity, eval, rate-limit]
related: ["[[redis-bigkey]]", "[[lazy-freeing]]", "[[redis-pipelining-vs-tx]]", "[[rate-limit-token-bucket]]", "[[redis-scan-vs-keys]]", "[[idempotency-key]]"]
difficulty: 3
short_text: |
  ⚠️ [Day 23] Redis Lua 원자성은 공짜가 아니다
  오해: EVAL로 감싸면 안전한 트랜잭션
  실제: 싱글 스레드 점유→전체 블로킹→BUSY→롤백 없음
  "스크립트 1개가 5초간 인스턴스를 얼렸다"
  📖 https://github.com/kimyuchan-k1/IT-deep-dive-vault/blob/main/posts/2026-06-25-redis-lua-atomic.md
---

# EVAL로 감싸면 원자성이 공짜로 따라온다고 믿었는데, 스크립트 하나가 인스턴스 전체를 얼려 SCRIPT KILL조차 안 먹혔다

## 흔한 오해

"여러 명령을 묶어서 race condition 없이 처리하고 싶으면 Lua 스크립트로 `EVAL` 하면 되잖아. 원자적이니까 트랜잭션처럼 안전하지."

대부분 그렇게 안다. `GET` 해서 검사하고 조건 맞으면 `SET` 하는 check-and-set을, [[redis-pipelining-vs-tx]]의 MULTI/EXEC 대신 Lua로 감싸면 중간에 다른 클라이언트가 끼어들 수 없으니 "안전한 트랜잭션"을 얻었다고 믿는다.

**원자성은 진짜다. 하지만 그게 트랜잭션이라는 뜻은 아니다.** Redis의 Lua 원자성은 "방해받지 않음(no interleaving)"이지, "실패하면 되돌림(rollback)"이 아니다. 그리고 그 원자성을 떠받치는 메커니즘이 바로 [[redis-bigkey]]를 클러스터 킬러로 만들었던 그 싱글 스레드다.

## 실제 원리

### 원자성의 정체는 "싱글 스레드 점유"다

Redis가 Lua 스크립트를 원자적으로 실행하는 방법은 마법이 아니다. **스크립트가 끝날 때까지 다른 명령을 단 하나도 처리하지 않는 것**이다. Redis의 명령 처리는 싱글 스레드이므로, `EVAL`이 시작되면 그 스크립트 안의 모든 `redis.call()`이 끝날 때까지 이벤트 루프 전체가 그 스크립트 차지다.

여기서 원자성과 비용이 같은 동전의 양면이라는 게 드러난다:

- **좋은 면**: 스크립트 중간에 다른 클라이언트의 명령이 끼어들 수 없다 → race condition 0.
- **나쁜 면**: 스크립트가 50ms 걸리면, 그 50ms 동안 **다른 모든 클라이언트가 대기**한다. 1만 번 도는 루프나 큰 `KEYS` 결과를 순회하는 스크립트는 인스턴스 전체를 그만큼 멈춘다.

즉 Lua 스크립트는 "빠르고 짧아야" 원자성이 자산이 된다. 길어지면 그 즉시 단일 장애점이 된다.

### 원자적이지만 트랜잭션은 아니다 — 롤백이 없다

여기가 핵심이다. 관계형 DB의 트랜잭션은 중간에 에러가 나면 이미 한 변경을 **되돌린다**. Lua는 그러지 않는다.

```lua
redis.call('SET', KEYS[1], 'a')   -- 성공
redis.call('INCR', KEYS[2])        -- KEYS[2]가 문자열이면 여기서 에러
-- 스크립트 중단. 하지만 KEYS[1]='a'는 이미 쓰였고 되돌아가지 않는다
```

스크립트가 중간에 죽어도 **그 전까지 실행된 쓰기는 그대로 남는다.** MULTI/EXEC도 마찬가지지만, Lua는 "프로그래밍 언어"처럼 보여서 사람들이 try-catch와 롤백을 기대한다는 게 함정이다. 멱등성([[idempotency-key]])을 스크립트 안에서 직접 보장하지 않으면, 재시도가 부분 적용을 두 번 쌓는다.

### lua-time-limit는 스크립트를 죽이지 않는다

스크립트가 폭주하면 Redis가 알아서 끊어줄 것 같지만 아니다. `lua-time-limit`(기본 5000ms)는 **타임아웃이 아니라 경고선**이다. 이 시간을 넘으면 Redis는 스크립트를 멈추지 않고, 대신 다른 클라이언트에게 `-BUSY` 에러를 응답하기 시작한다. 그 상태에서 받아주는 명령은 단 두 개:

- `SCRIPT KILL` — 단, **아직 쓰기를 하지 않은 스크립트만** 죽일 수 있다. 이미 `redis.call('SET', ...)`을 한 스크립트는 죽이면 데이터 일관성이 깨지므로 거부된다.
- `SHUTDOWN NOSAVE` — 인스턴스를 통째로 내린다. 마지막 수단.

쓰기를 한 무한 루프 스크립트에 걸리면, `SCRIPT KILL`이 거부되고 `SHUTDOWN NOSAVE`밖에 답이 없는 상황이 만들어진다.

### KEYS와 ARGV는 장식이 아니다

스크립트가 건드리는 키는 반드시 `KEYS[]`로 넘겨야 한다. 클러스터 모드에서 Redis는 `KEYS`에 선언된 키들의 해시 슬롯([[redis-cluster-slot]] 개념)으로 스크립트를 라우팅하고, **모든 키가 같은 슬롯에 있는지 검증**한다. 키를 하드코딩하거나 ARGV로 몰래 넘기면 클러스터에서 `CROSSSLOT` 에러로 터지거나, 더 나쁘게는 엉뚱한 노드에서 돈다.

## 현장 시나리오

선착순 쿠폰 발급에 Lua 기반 레이트 리미터를 썼다. 스크립트는 사용자별 키에 `INCR` 하고, 한도를 넘으면 거부하는 단순한 구조였다 — 여기까진 1ms도 안 걸렸다.

문제는 "전체 발급 현황"을 같은 스크립트에서 집계하려고 한 줄을 추가하면서 시작됐다.

- 누군가 `redis.call('KEYS', 'coupon:user:*')`를 스크립트 안에 넣었다. 사용자 키가 50만 개로 자랐다.
- 이벤트 오픈 순간 트래픽이 몰리자, **매 발급 요청마다** 50만 키를 순회하는 `KEYS`가 돌았다.
- 한 번 호출에 약 80ms. 싱글 스레드라 이 80ms 동안 다른 모든 발급·조회 요청이 큐에 쌓임.
- 처리량이 초당 수천에서 수십으로 붕괴. 쌓인 요청이 또 `KEYS`를 부르는 악순환.
- 그 와중에 한 스크립트가 `lua-time-limit` 5초를 넘김 → Redis가 전 클라이언트에 `-BUSY` 응답 시작.
- 운영자가 `SCRIPT KILL` 시도 → 그 스크립트는 이미 `INCR`로 쓰기를 한 뒤라 **거부됨**.
- 결국 `SHUTDOWN NOSAVE`로 인스턴스를 내리고 재기동. 그 사이 쿠폰 발급 전면 중단.

원인 한 줄: **"스크립트 안에서 KEYS를 호출했다."** [[redis-scan-vs-keys]]의 교훈이 Lua 안에서 그대로, 더 치명적으로 반복됐다 — 이번엔 블로킹이 원자성으로 보장됐으니까.

## 실무 적용 포인트

1. **스크립트 안에서 `KEYS`·`SMEMBERS`·`HGETALL` 금지**: 풀스캔/대량 순회는 싱글 스레드를 통째로 점유한다. 집계는 스크립트 밖에서 `SCAN` 커서로 끊어 돌려라([[redis-scan-vs-keys]]).
2. **스크립트는 O(1)~O(작은 상수)로 유지**: Lua 한 번 실행이 1ms를 넘으면 설계를 의심하라. 루프 횟수에 외부 입력이 들어가면 위험.
3. **`lua-time-limit`은 5000ms 기본 유지, 단 타임아웃으로 오해 말 것**: 이건 BUSY 전환 시점일 뿐 스크립트를 안 죽인다. 진짜 안전장치는 짧은 스크립트 그 자체.
4. **모든 키는 `KEYS[]`로**: 클러스터 슬롯 라우팅과 `CROSSSLOT` 검증을 위해 필수. ARGV에 키 숨기지 마라.
5. **`SCRIPT LOAD` + `EVALSHA`로 운영**: 매번 스크립트 본문을 보내지 말고 SHA로 호출해 네트워크·파싱 비용을 줄여라. 단 `NOSCRIPT` 에러 시 `EVAL` 폴백 로직 필수.
6. **롤백을 기대하지 마라**: 스크립트가 중간 실패해도 앞선 쓰기는 남는다. 부분 적용이 위험하면 멱등하게 짜거나, 한 번에 하나의 논리적 변경만 하도록 쪼개라.
7. **Redis 7+면 Functions 검토**: `FUNCTION LOAD`로 스크립트를 이름 붙은 라이브러리로 관리하면 EVALSHA의 SHA 관리 부담이 준다. 단 블로킹 특성은 동일하다.

## 더 깊은 토끼굴

- Redis 공식: [EVAL / Scripting 문서](https://redis.io/docs/latest/commands/eval/) — 원자성·KEYS/ARGV·replication 규칙 1차 출처
- Redis 공식: [Redis programmability 개요](https://redis.io/docs/latest/develop/interact/programmability/) — Functions vs Scripts
- [[redis-bigkey]]: 같은 싱글 스레드가 다른 얼굴로 — BigKey 블로킹
- [[redis-pipelining-vs-tx]]: MULTI/EXEC와 Lua의 원자성 차이
- [[lazy-freeing]]: 블로킹을 백그라운드로 넘기는 반대편 설계
- [[rate-limit-token-bucket]]: Lua로 원자적 레이트 리밋을 제대로 짜는 법
