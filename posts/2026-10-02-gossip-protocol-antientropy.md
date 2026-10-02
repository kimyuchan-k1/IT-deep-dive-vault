---
title: 지운 데이터가 12일 뒤 되살아났다. 퍼뜨린 건 복구하려고 돌린 repair였다
date: 2026-10-02
day: 102
category: distributed
tags: [gossip, anti-entropy, cassandra, tombstone, merkle-tree]
related: ["[[eventual-vs-strong-consistency]]", "[[quorum-rw-n]]", "[[crdt-intro]]", "[[lsm-tree-rocksdb]]", "[[consistent-hashing-vnode]]"]
difficulty: 4
short_text: |
  🔥 [Day 102] 지운 데이터가 12일 뒤 부활

  오해: gossip이면 다 퍼진다
  실제: 묘비 10일 만료→죽은 노드 복귀→repair가 옛값 전파

  📖 https://github.com/kimyuchan-k1/IT-deep-dive-vault/blob/main/posts/2026-10-02-gossip-protocol-antientropy.md
---

# 지운 데이터가 12일 뒤 되살아났다. 퍼뜨린 건 복구하려고 돌린 repair였다

## 흔한 오해

> "Gossip은 노드끼리 무작위로 소문을 퍼뜨리는 거다. O(log N) 라운드면 전부 알게 되니까, 기다리면 결국 다 맞춰진다."

분산 DB 소개 글은 "전염병처럼 퍼진다"는 그림 한 장과 [[eventual-vs-strong-consistency]]의 "eventual" 한 단어에서 멈춘다.

절반만 맞다. 빠르게 퍼지는 방식과 **빠짐없이** 퍼지는 방식은 서로 다른 프로토콜이다. 그리고 "없다"는 사실은 퍼뜨릴 수가 없다. 삭제는 묘비(tombstone)라는 데이터로 바꿔서 퍼뜨려야 하고, 그 묘비에는 유효기간이 있다.

## 실제 원리

### 1. 전염 방식은 세 가지고, 보장하는 것이 다르다

1987년 Xerox PARC의 Demers 등이 Clearinghouse 복제 DB를 고치면서 정리한 분류가 지금도 그대로 쓰인다.

| 방식 | 동작 | 속도 | 누락 |
|---|---|---|---|
| direct mail | 변경 즉시 전 노드에 전송 | 가장 빠름 | 수신자가 죽어 있으면 유실 |
| rumor mongering | 새 변경만 "hot"으로 들고 무작위 전파, 이미 아는 상대를 만나면 흥미를 잃음 | 빠름, 메시지 작음 | 일부 노드가 끝내 못 받음 |
| anti-entropy | 무작위 상대와 **전체 상태**를 비교해 차이를 메움 | 느림, 비쌈 | 없음 |

여기가 핵심이다. rumor mongering은 확률적으로 멈춘다. 논문의 분석으로, 이미 아는 상대를 만날 때마다 1/k 확률로 전파를 그만두는 모델에서 못 받는 노드 비율 `s`는 `s = e^(-(k+1)(1-s))`를 따른다. `k=1`이면 약 20%, `k=2`면 약 6%가 소문을 못 듣는다. 그래서 실제 시스템은 rumor로 빨리 퍼뜨리고 anti-entropy로 잔여분을 쓸어 담는 2단 구조를 쓴다.

### 2. push와 pull은 꼬리에서 갈린다

아직 못 받은 노드 비율을 `p`라 하자.

```
pull  (모르는 쪽이 물어봄):  p_next = p²
push  (아는 쪽이 밀어줌):    p_next ≈ p · e⁻¹     (p가 작을 때)

p = 1% 에서 한 라운드 뒤
  pull → 0.01%
  push → 0.37%
```

push는 막판에 이미 아는 노드끼리 서로 밀어주느라 헛돈다. pull은 모르는 노드가 직접 물어보므로 제곱으로 줄어든다. 초반 확산은 push가 빠르고 꼬리는 pull이 빠르다. 그래서 양쪽이 서로의 상태를 교환하는 push-pull이 표준이 됐다. Cassandra gossip의 `SYN → ACK → ACK2` 3단계가 이 push-pull이다.

### 3. 멤버십 gossip과 데이터 anti-entropy는 다른 층이다

Cassandra에서 "gossip"은 데이터를 나르지 않는다. 1초마다 최대 3개 노드(무작위 live 1개, 확률적으로 죽은 노드 1개와 seed 1개)와 **클러스터 메타데이터**만 교환한다. 노드별 `generation`(재시작 시각)과 `version`(단조 증가 카운터)을 비교해 더 큰 쪽이 이긴다. 이 heartbeat 도착 간격으로 phi accrual failure detector가 장애를 판정한다(`phi_convict_threshold` 기본 8).

실제 데이터를 맞추는 건 세 장치다.

- **hinted handoff** — 죽은 복제본 몫의 쓰기를 코디네이터가 보관했다가 재전송. `max_hint_window` 기본 3시간까지만 쌓는다.
- **read repair** — 읽을 때 복제본 간 차이가 보이면 그 자리에서 고친다. 읽히지 않는 데이터는 영영 안 고쳐진다.
- **repair (anti-entropy)** — 토큰 범위별로 Merkle tree를 만들어 루트 해시부터 비교하고, 다른 가지의 데이터만 스트리밍한다. Dynamo 논문 4.7절의 그 구조다.

Merkle tree 덕에 어긋난 범위만 스트리밍하지만, 트리를 만들려면 디스크 전체를 읽어야 한다. repair는 비싸다. 비싸니까 미룬다. 사고는 거기서 난다.

### 4. "없음"은 전파되지 않는다

anti-entropy는 두 복제본을 비교해 **있는 쪽이 없는 쪽에 채운다.** A에는 행이 있고 B에는 없을 때, B가 아직 못 받은 건지 B가 지운 건지 구별할 방법이 없다. 그래서 삭제는 "이 시각에 지웠다"는 묘비를 쓰는 것으로 구현된다. Demers 논문은 이것을 death certificate라 불렀다.

묘비를 영원히 들고 있으면 디스크와 읽기 성능이 무너진다([[lsm-tree-rocksdb]]의 tombstone 스캔 비용). 그래서 Cassandra는 `gc_grace_seconds`(기본 864000초, 10일)가 지난 묘비를 컴팩션 때 버린다. 이 숫자가 담고 있는 계약은 하나다. **모든 복제본이 10일 안에 묘비를 받았어야 한다.** 그 보장은 gossip이 아니라 repair가 한다.

## 현장 시나리오

회원 서비스의 Cassandra 6노드, RF=3. 탈퇴 회원 개인정보를 배치로 지운다.

```
D+0   노드 n4 디스크 컨트롤러 고장, 부품 대기로 다운
D+2   탈퇴 배치가 4만 건 DELETE (QUORUM) → n1, n2에 묘비 기록, 성공 응답
      n4 몫 hint는 max_hint_window 3시간을 이미 넘겨 저장 안 됨
D+12  n1, n2에서 묘비가 gc_grace 10일 경과 → 컴팩션이 묘비와 원본을 함께 제거
D+12  n4 부품 교체, 디스크 데이터 그대로 재기동. 4만 건이 살아 있는 채로 복귀
D+12  운영자가 "오래 죽어 있었으니 맞춰 주자"며 nodetool repair 실행
      → Merkle tree 불일치: n4에는 행 있음, n1·n2에는 아무것도 없음
      → 묘비가 없으니 "n1·n2가 못 받은 데이터"로 판정, n4 → n1·n2 스트리밍
D+13  탈퇴 회원 4만 명이 조회됨. 마케팅 메일 발송 대상에 다시 포함
```

장애 감지는 정상이었다. gossip은 n4 다운을 몇 초 만에 전 노드에 알렸다. 쓰기도 QUORUM으로 성공했다. 어긋난 곳은 한 군데다. **묘비 수명 10일보다 노드 부재 12일이 길었다.** 그 순간 n4의 디스크는 "늦은 복제본"이 아니라 "지워진 적 없는 과거"가 됐고, repair는 설계대로 그 과거를 전파했다.

## 실무 적용 포인트

1. **모든 노드를 `gc_grace_seconds` 안에 한 번 이상 repair한다.** 기본 10일이면 7일 주기 `nodetool repair -pr`을 노드마다 돌린다. 마지막 repair 성공 시각을 지표로 뽑고 `gc_grace`의 70%를 넘으면 알림.

2. **`gc_grace_seconds`보다 오래 죽어 있던 노드는 그대로 올리지 않는다.** 데이터 디렉터리를 비우고 `-Dcassandra.replace_address_first_boot=<IP>`로 새 노드처럼 스트리밍받게 한다.

3. **`max_hint_window`(기본 3시간)를 장애 복구 시간과 비교한다.** 3시간을 넘긴 다운은 hint로 복구되지 않는다. 그때부터는 repair만이 유일한 수단이다.

4. **`gc_grace_seconds`를 줄이려면 repair 주기를 먼저 줄인다.** 묘비 스캔 경고(`tombstone_warn_threshold` 기본 1000) 때문에 값을 1일로 내리면 부활 창이 그만큼 넓어진다. TTL 전용·삭제 없는 테이블에서만 낮춘다.

5. **SWIM 계열(memberlist, Consul, Serf)도 같은 2단 구조다.** memberlist LAN 기본값은 `GossipInterval` 200ms, `GossipNodes` 3으로 rumor를 뿌리고, `PushPullInterval` 30초마다 TCP로 전체 상태를 맞춘다. 장애 판정은 `ProbeInterval` 1초, `IndirectChecks` 3.

요약: gossip은 "누가 살아 있나"를 빠르게 퍼뜨린다. 데이터를 빠짐없이 맞추는 건 anti-entropy다. 그리고 anti-entropy는 묘비가 남아 있는 동안에만 삭제를 기억한다.

## 더 깊은 토끼굴

- [[eventual-vs-strong-consistency]] — "eventual"이 성립하려면 anti-entropy가 실제로 돌아야 한다는 전제.
- [[quorum-rw-n]] — W=2로 성공한 삭제가 세 번째 복제본에는 닿지 않은 상태로 남는 구조.
- [[crdt-intro]] — 삭제를 병합 가능한 데이터로 만드는 또 다른 접근(OR-Set의 tombstone 문제).
- [[lsm-tree-rocksdb]] — 묘비가 컴팩션 전까지 읽기 경로에 남는 이유.
- [[consistent-hashing-vnode]] — repair의 단위가 되는 토큰 범위와 `-pr` 옵션의 의미.

**1차 출처**
- Demers et al., "Epidemic Algorithms for Replicated Database Maintenance" (PODC 1987): https://dl.acm.org/doi/10.1145/41840.41841
- Apache Cassandra docs — Tombstones (`gc_grace_seconds`, zombie data): https://cassandra.apache.org/doc/latest/cassandra/managing/operating/compaction/tombstones.html
- DeCandia et al., "Dynamo: Amazon's Highly Available Key-value Store" (SOSP 2007), 4.7 Replica synchronization: https://www.allthingsdistributed.com/files/amazon-dynamo-sosp2007.pdf
- Das, Gupta, Motivala, "SWIM: Scalable Weakly-consistent Infection-style Process Group Membership Protocol" (DSN 2002): https://www.cs.cornell.edu/projects/Quicksilver/public_pdfs/SWIM.pdf
- HashiCorp memberlist `config.go` (DefaultLANConfig): https://github.com/hashicorp/memberlist/blob/master/config.go
