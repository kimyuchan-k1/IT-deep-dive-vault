---
title: 지운 문장이 동기화 후 되살아났다
date: 2026-07-30
day: 53
category: distributed
tags: [crdt, eventual-consistency, replication, collaborative-editing, lww, semilattice]
related: ["[[eventual-vs-strong-consistency]]", "[[quorum-rw-n]]", "[[paxos-5min]]", "[[read-your-writes]]", "[[idempotency-key]]"]
difficulty: 4
short_text: |
  ⚠️ [Day 53] 지운 문장이 동기화 후 되살아났다
  오해: 타임스탬프 최신값이 이긴다
  실제: 삭제=원소 제거→union이 부활→tag tombstone 필요
  📖 https://github.com/kimyuchan-k1/IT-deep-dive-vault/blob/main/posts/2026-07-30-crdt-intro.md
---

# 지운 문장이 동기화 후 되살아났다

지하철에서 오프라인으로 문서를 정리했다. 필요 없어진 항목 몇 개를 지우고, 회사에 도착해 와이파이가 붙었다. 동기화 스피너가 한 바퀴 돌고 나서, 지웠던 항목들이 목록 중간에 그대로 다시 나타났다. 충돌 경고도 없었고, 에러 로그도 없었다. 시스템 입장에서 이건 정상 동작이었다.

## 흔한 오해

> "동시 편집은 결국 마지막 쓰기가 이기는 거잖아. 타임스탬프 비교해서 최신 걸 고르면 되지 않나?"

Last-Write-Wins는 널리 쓰이고 잘 동작하는 것처럼 보인다. Cassandra의 셀 타임스탬프도, 모바일 동기화 코드의 `if (remote.updatedAt > local.updatedAt)` 한 줄도 이 발상이다. 레플리카가 몇 개든 같은 규칙을 적용하면 결국 같은 값에 도달하니 논리적으로 틀린 것도 아니다.

틀린 건 아니다. 다만 **"모든 레플리카가 같은 상태로 수렴한다"와 "사용자가 한 작업이 살아남는다"는 완전히 다른 보장**이다. LWW는 앞의 것만 약속한다. 뒤의 것은 목표가 아니다. 두 사람이 각자 다른 필드를 고쳤을 때 한쪽 편집이 통째로 사라지는 것도, 지운 항목이 되살아나는 것도 LWW 입장에서는 명세대로 동작한 결과다.

## 실제 원리

### 수렴은 merge 함수의 대수적 성질에서 나온다

CRDT가 보장하는 건 strong eventual consistency다. 같은 업데이트 집합을 받은 레플리카는 **순서와 무관하게, 중복 수신과 무관하게** 같은 상태가 된다. 합의도, 롤백도, 서버의 순서 결정도 필요 없다.

이게 가능한 조건은 하나다. merge 함수가 세 법칙을 만족해야 한다.

```
교환법칙: merge(a, b) = merge(b, a)          → 도착 순서 무관
결합법칙: merge(merge(a,b), c) = merge(a, merge(b,c))  → 묶어서 합쳐도 동일
멱등법칙: merge(a, a) = a                     → 재전송해도 안전
```

이 세 조건을 만족하는 구조가 join-semilattice이고, merge는 두 상태의 least upper bound를 구하는 연산이 된다. 즉 merge는 "고르는" 연산이 아니라 **"올려주는" 연산**이어야 한다. LWW가 위험한 이유가 여기서 보인다. LWW의 merge는 둘 중 하나를 고르고 다른 하나를 버린다. 세 법칙은 만족하지만(타임스탬프 동률 tie-break만 결정적이면), 버려진 쪽 정보는 복구할 방법이 없다.

G-Counter가 이 대비를 보여준다. 카운터를 정수 하나로 두면 merge할 방법이 없다. 노드별 카운터 맵 `{A: 3, B: 5, C: 2}`으로 두고 merge를 **키별 max**로 정의하면 세 법칙이 전부 성립하고, 값은 각 원소의 합이 된다. 노드가 자기 슬롯만 증가시키므로 두 노드의 증가가 서로를 덮어쓸 수 없다. 감소가 필요하면 증가용/감소용 맵 두 개(PN-Counter)를 두고 각각 max로 merge한다.

### 삭제가 부활하는 지점

집합은 여기서 무너진다. add만 있는 G-Set은 merge를 union으로 두면 끝이다. 문제는 remove다.

원소를 그냥 배열에서 빼면, merge=union인 다른 레플리카가 그 원소를 아직 들고 있다. union은 "있는 것"만 올려주는 연산이므로, **"없어진 것"은 표현할 수단이 없다.** 지운 쪽 상태가 안 지운 쪽 상태보다 작으니 merge 결과는 안 지운 쪽으로 올라간다. 삭제가 아니라 삭제의 소멸이다.

그래서 삭제는 원소를 지우는 게 아니라 **삭제했다는 사실을 추가**하는 방식으로 표현한다. OR-Set(Observed-Remove Set)이 표준적인 답이다. add할 때마다 원소에 고유 태그를 붙이고, remove는 **그 시점에 관찰한 태그들만** tombstone 집합에 넣는다.

```
add("x") on A  → {("x", t1)}
add("x") on B  → {("x", t2)}
remove("x") on A → tombstone {t1}
merge → 원소 {("x",t1),("x",t2)}, tombstone {t1} → 살아있는 태그 t2 → "x" 존재
```

A가 지운 건 자기가 본 t1이지, B가 나중에 추가한 t2가 아니다. 태그 단위로 인과관계를 좁혀놨기 때문에 이 결과는 우연이 아니라 정의된 의미다. 동시 add/remove는 add가 이긴다(add-wins).

텍스트도 같은 발상의 확장이다. RGA나 YATA 계열은 문자마다 불변 ID와 왼쪽 이웃 참조를 붙이고, 삭제는 tombstone으로만 처리한다. "3번째 위치에 삽입"이 아니라 "ID `(client7, 42)` 다음에 삽입"이라서, 다른 사람의 편집으로 오프셋이 밀려도 연산이 무효화되지 않는다. 인덱스를 버리고 ID를 쓰는 것이 협업 편집기가 서버 중재 없이 동작하는 핵심이다.

### 대가는 메타데이터

이 모든 것은 공짜가 아니다. tombstone은 쌓이고, 태그와 문자 ID는 실제 데이터보다 커진다. 한 글자를 지워도 그 글자의 ID는 남아야 한다. tombstone을 일찍 버리면 아까 그 union 문제가 그대로 돌아온다. 안전하게 버릴 수 있는 시점은 **모든 레플리카가 그 삭제를 이미 봤다고 확신할 때**(causal stability)뿐이고, 오프라인 클라이언트가 있는 시스템에서 그 시점은 잘 오지 않는다.

## 현장 시나리오

사내 협업 체크리스트 앱. 모바일 오프라인 편집을 지원하고, 항목 목록을 JSON 배열로 들고 있다가 온라인 복귀 시 서버와 병합했다. 병합 코드는 두 부분이었다.

- 항목 필드 변경: `updatedAt`(초 단위 ISO 문자열) 비교 후 최신 것으로 덮어쓰기
- 항목 목록: 로컬 배열과 서버 배열의 `id` 기준 union

프로젝트 마감 주간, PM이 지하철에서 4시간 동안 1,200개 항목을 정리했다. 끝난 항목 37개 삭제, 라벨 다수 수정. 회사 도착 후 동기화.

37개가 전부 돌아왔다. 삭제는 로컬 배열에서 원소를 빼는 것으로 구현돼 있었고, 서버 배열에는 그 항목들이 그대로 있었다. union은 서버 쪽 원소를 살렸다. 삭제 의도를 담은 데이터가 애초에 전송되지 않았으니, 서버는 삭제가 있었다는 사실 자체를 알 수 없었다.

두 번째 사고가 같은 동기화에 겹쳤다. 같은 시간 데스크톱에서 다른 팀원이 항목 담당자를 바꿨다. 모바일 기기 시계는 NTP 동기화 실패로 1.2초 앞서 있었고 `updatedAt`은 초 단위였다. 4시간 전 모바일 편집이 30분 전 데스크톱 편집보다 "최신"으로 판정된 필드가 나왔다. 로그에는 흔적조차 없었다.

원인 한 줄: 삭제를 "원소를 빼는 일"로 구현한 순간, 삭제는 전송할 수 있는 정보가 아니게 됐다.

## 실무 적용 포인트

1. **LWW를 쓰겠다면 물리 시각을 단독으로 쓰지 마라.** Lamport clock 또는 hybrid logical clock을 쓰고, 값이 같을 때는 노드 ID로 결정적 tie-break을 넣는다. 최소 밀리초 정밀도 + 단조 증가 보장. 초 단위 `updatedAt` 문자열 비교는 clock skew 1초에 편집 4시간이 뒤집힌다.

2. **LWW의 단위를 레코드가 아니라 필드로 내려라.** JSON 문서 전체를 LWW-Register로 두면 서로 다른 필드를 고친 두 편집 중 하나가 통째로 사라진다. 필드별 레지스터(LWW-Map)로 쪼개면 그 손실이 없다. 손실을 못 받아들이는 필드는 MV-Register로 두고 두 값을 다 남겨 UI에서 선택하게 한다.

3. **삭제는 tombstone으로 표현한다.** OR-Set 형태 — add 시 UUID 태그 부착, remove 시 관찰한 태그만 제거 집합에 추가. "지운 원소를 목록에서 빼고 union으로 merge"는 삭제가 항상 지는 조합이다. 직접 구현하기보다 Yjs / Automerge 같은 검증된 구현을 쓰는 편이 낫다.

4. **op-based CRDT는 전달 보장을 요구한다.** state-based(CvRDT)는 상태 전체를 merge하므로 중복·순서 뒤바뀜에 안전하지만 payload가 크다. op-based(CmRDT)는 작은 연산만 보내지만 causal delivery + exactly-once가 필요하다 — counter increment는 멱등이 아니라서 at-least-once 재전송이 값을 부풀린다. 큰 상태가 부담이면 delta-state CRDT로 변경분만 보낸다. [[idempotency-key]]와 같은 문제를 다른 층에서 만난 것이다.

5. **merge 함수는 property-based test로 검증한다.** 무작위 연산 시퀀스를 순서만 섞어 재생했을 때 최종 상태가 같은지(교환·결합), 같은 업데이트를 두 번 적용해도 변하지 않는지(멱등)를 자동으로 돌린다. 단위 테스트 몇 개로는 순서 의존 버그가 안 잡힌다.

6. **CRDT는 불변식을 지켜주지 않는다.** "재고가 0 미만이 되면 안 된다", "같은 좌석을 두 명이 예약하면 안 된다" 같은 전역 제약은 CRDT로 표현할 수 없다 — 각 레플리카가 독립적으로 결정하니 합쳐진 결과가 제약을 깬다. 그런 건 [[paxos-5min]] 계열의 합의나 단일 직렬화 지점이 필요하다. 장바구니·카운터·문서·프레즌스는 CRDT, 결제·재고 차감·좌석 배정은 합의.

## 더 깊은 토끼굴

CRDT는 [[eventual-vs-strong-consistency]]에서 "eventual 쪽을 골랐을 때 무엇을 되찾을 수 있나"에 대한 답이다. 합의 없이 수렴을 얻고 불변식을 포기하는 거래인데, 반대편에서 정족수로 같은 문제를 다루는 방식은 [[quorum-rw-n]]에, 순서를 한 지점에서 못 박는 방식은 [[paxos-5min]]에 있다. 클라이언트가 자기 편집을 즉시 다시 읽는 문제는 [[read-your-writes]]와 겹치는데, CRDT는 로컬 상태에 먼저 적용하므로 이 부분만은 공짜로 얻는다.

출처:

- Shapiro, Preguiça, Baquero, Zawirski, "Conflict-Free Replicated Data Types", SSS 2011 (INRIA RR-7687): https://inria.hal.science/inria-00609399/
- Shapiro et al., "A comprehensive study of Convergent and Commutative Replicated Data Types", INRIA RR-7506, 2011: https://inria.hal.science/inria-00555588/
- Kleppmann, Beresford, "A Conflict-Free Replicated JSON Datatype", arXiv:1608.03960: https://arxiv.org/abs/1608.03960
- Yjs 내부 구조 문서 (YATA 알고리즘): https://docs.yjs.dev/api/internals
