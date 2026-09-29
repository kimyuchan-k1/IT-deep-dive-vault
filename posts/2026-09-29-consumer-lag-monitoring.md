---
title: 랙 합계는 임계값 아래였다. 파티션 하나가 100분째 멈춰 있었다
date: 2026-09-29
day: 100
category: kafka
tags: [kafka, consumer-lag, burrow, monitoring, consumer-group]
related: ["[[kafka-partition-math]]", "[[backpressure-patterns]]", "[[dead-letter-queue]]", "[[percentile-p99]]", "[[sli-slo-sla]]"]
difficulty: 3
short_text: |
  ⚠️ [Day 100] 랙 합계 정상. 파티션 하나는 100분째 멈춤

  오해: 랙은 밀린 메시지 개수
  실제: 개수÷유입속도=지연→밤엔 작게 보임→합계가 정지를 가림

  📖 https://github.com/kimyuchan-k1/IT-deep-dive-vault/blob/main/posts/2026-09-29-consumer-lag-monitoring.md
---

# 랙 합계는 임계값 아래였다. 파티션 하나가 100분째 멈춰 있었다

## 흔한 오해

> "컨슈머 랙은 밀린 메시지 수다. `sum(lag) > 50000`에 알림을 걸면 컨슈머가 밀리는 순간 바로 안다."

대시보드 대부분이 이 한 줄로 시작한다. `kafka-consumer-groups.sh --describe`가 LAG 컬럼을 숫자로 보여주고, 그 숫자를 그대로 Grafana에 올리는 게 제일 빠르니까.

틀린 건 아니다. 다만 이 숫자에는 세 가지가 빠져 있다. **얼마나 오래 밀렸는지**, **어느 파티션이 밀렸는지**, **누가 측정했는지**. 장애는 거의 항상 이 빈칸에서 난다.

## 실제 원리

### 1. 랙은 두 오프셋의 뺄셈이다. 그리고 둘 다 늦게 갱신된다

파티션 하나의 랙은 `high watermark - committed offset`이다. `read_committed` 컨슈머라면 기준이 LSO(Last Stable Offset)로 바뀐다.

```
partition 7:  [0 ........ 812,340 | ........ 861,990]
                          ^committed          ^high watermark
              lag = 861,990 - 812,340 = 49,650 (오프셋 단위)
```

committed offset은 처리한 순간이 아니라 **커밋한 순간** 움직인다. `enable.auto.commit=true`면 `auto.commit.interval.ms` 기본 5초마다다. 그래서 정상 컨슈머도 랙이 톱니 모양으로 오르내린다. 리밸런스 중에는 소비가 멈추니 톱니가 한 번 크게 튄다.

### 2. 오프셋 개수는 시간이 아니다

랙 5만 건의 의미는 유입 속도에 달려 있다. 파티션당 초당 1,000건이면 50초 지연이다. 초당 10건이면 83분이다. **같은 숫자가 100배 다른 장애를 뜻한다.**

그래서 고정 임계값은 양쪽에서 실패한다. 낮 피크에 맞추면 밤에는 멈춰도 한참 안 울린다. 밤에 맞추면 낮에는 정상 톱니에도 울린다. 사람이 알고 싶은 건 "지금 처리 중인 메시지가 몇 초 전에 들어온 것인가", 즉 time lag다. 계산법은 둘이다. committed offset 위치 메시지의 타임스탬프를 현재 시각에서 빼거나, 최근 오프셋-타임스탬프 쌍으로 보간한다. `kafka-lag-exporter`가 후자로 time lag를 뽑는다.

오프셋 차이가 메시지 수라는 가정도 깨진다. compacted 토픽은 오프셋에 구멍이 있다. 트랜잭션 프로듀서의 커밋·어보트 마커도 오프셋 한 칸씩을 차지한다. 숫자는 "대략의 거리"다.

### 3. 합계와 최댓값은 정지를 숨긴다

파티션 24개 중 23개가 0이고 1개가 계속 쌓이면 `sum`은 천천히 오른다. 정상 톱니 범위 안에 한참 머문다. 여기서 핵심은 **크기가 아니라 방향**이다. 멈춘 파티션은 committed offset이 제자리인데 high watermark만 오른다. 이 패턴은 값이 작을 때부터 보인다.

LinkedIn의 Burrow가 이걸 규칙으로 만들었다. 파티션별로 최근 커밋 N개(기본 10개) 창을 평가한다. committed offset이 변하지 않은 채 랙이 0보다 크면 `STALL`. 커밋 자체가 끊기면 `STOP`. 랙이 창 안에서 계속 증가하면 `WARN`. 임계값이 없다.

### 4. 클라이언트 지표는 컨슈머와 같이 죽는다

컨슈머 JMX의 `records-lag-max`(consumer-fetch-manager-metrics)는 **fetch position** 기준이다. committed offset 기준이 아니다. 가져왔지만 처리 못 한 레코드는 랙에서 이미 빠져 있다. 처리 루프가 한 레코드에서 막혀도 이 지표는 낮게 나온다.

더 나쁜 경우도 있다. 컨슈머 프로세스가 죽으면 지표가 0이 아니라 **사라진다**. 대부분의 알림 규칙은 없는 시계열에 울리지 않는다. 그래서 랙은 브로커 쪽, 즉 `__consumer_offsets`의 커밋 기록과 파티션 high watermark로 바깥에서 재야 한다.

## 현장 시나리오

주문 이벤트를 받아 알림톡을 보내는 서비스. 토픽 파티션 24개, 컨슈머 파드 6대. 알림은 `sum(kafka_consumergroup_lag) > 50000 for 5m`. 낮 피크 초당 3,000건일 때 정상 톱니가 2~4만이라 5만으로 잡았다.

새벽 1시 10분, 트래픽 초당 200건.

```
파티션 7에 스키마가 깨진 이벤트 1건 도착
  → 핸들러가 예외 → 에러 핸들러가 같은 오프셋으로 seek 후 재시도, 횟수 제한 없음 (poll은 계속 호출됨)
  → 세션·poll 타임아웃 안 걸림, 리밸런스 없음, committed offset 고정
  → 파티션 7 유입은 초당 약 8건 (200 / 24) → 랙 분당 약 500 증가
  → 나머지 23개는 랙 0 근처. sum = 파티션 7 랙과 거의 같음
  → 5만 도달까지 약 100분. 그동안 알림 0건
  → 02:52 알림 발생. 파티션 7 주문 약 5만 건의 알림톡이 최대 100분 지연
  → 고객센터 문의가 알림보다 먼저 도착
```

같은 날 `kafka-lag-exporter`의 time lag를 띄워 보니 파티션 7은 01:15에 이미 300초를 넘었다. Burrow 규칙이었다면 커밋 창 10개가 채워지는 순간 `STALL`이었다.

원인 한 줄. **알림이 "얼마나 많이"를 봤고, 장애는 "얼마나 오래"와 "어디서"에 있었다.** 수정은 셋이다. 알림을 파티션별 time lag로 바꾸고, 재시도를 3회로 제한한 뒤 DLQ로 보내고([[dead-letter-queue]]), committed offset 정지 감지를 따로 걸었다.

## 실무 적용 포인트

1. **알림은 파티션별 time lag로 건다.** 기준은 비즈니스 SLO에서 가져온다. "알림톡 95%가 60초 안에"라면 `max by (partition) (time_lag_seconds) > 60 for 3m`. 오프셋 랙은 대시보드 보조 지표로 내린다([[sli-slo-sla]]).

2. **정지는 크기가 아니라 변화율로 잡는다.** `changes(committed_offset[10m]) == 0 and lag > 0`이면 멈춘 것이다. Burrow를 쓰면 `STALL`/`STOP` 상태를 그대로 알림으로 연결한다.

3. **랙은 브로커 쪽에서 잰다.** `kafka-consumer-groups.sh --bootstrap-server ... --describe --group <group>`과 같은 소스(`__consumer_offsets` + high watermark)를 쓰는 exporter를 둔다. 클라이언트 `records-lag-max`만 보면 컨슈머가 죽을 때 알림도 같이 죽는다. 쓰더라도 `absent()` 알림을 짝으로 건다.

4. **메시지 단위 재시도는 횟수와 시간을 모두 제한한다.** 3회, 총 30초를 넘으면 DLQ로 보내고 커밋한다. 무한 재시도는 `max.poll.interval.ms`(기본 300,000ms) 안에서 poll을 계속 부르면 리밸런스도 안 일으키고 조용히 파티션을 멈춘다([[backpressure-patterns]]).

5. **비어 있는 그룹의 오프셋 만료를 확인한다.** `offsets.retention.minutes` 기본 10,080분(7일)이 지나면 컨슈머가 없는 그룹의 커밋이 지워진다. 랙 시계열이 사라지고, 재기동 시 `auto.offset.reset` 설정대로 earliest/latest로 점프한다.

6. **파티션 간 편차를 같이 본다.** `max(lag) / avg(lag)`가 10배를 넘으면 핫 파티션 또는 정지다. 파티션 수를 늘려도 해결되지 않는다([[kafka-partition-math]]).

## 더 깊은 토끼굴

- [[kafka-partition-math]] — 파티션과 컨슈머 수의 관계. 한 파티션은 한 컨슈머만 읽는다는 제약이 랙 편차를 만든다.
- [[dead-letter-queue]] — 독성 메시지를 파티션 밖으로 빼는 방법.
- [[backpressure-patterns]] — 랙이 쌓일 때 생산자 쪽에서 할 수 있는 것.
- [[percentile-p99]] — 평균·합계가 꼬리를 가리는 같은 구조.
- [[sli-slo-sla]] — time lag 임계값을 어디서 가져올지.
- [[kafka-exactly-once]] — 트랜잭션 마커와 LSO가 랙 계산에 끼어드는 지점.

**1차 출처**
- Apache Kafka — Consumer Configs (`auto.commit.interval.ms`, `max.poll.interval.ms`): https://kafka.apache.org/documentation/#consumerconfigs
- Apache Kafka — Monitoring (consumer fetch metrics, `records-lag-max`): https://kafka.apache.org/documentation/#monitoring
- LinkedIn Burrow — Consumer Lag Evaluation Rules: https://github.com/linkedin/Burrow/wiki/Consumer-Lag-Evaluation-Rules
- kafka-lag-exporter (time lag 보간): https://github.com/seglo/kafka-lag-exporter
