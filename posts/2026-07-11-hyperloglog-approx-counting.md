---
title: 10억 개를 세는데 메모리 12KB면 충분하다 — HyperLogLog
date: 2026-07-11
day: 38
category: db
tags: [hyperloglog, cardinality, probabilistic, redis, approximate-counting]
related: ["[[redis-bigkey]]", "[[redis-ttl-eviction]]", "[[cache-stampede]]", "[[redis-lua-atomic]]"]
difficulty: 3
short_text: |
  💡 [Day 38] 10억을 12KB로 센다
  오해: 유니크는 목록을 저장해야
  실제: 앞자리 0 최댓값→2ᵏ 역산→오차 0.81%
  "UV 집계 수백MB→12KB"
  📖 https://github.com/kimyuchan-k1/IT-deep-dive-vault/blob/main/posts/2026-07-11-hyperloglog-approx-counting.md
---

# 10억 개를 세는데 메모리 12KB면 충분하다 — HyperLogLog

## 흔한 오해

"유니크 방문자 수를 세려면 결국 본 사람 목록을 다 갖고 있어야 하는 거 아닌가? 중복을 걸러야 하니까 `SET`에 넣고 크기를 세는 게 정석이지."

대부분 그렇게 안다. `COUNT(DISTINCT user_id)`나 Redis `SADD` + `SCARD`가 가장 자연스러운 답이니까. 중복 제거의 정의상 "지금까지 본 원소 전부"를 어딘가 기억해야 한다고 믿는다.

**틀린 건 아닌데, "정확한 값"을 포기하면 이야기가 완전히 달라진다.** 오차 1%를 감수할 수 있다면, 원소를 하나도 저장하지 않고도 카디널리티(distinct count)를 셀 수 있다. 필요한 건 원소 목록이 아니라 **딱 하나의 숫자 — 지금까지 본 해시값 중 앞자리 0이 가장 길었던 개수**뿐이다. HyperLogLog는 이 한 줄 직관을 통계로 다듬은 자료구조다.

## 실제 원리

### 앞자리 0의 개수가 곧 규모의 로그다

동전을 던진다고 하자. 앞면이 연속으로 10번 나올 확률은 1/2¹⁰이다. 뒤집으면, **앞면이 10번 연속 나오는 걸 봤다면 대략 2¹⁰ ≈ 1024번쯤 던졌겠구나** 하고 역산할 수 있다.

HyperLogLog는 원소를 해시해서 균일한 비트열로 만든 뒤, **맨 앞의 0이 몇 개 연속되는지**를 본다. 어떤 원소의 해시가 `0001…`이면 앞자리 0이 3개다. 이런 일이 벌어지려면 대략 2³개의 서로 다른 값을 봤어야 한다. 그래서 지금까지 관측한 원소들의 "앞자리 0 최댓값"이 `k`라면, 카디널리티는 대략 **2ᵏ**로 추정한다. 원소는 한 개도 저장하지 않는다. 그냥 최댓값 하나만 갱신하며 흘려보낸다.

### 버킷을 나눠 분산을 줄인다

문제는 이 추정이 운에 크게 흔들린다는 것이다. 딱 한 번 운 나쁘게 앞자리 0이 20개인 해시가 튀어나오면 추정치가 백만 배로 튄다.

여기가 핵심이다. HyperLogLog는 해시의 **앞쪽 몇 비트로 원소를 `m`개의 버킷(레지스터)에 배분**하고, 나머지 비트에서 앞자리 0 개수를 잰다. 각 버킷은 자기가 본 최댓값만 6비트로 기억한다. 그리고 전체 추정은 버킷들의 **조화 평균(harmonic mean)**으로 합친다. 조화 평균은 큰 이상치에 둔감해서 튀는 값을 눌러준다.

최종 추정식은 대략 이렇다:

```
E = α_m · m² / Σ 2^(-M[j])
```

`M[j]`는 j번째 버킷의 최댓값, `α_m`은 편향 보정 상수(약 0.7213)다. 버킷을 많이 쓸수록 표준오차가 줄어든다:

```
표준오차 ≈ 1.04 / √m
```

Redis의 HyperLogLog는 **m = 16384개(2¹⁴) 버킷 × 6비트 = 98304비트 ≈ 12KB**를 쓴다. 표준오차는 `1.04/√16384 ≈ 0.81%`. 이 12KB는 원소가 1000개든 10억 개든 **고정**이다. 그래서 "10억 개를 12KB로 센다"가 성립한다.

### 작을 때와 클 때를 다르게 다룬다

카디널리티가 아주 작으면(빈 버킷이 많으면) 위 공식은 편향이 크다. 그래서 HyperLogLog는 그 구간에서 **선형 카운팅(linear counting)**으로 폴백한다 — 빈 버킷 비율로 개수를 역산하는 별도 공식이다. Redis는 여기에 더해 카디널리티가 작을 땐 레지스터 전체 배열 대신 **sparse 표현**으로 저장해, 실제로는 12KB보다 훨씬 적은 메모리로 시작한다. 값이 커지면 dense 표현으로 자동 전환된다.

## 현장 시나리오

한 콘텐츠 플랫폼이 캠페인 페이지의 **일 유니크 방문자(UV)**를 Redis `SET`으로 집계했다. 요청마다 `SADD uv:2026-07-11 {userId}`, 조회는 `SCARD`. 처음엔 잘 돌았다.

바이럴이 터진 날, 인과 사슬은 이렇게 번졌다:

- 하루 UV가 5천만으로 치솟음 → `SET` 하나가 5천만 멤버로 성장
- 8바이트 ID + 내부 오버헤드로 이 키 하나가 수백 MB~1GB 차지 ([[redis-bigkey]]와 정확히 같은 함정)
- Redis `maxmemory` 도달 → `allkeys-lru` eviction 시작 → 세션·캐시 키까지 축출
- 캐시 미스 폭증 → DB로 트래픽 우회 → [[cache-stampede]] 조짐과 응답 지연

수정은 자료구조 교체 한 번이었다. `SADD`/`SCARD`를 `PFADD`/`PFCOUNT`로 바꿨다:

```
PFADD uv:2026-07-11 {userId}
PFCOUNT uv:2026-07-11
```

키당 메모리가 **최대 12KB로 고정**되며 eviction 캐스케이드가 사라졌다. 대신 UV 숫자엔 0.81% 오차가 생겼다 — 5천만이면 ±40만 안팎. **마케팅 대시보드엔 아무도 신경 쓰지 않을 오차였고, 정확한 값을 포기한 대가로 메모리를 5자리수 줄였다.**

## 실무 적용 포인트

1. **고카디널리티 + 오차 허용일 때만 이득**: UV, 도달 수, 유니크 IP, 검색어 다양성 집계에 적합. 정산·과금처럼 정확한 수가 필요하면 절대 쓰지 마라. 표준오차 0.81%는 100만에서 ±8100이다.
2. **`PFADD`/`PFCOUNT`/`PFMERGE` 3종**: 추가·조회·병합 모두 O(1). 키당 최대 12KB(16384 레지스터 × 6비트) 고정. 원소 수와 무관하다.
3. **`PFMERGE`로 합집합을 공짜로**: 샤드별·시간대별 HLL을 하나로 합쳐 전체 유니크 수를 재계산 없이 구한다. 주간 UV = 7개 일별 HLL을 머지. 단 **교집합은 직접 못 구한다** — 포함배제로 근사는 되지만 오차가 누적된다.
4. **`PFCOUNT`는 사실 쓰기다**: 내부에 캐시된 추정값을 갱신하느라 키를 변경할 수 있다. 읽기 전용 복제본에 그대로 날리면 예상과 다르게 동작할 수 있으니 주의.
5. **작은 카운트엔 그냥 `SET`**: 원소가 수천 개 이하면 HLL의 12KB가 오히려 손해다. Redis는 sparse 표현으로 완화하지만, 정확 + 소규모면 `SADD`가 단순하고 정확하다.
6. **TTL을 꼭 걸어라**: 일별 UV 키는 며칠 뒤 필요 없다. `EXPIRE`로 자동 정리하지 않으면 HLL 키가 무한히 쌓인다 — [[redis-ttl-eviction]] 참고.

## 더 깊은 토끼굴

- 원논문: Flajolet, Fusy, Gandouet, Meunier, [HyperLogLog: the analysis of a near-optimal cardinality estimation algorithm](https://algo.inria.fr/flajolet/Publications/FlFuGaMe07.pdf) (2007) — 조화 평균과 편향 보정 유도
- Redis 공식: [HyperLogLog 데이터 타입](https://redis.io/docs/latest/develop/data-types/probabilistic/hyperloglogs/) — `PFADD`/`PFCOUNT`/`PFMERGE`와 12KB 근거
- antirez, [Redis new data structure: the HyperLogLog](http://antirez.com/news/75) — sparse/dense 표현과 구현 결정
- [[redis-bigkey]]: 정확한 `SET`으로 세다가 BigKey가 되는 바로 그 경로
- [[cache-stampede]]: eviction 캐스케이드가 부르는 다음 장애
- [[redis-lua-atomic]]: 여러 HLL 갱신을 원자적으로 묶고 싶을 때
