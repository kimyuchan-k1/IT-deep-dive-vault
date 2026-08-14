---
title: 락은 정상 동작했는데 두 워커가 같은 정산을 돌렸다
date: 2026-08-14
day: 68
category: distributed
tags: [distributed-lock, redlock, fencing-token, gc-pause, consensus]
related: ["[[quorum-rw-n]]", "[[raft-easier-than-paxos]]", "[[idempotency-key]]", "[[redis-lua-atomic]]", "[[cap-theorem-real-meaning]]"]
difficulty: 4
short_text: |
  🔥 [Day 68] 락 하나를 두 워커가 동시에 잡았다

  오해: SET NX PX면 안전
  실제: GC 정지→TTL 만료→중복 획득

  "과반 5노드도 fencing 없인 못 막는다"

  📖 https://github.com/kimyuchan-k1/IT-deep-dive-vault/blob/main/posts/2026-08-14-redlock-debate.md
---

# 락은 정상 동작했는데 두 워커가 같은 정산을 돌렸다

## 흔한 오해

> "Redis에 `SET lock:job {uuid} NX PX 30000`을 걸면 분산 락이다. 한 대가 불안하면 Redlock으로 5노드 중 3노드에서 잡으면 되고, 그러면 두 프로세스가 동시에 임계 구역에 들어올 일은 없다."

이 통념은 근거가 있다. Redis 공식 문서에 분산 락이 패턴으로 실려 있고, Redisson이나 node-redlock 같은 라이브러리는 이걸 메서드 한 줄로 감싸준다. 그래서 분산 락은 라이브러리 선택 문제로 취급된다.

명령은 맞다. 틀린 건 그다음 결론이다. `NX`는 원자적이고 `PX`는 데드락을 막지만, 그 둘을 합쳐도 상호배제는 나오지 않는다.

## 실제 원리

### 락을 왜 잡는지부터 갈린다

Kleppmann이 논쟁의 출발점에서 그은 선이 이거다. 락의 목적은 **효율(efficiency)**이거나 **정확성(correctness)**이다.

효율이면 락이 가끔 깨져도 같은 작업을 두 번 하는 것뿐이다. 썸네일을 두 번 만들고 API 요금을 두 번 낸다. 정확성이면 한 번 깨지는 순간 데이터가 파손되거나 돈이 두 번 나간다. 여기서는 "거의 항상 맞다"가 아무 의미가 없다. 그리고 Redis 락은 전자를 위한 도구다.

### 상호배제를 깨는 건 네트워크가 아니라 정지다

락이 깨지는 경로를 물으면 대개 네트워크 분단을 말한다. 실제로 더 자주 깨는 건 클라이언트 자신이 멈추는 순간이다.

- JVM stop-the-world GC
- 페이지 폴트와 스왑
- CPU 스케줄러 preemption, 컨테이너 CFS quota throttling
- VM 라이브 마이그레이션, 스냅샷에서의 복원

순서는 이렇게 흐른다. 클라이언트 A가 TTL 30초로 락을 잡는다. 작업 도중 45초 멈춘다. TTL이 만료되고 Redis는 키를 지운다. 클라이언트 B가 같은 락을 잡고 임계 구역에 들어간다. A가 깨어난다. **A는 자기가 아직 락 주인이라고 믿고, 하던 쓰기를 이어서 한다.**

여기가 핵심이다. Redis는 이 시점에 A를 막을 수단이 없다. A가 스토리지로 보내는 쓰기 요청에는 락 정보가 붙어 있지 않고, 스토리지는 그게 만료된 락에서 나온 요청인지 알 방법이 없다. 락은 정상 동작했다. 만료도, 재획득도 설계대로였다.

근본 원인은 시간이다. TTL은 "작업이 이 시간 안에 끝난다"는 가정인데, 비동기 네트워크와 정지 가능한 프로세스에서 그 상한은 존재하지 않는다. 안전성(safety)을 타이밍 가정 위에 세운 구조다.

### 노드를 5개로 늘려도 같은 자리에서 깨진다

Redlock의 과반은 다른 문제를 푼다. Redis 노드 하나가 죽어도 락이 유지되느냐 — 가용성 축이다. 클라이언트 정지는 노드 수와 무관하므로, 위 시나리오는 5노드에서도 그대로 재현된다.

게다가 노드를 늘리면서 가정이 하나 추가된다. **시계다.** Redlock은 각 노드에서 락을 잡는 데 걸린 시간을 재서 남은 유효기간을 계산하고, 그 계산은 노드들의 시계가 비슷한 속도로 흐른다는 전제 위에 있다. NTP의 step 조정, VM 스냅샷 복원, 수동 시각 변경이 끼어들면 여러 노드의 키가 예정보다 일찍 만료되고, 과반이 두 번 성립할 수 있다.

지속성 축도 있다. Redis는 기본 설정에서 매 쓰기를 fsync하지 않는다. 락을 잡아준 노드 하나가 재시작하면 그 키를 잃은 채 돌아와 다시 과반의 한 표가 된다. antirez의 대응은 **delayed restart** — 재시작한 노드는 최대 TTL만큼 기다린 뒤 복귀한다. 알고리즘이 아니라 운영 규약이고, 그만큼 가용성을 깎아 안전성을 사는 거래다.

### 진짜 해법은 fencing token이다

락 서비스가 락을 줄 때 **단조 증가하는 번호**를 함께 준다. A는 33번, B는 34번을 받는다. 스토리지는 지금까지 본 최대 토큰을 기억하고, 그보다 작은 번호를 달고 온 쓰기를 거절한다. A가 늦게 깨어나 33번으로 써도 34를 본 스토리지가 튕겨낸다. 안전성의 근거가 **시간 가정에서 순서 가정으로** 옮겨간다.

Redis는 이 토큰을 안전하게 만들지 못한다. `INCR`은 단일 마스터에서는 단조지만, 비동기 복제 상태에서 페일오버가 일어나면 값이 되돌아갈 수 있다. ZooKeeper는 zxid와 znode 버전을, etcd는 `mod_revision`과 lease를 합의 로그 위에서 발급한다. 단조성이 공짜가 아니라 합의의 산물이라는 뜻이고, 그래서 [[raft-easier-than-paxos]]가 여기서 다시 나온다.

antirez의 반론도 같이 봐야 공정하다. 토큰을 검사해줄 스토리지가 없으면 fencing은 실용성이 없고, 많은 경우 조건부 쓰기(CAS)로 같은 효과를 낸다는 지적이다. 두 입장이 남긴 합의는 이거다 — **락의 목적이 정확성이면 락 하나로 끝나지 않고, 아래쪽에서 한 번 더 거절할 수 있어야 한다.**

### 그리고 대부분은 여기까지 오기 전에 틀린다

해제를 `DEL lock:job`으로 하면 이미 만료돼 남이 새로 잡은 락을 지운다. 값 비교와 삭제가 원자적이어야 하고, 그래서 Lua가 필요하다 — [[redis-lua-atomic]] 그대로다. Redisson류의 watchdog(락 자동 연장)은 TTL 만료를 막아주지만, 프로세스가 GC로 멈추면 갱신 스레드도 같이 멈춘다. 정지 구간에서 무력한 건 변하지 않는다.

## 현장 시나리오

가맹점 정산 배치. 워커 8대가 매일 새벽 같은 락을 두고 경쟁한다. `SET settle:2026-08-13 {worker-id} NX PX 30000`, 락을 잡은 한 대가 전날 거래를 읽어 가맹점 대금 송금 API를 호출한다. 평소 대상 220만 건, 소요 18초. TTL 30초는 넉넉해 보였다.

그날은 프로모션 정산이 겹쳐 대상이 340만 건이 됐다. 워커 1의 힙이 모자라 Full GC가 연달아 걸렸고, 누적 정지 시간이 41초였다. 30초에 TTL이 만료됐다. 워커 3이 같은 키를 `NX`로 잡았고, 자기가 유일한 소유자라고 로그를 남기고 정산을 시작했다. 41초에 워커 1이 GC에서 깨어나 중단됐던 송금 루프를 이어갔다. 두 워커가 겹친 11초 동안 1,900건의 송금 요청이 두 번 나갔다. 송금 API는 요청 식별자를 받지 않는 설계라 두 번 다 정상 처리했다.

탐지가 늦은 이유가 이 사고의 진짜 얼굴이다. **락 획득 실패 로그가 한 줄도 없었다.** 워커 1과 워커 3 모두 "lock acquired"를 정상적으로 남겼고, Redis의 `INFO`에도 이상 신호가 없었다. 이중 송금은 다음 날 가맹점 문의로 발견됐다.

원인 한 줄: 락 TTL은 프로세스가 30초 안에 깨어난다는 약속이었고, GC는 그 약속의 당사자가 아니었다.

## 실무 적용 포인트

1. **이 락이 지키는 게 효율인지 정확성인지 코드 주석에 한 줄로 박아라.** 효율이면 Redis 단일 인스턴스 락으로 충분하다. 정확성이면 락만으로 끝나지 않으니 3번으로 간다. 이 구분을 안 적으면 6개월 뒤 누군가 효율용 락에 송금을 얹는다.

2. **단일 인스턴스 락도 최소 형태를 지켜라.** 획득은 `SET key {uuid} NX PX 30000` 한 명령으로, 해제는 값을 비교한 뒤 삭제하는 Lua로 한다(`redis.call("get",KEYS[1]) == ARGV[1] and redis.call("del",KEYS[1])`). `SETNX` + `EXPIRE`로 쪼개면 사이에서 죽었을 때 TTL 없는 영구 락이 남는다.

3. **정확성이 목적이면 fencing token을 발급하는 곳에서 락을 받아라.** etcd의 `mod_revision`/lease, ZooKeeper의 zxid가 합의 로그 위에서 단조성을 보장한다. 토큰을 실제로 검사하는 주체는 락 서비스가 아니라 스토리지다 — `UPDATE settlement SET ... WHERE fence_token < :token`. 검사하는 쪽이 없으면 토큰은 장식이다.

4. **토큰을 받아줄 스토리지가 없으면 멱등성으로 막아라.** 요청마다 고유 키를 보내고 서버가 유니크 제약으로 거절하게 한다 — [[idempotency-key]]. 위 사례에서 송금 API가 요청 식별자만 받았어도 이중 송금은 0건이었다.

5. **TTL은 작업 시간의 상한이 아니라 장애 감지 시간으로 잡아라.** 30분짜리 작업을 락 하나로 붙잡지 말고 청크로 쪼개, 청크마다 소유권을 재확인하고 조건부 쓰기를 한다. 정지가 일어나도 손실 구간이 청크 하나로 제한된다.

6. **watchdog 갱신을 믿을 거면 최대 정지 시간을 관측해라.** JVM은 `-Xlog:gc*`와 `jvm.gc.pause` p99, 컨테이너는 `container_cpu_cfs_throttled_seconds_total`을 본다. 관측된 최대 정지 시간이 TTL보다 크면 그 락은 이미 깨진 상태로 운영 중인 것이다.

7. **Redlock을 선택했다면 전제 조건까지 같이 운영해라.** 노드 시계는 NTP slew만 허용하고 step 조정 금지, 재시작한 노드는 최대 TTL만큼 delayed restart, 노드 간 클럭 드리프트를 지표로 올린다. 전제를 운영하지 않는 Redlock은 노드 수만 5배인 단일 인스턴스 락이다.

8. **락 획득 로그에 소유자 uuid, 토큰, 만료 시각을 같이 남겨라.** 위 사고에서 겹침을 확인하는 데 하루가 걸린 이유가 이 세 값이 없어서였다.

## 더 깊은 토끼굴

- [[quorum-rw-n]] — 과반이 보장하는 것과 보장하지 않는 것
- [[raft-easier-than-paxos]] — 단조 증가 토큰이 왜 합의를 요구하는가
- [[idempotency-key]] — 락이 깨진 뒤 마지막으로 막아주는 층
- [[redis-lua-atomic]] — 비교 후 삭제를 원자적으로 만드는 방법
- [[cap-theorem-real-meaning]] — 락 서비스의 가용성과 안전성 트레이드오프
- [[two-generals]] — "락을 놓았다"는 통보가 도착했는지 알 수 없는 문제

**출처**
- Martin Kleppmann, "How to do distributed locking" (2016-02-08) — https://martin.kleppmann.com/2016/02/08/how-to-do-distributed-locking.html (효율/정확성 구분, GC 정지로 인한 락 침범, fencing token 제안)
- antirez, "Is Redlock safe?" — http://antirez.com/news/101 (Redlock 저자의 반론, delayed restart, 시계 가정)
- Redis 공식 문서, "Distributed Locks with Redis" — https://redis.io/docs/latest/develop/use-cases/patterns/distributed-locks/ (SET NX PX 패턴, Lua 해제 스크립트, Redlock 원문)
- Apache ZooKeeper, "ZooKeeper Recipes and Solutions" — https://zookeeper.apache.org/doc/current/recipes.html (znode 시퀀스 번호 기반 락 레시피)
