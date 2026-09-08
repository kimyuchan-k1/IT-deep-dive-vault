---
title: 메모리를 절반만 썼는데 Pod이 OOMKilled로 죽었다
date: 2026-09-08
day: 90
category: cloud
tags: [kubernetes, cgroup, oomkilled, eviction, memory]
related: ["[[liveness-readiness-startup]]", "[[hpa-internals]]", "[[spot-instance-safe]]", "[[sidecar-tradeoff]]"]
difficulty: 3
short_text: |
  🔥 [Day 90] 메모리 절반 쓴 Pod이 OOMKilled

  오해: limit 넘어야 죽는다
  실제: page cache도 cgroup 메모리→회수 실패→137

  📖 https://github.com/kimyuchan-k1/IT-deep-dive-vault/blob/main/posts/2026-09-08-k8s-pod-death-5-reasons.md
---

# 메모리를 절반만 썼는데 Pod이 OOMKilled로 죽었다

## 흔한 오해

> "Pod이 `OOMKilled`로 죽었으면 앱이 limit보다 메모리를 많이 쓴 거다. limit을 올리면 된다."

그래서 대시보드에서 `container_memory_rss`를 열어보고, 2Gi limit에 1.1Gi밖에 안 쓰고 있으면 혼란에 빠진다. "여유가 900Mi인데 왜 OOM이 나지?" 그리고 결국 limit을 4Gi로 올린다. 며칠 뒤 같은 자리에서 또 죽는다.

Pod이 죽는 이유를 흔히 5가지로 센다 — OOMKilled, liveness 실패, 노드 eviction, CrashLoopBackOff, preemption. 이 목록 자체는 맞다. 문제는 **다섯 개가 서로 독립인 것처럼 외운다**는 점이다. 실제로는 "누가 죽였느냐"에 따라 두 갈래로 갈리고, 그 갈래를 모르면 진단이 통째로 어긋난다.

## 실제 원리

### 1. 죽인 주체는 커널 아니면 kubelet, 둘 중 하나다

컨테이너를 죽일 수 있는 주체는 두 개다.

- **커널의 cgroup OOM killer** — `SIGKILL`. 유예 없음. 종료 코드 `137` (= 128 + 9). 앱의 shutdown 훅은 실행되지 않는다.
- **kubelet** — liveness 실패, eviction, preemption 전부 여기다. `SIGTERM` 먼저 보내고 `terminationGracePeriodSeconds`(기본 30초) 기다린 뒤 `SIGKILL`. 정상 종료하면 코드 `143` (= 128 + 15).

그래서 종료 코드 하나로 갈래가 갈린다. `137`이면 커널이 죽인 것이고, 그건 앱 코드나 probe 설정을 아무리 봐도 답이 안 나온다. `143`이면 반대로 커널 메모리는 무죄고 kubelet의 판단 근거를 봐야 한다.

### 2. cgroup이 세는 "메모리"는 앱이 생각하는 메모리가 아니다

여기가 핵심이다. cgroup v2의 `memory.current`는 프로세스의 익명 메모리(RSS)만 세지 않는다. **그 컨테이너가 파일을 읽고 쓰면서 커널이 잡아둔 page cache도 같이 센다.**

로그를 초당 수십 MB 쓰는 서비스를 생각해 보자. write한 데이터는 곧바로 디스크로 가지 않고 page cache에 dirty page로 쌓인다. 이 페이지들은 그 컨테이너의 cgroup에 청구된다. 앱의 RSS는 1.1Gi로 멀쩡한데 `memory.current`는 1.9Gi가 된다.

limit에 닿으면 커널은 곧바로 죽이지 않는다. 먼저 **회수(reclaim)를 시도한다.** 깨끗한(clean) page cache는 그냥 버리면 되니 즉시 회수된다. 문제는 dirty page다. 디스크에 flush한 뒤에야 회수할 수 있고, 그 사이 앱은 계속 새 메모리를 요구한다. 할당 속도가 회수 속도를 넘어서는 순간 커널은 포기하고 cgroup 안에서 OOM killer를 돌린다.

즉 **OOMKilled는 "메모리를 다 썼다"가 아니라 "제때 회수하지 못했다"의 결과다.** 그래서 RSS 그래프만 보면 영원히 원인을 못 찾는다.

kubelet이 eviction 판단에 쓰는 값은 `working_set`이고, 이건 `memory.current - inactive_file`이다. 회수 가능한 비활성 파일 캐시를 빼준 값이다. 프로메테우스로 치면 `container_memory_working_set_bytes`. **`container_memory_rss`가 아니다.** 대시보드가 rss를 그리고 있으면 OOM 직전 상태가 그래프에 아예 안 보인다.

### 3. 노드가 터질 때 누가 먼저 죽는지는 이미 정해져 있다

컨테이너 cgroup이 아니라 노드 전체 메모리가 부족해지면 순서가 또 다르다. kubelet은 QoS 클래스에 따라 각 컨테이너에 `oom_score_adj`를 미리 박아둔다.

| QoS | 조건 | `oom_score_adj` |
|---|---|---|
| Guaranteed | requests == limits (CPU/메모리 모두) | `-997` |
| Burstable | requests < limits | `1000 - (1000 × request / 노드 capacity)` |
| BestEffort | requests/limits 없음 | `1000` |

값이 클수록 먼저 죽는다. requests를 안 적은 Pod은 노드가 흔들리는 순간 1순위 제물이다. 반대로 requests와 limits를 같게 맞춘 Pod은 `-997`이라 커널 입장에서 거의 마지막 후보다. **YAML에 requests를 채워 넣는 건 스케줄러를 위한 힌트가 아니라 생존 순위표다.**

## 현장 시나리오

주문 API. Java 21, Pod limit `memory: 2Gi`, JVM 옵션은 `-Xmx1g`. 평소 RSS 1.1Gi로 안정적이었다.

프로모션 트래픽이 3배로 뛰면서 요청당 상세 로그를 남기던 코드가 초당 40MB를 파일로 쓰기 시작했다. 컨테이너의 로그 볼륨은 `emptyDir`, 즉 노드 디스크다.

- write된 로그가 page cache에 dirty page로 적재 → cgroup `memory.current` 1.1Gi → 1.95Gi로 상승
- 2Gi limit 도달 → 커널 reclaim 시작 → dirty page라 flush 대기
- 그 사이 GC가 새 영역 할당 요청 → 회수 속도 < 할당 속도
- cgroup OOM killer 발동 → `SIGKILL` → 종료 코드 `137`, 셧다운 훅 미실행 → 처리 중이던 주문 요청 40여 건 응답 없이 끊김
- Pod 재시작 → 30초 후 같은 패턴 반복 → `CrashLoopBackOff`

이 30분 동안 대시보드의 `container_memory_rss`는 내내 **55% 근처의 평평한 선**이었다. 그래서 팀은 메모리 문제가 아니라고 판단했고, liveness probe와 GC 로그를 3시간 팠다.

원인 한 줄: 앱이 안 쓴 메모리를 커널이 앱 앞으로 청구했다.

## 실무 적용 포인트

1. **대시보드 지표를 `container_memory_working_set_bytes`로 바꿔라.** `container_memory_rss`는 OOM 판단 기준이 아니다. 알림 임계는 limit 대비 `working_set` 85%.
2. **죽은 이유는 종료 코드부터 본다.** `kubectl get pod {name} -o jsonpath='{.status.containerStatuses[0].lastState.terminated}'` — `reason`, `exitCode`, `finishedAt`이 나온다. `137`이면 커널, `143`이면 kubelet.
3. **PID 1이 안 죽어도 OOM은 일어난다.** 컨테이너 안에서 `cat /sys/fs/cgroup/memory.events`의 `oom_kill` 카운터를 확인해라. 자식 프로세스만 죽으면 Pod 상태는 `Running`인데 기능 일부가 조용히 사라진다.
4. **JVM은 heap 밖 메모리가 크다.** `-Xmx`만 잡으면 Metaspace, 스레드 스택(스레드당 1MB), Direct Buffer, JIT 코드 캐시가 계산에서 빠진다. limit 2Gi면 `-Xmx1g` 대신 `-XX:MaxRAMPercentage=70`으로 두고 남은 30%를 비heap과 page cache 몫으로 남긴다.
5. **requests를 limits와 같게 맞춰 Guaranteed로 올려라.** `oom_score_adj`가 `-997`이 되어 노드 압박 시 생존 순위가 뒤로 밀린다. 최소한 requests를 비워두지는 마라 — BestEffort는 `1000`이라 항상 1순위로 죽는다.
6. **로그는 파일이 아니라 stdout으로.** `emptyDir`에 쓰면 그 page cache가 내 cgroup에 청구된다. stdout은 kubelet의 로그 로테이션(`--container-log-max-size`, 기본 10Mi) 관리 대상으로 넘어간다.
7. **liveness probe 기본값을 그대로 쓰지 마라.** `timeoutSeconds`는 기본 `1`이다. GC pause가 2초면 정상 앱이 재시작된다. `timeoutSeconds: 5`, `failureThreshold: 3`으로 두고 기동 지연은 [[liveness-readiness-startup]]의 startupProbe로 분리한다.

## 더 깊은 토끼굴

- [[liveness-readiness-startup]] — kubelet이 죽이는 쪽 갈래의 전부
- [[hpa-internals]] — working_set 기반 스케일링이 OOM보다 먼저 걸리게 만드는 법
- [[spot-instance-safe]] — 종료 코드 `143`짜리 죽음을 다루는 반대편 문제
- [[sidecar-tradeoff]] — 사이드카가 같은 Pod의 메모리 limit을 나눠 쓰는 구조
- [[backpressure-patterns]] — 회수 속도 < 할당 속도라는 같은 형태의 문제

**출처**

- Kubernetes 공식 문서, Node-pressure Eviction (`working_set` 계산식과 하드 eviction 기본 임계값): https://kubernetes.io/docs/concepts/scheduling-eviction/node-pressure-eviction/
- Kubernetes 공식 문서, Configure Quality of Service for Pods (QoS 클래스 판정 규칙): https://kubernetes.io/docs/tasks/configure-pod-qos/
- Linux 커널 문서, Control Group v2 — Memory Interface Files (`memory.current`, `memory.events`, reclaim 동작): https://www.kernel.org/doc/html/latest/admin-guide/cgroup-v2.html
