---
title: Redis BigKey가 클러스터 전체를 마비시키는 이유
date: 2026-06-01
day: 1
category: redis
tags: [cache, performance, distributed, redis-cluster]
related: ["[[redis-hotkey]]", "[[redis-cluster-slot]]", "[[lazy-freeing]]", "[[cache-stampede]]"]
difficulty: 3
source_inspiration: youtube/gTCgEFnoi6U
short_text: "BigKey는 한 노드 문제로 보이지만, 실은 클러스터 전체를 마비시킨다. 단일 명령이 수 초간 이벤트 루프를 잡으면 어떤 일이 벌어질까?"
---

# Redis BigKey가 클러스터 전체를 마비시키는 이유

## 흔한 오해

"BigKey는 그 키가 있는 한 노드에서만 느려지는 문제 아닌가?"

대부분 그렇게 안다. 그래서 "그 키 안 쓰는 다른 서비스는 괜찮을 거야"라고 생각한다. 그리고 [[redis-hotkey]]가 더 위험하다고 말한다 — 트래픽이 한 노드에 쏠리니까.

**둘 다 맞는데, 절반만 맞다.** BigKey는 단일 노드만의 문제가 아니다. Redis의 두 가지 핵심 설계 — 싱글 스레드 이벤트 루프와 [[redis-cluster-slot]] 토폴로지 — 가 만나면 BigKey는 클러스터 전체를 사실상 멈춘다.

## 실제 원리

### Redis는 싱글 스레드다

Redis 6 이후 I/O 멀티스레딩이 도입됐지만, **명령 처리는 여전히 싱글 스레드**다. 즉:

- `HGETALL hash:huge` 같은 명령 하나가 100ms 걸리면, 그 100ms 동안 **그 노드의 다른 모든 명령은 대기**
- TTL 만료 후 `DEL` 한 번이 키 크기에 비례해서 블로킹

1MB짜리 hash를 `HGETALL` 하면 직렬화·네트워크 전송까지 수십~수백 ms. 10MB면 초 단위.

### 그래서 클러스터 전체로 어떻게 번지나

여기가 핵심이다:

1. **클라이언트 풀 고갈**: 애플리케이션은 보통 커넥션 풀을 노드별로 나누지 않고 클러스터 전체 풀로 운영. 한 노드가 느려지면 그 노드로 가야 하는 요청이 풀을 점유하고, 풀이 비면 **다른 정상 노드 요청도 같이 대기**.

2. **헬스체크 타임아웃**: 클러스터 노드 간 PING/PONG이 BigKey 명령 뒤에 줄 서서 늦게 처리됨. `cluster-node-timeout` 초과하면 **정상 노드를 fail로 오판**하고 페일오버 시도. 페일오버 중에는 슬롯 이동으로 또 클라이언트가 헷갈림.

3. **마스터-레플리카 복제 지연**: BigKey의 SET/DEL이 복제 버퍼를 채우면서 **다른 키의 복제도 지연**. 레플리카가 늦으면 읽기 분산이 깨지고 마스터에 트래픽이 더 쏠림.

4. **AOF rewrite/RDB snapshot 시간 증가**: BigKey가 많을수록 백업 시 `fork()` 메모리 카피온라이트 페이지가 커지고, 다른 쓰기가 지연됨.

요약: **BigKey 하나 = 그 노드 블로킹 → 풀 고갈 → 다른 노드까지 응답 지연 → 헬스체크 실패 → 잘못된 페일오버 → 캐스케이드.**

## 현장 시나리오

게임 서버에서 길드 채팅 로그를 Redis hash에 쌓는 구조였다. 길드별 키 하나에 메시지를 HSET으로 누적. 인기 길드는 hash 크기가 5MB까지 자랐다.

평소 잘 돌아갔다. 그러다 어느 날 운영자가 그 길드를 해체하면서 `DEL guild:chat:12345`를 실행. 그 순간:

- 5MB 메모리 해제에 약 200ms 블로킹
- 그동안 클러스터 6개 노드 전부 PING/PONG 지연
- `cluster-node-timeout` 150ms로 설정돼 있어서 다른 정상 마스터가 fail로 판정됨
- 페일오버 + 슬롯 재분배 시작
- 그 사이 들어온 채팅/매칭 요청 수만 건이 타임아웃
- 1분간 전 게임 서비스 응답 실패

원인 조사 보고서 한 줄: **"DEL one key."**

## 실무 적용 포인트

1. **BigKey 발견**: `redis-cli --bigkeys` 로 주기 스캔. 1MB 넘으면 알람.
2. **DEL 대신 UNLINK** ([[lazy-freeing]]): UNLINK는 키 해제를 백그라운드 스레드로 넘긴다. Redis 4 이후 기본 활용.
3. **자료구조 분할**: 5MB hash → 100개의 50KB hash로 샤딩 (예: `guild:chat:12345:0001` ~ `0050`).
4. **HGETALL 금지령**: HSCAN으로 커서 기반 페치. 100개씩 끊어 읽기.
5. **`cluster-node-timeout` 여유롭게**: 150ms는 너무 빡빡. 500ms~1s 권장 (트레이드오프: 진짜 장애 감지가 늦어짐).
6. **`hash-max-ziplist-entries` 모니터링**: ziplist 임계 넘으면 hashtable로 전환되며 메모리/CPU 패턴 바뀜.

## 더 깊은 토끼굴

- Redis 공식: [Latency caused by big keys](https://redis.io/docs/latest/operate/oss_and_stack/management/optimization/latency/) — 공식 가이드
- [[lazy-freeing]]: UNLINK의 내부 — 왜 Redis 4부터 추가됐나
- [[redis-cluster-slot]]: 16384 슬롯이라는 숫자의 의미
- [[redis-hotkey]]: BigKey와 동시에 발생하면 진짜 최악
- 영감: 코딩하는기술사, "If the key design is poor, Redis will crash" (https://www.youtube.com/watch?v=gTCgEFnoi6U)
