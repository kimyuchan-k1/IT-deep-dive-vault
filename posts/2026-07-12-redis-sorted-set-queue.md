---
title: 선착순 큐를 카운터로 만들면 왜 DB가 녹아내리나 — Redis Sorted Set
date: 2026-07-12
day: 39
category: redis
tags: [redis, sorted-set, queue, concurrency, flash-sale]
related: ["[[redis-lua-atomic]]", "[[redis-bigkey]]", "[[rate-limit-token-bucket]]", "[[idempotency-key]]"]
difficulty: 3
short_text: |
  🔥 [Day 39] 선착순을 카운터로 짜면 DB가 죽는다

  오해: 재고 UPDATE면 끝
  실제: 순서·중복·순번을 못 묶어→ZSET

  "1천 티켓에 1만 요청 직렬화"

  📖 https://github.com/kimyuchan-k1/IT-deep-dive-vault/blob/main/posts/2026-07-12-redis-sorted-set-queue.md
---

# 선착순 큐를 카운터로 만들면 왜 DB가 녹아내리나 — Redis Sorted Set

## 흔한 오해

"선착순은 그냥 카운터 하나면 되는 거 아닌가? `INCR`로 번호표 뽑고, 재고가 남아 있으면 `UPDATE stock SET qty = qty - 1 WHERE qty > 0` 하면 끝이지."

대부분 그렇게 시작한다. List로 `LPUSH` 해서 순서를 만들고, 재고는 DB 한 행에 두고 조건부 UPDATE로 깎는다. 트래픽이 적을 때는 정말 잘 돌아간다.

**틀린 건 아닌데, 선착순 큐가 실제로 요구하는 세 가지를 한 번에 못 준다.** 순서(누가 먼저 왔나), 중복 방지(같은 사람이 두 번 들어오면?), 내 위치 조회(나 몇 번째야?) — 카운터와 List로는 이 셋을 원자적으로 묶지 못한다.

## 실제 원리

### 왜 카운터·List로는 부족한가

- **카운터(`INCR`)**: 번호는 주지만 "지금 몇 명 들어왔고 내가 커트라인 안인가"를 하나의 원자 연산으로 못 본다. 번호 뽑기와 재고 확인이 분리되면 그 틈에 초과 발급이 난다.
- **List(`LPUSH`/`RPOP`)**: FIFO는 되지만 중복 진입을 막을 수 없고, "내 순번"을 O(1)로 물어볼 수 없다. 위치를 알려면 전체를 훑어야 한다.
- **DB 재고 행**: `WHERE qty > 0` UPDATE는 정확하다. 하지만 **모든 요청이 같은 한 행의 락을 두고 줄을 선다.** 이게 뒤에서 터진다.

### Sorted Set이 세 가지를 한꺼번에 준다

Redis의 Sorted Set(ZSET)은 각 멤버에 **score**를 붙여 정렬 상태로 유지한다. 선착순 큐에선 **score = 도착 시각(마이크로초 epoch)**으로 쓴다.

- `ZADD queue NX <ts> user:123` — score 순으로 자동 정렬 삽입. `NX`는 이미 있으면 무시 → **첫 도착 시각이 보존되고 재진입이 무시된다(중복 방지·멱등).**
- `ZRANK queue user:123` — 내 순번을 **O(log N)** 으로 조회. List처럼 전체를 훑지 않는다.
- `ZCARD queue` / `ZCOUNT` — 현재 대기 인원. 커트라인 계산은 여기서.

내부적으로 ZSET은 **skip list + 해시테이블** 두 자료구조를 함께 쓴다. skip list가 score 정렬을 담당해 삽입·순위 조회가 O(log N), 해시테이블이 "이 멤버의 score" 조회를 O(1)로 준다. (멤버가 적으면 listpack으로 압축 저장하다가 임계를 넘으면 이 구조로 승격.)

### 핵심은 "원자적으로 묶는 것"

세 연산을 따로 호출하면 그 사이에 다른 요청이 끼어든다. 그래서 [[redis-lua-atomic]] 스크립트로 묶는다. Redis는 명령 처리가 싱글 스레드라, Lua 스크립트 하나가 도는 동안 다른 명령이 절대 끼어들지 못한다.

```lua
-- KEYS[1]=queue, ARGV[1]=ts, ARGV[2]=user, ARGV[3]=limit
redis.call('ZADD', KEYS[1], 'NX', ARGV[1], ARGV[2])
local rank = redis.call('ZRANK', KEYS[1], ARGV[2])
if rank < tonumber(ARGV[3]) then return 'ADMITTED:'..rank
else return 'WAIT:'..rank end
```

진입·순위 확인·커트라인 판정이 **하나의 원자 블록**으로 끝난다. 초과 발급이 원천적으로 불가능하다.

## 현장 시나리오

콘서트 티켓 오픈. 재고 1,000장, 오픈 순간 대기 1만 명이 동시에 몰렸다.

초기 구조는 DB였다. `UPDATE tickets SET stock = stock - 1 WHERE stock > 0`.

- 1만 개 요청이 전부 **같은 재고 행 하나의 락**을 잡으려 줄을 섬
- InnoDB 행 락이 직렬화되면서 트랜잭션이 하나씩만 통과
- 대기 트랜잭션이 커넥션 풀을 점유 → 풀 고갈
- 티켓과 **무관한 다른 API(로그인·상품조회)까지 커넥션을 못 얻어 같이 느려짐**
- p99 응답 8초, 결제 타임아웃 속출

Redis ZSET으로 바꿨다. 요청은 전부 `ZADD NX + ZRANK` Lua 한 방으로 처리. score가 커트라인(1,000) 안이면 즉시 "당첨" 응답, 밖이면 "대기 N번". **DB는 실제 당첨된 1,000명의 확정 INSERT에만 닿았다.** 핫 로우 경합이 사라졌고, 대기자는 자기 순번을 실시간으로 봤다. p99 30ms.

바뀐 건 하나였다: **경합을 DB 한 행이 아니라, 정렬을 원래 잘하는 자료구조로 옮긴 것.**

## 실무 적용 포인트

1. **진입은 `ZADD NX`**: `NX`로 첫 도착 시각을 고정하고 재진입을 멱등하게 무시. 새로고침 난사에도 순번이 안 바뀐다. ([[idempotency-key]] 설계와 같은 결.)
2. **score 충돌 방지**: 마이크로초 epoch도 동시 도착이면 겹친다. `ts * 1e6 + 서버시퀀스`처럼 tie-break 비트를 섞어 완전 순서 보장.
3. **판정은 반드시 Lua로**: `ZADD`→`ZRANK`→커트라인 비교를 한 스크립트에. 앱에서 3번 왕복하면 그 사이 레이스가 난다.
4. **순번 조회는 `ZRANK`**, 절대 `ZRANGE 0 -1` 후 앱에서 찾지 마라 — O(N) 스캔은 [[redis-bigkey]]처럼 이벤트 루프를 잡는다.
5. **키 정리**: 이벤트 종료 후 `EXPIRE queue 3600`. 안 지우면 ZSET이 계속 자라 BigKey가 된다.
6. **재고 감소 방식과 분리**: ZSET은 "순서·자격"만 판정. 실제 재고 확정은 당첨자만 DB/`DECR`로. 두 관심사를 섞지 마라.

## 더 깊은 토끼굴

- Redis 공식: [Sorted Sets 튜토리얼](https://redis.io/docs/latest/develop/data-types/sorted-sets/) — ZADD 플래그(NX/XX/GT/LT)와 복잡도
- Redis 공식: [ZADD 커맨드 레퍼런스](https://redis.io/docs/latest/commands/zadd/) — `NX`/`GT` 동작 정확히
- [[redis-lua-atomic]]: 왜 Lua가 MULTI/EXEC보다 이 경우에 나은가
- [[redis-bigkey]]: ZSET이 커지면 벌어지는 일
- [[rate-limit-token-bucket]]: 같은 "동시성 제어" 계열의 다른 접근
