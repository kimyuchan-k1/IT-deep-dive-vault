---
title: 방금 쓴 내 댓글이 새로고침하니 사라졌다 — Read-your-writes
date: 2026-07-23
day: 47
category: distributed
tags: [consistency, replication, session-consistency, monotonic-read]
related: ["[[replication-lag]]", "[[eventual-vs-strong-consistency]]", "[[cap-theorem-real-meaning]]", "[[quorum-rw-n]]", "[[lamport-vs-vector-clock]]"]
difficulty: 3
short_text: |
  ⚠️ [Day 47] 방금 쓴 내 댓글이 새로고침하니 사라졌다
  오해: 복제 지연은 남 얘기
  실제: 쓰기=마스터, 읽기=레플리카, 내 글이 없다
  📖 https://github.com/kimyuchan-k1/IT-deep-dive-vault/blob/main/posts/2026-07-23-read-your-writes.md
---

# 방금 쓴 내 댓글이 새로고침하니 사라졌다 — Read-your-writes

댓글을 달았다. 화면에 잘 떴다. F5를 눌렀다. 댓글이 없다. 3초 뒤 다시 누르니 다시 나타난다. 버그 리포트에는 "간헐적 데이터 유실"이라고 적히지만, 데이터는 한 번도 유실된 적이 없다. 사라진 건 데이터가 아니라 **일관성 보장**이다.

## 흔한 오해

"Read Replica는 그냥 읽기 부하 분산용이잖아. 복제 지연이래봤자 수십 ms인데, 사람이 그걸 어떻게 느껴?"

그리고 한 단계 더 나간 통념도 있다: "그래서 eventual consistency는 결국 시간이 지나면 맞춰지니까 괜찮다."

**완전히 틀린 말은 아니다. 다만 주어가 빠졌다.** "결국 맞춰진다"는 시스템 전체의 관점이다. 문제는 사용자 한 명이 자기가 방금 한 쓰기를 다음 읽기에서 못 볼 때 생긴다. 시스템은 정상이고, 데이터도 정상이고, 오직 **한 사람의 경험만 깨진다**. 그래서 재현이 안 되고, 그래서 "간헐적"이라고 티켓에 적힌다.

## 실제 원리

### 문제는 지연이 아니라 "라우팅"

쓰기는 마스터로 간다. 읽기는 로드밸런서가 레플리카 중 하나로 보낸다. 여기서 사용자의 두 요청이 서로 다른 물리 노드에 도착한다는 사실이 전부다.

```
T0  POST /comments  → Primary   (커밋 완료)
T1  GET  /comments  → Replica-2 (아직 T0 반영 전)  ← 여기서 사라진다
T2  GET  /comments  → Replica-1 (반영 완료)        ← 다시 나타난다
```

복제 지연이 10ms여도, 사용자의 다음 요청이 8ms 뒤에 오면 깨진다. 브라우저의 낙관적 UI 업데이트가 화면에 즉시 그려주기 때문에 사용자는 그 8ms 창을 정확히 조준한다. 여기가 핵심이다: **복제 지연이 짧아서 안전한 게 아니라, 읽기가 어디로 가느냐가 통제되지 않아서 위험하다.**

### 세션 보장 4형제

Eventual consistency 위에 얹는 클라이언트 중심 보장(session guarantees)이 정확히 이 문제를 위해 정의돼 있다. 1994년 Bayou 논문에서 정리된 네 가지다.

1. **Read-your-writes** — 내가 쓴 값은 내 다음 읽기에서 반드시 보인다.
2. **Monotonic Read** — 한 번 본 값보다 과거로 되돌아가지 않는다. (댓글이 보였다가 다시 사라지지 않는다)
3. **Monotonic Write** — 같은 클라이언트의 쓰기는 순서대로 적용된다.
4. **Writes-follow-reads** — 내가 읽은 값에 근거해 쓴 값은, 그 읽은 값보다 뒤에 정렬된다.

앞의 두 개가 실무에서 90%다. 그리고 둘은 다른 문제다. Read-your-writes만 보장해도 "봤다 안 봤다" 깜빡임([[replication-lag]]이 서로 다른 두 레플리카에서 다를 때)은 남는다.

### 구현 축은 세 가지뿐

- **Sticky read (write-through-primary)**: 쓰기 후 N초 동안 그 사용자의 읽기를 마스터로 보낸다. 가장 단순하고 가장 널리 쓰인다. 대가는 마스터 부하.
- **버전 토큰 (LSN/GTID pinning)**: 쓰기 응답에 그 시점의 로그 위치(Postgres `pg_current_wal_lsn()`, MySQL GTID)를 담아 클라이언트에 준다. 다음 읽기에 이 토큰을 실어 보내고, 레플리카는 자기 적용 위치가 그보다 뒤일 때만 응답한다. 정확하고, 마스터 부하도 안 늘고, 대신 애플리케이션이 토큰을 물고 다녀야 한다.
- **Sticky session (세션-노드 고정)**: 한 사용자를 같은 레플리카에 계속 붙인다. Monotonic Read는 공짜로 얻지만, 그 노드가 죽으면 보장이 함께 죽는다.

토큰 방식이 [[lamport-vs-vector-clock]]의 논리적 시계와 같은 뼈대다. "언제"가 아니라 "어느 버전 이후"로 말한다.

## 현장 시나리오

한 커머스 플랫폼의 상품 리뷰. Primary 1대 + Replica 3대, 평균 복제 지연 15ms. 마케팅 이벤트로 트래픽이 6배가 되자 복제 지연이 **15ms에서 900ms**로 올라갔다.

인과 사슬은 이렇다. 트래픽 증가 → 마스터 쓰기 폭증 → WAL 생성 속도가 레플리카 적용 속도를 초과 → 지연 900ms → 리뷰 작성 후 리다이렉트(약 200ms)가 지연 창 안에 완전히 들어감 → **리뷰 작성자의 90%가 자기 리뷰를 못 봄** → "리뷰가 등록 안 된다"는 CS 문의 폭주 → 사용자들이 재작성 → 중복 리뷰 생성 → 쓰기 부하가 더 늘어 복제 지연이 더 벌어짐.

마지막 고리가 이 장애의 성격을 결정했다. 일관성 깨짐이 사용자 행동을 바꾸고, 그 행동이 원인을 증폭시킨다. 지연이 900ms가 아니라 3초가 된 건 DB가 아니라 사람 때문이었다.

수정은 코드 세 줄이었다. 리뷰 POST 응답에 LSN을 실어주고, 이후 5초간 같은 사용자의 GET에 그 LSN을 태그로 붙였다. 마스터 부하는 2% 늘었고 CS 문의는 사라졌다.

## 실무 적용 포인트

1. **쓰기 후 읽기 경로를 먼저 그려라.** "POST 후 몇 ms 안에 GET이 오는가"를 측정한다. 리다이렉트 기반 플로우는 보통 100~300ms — 대부분의 복제 지연 p99보다 짧다. 위험 구간이다.

2. **가장 싼 1차 방어는 sticky read.** 쓰기 발생 후 **복제 지연 p99의 2~3배**(보통 1~5초) 동안 해당 사용자 세션의 읽기를 primary로 라우팅. 세션 쿠키나 Redis에 `last_write_at` 타임스탬프 하나면 된다.

3. **정확도가 필요하면 LSN/GTID 토큰.** PostgreSQL은 `pg_current_wal_lsn()`으로 쓰기 위치를, 레플리카에서 `pg_last_wal_replay_lsn()`으로 적용 위치를 얻는다. MySQL은 `WAIT_FOR_EXECUTED_GTID_SET(gtid, timeout)`으로 레플리카에서 직접 대기할 수 있다.

4. **복제 지연을 SLI로 감시하라.** PostgreSQL `pg_stat_replication`의 `replay_lag`, MySQL `Seconds_Behind_Master`. 임계값을 p99 기준으로 잡고(예: 500ms) 넘으면 해당 레플리카를 읽기 풀에서 자동 제외한다.

5. **"내 데이터"와 "남의 데이터"를 다르게 취급하라.** 내 주문 내역·내 리뷰·내 프로필은 read-your-writes 필수. 남의 게시글 목록·인기 순위는 1~2초 stale해도 무해하다. 전 구간에 강한 일관성을 거는 건 [[eventual-vs-strong-consistency]]의 비용을 이유 없이 다 내는 것이다.

6. **여러 탭/기기는 별개 세션이다.** 쿠키 기반 sticky는 같은 브라우저에서만 유효하다. 모바일 앱에서 쓰고 웹에서 읽는 시나리오까지 보장하려면 세션이 아니라 **사용자 ID 단위**로 토큰을 저장해야 한다.

## 더 깊은 토끼굴

- [[replication-lag]]: 지연 자체를 줄이는 쪽 — 동기 복제, `synchronous_commit` 단계별 트레이드오프
- [[eventual-vs-strong-consistency]]: 세션 보장은 그 사이에 있는 "충분히 강한" 지점
- [[quorum-rw-n]]: R+W>N이 read-your-writes를 어디까지 대신해주고 어디서 못 하는가
- [[cap-theorem-real-meaning]]: PACELC의 L(지연) 축이 바로 이 선택
- [[lamport-vs-vector-clock]]: 버전 토큰의 이론적 뿌리

**출처**:
- Terry et al., "Session Guarantees for Weakly Consistent Replicated Data" (PDIS 1994): https://www.cs.utexas.edu/~lorenzo/corsi/cs380d/papers/session.pdf
- PostgreSQL 공식 문서 — Hot Standby / 복제 위치 함수: https://www.postgresql.org/docs/current/functions-admin.html#FUNCTIONS-RECOVERY-CONTROL
- MySQL 공식 문서 — `WAIT_FOR_EXECUTED_GTID_SET()`: https://dev.mysql.com/doc/refman/8.0/en/gtid-functions.html
- Jepsen — Consistency Models (읽기 일관성 계층도): https://jepsen.io/consistency
