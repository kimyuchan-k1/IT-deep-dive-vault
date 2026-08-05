---
title: 캐시 히트율 99%인데 DB가 무너졌다
date: 2026-08-05
day: 59
category: redis
tags: [cache-stampede, thundering-herd, xfetch, per, singleflight, stale-while-revalidate]
related: ["[[cache-aside-vs-write-through]]", "[[redis-ttl-eviction]]", "[[redis-lua-atomic]]", "[[circuit-breaker]]", "[[bulkhead-pattern]]"]
difficulty: 4
short_text: |
  🔥 [Day 59] 캐시 히트율 99%인데 DB가 죽었다

  오해: 미스 1%는 고르게 흩어진다
  실제: 만료 순간 정렬→재계산 내내 전부 미스

  "1만 건이 같은 쿼리를 던졌다"

  📖 https://github.com/kimyuchan-k1/IT-deep-dive-vault/blob/main/posts/2026-08-05-cache-stampede.md
---

# 캐시 히트율 99%인데 DB가 무너졌다

대시보드의 캐시 히트율은 사고가 나는 동안에도 99%였다. DB로 내려간 쿼리는 전체의 1%가 맞았다. 그 1%가 한꺼번에 갔을 뿐이다.

## 흔한 오해

> "히트율 99%면 DB는 1%만 받는 거잖아. 초당 1만 건 조회면 DB는 100건. 그 정도는 여유지."

이 계산은 캐시 용량을 잡을 때 쓰는 공식 그대로다. 그래서 캐시 튜닝 글도, 용량 산정 시트도 히트율 하나로 부하를 환산한다.

산술 자체는 맞다. 하루를 통으로 놓고 평균 내면 DB가 받는 건 정확히 1%다. **틀린 건 미스가 시간축에 고르게 흩어져 있다는 가정이다.** 미스는 무작위로 발생하지 않는다. TTL이 끝나는 그 한 시점에 정렬돼서 발생한다.

## 실제 원리

### 미스 창의 폭은 히트율이 아니라 재계산 시간이 정한다

핫 키 하나가 초당 10,000번 조회되고, 그 값을 다시 만드는 데 400ms가 걸린다고 하자. TTL이 끝나는 순간부터 새 값이 캐시에 들어갈 때까지 400ms 동안, **그 키를 찾는 모든 요청이 미스다.** 10,000 × 0.4 = 4,000건이 같은 쿼리를 동시에 DB로 던진다.

여기가 핵심이다. 이 4,000이라는 숫자에 히트율은 들어가지 않는다. 들어가는 건 **조회 빈도와 재계산 지연, 두 개뿐**이다. 히트율을 99%에서 99.9%로 올려도 미스 창의 폭은 그대로다. TTL을 늘리면 창이 오는 빈도만 줄어들 뿐, 한 번 열렸을 때의 깊이는 똑같다.

### 이 구조는 스스로를 증폭시킨다

4,000건이 동시에 들어오면 DB의 재계산이 400ms로 끝나지 않는다. 커넥션 풀이 고갈되고 쿼리가 큐에서 대기하면서 6초가 된다. 그러면 미스 창이 400ms가 아니라 6초로 벌어지고, 그 6초 동안 60,000건이 미스가 된다.

미스가 늘면 재계산이 느려지고, 재계산이 느려지면 미스 창이 넓어진다. 양의 피드백이다. 임계점을 넘는 순간 캐시가 있는 상태에서 없는 상태로 계단식으로 떨어지고, 부하를 걷어내기 전까지 스스로 회복하지 못한다.

### 락은 문제를 옮기지, 없애지 않는다

가장 먼저 나오는 대안이 뮤텍스다. `SET lock:key 1 NX PX 3000`으로 한 요청만 재계산하게 하고 나머지는 기다린다. DB로 가는 쿼리는 1건이 된다.

그런데 나머지 9,999건을 **기다리게** 만들면 그 요청들이 애플리케이션 스레드와 커넥션을 400ms 동안 붙잡는다. 톰캣 스레드풀 200개짜리 인스턴스는 즉시 고갈된다. DB 과부하를 앱 티어 스레드 고갈로 바꾼 것이다 — [[bulkhead-pattern]]이 막으려는 바로 그 형태다. 락 TTL을 재계산 p99보다 짧게 잡으면 락이 만료되면서 두 번째 스탬피드까지 열린다.

락이 쓸모없다는 게 아니다. **대기자가 무엇을 받느냐가 갈림길이다.** 기다리면 스레드 고갈, 낡은 값을 즉시 돌려주면 해결이다.

### XFetch — 만료를 확률로 흩뿌린다

2015년 VLDB 논문 "Optimal Probabilistic Cache Stampede Prevention"이 제안한 방법이다. 만료되기 **전에**, 각 요청이 독립적으로 주사위를 굴려 재계산 여부를 정한다.

캐시에 값과 함께 두 가지를 더 저장한다. `delta`(지난번 재계산에 걸린 시간)와 `expiry`(논리적 만료 시각). 조회할 때마다 이 판정을 한다.

```
now - delta * beta * ln(rand()) >= expiry   →  지금 재계산
```

`rand()`는 (0,1) 균등난수라 `ln(rand())`는 음수이고, 앞의 마이너스가 붙어 전체는 양수가 된다. 만료가 가까울수록 이 부등식이 참이 될 확률이 지수적으로 커진다. 만료 훨씬 전에는 거의 아무도 재계산하지 않고, 만료 직전에는 누군가는 반드시 먼저 굴린다.

두 가지가 중요하다. 첫째, **`delta`가 곱해져 있다.** 재계산이 비싼 키일수록 더 일찍 시작한다. 둘째, **판정이 요청마다 독립적이다.** 중앙 조율도 락도 없어 동기화 자체가 생기지 않는다. `beta`는 공격성 손잡이로, 기본 1.0에서 올리면 더 일찍 재계산한다.

정상 동작이라면 값은 만료 전에 갱신되므로 **미스 창이 아예 열리지 않는다.** 락은 창이 열린 뒤 피해를 줄이고, XFetch는 창이 열리지 않게 한다.

### Facebook의 lease — 미스에 토큰을 발급한다

Scaling Memcache at Facebook(NSDI 2013)은 다른 각도로 푼다. memcached가 미스를 반환할 때 64비트 lease 토큰을 같이 주고, 그 토큰을 가진 클라이언트만 값을 쓸 수 있다. 같은 키의 lease는 10초에 한 번만 발급되므로 재계산 주체가 자연히 하나로 좁혀진다.

덤으로 하나를 더 잡는다. 느린 재계산이 뒤늦게 돌아와 이미 갱신된 최신 값을 덮어쓰는 stale set 문제다. 그 사이 무효화가 있었다면 토큰이 죽어 쓰기가 거부된다.

## 현장 시나리오

커머스 메인의 추천 블록. 캐시 키 하나에 응답 전체를 담았고, 값 하나를 만드는 데 랭킹 쿼리 + 재고 조인 + 개인화 머지로 900ms가 걸렸다. TTL 300초, 조회 12,000 RPS, 히트율 99.3%. 8개월간 조용했다.

깨진 건 프로모션 시작 시각이었다. 배포 파이프라인이 릴리스마다 캐시 네임스페이스를 flush했고, 그날 배포가 정각 트래픽 피크와 겹쳤다.

인과는 이랬다. flush로 키가 사라진다 → 900ms 동안 10,800건이 전부 미스 → 전부 같은 랭킹 쿼리를 DB로 던진다 → 커넥션 풀 200개 고갈, 나머지는 대기 큐 → 재계산이 900ms에서 7초로 늘어난다 → **미스 창이 7초로 벌어지면서 84,000건이 추가로 미스** → 앱 인스턴스 스레드풀도 대기 요청으로 가득 찬다 → 헬스체크 타임아웃으로 인스턴스가 LB에서 빠지고, 남은 인스턴스에 같은 트래픽이 다시 몰린다.

11분간 5xx 4.1%. 히트율 그래프는 그 시간에도 99%를 찍고 있었다. 미스가 1%였던 게 아니라, 미스 8만 건과 히트 800만 건을 5분 버킷으로 평균 낸 값이 1%였을 뿐이다.

고친 건 세 가지다. 배포 시 flush를 버전 프리픽스 점진 교체로 바꿨고, 인스턴스 내부에 singleflight를 넣었고, XFetch를 `beta=1.0`으로 넣어 실측 `delta` 900ms 기준 만료 2~3초 전에 한 요청이 먼저 갱신하게 했다. 재현 테스트에서 같은 조건의 DB 피크 쿼리는 10,800건에서 8건이 됐다.

## 실무 적용 포인트

1. **TTL에 jitter를 넣어라.** `TTL = 300 + rand(-30, +30)`처럼 ±10%면 충분하다. 단, 이건 **배치 워밍으로 수만 개 키의 만료가 같은 초에 정렬된 경우**를 푸는 도구다. 위 사고처럼 핫 키 **하나**가 문제일 때는 jitter가 아무것도 해결하지 않는다.

2. **XFetch는 캐시에 필드 3개를 저장하는 것으로 시작한다.** `HSET k v value, v delta, v expiry` 형태로 값·재계산 소요·논리 만료를 함께 넣고, Redis 키의 물리 TTL은 논리 `expiry`보다 넉넉히(예: +60초) 길게 잡는다. 물리 TTL이 먼저 끝나면 낡은 값도 없어져 원래 문제로 돌아간다. 판정과 갱신은 [[redis-lua-atomic]]으로 묶는다.

3. **분산 락보다 프로세스 내 singleflight를 먼저 넣어라.** Go의 `golang.org/x/sync/singleflight`, JVM이면 Caffeine `LoadingCache`가 같은 키의 동시 로드를 하나로 합친다. 네트워크 왕복이 없고 DB에는 인스턴스 수만큼만 간다. 8대면 10,800건이 8건이다.

4. **분산 락을 쓴다면 대기자를 절대 블로킹하지 마라.** `SET lock:{key} {uuid} NX PX {재계산 p99 × 2}`로 잡고, 락 획득에 실패한 요청은 **즉시 낡은 값을 반환**한다. 해제는 uuid를 비교하는 Lua로 해야 남의 락을 지우지 않는다.

5. **stale-while-revalidate를 켜라.** HTTP 캐시 계층이라면 RFC 5861의 `stale-while-revalidate`와 `stale-if-error`가 표준으로 있고, nginx는 `proxy_cache_use_stale updating`과 `proxy_cache_lock on`이 같은 일을 한다. "5초 낡은 데이터"와 "500 에러" 중 무엇을 줄지는 대부분 답이 정해져 있다.

6. **배포와 무효화가 스탬피드를 만들지 않게 하라.** 캐시 flush 대신 `v12:` 같은 버전 프리픽스를 올려 점진 교체하고, 무효화는 삭제(`DEL`) 대신 갱신으로 처리한다. 삭제는 그 순간 미스 창을 여는 행위다. 존재하지 않는 키로 들어오는 트래픽(cache penetration)은 별개 문제이므로 짧은 TTL의 negative caching으로 따로 막는다.

## 더 깊은 토끼굴

- [[cache-aside-vs-write-through]] — 무효화를 삭제로 할지 갱신으로 할지가 갈리는 지점
- [[redis-ttl-eviction]] — TTL이 끝나기 전에 LRU가 먼저 키를 지우는 경우
- [[redis-lua-atomic]] — XFetch 판정과 락 해제를 원자화하는 방법
- [[circuit-breaker]] — 재계산 자체가 무너졌을 때 증폭 루프를 끊는 쪽
- [[bulkhead-pattern]] — 대기 요청이 스레드풀 전체를 삼키지 않게 나누기
- [[redis-bigkey]] — 응답 전체를 키 하나에 담을 때 따라오는 다른 비용

**출처**

- A. Vattani, F. Chierichetti, K. Lowenstein, *Optimal Probabilistic Cache Stampede Prevention*, VLDB 2015 — https://cseweb.ucsd.edu/~avattani/papers/cache_stampede.pdf
- R. Nishtala et al., *Scaling Memcache at Facebook*, NSDI 2013 (lease 메커니즘 §3.2.1) — https://www.usenix.org/system/files/conference/nsdi13/nsdi13-final170.pdf
- RFC 5861, HTTP Cache-Control Extensions for Stale Content — https://www.rfc-editor.org/rfc/rfc5861.html
- nginx docs, `proxy_cache_lock` / `proxy_cache_use_stale` — https://nginx.org/en/docs/http/ngx_http_proxy_module.html#proxy_cache_lock
- Go, `singleflight` 패키지 — https://pkg.go.dev/golang.org/x/sync/singleflight

정리하면, 히트율은 **어떤 요청이 캐시를 찾았는가**를 세는 지표지 **미스가 언제 몰리는가**는 세지 않는다. 이 사고에서 DB가 받은 총 쿼리 수는 하루 평균으로 보면 평소와 다르지 않았다. 다만 그중 8만 건이 같은 7초 안에 도착했고, 그걸 세는 그래프가 대시보드에 없었다.
