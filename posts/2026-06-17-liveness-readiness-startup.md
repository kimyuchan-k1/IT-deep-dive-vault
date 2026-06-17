---
title: Liveness 프로브를 꼼꼼히 걸었더니, 멀쩡한 파드가 줄줄이 재시작되며 장애가 번졌다
date: 2026-06-17
day: 15
category: cloud
tags: [kubernetes, probe, liveness, readiness, startup, resilience]
related: ["[[k8s-pod-death-5-reasons]]", "[[hpa-internals]]", "[[circuit-breaker]]", "[[sidecar-tradeoff]]", "[[retry-exponential-backoff-jitter]]", "[[connection-pool-sizing]]"]
difficulty: 2
short_text: |
  🔥 [Day 15] Liveness가 멀쩡한 파드를 죽인다
  오해: 다 같은 체크
  실제: liveness=재시작 readiness=차단
  DB 느리면 파드 kill
  📖 https://github.com/kimyuchan-k1/IT-deep-dive-vault/blob/main/posts/2026-06-17-liveness-readiness-startup.md
---

# Liveness 프로브를 꼼꼼히 걸었더니, 멀쩡한 파드가 줄줄이 재시작되며 장애가 번졌다

## 흔한 오해

"쿠버네티스 프로브는 그냥 헬스체크 아닌가? `/health` 엔드포인트 하나 만들어서 liveness, readiness에 똑같이 붙이면 되지. 둘 다 '이 파드 살아있나?'를 묻는 거고, 응답이 200이면 정상, 아니면 문제 — 그게 전부 아닌가? startup 프로브는 잘 안 써봤는데, 어차피 liveness가 있으니 없어도 되겠지."

쿠버네티스에 처음 워크로드를 올릴 때 거의 모두가 이렇게 한다. 그래서 입문 예제들도 liveness와 readiness에 같은 `/health` 경로를 복사해 붙인다.

**세 프로브는 실패했을 때 쿠버네티스가 하는 행동이 완전히 다르다.** liveness 실패는 **컨테이너를 죽이고 재시작**시키고, readiness 실패는 **Service 엔드포인트에서 빼서 트래픽만 끊는다**(파드는 산다). 같은 엔드포인트를 둘에 붙이면, 트래픽만 끊으면 될 상황에서 멀쩡한 파드를 재시작시킨다. 그리고 그 재시작이 전염된다.

## 실제 원리

세 프로브는 묻는 질문도, 실패의 결과도 다르다.

### liveness — "이 컨테이너를 죽여야 하나?"

`kubelet`이 주기적으로 찌른다. 실패가 `failureThreshold`만큼 연속되면 **kubelet이 컨테이너를 `SIGTERM`으로 죽이고 재시작**한다. 용도는 단 하나 — **데드락처럼 프로세스가 살아는 있는데 영영 응답 못 하는 상태**를 깨는 것. 정상 동작 중 일시적으로 느려진 것까지 잡으면 안 된다. 죽이면 더 나빠지니까.

### readiness — "이 파드로 트래픽을 보내도 되나?"

실패하면 컨테이너를 **죽이지 않는다.** 대신 이 파드를 **Service의 엔드포인트 목록에서 제거**한다. 즉 로드밸런서가 트래픽을 안 보낸다. 파드는 계속 살아서 의존성(DB, 캐시)이 회복되길 기다린다. 회복되면 다시 엔드포인트에 들어온다. **"지금은 받지 마, 근데 나 살아있어"** 가 readiness의 메시지다.

### startup — "아직 부팅 중이니 liveness를 잠재워라"

JVM처럼 워밍업에 30~60초 걸리는 앱이 문제다. liveness `initialDelaySeconds`를 길게 잡으면 부팅은 봐주지만, **부팅 후 데드락엔 반응이 느려진다.** startup 프로브는 이 딜레마를 푼다. **startup이 성공할 때까지 liveness와 readiness는 아예 비활성**이다. 느린 부팅은 startup이 길게 봐주고(`failureThreshold × periodSeconds`로 예산 설정), 부팅이 끝나면 liveness가 짧은 주기로 빡세게 감시한다.

### 왜 재시작이 전염되나

여기가 핵심이다. liveness 프로브가 **공유 의존성**(같은 DB, 같은 캐시)을 체크하면, 그 의존성이 느려지는 순간 **모든 파드의 liveness가 동시에 실패**한다. kubelet들이 일제히 파드를 죽인다. 재시작된 파드는 커넥션 풀을 새로 채우고 캐시를 다시 데우며 **부팅 부하**를 만들고, 이게 이미 느린 의존성을 더 누른다. 죽임 → 재부팅 부하 → 더 느려짐 → 또 죽임. **재시작 폭풍(restart storm)** 이다.

## 현장 시나리오

한 핀테크가 주문 API를 쿠버네티스에 12개 파드로 운영했다. liveness, readiness 모두 `/health`에 붙였고, `/health`는 **DB에 `SELECT 1`을 날려** 연결을 확인했다. 평소엔 완벽했다. 인과 사슬은 이랬다:

- 마케팅 푸시로 트래픽이 몰리자 DB 커넥션 풀이 포화됐다. `/health`의 `SELECT 1`이 풀에서 커넥션을 못 받아 **타임아웃(1초 초과)** 나기 시작했다
- liveness `failureThreshold: 3`, `periodSeconds: 5` — 15초 연속 실패하자 kubelet이 파드를 죽였다. 그런데 DB는 **모든 파드가 공유**하니, 12개 파드의 liveness가 거의 동시에 실패했다
- 12개가 한꺼번에 재시작되며 커넥션 풀을 새로 채우려 **DB에 커넥션 폭주** — 가뜩이나 포화된 DB가 더 느려졌다
- 더 느려진 DB 때문에 새로 뜬 파드들도 `SELECT 1`이 또 타임아웃 → 또 kill. **죽임-재시작 루프**가 5분간 돌며 주문 API가 사실상 전면 마비됐다
- 정작 앱 프로세스 자체는 **단 한 번도 데드락에 빠진 적이 없었다.** 그냥 DB가 잠깐 느렸을 뿐이다

복구는 `kubectl edit`로 **liveness에서 DB 체크를 떼는 것**이었다. liveness는 프로세스 자체의 생존(이벤트 루프 응답)만 보게 바꾸고, DB 의존성은 readiness로 옮겼다. 그러자 DB가 느릴 때 파드들은 **죽지 않고 트래픽만 빠졌다가**, 풀이 회복되자 조용히 다시 들어왔다. 원인은 DB 부하가 아니라, **"트래픽을 끊으면 될 일"을 "프로세스를 죽이는 일"로 처리한 프로브 설계**였다.

## 실무 적용 포인트

1. **liveness에 외부 의존성을 절대 넣지 마라**: liveness는 **프로세스 자신의 생존**만 봐야 한다. DB·캐시·다운스트림 API 체크는 readiness로. liveness `/healthz`는 이벤트 루프가 응답하는지만 확인하는 **가벼운 인메모리 체크**로 둔다.
2. **liveness와 readiness 엔드포인트를 분리하라**: `/healthz/live`(자기 생존)와 `/healthz/ready`(의존성 포함)를 따로 만든다. 같은 `/health` 하나를 둘에 붙이는 게 재시작 폭풍의 출발점이다.
3. **느린 부팅엔 startup 프로브를 써라**: `startupProbe`에 `failureThreshold: 30`, `periodSeconds: 10`이면 **최대 300초** 부팅 예산. 그동안 liveness는 잠잠하다. liveness `initialDelaySeconds`를 무작정 늘리는 것보다 정확하다.
4. **liveness `failureThreshold`를 너무 빡세게 잡지 마라**: `failureThreshold: 1`, `periodSeconds: 1`은 일시적 GC 멈춤에도 죽인다. **최소 `failureThreshold × periodSeconds ≥ 15초`** 정도 여유를 둬 진짜 데드락만 잡게 한다.
5. **공유 의존성 체크는 한쪽 방향으로만**: 모든 파드가 같은 DB를 readiness로 체크하면, DB 장애 시 **전 파드가 동시에 NotReady**가 돼 서비스가 통째로 빠질 수 있다. 다운스트림 장애엔 [[circuit-breaker]]로 빠르게 실패시키고, readiness는 자기가 요청을 처리할 수 있는지에 집중한다.
6. **`terminationGracePeriodSeconds`와 `preStop`을 함께 설계하라**: 재시작이 일어나도 graceful하게 끝나야 커넥션이 깔끔히 정리된다. `preStop`에서 readiness를 먼저 떨궈 새 트래픽을 끊은 뒤 in-flight 요청을 비운다.

## 더 깊은 토끼굴

- Kubernetes 공식 문서 — [Configure Liveness, Readiness and Startup Probes](https://kubernetes.io/docs/tasks/configure-pod-container/configure-liveness-readiness-startup-probes/): 세 프로브의 정의와 필드별 동작의 1차 출처
- Kubernetes 공식 문서 — [Pod Lifecycle: Container probes](https://kubernetes.io/docs/concepts/workloads/pods/pod-lifecycle/#container-probes): 프로브 실패 시 kubelet의 행동과 startup 프로브의 게이팅 규칙
- [[k8s-pod-death-5-reasons]]: 파드가 죽는 다른 이유들 — 프로브 오설계도 그중 하나다
- [[circuit-breaker]]: 다운스트림 장애를 readiness로 떠넘기지 않고 앱 안에서 격리하는 방법
- [[retry-exponential-backoff-jitter]]: 재시작 폭풍을 완화하는 백오프·지터의 원리
- [[hpa-internals]]: 프로브가 만든 NotReady 상태가 오토스케일링 판단에 어떻게 섞이나
