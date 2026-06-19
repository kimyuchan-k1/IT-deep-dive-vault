---
title: SNS 하나로 세 소비자에게 직접 팬아웃했는데, 한 소비자가 죽자 그쪽 주문만 조용히 증발했다
date: 2026-06-19
day: 17
category: kafka
tags: [sqs, sns, eventbridge, fan-out, event-driven, messaging]
related: ["[[dead-letter-queue]]", "[[pull-vs-push-model]]", "[[at-least-once-vs-at-most-once]]", "[[outbox-pattern]]", "[[idempotency-key]]", "[[backpressure-patterns]]"]
difficulty: 2
short_text: |
  🔥 [Day 17] SNS로 팬아웃하다 주문이 증발
  오해: 셋 다 메시지 전송
  실제: 큐=버퍼·펍섭=팬아웃·버스=라우팅
  "소비자 하나 죽자 SNS가 버렸다"
  📖 https://github.com/kimyuchan-k1/IT-deep-dive-vault/blob/main/posts/2026-06-19-sqs-vs-sns-vs-eventbridge.md
---

# SNS 하나로 세 소비자에게 직접 팬아웃했는데, 한 소비자가 죽자 그쪽 주문만 조용히 증발했다

## 흔한 오해

"SQS, SNS, EventBridge? 결국 다 AWS에서 메시지 보내는 도구잖아. 셋 다 비동기로 이벤트를 흘려보내는 거니까 익숙한 거 하나 골라 쓰면 되지. SNS가 여러 군데로 뿌려주니까 주문 이벤트 하나 만들면 SNS 토픽에 던지고, 결제·재고·메일 Lambda를 구독시켜 두면 끝 아닌가. 큐를 또 끼우는 건 괜히 단계만 늘리는 거고."

처음 이벤트 드리븐을 붙이면 거의 다 이렇게 생각한다. 셋 다 "publish/subscribe 비슷한 그림"으로 보이니, 아무거나 골라도 메시지는 도착할 것 같다. 그래서 "팬아웃이 필요하면 SNS"라는 한 줄 공식으로 끝내 버린다.

**셋은 전송 도구가 아니라 서로 다른 전달 모델이다.** SQS는 **버퍼(큐)**, SNS는 **팬아웃(펍섭)**, EventBridge는 **라우팅(버스)**. 무엇이 갈리느냐 — 메시지가 **소비자를 기다려 주느냐**, **몇 명에게 복제되느냐**, **내용으로 분기되느냐**다. SNS 단독으로 소비자에게 직접 꽂는 구성은 이 중 첫 번째, "기다려 주는 버퍼"를 빼먹은 설계다.

## 실제 원리

세 서비스의 본질적 차이는 "메시지가 **어디에 머무느냐**"다.

### SQS — 소비자를 기다려 주는 버퍼

SQS는 **풀(pull) 기반 큐**다([[pull-vs-push-model]]). 프로듀서가 메시지를 넣으면 큐에 **최대 14일까지 쌓여** 소비자가 `ReceiveMessage`로 가져갈 때까지 기다린다. 핵심은 이 "기다림"이다. 소비자가 느리거나 죽어 있어도 메시지는 큐에 남는다. 가져간 메시지는 **visibility timeout** 동안 다른 소비자에게 안 보이고, 처리 성공 후 `DeleteMessage`를 해야 사라진다. 처리에 실패해 timeout이 지나면 다시 보이고, 일정 횟수 실패하면 **DLQ**로 떨어진다([[dead-letter-queue]]).

단, **한 메시지는 한 소비자만** 가져간다. SQS 큐 하나에 워커 여러 대를 붙이면 부하 분산일 뿐, 같은 메시지를 둘이 받지 않는다. 즉 SQS는 **점대점**이지 팬아웃이 아니다.

### SNS — 한 번에 N명에게 복제하는 팬아웃

SNS는 **푸시(push) 기반 펍섭**이다. 토픽에 던진 메시지를 **구독자 수만큼 복제**해 동시에 밀어 넣는다. 결제·재고·메일이 각자 구독하면 한 번의 publish로 셋 다 받는다. 여기까지가 오해가 옳은 부분이다.

문제는 SNS가 **버퍼가 없다**는 것이다. SNS는 받은 즉시 각 구독자에게 **배달을 시도**하고, 실패하면 정책에 따라 **재시도하다 끝내 버린다(drop)**. 구독자가 큐가 아니라 Lambda·HTTP 엔드포인트라면, 그 소비자가 다운돼 있는 동안 도착한 이벤트는 **머물 곳이 없다.** SNS 입장에선 "배달 시도 → 실패 → 재시도 소진 → 폐기"가 정상 동작이다.

### 둘을 합치는 정석 — SNS → SQS 팬아웃

그래서 실무의 표준은 **SNS로 복제하고, 각 소비자 앞에 SQS를 둔다.** SNS 토픽 하나에 소비자별 SQS 큐 3개를 구독시키면 — 한 번 publish가 3개 큐에 각각 복사되고, 각 큐는 **자기 소비자만의 독립 버퍼**가 된다. 재고 소비자가 죽어도 그 큐에 메시지가 쌓이고, 살아나면 밀린 걸 처리한다. 큐마다 DLQ·재시도·처리 속도를 **따로** 가져간다. "팬아웃(SNS) + 내구성 버퍼(SQS)"를 둘 다 얻는 구성이다.

### EventBridge — 내용으로 분기하는 라우팅 버스

EventBridge는 다른 축이다. SNS가 "구독한 사람 전부에게 뿌린다"면, EventBridge는 **이벤트 내용(JSON)을 보고 규칙으로 분기**한다. `"detail.amount > 10000"`이거나 `"source": "billing"`인 이벤트만 특정 타깃으로 보내는 식의 **콘텐츠 기반 라우팅**이 핵심이다. 80개 넘는 AWS 서비스 이벤트와 SaaS 이벤트를 받는 기본 버스, **아카이브·리플레이**, 스키마 레지스트리, 스케줄러까지 붙는다.

대신 공짜가 아니다. 라우팅·필터링 단을 거치므로 **지연이 SQS/SNS보다 크고**(수백 ms대), 순수 처리량·단순 전달 비용은 불리하다. 즉 EventBridge는 "많은 출처에서 온 이벤트를 **내용 보고 똑똑하게 나눠 보낼 때**"고, SNS/SQS는 "**알려진 소비자에게 빠르고 싸게 전달**할 때"다.

## 현장 시나리오

한 커머스의 주문 서비스가 `order.placed`를 SNS 토픽에 던지고, **결제·재고·메일 Lambda 셋을 토픽에 직접 구독**시켰다. 2년간 멀쩡했다. 인과 사슬은 이랬다:

- 재고 Lambda가 의존하던 외부 창고 API가 30분간 5xx를 뱉었다. 재고 Lambda는 호출마다 타임아웃 후 실패했다
- SNS는 Lambda **비동기 호출**로 밀어 넣는데, 실패 시 정책에 따라 몇 차례 재시도하다 **소진되면 폐기**한다. 재고 Lambda엔 **on-failure DLQ가 설정돼 있지 않았다**
- 그 30분 사이 들어온 주문 이벤트들은 재시도가 모두 깨진 뒤 **흔적 없이 사라졌다.** 결제와 메일은 정상이라, 주문은 결제됐는데 **재고만 차감 안 된 상태**가 됐다
- 모니터링엔 에러 카운트만 잠깐 튀고 끝났다. 큐 깊이 같은 "밀린 일감" 지표가 **존재하지 않았다** — 버퍼가 없으니 백로그도 없었다
- 발견은 사흘 뒤, 재고 수량이 실주문과 어긋난 걸 운영팀이 손으로 맞추다 드러났다

수정은 단순했다. **SNS와 각 Lambda 사이에 SQS 큐를 끼웠다.** 토픽 → 소비자별 SQS 3개 → 각 큐를 Lambda가 소비. 이제 재고 API가 죽어도 이벤트는 **재고 큐에 쌓이고**, 큐 깊이가 알람을 울리고, API가 살아나면 밀린 주문이 순서대로 처리됐다. 반복 실패분은 DLQ로 격리됐다. 원인은 SNS가 고장 난 게 아니었다 — **버퍼가 필요한 자리에 팬아웃 도구만 쓴 선택**이었다. SNS는 복제는 하지만, 소비자를 기다려 주진 않는다.

## 실무 적용 포인트

1. **팬아웃이 필요하면 SNS→SQS, 소비자에 직접 꽂지 마라**: 구독자가 둘 이상이고 각자 죽을 수 있으면, 토픽에 **소비자별 SQS 큐**를 붙여라. 소비자 앞 큐가 곧 내구성 버퍼이자 백로그 지표다.
2. **순서·중복이 중요하면 FIFO를 쓰되 처리량 한계를 알라**: SQS FIFO는 배치 없이 초당 **약 300건**, 배치(`SendMessageBatch`)로 **약 3,000건**까지다. 표준 큐는 **at-least-once + 최선 순서**라 소비자가 멱등해야 한다([[idempotency-key]], [[at-least-once-vs-at-most-once]]).
3. **모든 큐에 DLQ와 `maxReceiveCount`를 박아라**: 독약 메시지(poison message) 하나가 큐를 막지 않도록 재수신 횟수(예: 3~5회) 초과분을 DLQ로 보내라. SNS→Lambda 직결이면 **on-failure destination**을 반드시 설정하라.
4. **`visibility timeout`은 처리 최대 시간보다 길게**: 너무 짧으면 처리 중인 메시지가 다시 보여 **중복 처리**가 난다. 핸들러 p99 처리시간 + 여유로 잡아라.
5. **내용으로 분기하거나 SaaS/AWS 이벤트를 받으면 EventBridge**: `amount > 10000`만 따로 보내는 식의 콘텐츠 라우팅, 또는 출처가 여럿이고 **리플레이·아카이브**가 필요하면 EventBridge다. 단순·고빈도·저지연 전달엔 과하다.
6. **롱 폴링으로 빈 폴링 비용을 죽여라**: SQS는 `WaitTimeSeconds`를 **20초**로 두는 롱 폴링이 기본값이어야 한다. 0이면 빈 응답에도 API 호출이 과금되고 지연도 더 나쁘다.

## 더 깊은 토끼굴

- AWS 공식 — [Amazon SQS Developer Guide](https://docs.aws.amazon.com/AWSSimpleQueueService/latest/SQSDeveloperGuide/welcome.html): visibility timeout·DLQ·FIFO 처리량의 1차 정의
- AWS 공식 — [Amazon SNS — Fanout to Amazon SQS queues](https://docs.aws.amazon.com/sns/latest/dg/sns-sqs-as-subscriber.html): SNS→SQS 팬아웃 표준 패턴의 출처
- AWS 공식 — [Amazon EventBridge — Content-based filtering with event patterns](https://docs.aws.amazon.com/eventbridge/latest/userguide/eb-event-patterns.html): 콘텐츠 기반 라우팅 규칙
- [[pull-vs-push-model]]: 큐(pull)와 펍섭(push)이 백프레셔를 어떻게 다르게 다루나
- [[dead-letter-queue]]: 폐기 대신 격리 — 위 시나리오를 막는 안전망
- [[outbox-pattern]]: 이벤트를 "발행했다고 믿는데 안 나간" 문제는 발행 직전 단계에서도 터진다
