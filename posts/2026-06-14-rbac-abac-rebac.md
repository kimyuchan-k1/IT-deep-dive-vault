---
title: 역할을 100개 만들어 권한을 다 막았는데, "이 폴더를 공유받은 사람의 팀장"은 역할로 그릴 수 없었다
date: 2026-06-14
day: 12
category: security
tags: [authorization, rbac, abac, rebac, zanzibar, access-control]
related: ["[[oauth2-grant-types]]", "[[jwt-vs-session]]", "[[mtls-zero-trust]]", "[[rate-limit-token-bucket]]", "[[secret-management]]"]
difficulty: 3
short_text: |
  🧠 [Day 12] 권한은 역할이 아니라 관계로 풀린다
  오해: 역할만 쪼개면 다 된다
  실제: 역할 폭발→ABAC 속성→ReBAC 그래프
  "공유받은 사람의 팀장을 역할로 못 그렸다"
  📖 https://github.com/kimyuchan-k1/IT-deep-dive-vault/blob/main/posts/2026-06-14-rbac-abac-rebac.md
---

# 역할을 100개 만들어 권한을 다 막았는데, "이 폴더를 공유받은 사람의 팀장"은 역할로 그릴 수 없었다

## 흔한 오해

"권한 관리? 그건 결국 역할(role)을 잘 설계하는 문제 아닌가. `admin`, `editor`, `viewer` 같은 역할을 만들고, 사용자한테 역할을 붙여주면 끝이지. 더 세밀하게 가야 하면 `billing-admin`, `read-only-auditor`처럼 역할을 더 쪼개면 되고."

권한 시스템을 처음 짜면 거의 다 RBAC(Role-Based Access Control)로 시작한다. 직관적이고, 조직도랑 잘 맞고, 입문 자료도 "역할 테이블 만들고 매핑하세요"로 가르친다.

**역할은 '누구냐'만 본다. 그런데 현실의 권한은 '무엇에 대해, 어떤 맥락에서, 누구와 어떤 관계로'에 달려 있다.** 역할 하나로 표현되는 권한은 사용자 전역에 고정된 권한뿐이다. "이 **특정** 문서를, 영업시간에만, 그 문서를 **공유한 사람의 팀에 속한** 사용자에게" 같은 조건은 역할로 그리는 순간 역할이 폭발한다.

## 실제 원리

세 모델을 가르는 축은 하나다 — **권한이 무엇에 의존하는가.**

- **RBAC**: 주체의 **역할**에 의존한다. `user → role → permission`. 정적 매핑.
- **ABAC**: 주체·자원·환경의 **속성(attribute)**에 의존한다. 권한이 그때그때 **계산**된다.
- **ReBAC**: 주체와 자원 사이의 **관계(relationship)**에 의존한다. 권한이 그래프 **탐색**으로 도출된다.

### RBAC — 빠르지만 맥락을 못 담는다

RBAC의 권한 체크는 본질적으로 **테이블 조회**다. "이 사용자가 가진 역할들 중 이 권한을 포함한 게 있나?" 한 번 보면 끝이라 빠르고 감사(audit)도 쉽다. "누가 이 권한을 갖나?"를 역할 매핑만 뒤지면 바로 답한다.

문제는 **자원 단위 예외**가 들어오는 순간이다. 문서 100만 개를 각각 다른 사용자 조합에 공유해야 하면, `doc-12345-editor` 같은 역할을 자원 수만큼 만들게 된다. 이게 **역할 폭발(role explosion)** — 역할 수가 `사용자 × 자원`으로 곱해져 버린다.

### ABAC — 유연하지만 "누가 접근 가능한가"를 못 묻는다

ABAC는 권한을 **정책(policy)**으로 쓴다. `subject.dept == resource.owner_dept AND env.time in 9..18` 같은 술어(predicate)를 정책 엔진(OPA의 Rego, XACML)이 요청마다 평가한다. 속성만 추가하면 새 조건이 표현되니 표현력이 가장 크다.

여기가 핵심 함정이다. ABAC의 결정은 **요청 시점에 계산**된다. 그래서 **역질의(reverse query)가 어렵다**. "이 문서에 접근 가능한 사용자를 전부 나열하라"는 요청에 답하려면, 모든 사용자 × 모든 정책을 다 돌려봐야 한다. 권한 공유 UI("이 문서를 볼 수 있는 사람 목록")를 만들 수가 없다.

### ReBAC — 권한을 그래프 도달성으로 본다

ReBAC는 권한을 **관계 튜플(tuple)**로 저장한다. Google의 **Zanzibar**가 정립한 형태로, `object#relation@subject` 꼴이다. 예: `doc:readme#editor@user:alice`(alice는 readme의 editor), `doc:readme#viewer@group:eng#member`(eng 그룹의 멤버는 readme의 viewer).

권한 체크는 **그래프 도달성(reachability) 질의**가 된다. "alice가 readme를 볼 수 있나?"는 `viewer` 관계에서 출발해 그룹 멤버십을 따라 **그래프를 순회**하며 alice에 닿는지 본다. Zanzibar는 여기에 두 가지 핵심 연산을 둔다:

- **computed_userset**: "editor면 자동으로 viewer다" 같은 관계 간 함의
- **tuple_to_userset**: "이 문서의 **부모 폴더**의 viewer는 이 문서의 viewer다" 같은 **상속** — 폴더 계층이 자연스럽게 풀린다

덕분에 "공유받은 사람의 팀장"도 그래프 한 줄로 그려진다. 대신 RBAC의 단순 조회보다 비싸고, 분산 환경에서 **일관성** 문제가 생긴다 — 이게 Zanzibar가 푼 어려운 부분이다.

### ReBAC가 진짜 어렵게 만든 것 — New Enemy Problem

ACL이 분산 캐시에 흩어지면 위험한 시나리오가 있다. ① Bob을 문서에서 빼고 ② 민감 문서를 추가했는데, 권한 체크가 **옛 ACL 스냅샷**을 읽으면 빠진 Bob이 새 문서를 본다. Zanzibar는 이를 **New Enemy Problem**이라 부르고, **zookie**(스냅샷 토큰)로 막는다. "이 ACL 변경 **이후**의 상태로만 평가하라"는 하한선을 토큰에 박아, 오래된 권한이 재사용되는 걸 차단한다. [[jwt-vs-session]]에서 토큰이 상태를 나르듯, 여기선 토큰이 **일관성 시점**을 나른다.

## 현장 시나리오

협업 문서 SaaS(노션·드라이브류). 인과 사슬은 이랬다:

- 처음엔 워크스페이스 단위 RBAC였다. `owner / editor / viewer` 3개 역할. 깔끔했다
- "문서 **하나만** 외부 사용자에게 공유" 요구가 들어왔다. 워크스페이스 역할로는 표현 불가 → 자원별 역할(`doc-{id}-editor`)을 만들기 시작했다
- 문서가 늘자 역할이 **자원 수만큼 폭발**했다. 역할 테이블이 수십만 행이 됐고, "이 사람이 가진 권한 전부"를 묻는 쿼리가 수 초 걸렸다
- **폴더 상속**이 결정타였다. "상위 폴더 editor면 하위 문서도 editor"를 역할로 풀려니, 폴더가 옮겨질 때마다 수천 개 역할 매핑을 재계산해야 했다. 권한이 실제와 어긋나 **봐선 안 될 문서가 노출**됐다

팀은 Zanzibar 모델(OpenFGA)로 옮겼다. 관계 튜플로 바꾸자:

- 자원별 공유가 튜플 한 줄(`doc:X#viewer@user:bob`)이 됐다. 역할 폭발이 사라졌다
- 폴더 상속은 `tuple_to_userset` 한 정의로 풀렸다. 폴더를 옮겨도 **튜플 하나만 바뀌면** 하위 전체가 따라왔다
- "이 문서 볼 수 있는 사람 목록" UI가 그래프 역탐색으로 가능해졌다 — ABAC였다면 못 했을 기능이다

원인은 RBAC가 나빠서가 아니라, **관계로 표현해야 할 권한을 역할이라는 정적 라벨로 욱여넣은 것**이었다. 권한의 모양이 그래프인데 표를 쓰고 있었다.

## 실무 적용 포인트

1. **기본은 RBAC로 시작하라**: 권한이 조직 구조에 매핑되고 자원별 예외가 적으면 RBAC가 가장 싸고 감사도 쉽다. 역할 수가 `사용자 × 자원`으로 곱해지기 시작하면 그게 신호다 — 그때 갈아탄다.
2. **속성 조건이면 ABAC**: 시간·IP·데이터 등급·소유 여부처럼 **맥락**에 따라 갈리면 OPA(Rego)나 Cedar로 정책을 외부화하라. 단, ABAC는 "누가 접근 가능한가"를 못 나열하니, 그 기능이 필요하면 **별도 역색인**을 같이 둬야 한다.
3. **공유·중첩·계층이면 ReBAC**: 문서/폴더/리포지토리처럼 자원이 공유되고 상속되면 Zanzibar 모델(OpenFGA, SpiceDB, Ory Keto)을 검토하라. `tuple_to_userset`으로 상속이 한 정의로 풀린다.
4. **일관성 토큰을 반드시 써라**: ReBAC를 분산 배포하면 New Enemy Problem이 생긴다. OpenFGA의 `consistency` 옵션, SpiceDB의 **ZedToken**처럼 ACL 변경 이후 시점을 보장하는 토큰을 권한 체크에 넘겨라.
5. **PDP/PEP를 분리하라**: 권한 판단(Policy Decision Point)을 애플리케이션 코드에 하드코딩하지 말고, 강제 지점(Policy Enforcement Point)과 떼라. 인증([[oauth2-grant-types]])이 끝난 **그 다음** 단계가 인가(authorization)임을 분리해서 설계한다.
6. **하이브리드가 현실이다**: 실무는 보통 **RBAC로 거친 권한 + ABAC/ReBAC로 세밀한 권한**을 섞는다. GitHub도 org 역할(RBAC) 위에 리포 협력자 관계(ReBAC)를 얹는다. 한 모델로 다 풀려 하지 마라.

## 더 깊은 토끼굴

- Google 원논문: [Zanzibar: Google's Consistent, Global Authorization System (USENIX ATC 2019)](https://www.usenix.org/conference/atc19/presentation/pang) — 관계 튜플·zookie·New Enemy Problem의 출처
- OpenFGA 공식: [Modeling Guides](https://openfga.dev/docs/modeling/getting-started) — Zanzibar 모델을 실제 스키마로 쓰는 법
- NIST 공식: [RBAC 표준 (INCITS 359)](https://csrc.nist.gov/projects/role-based-access-control) — RBAC의 공식 정의와 한계
- OPA 공식: [Policy Language (Rego)](https://www.openpolicyagent.org/docs/latest/policy-language/) — ABAC 정책 엔진의 대표 구현
- [[oauth2-grant-types]]: 인증과 인가가 어디서 갈리는지
- [[mtls-zero-trust]]: "역할"이 아니라 "신원과 맥락"으로 신뢰를 다루는 확장
