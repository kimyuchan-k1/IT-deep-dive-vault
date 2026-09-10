---
title: 컨슈머 한 대가 죽었다. Kafka는 알아서 넘겼고 Redis Streams는 1만 건을 붙잡고 있었다
date: 2026-09-10
day: 92
category: redis
tags: [redis-streams, kafka, consumer-group, pel, xautoclaim]
related: ["[[redis-sorted-set-queue]]", "[[dead-letter-queue]]", "[[kafka-exactly-once]]", "[[redis-rdb-vs-aof]]"]
difficulty: 3
short_text: |
  🔥 [Day 92] 컨슈머 한 대가 죽자 1만 건이 멈췄다

  오해: Redis Streams = 가벼운 Kafka
  실제: 리밸런스 없음→PEL 고아→트리밍이 원본 삭제

  📖 https://github.com/kimyuchan-k1/IT-deep-dive-vault/blob/main/posts/2026-09-10-redis-streams-vs-kafka.md
---

# 컨슈머 한 대가 죽었다. Kafka는 알아서 넘겼고 Redis Streams는 1만 건을 붙잡고 있었다

## 흔한 오해

> "Redis Streams는 가벼운 Kafka다. 컨슈머 그룹도 있고 ACK도 있고 오프셋도 있으니, 하루 수백만 건 안 되는 서비스면 Kafka 클러스터 세울 것 없이 이미 띄워둔 Redis로 끝낸다."

이 통념이 어디서 왔는지는 명확하다. API 표면이 정말 닮았다. `XGROUP CREATE`로 그룹을 만들고, `XREADGROUP`으로 읽고, `XACK`으로 확인한다. Kafka에서 하던 동작이 그대로 있다. 그래서 [[redis-sorted-set-queue]]로 큐를 만들다 재배달 로직에서 고생한 팀이 Streams로 넘어오면 "이제 Kafka와 같은 걸 얻었다"고 판단한다.

닮은 건 API고, 다른 건 **누가 컨슈머의 죽음을 감지하느냐**다. Kafka에는 그걸 하는 서버 컴포넌트가 있고 Redis에는 없다.

## 실제 원리

### 1. 진행 상태를 저장하는 자료구조가 다르다

Kafka 컨슈머 그룹의 진행 상태는 `(group, topic, partition)`당 **정수 하나**다. 오프셋 517까지 처리했다는 숫자 하나가 `__consumer_offsets`에 커밋된다. 517번이 실패해도 그룹의 상태는 여전히 숫자 하나다. 그래서 재처리는 항상 "그 지점부터 다시"이고, 개별 메시지를 건너뛸 방법이 없다.

Redis Streams는 정반대다. 그룹마다 `last-delivered-id` 하나를 갖지만, 그건 "누구에게든 한 번은 넘겼다"는 표시일 뿐이다. 실제 미확인 상태는 **PEL(Pending Entries List)** 에 **엔트리 단위 레코드**로 쌓인다. 레코드 하나에 네 가지가 들어간다.

- 엔트리 ID
- **어느 컨슈머 이름에 배달됐는가**
- 마지막 배달 시각
- 배달 횟수(delivery count)

`XACK`이 오면 그 레코드가 PEL에서 지워진다. 개별 메시지를 개별적으로 재배달할 수 있다는 뜻이고, Kafka가 못 하는 일이다. 여기까지는 Redis 쪽이 유리하다.

문제는 두 번째 필드다. **미확인 엔트리는 컨슈머 이름에 묶여 있다.**

### 2. Redis에는 그룹 코디네이터가 없다

Kafka 브로커 중 하나는 해당 그룹의 **group coordinator** 역할을 맡는다. 컨슈머는 `heartbeat.interval.ms`(기본 3초)마다 하트비트를 보내고, 코디네이터는 `session.timeout.ms`(Kafka 3.0 이후 기본 45초) 동안 안 오면 그 멤버를 그룹에서 제거하고 리밸런스를 건다. 죽은 컨슈머가 잡고 있던 파티션은 살아있는 멤버에게 재할당된다. **서버가 능동적으로 판단하고 소유권을 옮긴다.**

Redis 서버는 이걸 안 한다. 애초에 컨슈머가 "연결"이라는 개념으로 등록돼 있지 않다. 컨슈머 이름은 `XREADGROUP GROUP notify worker-3`처럼 명령에 실려 오는 **문자열**일 뿐이다. worker-3이 죽었는지, 애초에 존재한 적이 있는지 Redis는 모른다. 죽어도 PEL 레코드는 `worker-3` 이름을 그대로 달고 남는다.

살아있는 컨슈머들이 `XREADGROUP ... STREAMS mystream >`으로 계속 읽어도 그 레코드는 안 나온다. `>`는 "아직 누구에게도 안 준 신규"만 달라는 뜻이기 때문이다. 고아가 된 PEL 레코드를 되찾으려면 누군가 **명시적으로** `XCLAIM` 또는 `XAUTOCLAIM`을 호출해야 한다. 그 "누군가"를 만드는 건 애플리케이션 개발자 몫이다. 아무도 안 만들면 영원히 안 나온다.

### 3. 트리밍은 PEL을 보지 않는다

Kafka의 보존은 시간/바이트 기준 세그먼트 파일 삭제다. 소비 여부와 무관하지만, 애초에 디스크에 있고 `retention.ms`가 기본 7일이라 여유가 크다.

Redis Stream은 RAM이다. 그래서 `XADD mystream MAXLEN ~ 100000 * ...`처럼 트리밍을 건다. 여기가 함정이다. **트리밍은 그 엔트리가 어느 PEL에 미확인으로 걸려 있는지 확인하지 않는다.** 잘라내야 할 범위면 그냥 지운다.

결과는 PEL에 ID는 남았는데 원본 데이터가 없는 상태다. Redis 7.0 이후 `XAUTOCLAIM`은 이런 항목을 세 번째 응답 배열에 "삭제된 ID"로 돌려주면서 PEL에서 제거한다. 정리는 되지만 **내용은 복구되지 않는다.** 미확인 메시지가 조용히 증발한 것이다.

## 현장 시나리오

주문 알림을 보내는 워커. `orders:events` 스트림, 컨슈머 그룹 `notify`, Pod 4개가 각자 Pod 이름을 컨슈머 이름으로 써서 `XREADGROUP`을 돈다. `XADD`에는 `MAXLEN ~ 100000`이 걸려 있고, 트래픽은 초당 40건이라 대략 40분치가 남는 설정이다.

새벽 2시, `worker-3`이 OOMKilled로 재시작된다. 새로 뜬 Pod은 이름이 `worker-3-7f9c2`로 바뀐다. 이 신규 이름으로 `>` 조회를 시작하니 신규 메시지는 문제없이 흐른다. 대시보드의 lag 지표는 `XLEN`에서 마지막 배달 ID 이후 개수를 뺀 값이라 **0을 가리킨다.** 죽은 이름의 PEL은 그 계산식에 들어가지 않는다.

8시간 뒤 아침, "결제는 됐는데 알림이 안 왔다"는 문의가 들어온다. `XPENDING orders:events notify`를 치니 **12,400건**이 `worker-3` 소유로 잡혀 있다. 그중 `XAUTOCLAIM`으로 되찾아지는 건 최근 40분치뿐이다. 나머지 **약 11,000건은 MAXLEN 트리밍이 이미 원본을 지웠다.** PEL에 ID만 남은 껍데기다.

인과 사슬: Pod 재시작으로 컨슈머 이름 변경 → 옛 이름의 PEL 고아화 → 코디네이터 부재로 재할당 없음 → lag 지표가 PEL을 안 봐서 무경보 → 8시간 방치 → MAXLEN 트리밍이 원본 삭제 → 복구 불가.

Kafka였다면 45초 만에 `session.timeout.ms`가 만료되고 해당 파티션이 다른 멤버로 넘어갔다. 알림은 몇 분 늦었을 뿐이다.

## 실무 적용 포인트

1. **컨슈머 이름을 휘발성 값으로 쓰지 마라.** Pod 이름, 랜덤 UUID, 호스트명은 재시작마다 PEL 고아를 하나씩 새로 만든다. StatefulSet ordinal이나 고정 슬롯(`worker-0`~`worker-N`)을 써서 재시작한 Pod이 같은 이름으로 돌아오게 한다. 같은 이름이면 `XREADGROUP ... STREAMS mystream 0`으로 자기 PEL부터 읽고 시작할 수 있다.

2. **회수 워커를 반드시 별도로 돌려라.** `XAUTOCLAIM orders:events notify recovery <min-idle-time> 0 COUNT 100`을 주기 실행한다. `min-idle-time`은 정상 처리 p99의 2~3배로 잡는다 — 처리가 5초 걸리면 `15000`. 짧으면 살아있는 컨슈머가 처리 중인 메시지를 뺏어가 중복 처리가 된다.

3. **lag 지표를 `XLEN` 기준으로 만들지 마라.** Redis 7.0의 `XINFO GROUP orders:events`가 주는 `lag`, `entries-read`와 `XPENDING` 개수를 **둘 다** 본다. PEL 개수에 별도 알람을 건다 — 예: 5분 이상 1,000건 초과면 경보.

4. **보존 창을 최악 복구 시간보다 길게 잡는다.** `MAXLEN ~ 100000`이 40분이면 새벽 장애를 못 버틴다. 초당 유입량 × 목표 보존 시간으로 역산해서 최소 24시간치를 잡거나, 시간 기준이 필요하면 `XADD ... MINID ~ <24시간 전 밀리초 타임스탬프>`를 쓴다. `~`는 매크로 노드 단위 근사 트리밍이라 실제로는 지정값보다 더 오래 남는다.

5. **delivery count로 DLQ 컷을 건다.** `XPENDING`이 돌려주는 배달 횟수가 임계(예: 5회)를 넘으면 별도 스트림으로 옮기고 `XACK`한다. 안 하면 독약 메시지 하나가 회수 워커를 무한히 돈다. [[dead-letter-queue]]

6. **메모리 회계를 잊지 마라.** 스트림은 listpack 매크로 노드로 저장되고 `stream-node-max-entries`(기본 100), `stream-node-max-bytes`(기본 4096)가 노드 크기를 정한다. PEL도 RAM을 먹는다. 그리고 이 전부가 RDB/AOF에 통째로 실린다 — [[redis-rdb-vs-aof]]

## 더 깊은 토끼굴

선택 기준은 한 줄이다. **컨슈머 신원이 불안정할수록 Redis Streams의 운영 비용이 올라간다.** 코디네이터가 없다는 사실을 애플리케이션 코드가 대신 메꿔야 하기 때문이다. 반대로 컨슈머가 고정되고 개별 재배달이 중요하면 Streams가 훨씬 단순하다.

- [[redis-sorted-set-queue]] — Streams 이전에 큐를 만들던 방식과 그 한계
- [[dead-letter-queue]] — 배달 횟수 임계와 독약 메시지 처리
- [[kafka-exactly-once]] — 오프셋 기반 모델에서 중복을 막는 방법
- [[redis-rdb-vs-aof]] — 스트림과 PEL이 영속화되는 경로
- [[backpressure-patterns]] — 회수 워커가 밀릴 때의 흐름 제어

출처:
- Redis Streams 공식 문서 (컨슈머 그룹과 PEL): https://redis.io/docs/latest/develop/data-types/streams/
- `XAUTOCLAIM` 명령 레퍼런스 (삭제된 엔트리 반환 동작): https://redis.io/docs/latest/commands/xautoclaim/
- Kafka 컨슈머 설정 (`session.timeout.ms`, `heartbeat.interval.ms` 기본값): https://kafka.apache.org/documentation/#consumerconfigs
