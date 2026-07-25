---
title: 컨슈머가 못 따라오자 버퍼를 키웠다, 그리고 OOM으로 죽었다
date: 2026-07-25
day: 49
category: kafka
tags: [backpressure, flow-control, reactive-streams, load-shedding, messaging]
related: ["[[circuit-breaker]]", "[[dead-letter-queue]]", "[[rate-limit-token-bucket]]", "[[connection-pool-sizing]]", "[[retry-exponential-backoff-jitter]]"]
difficulty: 4
short_text: |
  ⚠️ [Day 49] 버퍼를 키웠더니 OOM으로 죽었다
  오해: 밀리면 버퍼 늘리면 된다
  실제: 무한 버퍼는 지연된 죽음, 답은 생산자에게 "멈춰"를 되돌리는 것
  📖 https://github.com/kimyuchan-k1/IT-deep-dive-vault/blob/main/posts/2026-07-25-backpressure-patterns.md
---

# 컨슈머가 못 따라오자 버퍼를 키웠다, 그리고 OOM으로 죽었다

생산자가 초당 1만 건을 밀어 넣는데 컨슈머는 초당 6천 건만 처리한다. 큐가 차오르며 `queue full` 에러가 났다. 그래서 큐 용량을 10만에서 100만으로 늘렸다. 몇 분간 조용하더니, 이번엔 프로세스가 `OutOfMemoryError`로 통째로 죽었다. 버퍼를 키운 건 문제를 해결한 게 아니라 폭발 시점을 뒤로 미룬 것뿐이었다.

## 흔한 오해

> "컨슈머가 잠깐 밀리는 거니까, 그동안 쌓일 버퍼만 넉넉히 잡아두면 알아서 따라잡겠지."

밀림을 **일시적 현상**으로 보고 버퍼를 완충재로 이해하는 통념이다. 큐가 넘치면 큐를 키우는 방향, [[connection-pool-sizing]]에서 풀을 키우던 손버릇과 똑같은 반사다.

틀린 건 아니다. 트래픽이 순간적으로 튀는 버스트라면 버퍼가 그걸 흡수한다. 다만 생산 속도가 소비 속도보다 **지속적으로** 빠르면 이야기가 다르다. 이건 버스트가 아니라 구조적 불균형이고, 어떤 크기의 버퍼도 결국 찬다. 버퍼는 문제를 해결하는 장치가 아니라 **시간을 사는 장치**다.

## 실제 원리

### 무한 버퍼는 지연된 죽음이다

생산률 λ_in이 소비율 λ_out보다 크면, 큐 길이는 시간에 비례해 선형으로 늘어난다. 버퍼가 무한이면 두 가지 중 하나가 터진다. 메모리 큐면 OOM, 디스크 큐면 지연이 무한대로 벌어져 결국 타임아웃. 어느 쪽이든 시스템은 죽는다.

여기가 핵심이다. 버퍼링은 λ_in과 λ_out의 격차를 없애지 못한다. 격차를 잠시 감출 뿐이다. 진짜 해법은 딱 하나, **생산자에게 "천천히 보내"라는 신호를 되돌리는 것**이다. 이 되돌림이 backpressure다. 소비자의 처리 능력이 생산자의 전송 속도를 제어하도록 정보가 하류에서 상류로 흐른다.

### 신호를 되돌리는 4가지 방식

backpressure를 구현하는 전략은 크게 넷이다.

1. **Block (막기)** — 큐가 차면 생산자의 전송 호출 자체를 블로킹한다. TCP 흐름 제어가 이 방식이다. 수신 윈도우가 0이면 송신자는 못 보낸다. Kafka 프로듀서도 `buffer.memory`가 차면 `max.block.ms`만큼 `send()`를 막는다. 가장 정확하지만 생산자 스레드가 묶인다.
2. **Drop (버리기)** — 넘치는 걸 버린다. 오래된 걸 버리거나(drop-head), 새 걸 거절한다(drop-tail). 로그·메트릭처럼 유실이 허용되는 데이터에 쓴다. [[rate-limit-token-bucket]]의 leaky bucket이 이 계열이다.
3. **Latest/Sample (최신만)** — 중간을 버리고 가장 최근 값만 남긴다. 실시간 위치·주가처럼 "지금 값"만 의미 있는 스트림에 맞다. 초당 1000틱을 화면에 다 그릴 필요 없이 60프레임만 있으면 된다.
4. **Control (협상)** — 소비자가 "지금 N개까지 받을 수 있다"고 상류에 알린다. Reactive Streams의 `request(n)`이 정확히 이것이다. 생산자는 요청받은 개수만 보낸다. pull 기반이라 버퍼가 원천적으로 넘칠 수 없다. TCP 슬라이딩 윈도우의 credit과 같은 원리다.

대부분의 견고한 시스템은 이 넷을 조합한다. 협상으로 평상시를 처리하고, 한계를 넘으면 drop이나 block으로 물러선다.

## 현장 시나리오

실시간 알림 파이프라인. 이벤트 생산자 → 인메모리 큐 → 푸시 발송 컨슈머. 발송 API가 외부 벤더라 p99가 들쭉날쭉하다.

- 벤더 지연으로 컨슈머 처리율이 초당 6천으로 하락, 생산은 여전히 초당 1만
- 초당 4천씩 큐에 적체 → 팀은 큐 상한을 10만에서 100만으로 확대
- 100만 엔트리 × 엔트리당 약 1KB = **약 1GB**가 힙에 쌓임
- GC가 이 1GB를 계속 스캔하며 STW 시간 폭증, 처리율 오히려 하락 → 적체 가속
- 4분 뒤 `OutOfMemoryError`, 프로세스 강제 종료, 큐의 100만 건 통째로 유실

롤백은 반대 방향이었다. 큐 상한을 **1만으로 되돌리고**, 넘치면 오래된 알림부터 drop + 카운터 기록(전략 2), 그리고 생산자를 Reactive Streams `request(n)`으로 바꿔 컨슈머가 받을 수 있는 만큼만 당겨오게 했다(전략 4). 큐는 다시는 1만을 넘지 않았고, 유실은 통제된 소수로 줄었다. 원인 한 줄: "못 따라오는 걸 버퍼로 감췄더니, 메모리가 대신 터졌다."

## 실무 적용 포인트

1. **큐는 반드시 유계(bounded)로**: 무한 큐는 backpressure가 없다는 뜻이다. 상한을 정하고, 넘칠 때 무엇을 할지(block/drop/reject)를 명시적으로 골라라. 기본값이 무한인 라이브러리를 조심.
2. **Kafka 프로듀서는 `buffer.memory`와 `max.block.ms`로 막는다**: 버퍼가 차면 `send()`가 `max.block.ms`(기본 60s)만큼 블로킹된다. 이 값을 낮춰(예: 1~5s) 빠르게 실패시키면 상류로 backpressure가 전파된다.
3. **유실 허용 여부로 전략을 가른다**: 결제·주문은 block 또는 [[dead-letter-queue]]로 보존. 메트릭·로그·실시간 틱은 drop이나 latest로 과감히 버려라. 데이터 성격이 전략을 정한다.
4. **Reactive Streams/gRPC는 `request(n)` 기반 flow control을 공짜로 준다**: Project Reactor, Akka Streams, gRPC 스트리밍은 credit 기반 pull이 내장. 직접 큐를 돌리지 말고 이 위에 얹어라.
5. **backpressure가 한계에 닿으면 [[circuit-breaker]]로 승격**: 상류로 밀어낸 압력이 진원지(느린 외부 API)를 못 이기면, 회로를 열어 요청 자체를 끊어야 캐스케이드를 막는다.
6. **적체를 지표로 노출**: 큐 깊이, drop 카운트, `max.block.ms` 대기 시간을 대시보드에. 큐 깊이가 우상향 직선이면 그건 버스트가 아니라 구조적 불균형 신호다.

## 더 깊은 토끼굴

backpressure의 본질은 "빠른 쪽이 느린 쪽의 속도를 존중하게 만드는 것"이다. 버퍼를 키우는 건 느린 쪽을 무시하고 빠른 쪽을 계속 달리게 두는 선택이고, 그 대가는 메모리나 지연이 대신 치른다. 정보를 하류에서 상류로 흐르게 하는 순간, 시스템은 자기 처리 능력 안에서만 움직인다.

- [[circuit-breaker]] — 압력을 밀어내도 진원지가 안 죽을 때 회로를 끊기
- [[dead-letter-queue]] — drop 대신 실패 메시지를 보존하는 출구
- [[rate-limit-token-bucket]] — 생산 속도를 상류에서 미리 조이는 leaky/token bucket
- [[retry-exponential-backoff-jitter]] — 재시도가 오히려 backpressure를 역행시키는 함정
- [[connection-pool-sizing]] — 풀도 결국 유계 버퍼, 같은 밸브 논리

**출처**:
- Reactive Streams 명세, "Specification" (backpressure / `request(n)` 정의): https://github.com/reactive-streams/reactive-streams-jvm/blob/master/README.md
- Apache Kafka 공식 문서, Producer Configs (`buffer.memory`, `max.block.ms`): https://kafka.apache.org/documentation/#producerconfigs
