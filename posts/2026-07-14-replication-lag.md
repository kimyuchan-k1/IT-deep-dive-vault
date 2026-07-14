---
title: 읽기 부하를 리플리카로 넘겼는데, 방금 쓴 데이터가 사라졌다
date: 2026-07-14
day: 41
category: db
tags: [replication, read-replica, consistency, replication-lag]
related: ["[[read-your-writes]]", "[[wal-pitr]]", "[[connection-pool-sizing]]", "[[eventual-vs-strong-consistency]]", "[[mvcc-how]]"]
difficulty: 3
short_text: |
  🔥 [Day 41] 방금 쓴 데이터가 리플리카에 없다

  오해: 리플리카는 실시간 복사본
  실제: WAL 적용이 비동기→직후엔 과거를 본다

  "결제 직후 조회하니 '미결제'로 보였다"

  📖 https://github.com/kimyuchan-k1/IT-deep-dive-vault/blob/main/posts/2026-07-14-replication-lag.md
---

# 읽기 부하를 리플리카로 넘겼는데, 방금 쓴 데이터가 사라졌다

## 흔한 오해

"Read Replica 붙이면 읽기 부하가 분산되잖아. 마스터는 쓰기, 리플리카는 읽기. 리플리카는 마스터의 복사본이니까 데이터는 똑같고."

대부분 그렇게 안다. 그래서 트래픽이 늘면 리플리카 한두 대 더 붙이고 "읽기 스케일 아웃 끝"이라고 말한다. ORM 설정에서 `readOnly=true` 쿼리를 리플리카로 라우팅하도록 켜두고 잊어버린다.

**틀린 건 아닌데, "복사본"이라는 단어에 함정이 있다.** 리플리카는 마스터의 *실시간* 복사본이 아니다. 항상 **과거의 어느 시점**을 보고 있다. 그 시차가 replication lag이고, 이걸 0으로 만들 방법은 없다. 대부분의 복제는 **비동기**이기 때문이다.

## 실제 원리

### 복제는 데이터가 아니라 "로그"를 보낸다

마스터가 리플리카에 테이블을 통째로 보내는 게 아니다. **변경 로그**를 보낸다. PostgreSQL은 WAL(Write-Ahead Log), MySQL은 binlog다. 흐름은 이렇다:

1. 마스터가 트랜잭션을 커밋하면 WAL 레코드가 로컬 디스크에 쓰인다
2. WAL sender가 그 레코드를 네트워크로 리플리카에 스트리밍한다
3. 리플리카의 WAL receiver가 받아서 로컬 WAL에 쓴다
4. 리플리카의 recovery/apply 프로세스가 그 WAL을 **재생(replay)**해서 실제 데이터 페이지에 반영한다

여기서 핵심은 **커밋과 apply가 별개의 단계**라는 점이다. 마스터가 커밋을 성공으로 반환하는 시점에, 리플리카는 아직 2번은커녕 1번도 못 받았을 수 있다. 이게 **비동기 복제**의 정의다.

### lag은 세 군데에서 생긴다

replication lag을 "네트워크가 느려서"라고만 생각하면 절반만 맞다. 지연은 세 구간의 합이다:

- **전송 지연(send)**: 마스터 WAL → 리플리카 도착까지. 보통 밀리초 단위. 네트워크가 정상이면 여기는 거의 문제 안 된다.
- **쓰기 지연(write/flush)**: 받은 WAL을 리플리카 디스크에 쓰기까지.
- **적용 지연(replay)**: 받은 WAL을 실제로 재생하기까지. **여기가 진짜 병목**이다.

왜 replay가 병목인가. PostgreSQL의 스트리밍 복제에서 WAL replay는 **단일 프로세스**가 순차적으로 처리한다. 마스터는 수십 개 코어로 병렬 커밋을 쏟아내는데, 리플리카는 한 줄로 서서 재생한다. 그래서 마스터에 대량 UPDATE나 인덱스 생성, VACUUM이 몰리면 리플리카가 **못 따라간다**. lag이 초 단위, 심하면 분 단위로 벌어진다.

### "복사본"이 아니라 "지연된 재생"

정확히 말하면 리플리카는 마스터의 과거 상태를 재생 중인 플레이어다. `now`가 아니라 `now - lag`을 보고 있다. 이 lag은 부하에 따라 요동친다. 한산할 땐 5ms, 배치 잡이 돌면 30초. 이 값을 예측 가능한 상수로 취급하는 순간 사고가 난다.

PostgreSQL은 이걸 직접 볼 수 있다. 마스터에서 `pg_stat_replication`의 `replay_lag` 컬럼, 리플리카에서 `SELECT now() - pg_last_xact_replay_timestamp()`가 현재 지연이다.

## 현장 시나리오

이커머스 결제 플로우. 트래픽이 늘어 읽기를 리플리카로 넘겼다. 라우팅 규칙은 단순했다 — `SELECT`는 전부 리플리카, `INSERT/UPDATE`만 마스터.

블랙프라이데이. 마스터에 주문 쓰기가 폭주했다. WAL 생성량이 평소의 20배. 리플리카의 단일 replay 프로세스가 못 따라가면서 `replay_lag`이 **8초**까지 벌어졌다.

여기서 인과 사슬이 터진다. 사용자가 결제 버튼을 누른다 → 주문이 **마스터**에 커밋된다(성공 응답) → 프론트가 곧바로 "주문 내역" 페이지로 이동한다 → 이 조회 `SELECT`가 **리플리카**로 간다 → 리플리카는 8초 전 상태라 방금 그 주문이 **없다** → 화면에 "주문 내역 없음" 또는 "미결제"가 뜬다.

사용자는 돈이 빠져나간 걸 카드 문자로 확인했는데 앱에는 주문이 없다. CS로 항의가 쏟아진다. 개발팀은 "DB에 데이터 있는데요?"라며 마스터를 조회하고 정상이라 판단한다. 문제는 **데이터가 없는 게 아니라, 읽는 쪽이 과거를 보고 있었다**는 것. 이게 read-your-writes 일관성이 깨진 전형적 사고다. 자세한 결은 [[read-your-writes]]에서 판다.

## 실무 적용 포인트

1. **lag을 상수로 알람 걸지 마라 — 추세로 봐라.** PostgreSQL은 `pg_stat_replication.replay_lag`, MySQL은 `SHOW REPLICA STATUS`의 `Seconds_Behind_Source`. 임계는 서비스마다 다르지만, 결제·재고 같은 크리티컬 경로는 **1초 초과 시 경고**, 5초 초과 시 리플리카를 라우팅 풀에서 빼는 게 안전하다.

2. **read-your-writes가 필요한 경로는 마스터로 고정하라.** "쓰기 직후 같은 사용자가 읽는" 화면(주문 완료, 프로필 수정 직후)은 무조건 마스터 읽기(read-from-primary)로 라우팅한다. 전체를 마스터로 보내면 스케일아웃이 무의미하니, **경로 단위**로 선별한다.

3. **LSN 기반 대기(wait-for-LSN)를 써라.** 쓰기 후 커밋 LSN을 받아두고, 리플리카에서 `pg_last_wal_replay_lsn()`이 그 LSN을 넘었을 때만 읽는다. 넘기 전이면 마스터로 폴백. 넷플릭스·GitHub 같은 곳이 쓰는 정석 패턴이다.

4. **동기 복제는 만능이 아니다.** PostgreSQL `synchronous_commit = on` + `synchronous_standby_names`로 최소 1대 리플리카의 flush를 기다리게 하면 데이터 유실은 막는다. 하지만 **커밋 지연이 네트워크 왕복만큼 늘고**, 그 리플리카가 죽으면 마스터 쓰기가 멈춘다. `remote_apply`까지 켜면 replay까지 기다려 lag=0이지만 처리량이 크게 떨어진다. 트레이드오프를 알고 켜라.

5. **replay 병목을 줄여라.** 마스터에서 대량 배치는 청크로 쪼개 커밋하고(한 번에 100만 row UPDATE 금지), long-running 쿼리로 리플리카 replay를 막는 `hot_standby_feedback` 설정의 부작용(마스터 bloat 증가)을 이해하고 조정한다. [[wal-pitr]]에서 WAL 자체의 동작을 더 판다.

6. **커넥션 풀에서 마스터/리플리카를 분리 관리하라.** 리플리카를 라우팅 풀에서 뺄 때 커넥션이 우르르 마스터로 몰리면 마스터가 죽는다. 풀 사이즈는 [[connection-pool-sizing]] 공식으로 여유를 둔다.

## 더 깊은 토끼굴

- [[read-your-writes]] — 방금 쓴 걸 못 읽는 문제의 이론적 정의
- [[eventual-vs-strong-consistency]] — 비동기 복제가 파는 것과 그 비용
- [[wal-pitr]] — WAL이 복제와 백업 양쪽의 뿌리인 이유
- [[connection-pool-sizing]] — 리플리카 장애 시 마스터가 안 터지게 하는 여유
- [[mvcc-how]] — 리플리카에서 long query가 replay를 막는 진짜 이유

**1차 출처**:
- PostgreSQL 공식 문서 — Hot Standby / Streaming Replication: https://www.postgresql.org/docs/current/hot-standby.html
- PostgreSQL 공식 문서 — `pg_stat_replication` 뷰: https://www.postgresql.org/docs/current/monitoring-stats.html#MONITORING-PG-STAT-REPLICATION-VIEW
