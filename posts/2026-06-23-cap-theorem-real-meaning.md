---
title: CAP에서 P는 고를 수 있는 선택지가 아니다
date: 2026-06-23
day: 21
category: distributed
tags: [cap-theorem, pacelc, consistency, partition-tolerance, latency]
related: ["[[eventual-vs-strong-consistency]]", "[[quorum-rw-n]]", "[[lamport-vs-vector-clock]]"]
difficulty: 4
short_text: |
  💡 [Day 21] CAP에서 P는 못 고른다
  오해: 셋 중 둘을 고른다
  실제: 끊김은 늘 일어난다→P는 강제→끊긴 순간 C냐 A냐→평소엔 PACELC의 L↔C
  📖 https://github.com/kimyuchan-k1/IT-deep-dive-vault/blob/main/posts/2026-06-23-cap-theorem-real-meaning.md
---

분산 데이터베이스를 고를 때 "이건 CP 시스템이야, 저건 AP 시스템이야"라는 말을 자주 듣는다. 그런데 이 분류를 "셋 중 둘을 골랐다"로 이해하면 정작 운영에서 중요한 결정은 놓친다.

## 흔한 오해

> "CAP 정리는 Consistency, Availability, Partition tolerance 셋 중에서 둘만 가질 수 있다는 거잖아. 그러니까 우리는 CA를 고르면 되겠네 — 일관성도 있고 가용성도 있는 걸로."

세 꼭짓점 삼각형 그림 때문에 생긴 오해다. 입문 자료들이 CAP을 "vending machine처럼 둘을 고르는 메뉴"로 그려놓으니, 많은 사람이 P를 버리는 선택지로 본다. 그래서 "우리는 단일 데이터센터니까 CA면 충분하다"는 결론으로 간다.

틀린 건 아니다 — 정리의 이름이 정말 세 글자니까. 하지만 P를 "고르거나 버리는 것"으로 본 순간 핵심을 놓쳤다. CA 시스템은 현실에 존재하지 않는다.

## 실제 원리

### P는 환경이지 선택이 아니다

Partition은 네트워크가 두 그룹으로 쪼개져 서로 메시지를 못 주고받는 상태다. 그리고 네트워크는 **반드시** 끊긴다. 스위치가 죽고, 케이블이 뽑히고, GC stop-the-world가 길어져 노드가 응답을 못 하고, 클라우드 AZ 사이 링크가 순간 막힌다.

여기서 핵심은, partition은 당신이 허용하거나 거부하는 게 아니라 그냥 일어나는 사건이라는 점이다. 그러니 "P를 버린다"는 건 "네트워크 장애가 절대 안 난다고 가정한다"는 뜻이고, 그건 분산 시스템에서 성립하지 않는 가정이다.

그래서 CAP의 진짜 진술은 이렇다. **네트워크가 끊긴 그 순간, C와 A 중 하나만 지킬 수 있다.** 둘 중 어느 것도 P와 트레이드되는 게 아니라, P가 발생했을 때 C와 A가 서로 트레이드된다.

### 끊긴 순간 무슨 일이 벌어지나

노드 G1, G2가 같은 값 `v0`을 들고 있다가 둘 사이 링크가 끊겼다고 하자. 클라이언트가 G1에 `v1`을 쓴다. G1은 G2에게 이 변경을 전파할 수 없다. 이제 다른 클라이언트가 G2에게 그 값을 읽으러 온다. G2는 두 갈래뿐이다.

- **C를 지킨다**: "나는 최신값인지 확신할 수 없으니 응답을 거부한다." → 일관성은 지켰지만 그 요청은 **실패(가용성 포기)**.
- **A를 지킨다**: "일단 내가 가진 `v0`을 준다." → 응답은 했지만 **stale한 값(일관성 포기)**.

이 갈림길에서 어느 쪽을 기본값으로 잡느냐가 그 시스템이 CP냐 AP냐를 가른다. 그래서 ZooKeeper·etcd 같은 합의 시스템은 소수파(minority) 쪽을 의도적으로 멈춰 세운다(CP). DynamoDB·Cassandra는 일단 응답하고 나중에 [[eventual-vs-strong-consistency]]로 수렴시킨다(AP).

### PACELC — 정작 99.9%의 시간을 설명하는 정리

CAP의 함정은 partition이 "가끔"이라는 데 있다. 1년에 몇 번 끊길 시스템의 성격을 partition 동작만으로 규정하는 건 절반의 그림이다.

2010년 Daniel Abadi가 내놓은 PACELC가 나머지 절반을 채운다. **if Partition then (A or C), Else (L or C)** — 즉 끊겼을 땐 A냐 C냐, 그리고 **끊기지 않은 평상시(Else)엔 Latency냐 Consistency냐**를 고른다.

평소에도 강한 일관성을 보장하려면 쓰기를 여러 복제본에 동기적으로 반영하고 정족수의 응답을 기다려야 한다. 그게 곧 지연시간이다. 그래서 Cassandra는 `PC/EL`(끊기면 가용성, 평소엔 지연 우선), 완전 동기 복제 RDB는 `PC/EC`로 분류된다. 운영에서 매일 체감하는 건 partition이 아니라 이 **Else 쪽의 L↔C 트레이드오프**다.

## 현장 시나리오

결제 정산 시스템이 멀티 AZ Postgres를 동기 복제(`synchronous_commit=remote_apply`)로 깔았다. "데이터는 절대 안 틀려야 하니까" CP에 가깝게 — 모든 커밋이 다른 AZ 복제본의 apply까지 기다리도록.

평소엔 잘 돌았다. 그런데 AZ 사이 네트워크에 순간적인 패킷 손실이 생기면서 복제본 apply가 평소 2ms에서 **800ms로** 튀었다. partition까지 간 것도 아니고 그냥 느려진 것이다. 동기 커밋이라 모든 쓰기가 그 800ms를 함께 기다렸다 → 커넥션이 반환되지 않고 쌓임 → 커넥션 풀 고갈 → 결제 API 전체가 타임아웃. partition은 한 번도 안 났는데 서비스가 멈췄다.

원인은 "C를 골랐다"가 아니라, **Else 절의 L을 포기한 대가를 평상시 비용으로 계산하지 않은 것**이었다. 정산 무결성이 필요한 경로만 동기 복제로 두고 나머지를 비동기로 돌리자 p99가 800ms에서 다시 5ms로 내려갔다.

## 실무 적용 포인트

1. **"우리는 CA"라는 말이 나오면 멈춰라.** 단일 노드가 아닌 이상 CA는 없다. 무엇을 CP/AP로 운영할지로 질문을 바꿔라.
2. **시스템을 통째로 분류하지 말고 데이터 경로별로 나눠라.** 잔액·재고는 CP, 좋아요 수·조회수는 AP. 한 서비스 안에 둘이 공존하는 게 정상이다.
3. **Postgres `synchronous_commit`은 5단계다.** `off`/`local`/`remote_write`/`on`/`remote_apply` — 오른쪽으로 갈수록 C는 강해지고 평상시 L은 나빠진다. 무결성 경로에만 강하게.
4. **Cassandra/Dynamo는 `QUORUM`을 기본으로 잡고 출발하라.** `R + W > N`(예: N=3, R=W=2)이면 평상시 강한 일관성에 근접하면서 한 노드 장애를 견딘다. [[quorum-rw-n]] 참고.
5. **partition을 실제로 주입해 테스트하라.** `tc qdisc`나 카오스 도구로 AZ 링크를 끊고, 그때 시스템이 멈추는지(CP) stale을 주는지(AP) 눈으로 확인. 문서가 아니라 동작이 답이다.
6. **타임아웃을 partition의 대리값으로 설계하라.** 노드는 "끊김"과 "느림"을 구분 못 한다. failure detector 타임아웃을 너무 짧게 잡으면 멀쩡한 노드를 partition으로 오인해 불필요한 페일오버가 난다([[lamport-vs-vector-clock]]에서 본 시계 문제와 같은 뿌리).

## 더 깊은 토끼굴

CAP을 "둘을 고른다"로 외운 사람과 "끊긴 순간의 C·A, 평상시의 L·C"로 이해한 사람은 같은 데이터베이스를 골라도 운영이 갈린다. partition은 드물지만, Latency와 Consistency의 줄다리기는 매 요청마다 일어난다.

- [[eventual-vs-strong-consistency]] — AP가 약속하는 "결국 수렴"의 실제 비용
- [[quorum-rw-n]] — R+W>N으로 일관성을 사는 정족수 수학
- [[lamport-vs-vector-clock]] — "누가 먼저인가"를 시계 없이 판단하기

출처:
- Eric Brewer, "CAP Twelve Years Later: How the 'Rules' Have Changed" (IEEE Computer, 2012): https://www.infoq.com/articles/cap-twelve-years-later-how-the-rules-have-changed/
- Daniel Abadi, "Consistency Tradeoffs in Modern Distributed Database System Design" (PACELC 원전): https://www.cs.umd.edu/~abadi/papers/abadi-pacelc.pdf
- Martin Kleppmann, "A Critique of the CAP Theorem" (arXiv:1509.05393): https://arxiv.org/abs/1509.05393
