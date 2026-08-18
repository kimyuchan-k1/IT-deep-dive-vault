---
title: Kustomize 오버레이에서 리소스 한 줄을 지웠다. 3주 뒤 트래픽 30%가 구버전으로 갔다
date: 2026-08-18
day: 72
category: cloud
tags: [helm, kustomize, kubectl-apply, server-side-apply, prune, crd]
related: ["[[terraform-state]]", "[[blue-green-canary-rolling]]", "[[hpa-internals]]", "[[liveness-readiness-startup]]", "[[k8s-pod-death-5-reasons]]"]
difficulty: 3
short_text: |
  ⚠️ [Day 72] 지웠는데 클러스터엔 그대로 남았다
  오해: Helm vs Kustomize는 템플릿이냐 패치냐
  실제: 갈리는 건 삭제 책임. Kustomize엔 상태가 없다
  📖 https://github.com/kimyuchan-k1/IT-deep-dive-vault/blob/main/posts/2026-08-18-helm-vs-kustomize.md
---

# Kustomize 오버레이에서 리소스 한 줄을 지웠다. 3주 뒤 트래픽 30%가 구버전으로 갔다

## 흔한 오해

> "Helm은 Go 템플릿으로 YAML을 찍어내고, Kustomize는 base에 패치를 덮는다. 결국 둘 다 최종 YAML 만들어서 `kubectl apply` 하는 거잖아. 취향 문제 아닌가?"

거의 모든 입문 문서가 그렇게 비교한다. 그래서 팀 결정도 "조건문이 필요하니 Helm", "문법이 단순한 게 좋으니 Kustomize"로 끝난다.

렌더링 단계까지만 맞다. 진짜 차이는 YAML을 **만드는** 방식이 아니라, 만든 뒤 **클러스터에 무엇이 있어야 하는지를 누가 기억하느냐**에 있다. 한쪽은 상태를 저장하고 다른 쪽은 저장하지 않는다. 사고는 전부 이 비대칭에서 나온다.

## 실제 원리

### 렌더링 단계: 텍스트냐 객체냐

Helm은 YAML을 **문자열로** 다룬다. `templates/` 아래 파일이 Go text/template으로 처리되고 그 출력이 파싱되어 YAML이 된다. 파싱은 렌더링 **뒤**다. 그래서 `{{ .Values.x | indent 4 }}`에서 들여쓰기가 하나 틀리면 문법이 깨진 텍스트가 그대로 나오고, 에러 줄 번호도 원본이 아닌 렌더 결과 기준으로 찍힌다.

Kustomize는 입력을 먼저 YAML로 파싱해 구조화된 객체로 만든 뒤 패치를 적용한다. 문법이 깨진 결과가 나올 수 없다. 대신 조건문도 반복문도 없어 환경이 늘수록 오버레이 디렉터리가 곱셈으로 늘어난다.

여기까지가 흔히 비교되는 지점이다. 그런데 이건 배포 실패를 만들지언정 **조용한 사고**를 만들지 않는다. 깨진 YAML은 즉시 티가 난다.

### 상태 단계: 삭제는 누가 책임지나

`kubectl apply`가 하는 일은 하나다. **넘긴 매니페스트에 있는 객체를 원하는 상태로 맞춘다.** 넘기지 않은 객체에는 아무 일도 하지 않는다. 선언형 API의 핵심이자 함정이다. "선언한 것만이 존재해야 한다"가 아니라 "선언한 것은 이렇게 존재해야 한다"이다.

Helm은 이 빈틈을 자기 상태로 메운다. `helm install`은 렌더된 전체 매니페스트를 gzip으로 압축해 base64로 인코딩한 뒤, 릴리스와 같은 네임스페이스에 `sh.helm.release.v1.{릴리스명}.v{리비전}` Secret으로 저장한다. `helm upgrade`는 그걸 꺼내 새 매니페스트와 비교하고, **이전엔 있었는데 지금은 없는 객체를 삭제**한다. 값 비교도 3-way다 — 저장된 이전 매니페스트, 새 매니페스트, 클러스터의 실제 상태 셋으로 패치를 만든다. 그래서 누군가 `kubectl edit`으로 만진 필드는, 차트가 그 필드를 바꾸지 않는 한 유지된다.

Kustomize에는 이런 저장소가 없다. `kubectl apply -k ./overlays/prod`는 그 순간 렌더된 것만 적용한다. `kustomization.yaml`의 `resources:`에서 한 줄을 지우면 그 리소스는 **다음 apply의 입력에서 빠질 뿐** 클러스터에서 사라지지 않는다. 삭제 명령이 발생하지 않는다. 에러도 경고도 없다. Git diff에는 `- deployment-v1.yaml`이 빨갛게 찍혀 있으니 리뷰어는 지워졌다고 읽는다.

이걸 메우려면 `--prune`이 필요하고, `--prune`은 라벨 셀렉터로 "내 소유 범위"를 지정해야 한다:

```
kubectl apply -k ./overlays/prod \
  --prune -l app.kubernetes.io/part-of=checkout \
  --prune-allowlist=apps/v1/Deployment \
  --prune-allowlist=/v1/ConfigMap
```

셀렉터를 넓게 잡으면 남의 리소스를 지우고, 좁게 잡으면 아무것도 못 지운다. 이 위험 때문에 Argo CD 같은 GitOps 컨트롤러는 apply의 prune 대신 자체 트래킹으로 삭제 대상을 계산한다. **Kustomize를 쓴다는 건 상태 추적을 다른 레이어에 위임한다는 뜻**이고, 위임하지 않으면 그 자리는 비어 있다.

### 소유권 단계: managedFields

한 겹 더 있다. 클라이언트 사이드 apply는 `kubectl.kubernetes.io/last-applied-configuration` 어노테이션에 직전 매니페스트를 통째로 넣어두고 그걸 기준으로 diff를 낸다. 서버 사이드 apply(`--server-side`)는 필드마다 소유자를 `metadata.managedFields`에 기록하고, 다른 소유자가 그 필드를 건드리면 충돌 에러를 낸다.

Helm 3의 업그레이드는 기본이 클라이언트 사이드 3-way merge다. 서버 사이드 apply를 쓰는 컨트롤러와 같은 객체를 나눠 가지면 한쪽은 어노테이션을, 다른 쪽은 `managedFields`를 진실로 본다. 배포마다 필드가 앞뒤로 뒤집히는 플래핑은 대개 여기서 나온다.

### CRD는 또 예외다

Helm 차트의 `crds/` 디렉터리에 있는 CustomResourceDefinition은 `helm install` 때 한 번 설치되고 **`helm upgrade` 때는 갱신되지 않는다.** 공식 문서가 명시한 의도된 동작이다. 스키마 변경이 기존 커스텀 리소스를 깨뜨릴 수 있어 Helm이 책임지지 않겠다고 선을 그은 것이다. 차트 버전을 올렸으니 CRD도 올라갔겠지라고 가정하면 새 필드가 API 서버에서 조용히 잘려나간다.

## 현장 시나리오

결제 팀이 checkout API를 v1에서 v2로 넘겼다. Kustomize 오버레이 구조였고, 3주간 두 Deployment를 동시에 띄우는 [[blue-green-canary-rolling]] 전환이었다. 둘은 같은 Service 뒤에 있었고 `app: checkout` 라벨을 공유했다.

전환이 끝난 날 담당자가 `resources:`에서 `deployment-v1.yaml` 한 줄을 지우고 파일도 삭제했다. PR은 "-1 line, remove v1"으로 리뷰를 통과했고 CI가 `kubectl apply -k`를 돌렸다. 종료 코드 0.

v1 Deployment는 클러스터에 그대로 남았다. apply 입력에서 빠졌을 뿐이라 삭제 명령이 발생하지 않았다. 레플리카 3개는 계속 떠 있었고 `app: checkout` 라벨을 달고 있었으므로 Service endpoints에도 계속 포함됐다. v2가 7개, v1이 3개. 요청의 30%가 v1으로 갔다. 응답은 200이었다. 스키마만 구버전이었다.

3주 뒤 v2에 필드가 추가됐고 클라이언트가 그 필드를 필수로 읽기 시작했다. 그때부터 결제 내역 화면이 간헐적으로 비었다. 새로고침하면 고쳐지니 재현이 안 됐고, HPA가 v2만 스케일아웃하는 동안 v1 비중이 흔들려 실패율은 20~30% 사이를 오갔다. 대시보드 에러율은 0이었다 — 서버는 정상 응답했으니까. 원인 확인은 `kubectl get deploy -l app=checkout` 한 줄이면 됐다. 있어야 할 것보다 하나 더 있었다.

## 실무 적용 포인트

1. **Kustomize를 쓰면 삭제 경로를 별도로 만든다.** `kubectl apply -k`만 도는 CI는 리소스를 절대 지우지 않는다. `--prune -l <소유 라벨>`을 붙이거나 Argo CD의 `prune: true`에 위임한다.
2. **prune 셀렉터는 팀이 아니라 애플리케이션 단위로 좁힌다.** `app.kubernetes.io/part-of=checkout` 수준. `--prune-allowlist`를 명시하지 않으면 CRD 인스턴스가 대상에서 빠지므로 커스텀 리소스를 쓴다면 타입을 직접 나열한다.
3. **배포 후 실제 개수를 검증한다.** `kustomize build overlays/prod | grep -c '^kind:'` 결과와 `kubectl get deploy,sts,cronjob -l app.kubernetes.io/part-of=checkout`의 개수를 대조하는 스텝 하나면 이 사고 유형이 통째로 막힌다.
4. **Helm 릴리스 Secret이 무한히 쌓이지 않게 한다.** `helm upgrade --history-max 10`. 리비전마다 전체 매니페스트가 압축되어 들어가고, etcd 값 크기 상한 때문에 큰 차트는 업그레이드 자체가 실패한다. 크기는 `kubectl get secret -l owner=helm`으로 본다.
5. **`crds/`는 업그레이드 때 안 올라간다.** 차트 버전을 올릴 때 CRD 변경분이 있으면 `kubectl apply -f crds/`를 파이프라인에 별도 스텝으로 넣는다.
6. **HPA가 관리하는 `replicas`는 차트와 오버레이 양쪽에서 뺀다.** 매니페스트에 남아 있으면 배포마다 되돌려진다. [[hpa-internals]]가 조정한 값이 apply 한 번에 초기화되는 경로가 이것이다. 이때 `helm upgrade --force`로 뭉개지 않는다 — `--force`는 패치가 아니라 replace여서 Service의 clusterIP나 PVC 바인딩이 날아간다.

## 더 깊은 토끼굴

- [[terraform-state]] — 상태 파일을 가진 도구가 삭제를 책임지는 구조. Helm 릴리스 Secret과 정확히 대응된다.
- [[k8s-pod-death-5-reasons]] — 남은 Pod이 왜 죽지 않고 계속 트래픽을 받는지
- [[hpa-internals]] — `replicas` 소유권 충돌의 원본
- [[blue-green-canary-rolling]] — 두 버전을 동시에 띄우는 기간의 라벨 설계
- [[liveness-readiness-startup]] — 정상 응답하는 구버전은 어떤 프로브로도 걸러지지 않는다

**1차 출처**

- Helm 공식 문서 — CRD는 upgrade 시 갱신되지 않음: https://helm.sh/docs/chart_best_practices/custom_resource_definitions/
- Helm 공식 문서 — Helm 3의 3-way strategic merge patch: https://helm.sh/docs/faq/changes_since_helm2/
- Kubernetes 공식 문서 — Server-Side Apply와 `managedFields`: https://kubernetes.io/docs/reference/using-api/server-side-apply/
- Kubernetes 공식 문서 — 선언형 설정 관리와 `--prune`: https://kubernetes.io/docs/tasks/manage-kubernetes-objects/declarative-config/
