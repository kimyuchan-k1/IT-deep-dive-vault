---
title: S3가 방금 올린 파일을 "없다"고 한 시절이 있었다
date: 2026-07-18
day: 43
category: cloud
tags: [s3, consistency, aws, eventual-consistency, distributed]
related: ["[[quorum-rw-n]]", "[[replication-lag]]", "[[cap-theorem]]"]
difficulty: 3
short_text: |
  ☁️ [Day 43] S3가 방금 쓴 걸 없다던 시절
  오해: 쓰면 바로 읽힌다
  실제: 덮어쓰기는 eventual→2020년 strong
  'PUT 직후 GET이 옛것'
  📖 https://github.com/kimyuchan-k1/IT-deep-dive-vault/blob/main/posts/2026-07-18-s3-consistency-evolution.md
---

# S3가 방금 올린 파일을 "없다"고 한 시절이 있었다

## 흔한 오해

> "S3는 파일 스토리지잖아. PUT 하고 바로 GET 하면 당연히 방금 올린 게 나오지."

로컬 파일시스템의 감각을 그대로 클라우드에 투영한 생각이다. `write()` 하고 `read()` 하면 방금 쓴 바이트가 나오는 게 당연하니까. 그래서 많은 파이프라인이 "업로드 → 곧바로 다운로드해서 후처리" 구조로 짜였다.

그런데 2020년 12월 이전의 S3를 써본 사람은 안다. 방금 덮어쓴 객체를 GET 했는데 **옛날 내용**이 나오거나, 방금 지운 객체가 GET에서 **여전히 200으로 살아있거나**, 방금 만든 객체 리스트에 **안 잡히는** 일이 실제로 있었다. 버그가 아니라 설계였다.

## 실제 원리

S3는 단일 디스크가 아니다. 하나의 객체는 여러 AZ(가용영역)에 걸쳐 복제되고, 메타데이터(어떤 키가 어떤 버전을 가리키는가)는 또 별도의 분산 인덱스 계층이 관리한다. 여기서 **데이터 복제**와 **인덱스 갱신**이 즉시 원자적으로 끝나지 않는다는 게 문제의 뿌리였다.

2020년 이전 S3의 일관성 모델은 두 갈래로 갈렸다:

**1) 새 객체(PUT of new key) → read-after-write 일관성 O**
처음 만드는 키는 GET 하면 반드시 나왔다. 인덱스에 "이 키 = 이 버전" 항목을 새로 추가하는 것이므로, 존재하지 않던 것이 존재하게 되는 단방향 전이라 충돌이 없었다.

단, 여기에 함정이 하나 더 있었다. 만드는 키에 대해 **먼저 GET(또는 HEAD)을 날려 404를 받은 적이 있으면**, 그 404가 캐시되어 실제로 PUT 한 뒤에도 한동안 404가 나올 수 있었다. "존재를 미리 물어본" 대가였다.

**2) 덮어쓰기(overwrite PUT)와 삭제(DELETE) → eventual consistency**
이미 있는 키를 새 버전으로 덮어쓰거나 지우는 것은 인덱스의 포인터를 **바꾸는** 연산이다. 이 변경이 모든 복제본과 인덱스 노드로 전파되는 데 시간이 걸렸다. 전파 도중에 어떤 노드에 붙느냐에 따라 옛 버전이 나올 수도, 새 버전이 나올 수도 있었다.

여기가 핵심이다. 이건 [[replication-lag]]과 정확히 같은 종류의 문제다. 쓰기는 한 곳에서 받았는데 읽기가 아직 전파 안 된 다른 복제본에 붙는 것. 분산 스토리지에서 "읽은 값이 방금 쓴 값 이상"임을 보장하려면 [[quorum-rw-n]]처럼 R+W>N을 강제하거나, 인덱스 계층이 읽기 요청을 항상 최신 버전으로 라우팅해줘야 한다. S3는 오래도록 전자의 비용(모든 읽기에 정족수 검증)을 전 세계 규모에서 감당하지 못해 eventual로 뒀던 것이다.

**2020년 12월, 이게 바뀌었다.** AWS는 S3의 모든 GET/PUT/LIST에 대해 **strong read-after-write consistency**를 발표했다. 덮어쓰기 직후 GET도, 삭제 직후 GET도, LIST 결과도 즉시 최신 상태를 반영한다. 추가 비용 없음, 성능 저하 없음, API 변경 없음. 내부적으로 메타데이터 인덱스 계층을 강한 일관성으로 재설계해서, 읽기가 항상 확정된 최신 버전을 보도록 만들었다.

## 현장 시나리오

한 데이터 파이프라인이 이렇게 동작했다. 매시간 집계 결과를 `s3://bucket/report/latest.json`에 **덮어쓰고**, 곧바로 다운스트림 Lambda가 같은 키를 GET 해서 대시보드로 밀었다.

2020년 여름의 어느 날, 정산 리포트가 하루 종일 **한 시간 전 숫자**를 보여줬다. 코드는 멀쩡했다. 로그를 보니 PUT은 200, 곧이은 GET도 200이었다. 문제는 그 GET이 **덮어쓰기 전파가 끝나기 전에** 옛 버전을 든 복제본에 붙었다는 것.

인과 사슬은 이랬다. 집계 완료 → `latest.json` overwrite PUT → 50ms 뒤 Lambda가 GET → eventual 구간이라 옛 JSON 반환 → 대시보드에 옛 숫자 표시 → 다음 시간 덮어쓰기까지 그대로 → 재무팀이 "숫자가 안 바뀐다"고 신고. 임시 처방은 GET에 `If-Match` ETag 재시도 루프를 붙이는 것이었다. 2020년 12월 이후엔 이 루프가 통째로 필요 없어졌다.

## 실무 적용 포인트

1. **지금 S3는 strong consistency다.** 2020년 12월 이후 리전이면 "PUT 후 재시도 루프", "GET-then-PUT 회피 로직" 같은 옛 workaround는 지우는 게 맞다. 죽은 방어 코드는 오히려 버그를 숨긴다.

2. **하지만 "consistency ≠ atomicity"다.** 같은 키에 동시에 두 PUT이 들어가면 여전히 **last-writer-wins**다. 순서 보장이 필요하면 `If-None-Match: *`(생성 전용)이나 조건부 쓰기(`If-Match: <etag>`)로 낙관적 락을 걸어라.

3. **크로스 리전 복제(CRR)는 여전히 eventual이다.** strong consistency는 **한 리전 안**의 이야기다. `us-east-1`에 쓰고 `ap-northeast-2` 복제본을 읽으면 지연이 있다. 멀티리전 읽기는 [[replication-lag]] 가정을 유지해라.

4. **다른 서비스를 통한 알림 경로는 별개 지연이 있다.** S3 이벤트 → SNS/SQS/Lambda 트리거는 보통 몇 초, 드물게 더 걸린다. "이벤트가 왔다 = 객체가 읽힌다"는 이제 참이지만, "객체를 썼다 = 즉시 이벤트가 온다"는 아니다.

5. **LIST는 강해졌지만 대량 페이지네이션은 여전히 조심.** 단일 LIST는 최신을 반영하지만, `ListObjectsV2`를 수천 페이지 넘기는 중에 다른 클라이언트가 키를 추가/삭제하면 스냅샷이 아니다. 전수 처리엔 인벤토리(S3 Inventory) 리포트를 써라.

6. **옛 코드/문서 신뢰 주의.** 2020년 이전에 쓰인 블로그·스택오버플로 답변은 "S3는 eventual consistency"라고 단언한다. 날짜를 확인하고, 지금 기준으로는 틀린 전제임을 인지하고 읽어라.

## 더 깊은 토끼굴

S3의 이 변화는 "eventual이냐 strong이냐"가 코드 한 줄이 아니라 **글로벌 인덱스 계층의 재설계**가 걸린 문제라는 걸 보여준다. 같은 트레이드오프를 다른 각도에서 본 글들:

- [[quorum-rw-n]] — R+W>N이 어떻게 강한 읽기를 만드는가
- [[replication-lag]] — 방금 쓴 데이터가 읽기 복제본에서 사라지는 이유
- [[cap-theorem]] — 일관성과 가용성을 동시에 다 가질 수 없는 근본 제약
- [[dynamodb-consistency-read]] — DynamoDB의 eventual vs strong read 옵션

출처:
- AWS 공식 발표: [Amazon S3 Update – Strong Read-After-Write Consistency (2020-12-01)](https://aws.amazon.com/blogs/aws/amazon-s3-update-strong-read-after-write-consistency/)
- AWS S3 문서: [Amazon S3 data consistency model](https://docs.aws.amazon.com/AmazonS3/latest/userguide/Welcome.html#ConsistencyModel)
