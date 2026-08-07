---
title: R+W > N을 맞췄는데 읽기가 옛 값을 돌려줬다
date: 2026-08-07
day: 61
category: distributed
tags: [consistency, quorum, linearizability, replication, lww, pacelc]
related: ["[[cap-theorem-real-meaning]]", "[[read-your-writes]]", "[[replication-lag]]", "[[crdt-intro]]", "[[raft-easier-than-paxos]]"]
difficulty: 4
short_text: |
  🔥 [Day 61] R+W>N인데 옛 값을 읽었다

  오해: 쿼럼 겹치면 최신
  실제: LOCAL_QUORUM→옆 DC 쿼럼 밖

  "결제했는데 잔액 그대로"

  📖 https://github.com/kimyuchan-k1/IT-deep-dive-vault/blob/main/posts/2026-08-07-eventual-vs-strong-consistency.md
---

# R+W > N을 맞췄는데 읽기가 옛 값을 돌려줬다

설정 파일에는 `RF=3`, 쓰기 `LOCAL_QUORUM`, 읽기 `LOCAL_QUORUM`이라고 적혀 있었다. 2 + 2 > 3. 아키텍처 문서에는 그 아래 "따라서 강한 일관성"이라고 한 줄이 더 있었다. 그 한 줄이 결제 중복 청구 37건의 출발점이었다.

## 흔한 오해

> "읽기 쿼럼과 쓰기 쿼럼이 최소 한 노드에서 겹치잖아. 그럼 그 노드가 최신 값을 갖고 있으니 항상 최신을 읽는 거 아냐?"

Dynamo 논문의 `(N, R, W)` 표가 그렇게 읽히고, Cassandra 문서에도 `R + W > N`이 그대로 나온다. 그래서 이 부등식은 일관성 설정의 체크섬처럼 쓰인다. 부등식을 맞췄으면 끝났다고 본다.

겹친다는 것까지는 참이다. 집합 두 개의 크기 합이 전체보다 크면 교집합은 비지 않는다. 틀린 건 그 다음 두 가정이다. **겹친 노드가 최신 값을 갖고 있다는 것**, 그리고 **여러 응답 중에서 최신을 골라낼 수 있다는 것**. 쿼럼 산수는 둘 중 어느 것도 주지 않는다.

## 실제 원리

### N은 클러스터 크기가 아니라 "그 키의 복제본 수"다

여기서 대부분의 사고가 시작된다. `LOCAL_QUORUM`은 이름 그대로 **로컬 DC 안의** 복제본만 세는 정족수다. 서울·도쿄·싱가포르 3개 DC에 각각 `RF=3`이면 그 키의 복제본은 9개다. 서울에서 `LOCAL_QUORUM`으로 쓰고 서울에서 `LOCAL_QUORUM`으로 읽으면 2 + 2 = 4다. **N은 3이 아니라 9였고, 4 > 9는 거짓이다.**

같은 DC 안에 갇혀 있는 동안은 이 거짓이 드러나지 않는다. 서울 쿼럼끼리는 겹치니까. 드러나는 건 요청이 DC 경계를 넘는 순간이다 — GSLB 페일오버, 리전 헬스체크 흔들림, 모바일 클라이언트의 애니캐스트 재라우팅, 배치 잡의 다른 리전 실행. 그 순간 읽기는 **비동기 복제가 아직 도착하지 않은 복제본 집합**을 본다.

### 최신을 고르는 건 쿼럼이 아니라 시계다

노드 세 개가 서로 다른 값을 돌려주면 누군가는 승자를 정해야 한다. Cassandra의 기본 규칙은 **cell 단위 타임스탬프 Last-Write-Wins**다. 큰 타임스탬프가 이긴다. 그 타임스탬프는 대개 코디네이터 노드의 벽시계다.

그래서 정확성이 NTP 동기화 품질에 종속된다. 두 노드의 시계가 40ms 어긋나 있으면, 실제로는 나중에 도착한 쓰기가 더 작은 타임스탬프를 달고 들어와 **조용히 진다**. 에러도, 경고도, 충돌 로그도 없다. 읽기는 성공하고 값만 과거다. [[crdt-intro]]가 병합 함수를 값 타입 안에 넣어서 푸는 문제가 정확히 이 지점이다.

### 실패한 쓰기도 되살아난다

`W=2`를 못 채워 타임아웃을 반환한 쓰기를 생각해 보자. 이미 1개 복제본에는 값이 적혔고, **분산 스토어에는 롤백이 없다.** 나중에 read repair나 anti-entropy가 돌면 그 값이 다른 복제본으로 **번져서 성공한 쓰기가 된다**. "실패했다는 응답"과 "쓰이지 않았다"는 같은 말이 아니다. [[idempotency-key]]가 재시도 경로에 필요한 이유가 여기서도 나온다.

### Linearizability는 쿼럼 위에 왕복을 한 번 더 요구한다

이론 쪽에서 이 문제는 ABD 알고리즘으로 정리돼 있다. 쿼럼 읽기로 최신 값을 찾은 다음, **읽은 값을 다시 쿼럼에 써서 확정한 뒤** 클라이언트에 돌려준다. 이 write-back이 없으면 첫 읽기가 최신 복제본을 맞추고 둘째 읽기가 아직 수리 안 된 복제본을 맞아 시간이 거꾸로 흐른다.

리더 기반 시스템도 공짜가 아니다. 파티션 반대편의 옛 리더는 자기가 아직 리더인 줄 안다. 그래서 Raft는 읽기 전에 **ReadIndex**로 하트비트 한 라운드를 돌려 과반의 지지를 재확인하거나 리더 리스를 쓴다. [[raft-easier-than-paxos]]에서 합의의 비용이라고 부른 것이 읽기 경로에도 그대로 붙는다.

### 그래서 비용은 "왕복 한 번"이 아니라 "가장 느린 과반"

강한 일관성의 지연은 평균 RTT가 아니라 **과반이 채워지는 시점**이 결정한다. 복제본 하나가 GC로 300ms 멈추면 그 요청의 지연은 300ms다. 꼬리 지연이 그대로 응답 지연이 된다.

가격표는 제품마다 문서에 적혀 있다. DynamoDB의 strongly consistent read는 eventually consistent read의 **RCU 2배**를 먹고, GSI에서는 아예 지원하지 않는다. Cassandra의 경량 트랜잭션(`IF NOT EXISTS`)은 Paxos 때문에 **왕복 4번**이 붙는다. Spanner는 이 문제를 하드웨어로 산다 — GPS와 원자시계로 시각 불확실성 구간 ε를 한 자릿수 ms(논문 실측 평균 약 4ms)로 눌러놓고, 커밋마다 2ε만큼 **일부러 기다린다**. [[cap-theorem-real-meaning]]에서 PACELC의 `ELC`라고 부른 항이 이 밀리초들이다.

## 현장 시나리오

동남아 결제 SaaS. Cassandra를 서울·싱가포르·도쿄 3 DC, DC당 `RF=3`으로 운영했다. 잔액 테이블은 읽기·쓰기 모두 `LOCAL_QUORUM`이고, 사용자는 GSLB로 가장 가까운 리전에 붙는다. DC 간 복제 지연은 평상시 p50 60ms, p99 400ms.

사고 당일, 싱가포르 리전 앞단 ALB의 헬스체크가 배포 중 30초간 흔들렸다. GSLB가 해당 사용자 트래픽 일부를 도쿄로 넘겼다.

인과는 이랬다. 사용자가 싱가포르에서 12,000원을 결제한다 → 싱가포르 `LOCAL_QUORUM` 2/3 성공, 앱은 200을 받는다 → 200ms 뒤 잔액 조회 요청이 도쿄로 라우팅된다 → 도쿄 3개 복제본에는 아직 그 쓰기가 도착하지 않았다(그 순간 지연 380ms) → 화면 잔액이 결제 전 금액 그대로다 → 사용자가 결제 실패로 판단하고 다시 누른다 → 두 번째 결제도 성공한다 → 두 쓰기의 코디네이터 시계가 40ms 어긋나 있어 LWW가 **먼저 일어난 쓰기를 승자로 남긴다** → 잔액은 한 번 차감된 것처럼 보이는데 결제 원장에는 두 건이 남는다.

그날 하루 2,300만 건의 잔액 쓰기 중 라우팅이 흔들린 30초 창에 걸린 건 약 4,600건. 그중 사용자가 실제로 재시도해 중복 청구가 된 건 37건이었다. Cassandra 쪽 에러율은 0%였고, 복제 지연 대시보드도 400ms를 넘지 않았다. **모든 지표가 정상인 채로 일어난 사고다.**

원인 한 줄은 아키텍처 문서의 그 문장이었다. 부등식은 `2 + 2 > 3`이 아니라 `2 + 2 > 9`였어야 했다.

## 실무 적용 포인트

1. **쿼럼 산수를 "읽는 곳" 기준으로 다시 해라.** 멀티 DC에서 `LOCAL_QUORUM` 조합은 글로벌 `R + W > N`을 만족하지 않는다. 선택지는 셋뿐이다. 세션을 리전에 sticky하게 고정하거나(가장 싸다), 쓰기를 `EACH_QUORUM`으로 올리거나(서울↔버지니아 RTT는 대략 180ms대다), 그 화면은 eventual임을 받아들이고 UI에 반영한다.

2. **잔액·재고·카운터에 LWW를 쓰지 마라.** 시계로 승자를 정하는 순간 정확성이 NTP 품질에 묶인다. 대안은 조건부 쓰기(DynamoDB `ConditionExpression`, Cassandra `IF` — 대신 Paxos 왕복 4번), 아니면 값 타입 자체를 병합 가능하게 바꾸는 것([[crdt-intro]]의 PN-Counter).

3. **읽기 일관성 옵션의 기본값을 코드에서 확인해라.** etcd Go 클라이언트는 기본이 linearizable read이고 `clientv3.WithSerializable()`이 성능 최적화처럼 보이지만 stale read 허용 스위치다. DynamoDB는 반대로 기본이 eventually consistent라 `ConsistentRead=true`를 명시해야 한다(RCU 2배, GSI 불가).

4. **"쓰기 실패"를 "안 쓰였다"로 읽지 마라.** 타임아웃 난 쓰기는 일부 복제본에 남아 나중에 확산될 수 있다. 결제·주문처럼 재시도가 붙는 경로는 [[idempotency-key]]로 서버 쪽에서 중복을 흡수해야 한다. 클라이언트 재시도만으로는 절대 안전해지지 않는다.

5. **stale read를 지표로 만들어라.** 복제 지연(ms)은 원인 지표지 결과 지표가 아니다. 카나리 프로브로 "쓰기 직후 200ms 내 재조회 시 옛 값이 오는 비율"을 직접 측정해 SLI로 걸어라. [[replication-lag]] 대시보드만 보면 이번 사고는 끝까지 안 보인다.

6. **강한 일관성이 필요한 필드만 격리해라.** 잔액과 주문 상태만 조건부 쓰기 경로로 빼고 나머지 조회성 데이터는 eventual로 둔다. 전 구간 linearizable은 가장 느린 복제본의 꼬리 지연을 전 서비스가 나눠 갖는 선택이다.

## 더 깊은 토끼굴

- [[cap-theorem-real-meaning]] — PACELC의 `ELC`, 즉 장애가 없을 때도 내는 값
- [[read-your-writes]] — 전역 일관성 대신 세션 단위 보장으로 도망가는 법
- [[replication-lag]] — 이번 사고에서 끝까지 정상으로 보인 그 지표
- [[crdt-intro]] — 승자를 시계로 정하지 않는 값 타입
- [[raft-easier-than-paxos]] — 리더가 있어도 읽기에 왕복이 필요한 이유

**출처**

- DeCandia et al., *Dynamo: Amazon's Highly Available Key-value Store* (SOSP 2007) — https://www.allthingsdistributed.com/files/amazon-dynamo-sosp2007.pdf
- Corbett et al., *Spanner: Google's Globally-Distributed Database* (OSDI 2012) — https://research.google/pubs/pub39966/
- Apache Cassandra Documentation, *Dynamo — Consistency Levels* — https://cassandra.apache.org/doc/latest/cassandra/architecture/dynamo.html
- AWS, *DynamoDB Read Consistency* — https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/HowItWorks.ReadConsistency.html
- Jepsen, *Consistency Models* — https://jepsen.io/consistency

정리하면, `R + W > N`은 일관성 보장이 아니라 **교집합이 비지 않는다는 산수**다. 그 교집합에서 최신을 골라내는 건 시계가 하고, 그 교집합이 실제로 사용자가 읽는 복제본 집합인지는 라우팅이 정한다. 부등식 옆에 N이 무엇을 세는 숫자인지 적혀 있지 않다면, 그 문서는 아직 검산되지 않았다.
