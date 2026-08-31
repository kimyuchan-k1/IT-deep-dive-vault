---
title: R+W > N으로 맞춰놨다. 그런데 배송 상태가 과거로 되돌아갔다
date: 2026-08-31
day: 82
category: distributed
tags: [quorum, dynamo, cassandra, consistency, clock-skew]
related: ["[[paxos-5min]]", "[[cap-theorem-real-meaning]]", "[[vector-clock]]"]
difficulty: 3
short_text: |
  ⚠️ [Day 82] R+W>N인데 배송 상태가 과거로 돌아갔다

  오해: 쿼럼 겹치면 최신값 읽는다
  실제: 겹침≠순서→시계 400ms 밀림→나중 쓰기 폐기

  📖 https://github.com/kimyuchan-k1/IT-deep-dive-vault/blob/main/posts/2026-08-31-quorum-rw-n.md
---

# R+W > N으로 맞춰놨다. 그런데 배송 상태가 과거로 되돌아갔다

## 흔한 오해

> "N=3, R=2, W=2로 맞췄으니까 읽으면 항상 최신 값이 나오는 거 아닌가? 읽기 집합과 쓰기 집합이 반드시 한 노드에서 겹치잖아."

Dynamo 계열 저장소를 처음 세팅할 때 거의 모두가 이렇게 이해한다. 그래서 `R + W > N`을 "강한 일관성 스위치"처럼 취급한다. 켜두면 [[paxos-5min]] 같은 합의 프로토콜 없이도 최신 값이 보장된다고 믿는다.

통념의 출처는 명확하다. 비둘기집 원리로 증명이 한 줄에 끝나기 때문이다. 3개 중 2개에 쓰고 3개 중 2개를 읽으면, 두 집합은 최소 1개 노드를 공유한다. 증명이 너무 깔끔해서 그다음 질문을 안 하게 된다.

## 실제 원리

### 겹침은 "존재" 보장이지 "식별" 보장이 아니다

쿼럼이 실제로 보장하는 건 딱 하나다. **읽기 집합 안에 최신 값을 가진 노드가 최소 하나 존재한다.** 그 노드가 어느 것인지 알려주지는 않는다.

읽기 응답 2개를 받았는데 값이 서로 다르다고 하자. 하나는 `SHIPPED`, 하나는 `DELIVERED`. 둘 중 뭐가 최신인가? 쿼럼은 이 질문에 답하지 않는다. **순서를 정하는 건 완전히 별개의 메커니즘**이다.

그 별개의 메커니즘이 보통 두 가지 중 하나다.

- **LWW (Last Write Wins)**: 타임스탬프가 큰 쪽이 이긴다. Cassandra가 셀(cell) 단위로 이걸 강제한다.
- **버전 벡터 / 시블링**: 인과 관계를 못 정하면 두 값을 다 보관하고 클라이언트에 떠넘긴다. Riak의 `allow_mult=true`가 이 모드다.

LWW를 쓰는 순간, 데이터 정합성이 **노드들의 벽시계 정확도**에 종속된다. 쿼럼과 무관하게.

### 쓰기 타임스탬프는 어디서 오나

Cassandra에서 뮤테이션 하나하나에 마이크로초 단위 타임스탬프가 박힌다. 클라이언트가 `USING TIMESTAMP`로 명시하지 않으면 요청을 받은 **코디네이터 노드의 시계**가 값을 정한다. 요청마다 코디네이터가 달라지므로, 논리적으로 나중에 발생한 쓰기가 더 작은 타임스탬프를 달고 저장될 수 있다.

이 경우 저장소는 에러를 내지 않는다. 조용히 나중 값을 **버린다**. 정확히는, 조회 시점에 타임스탬프가 큰 옛날 값이 이긴다.

### 겹침 자체가 깨지는 경우들

- **sloppy quorum**: 원래 담당 노드가 죽으면 아무 노드나 대신 받아준다. W개의 ack는 받았지만 그 W개가 홈 노드가 아니다. 읽기 집합과 겹칠 근거가 사라진다. Dynamo 논문의 hinted handoff가 이 설계다.
- **hint 만료**: Cassandra는 죽은 노드 몫을 힌트로 들고 있다가 되살아나면 넘긴다. 그런데 `max_hint_window_in_ms` 기본값이 3시간이다. 노드가 4시간 만에 복귀하면 그 구간 쓰기는 힌트로 안 온다. `nodetool repair`를 돌리기 전까지 복제본 수가 부족한 상태로 남는다.
- **실패한 쓰기의 잔해**: W=2인데 1개만 ack되고 타임아웃 났다면 클라이언트는 실패로 받는다. 그런데 그 1개 노드에는 값이 남아 있다. 이후 읽기에서 이 값이 보였다 안 보였다 한다. **단조 읽기(monotonic read)가 깨진다.**

요약하면, `R + W > N`은 **가용성과 내구성의 트레이드오프를 조절하는 다이얼**이지 선형화 스위치가 아니다.

## 현장 시나리오

물류 스타트업. Cassandra 6노드, RF=3, 읽기/쓰기 모두 `LOCAL_QUORUM` (R=2, W=2). 배송 상태 API가 API 서버 4대 뒤에 물려 있다.

한 주문에 상태 전이가 200ms 간격으로 두 번 들어왔다. `SHIPPED` 는 api-2가, `DELIVERED` 는 api-4가 처리했다. 두 요청 모두 서로 다른 코디네이터 노드로 갔다.

문제는 3주 전부터 cass-04의 chronyd가 죽어 있었다는 것. 시계가 400ms 앞서 있었다. `SHIPPED` 쓰기가 cass-04를 코디네이터로 탔고, `DELIVERED` 쓰기는 정상 시계 노드를 탔다.

인과 사슬:

```
SHIPPED(코디네이터 cass-04, ts=T+400ms) 저장
  → 200ms 뒤 DELIVERED(코디네이터 cass-01, ts=T+200ms) 저장
  → 두 쓰기 모두 W=2 성공, 에러 0건
  → 읽기 QUORUM 성공, 두 복제본 값 비교
  → LWW: ts 큰 SHIPPED 승리
  → 배송 완료된 주문이 "배송중"으로 표시
```

메트릭은 전부 정상이었다. 쓰기 실패율 0%, 읽기 지연 p99 8ms, `nodetool status` 전 노드 UN. 대시보드에 잡힐 게 아무것도 없었다. 발견은 고객센터 문의 누적 47건으로 됐다.

## 실무 적용 포인트

1. **선형화가 필요한 필드는 쿼럼에 맡기지 마라.** 상태 전이·잔액·재고는 Cassandra `LWT`(`IF status='SHIPPED'`)를 쓰거나 아예 다른 저장소로 뺀다. LWT는 내부적으로 Paxos라 왕복이 4번 늘고 일반 쓰기보다 수 배 느리다. 그 비용을 알고 쓰는 것과 모르고 안 쓰는 건 다르다.
2. **시계 감시를 데이터 정합성 알람으로 승격시켜라.** `chrony`로 통일하고, 노드 간 offset 50ms 초과 시 페이지. `chronyc tracking`의 System time 값을 메트릭으로 뽑는다. NTP 죽은 걸 3주 뒤에 아는 상황을 없애는 게 우선이다.
3. **Riak이면 `pr`/`pw`를 써라.** `r=2`/`w=2`는 sloppy quorum을 허용한다. `pr=2`/`pw=2`(primary quorum)로 두면 홈 노드에서만 카운트해서 겹침 보장이 실제로 성립한다. 대신 가용성이 떨어진다 — 그게 정직한 트레이드오프다.
4. **LWW로 병합하면 안 되는 자료형은 CRDT로 바꿔라.** 카운터·집합에 LWW를 적용하면 동시 갱신 하나가 통째로 사라진다. Cassandra는 counter 타입, Riak은 Data Types(counter/set/map)을 쓴다.
5. **`nodetool repair`를 `gc_grace_seconds` 안에 돌려라.** 기본값 864000초(10일)다. 이걸 넘기면 삭제 마커(tombstone)가 먼저 사라져서 삭제한 데이터가 되살아난다. 주 1회 서브레인지 repair를 크론에 건다.
6. **W=1은 쓰지 마라.** `R=3, W=1`도 `R+W>N`을 만족하지만, ack한 노드 한 대가 복구 전에 죽으면 그 쓰기는 그냥 없어진다. 내구성은 W가 혼자 책임진다.

## 더 깊은 토끼굴

쿼럼이 순서를 안 정해준다는 건 [[paxos-5min]]이 왜 그렇게 복잡한지의 역방향 설명이다. 순서를 정하려면 결국 합의가 필요하고, 합의는 왕복 비용을 문다. 그 비용을 안 내겠다고 결정한 게 Dynamo 계열이다.

시계 대신 인과 관계로 순서를 정하는 방법은 [[vector-clock]]에서, "그래서 CP냐 AP냐"라는 흔한 오분류는 [[cap-theorem-real-meaning]]에서 이어진다. 복제본 사이 차이를 백그라운드로 좁히는 방식은 [[merkle-tree-anti-entropy]]로 간다.

**출처**

- Dynamo: Amazon's Highly Available Key-value Store (SOSP 2007) — sloppy quorum과 hinted handoff 원문: https://www.allthingsdistributed.com/files/amazon-dynamo-sosp2007.pdf
- Apache Cassandra 공식 문서, Consistency levels: https://cassandra.apache.org/doc/latest/cassandra/architecture/dynamo.html
- Jepsen, Cassandra 분석 (LWW와 시계 문제): https://jepsen.io/analyses/cassandra-4.1.5
