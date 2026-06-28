---
title: terraform apply를 두 명이 동시에 눌렀더니, 멀쩡하던 RDS가 통째로 사라졌다
date: 2026-06-28
day: 26
category: cloud
tags: [terraform, iac, state, locking, backend]
related: ["[[terraform-state]]", "[[outbox-pattern]]", "[[s3-consistency-evolution]]", "[[wal-pitr]]", "[[idempotency-key]]"]
difficulty: 3
short_text: |
  🔥 [Day 26] apply 두 번에 RDS가 통째로 날아갔다
  오해: state는 캐시라 깨져도 재생성
  실제: state=진실원천→동시apply→락없음→리소스 destroy
  📖 https://github.com/kimyuchan-k1/IT-deep-dive-vault/blob/main/posts/2026-06-28-terraform-state.md
---

# terraform apply를 두 명이 동시에 눌렀더니, 멀쩡하던 RDS가 통째로 사라졌다

## 흔한 오해

"terraform state는 일종의 캐시 아닌가? 깨지면 `terraform refresh`로 실제 인프라 보고 다시 만들면 되지."

그래서 입문 튜토리얼들이 `terraform.tfstate`를 그냥 로컬 디렉터리에 두고 시작한다. 코드(`.tf`)가 진짜 정의이고 state는 그걸 거든 부산물이라고 여긴다. 그래서 state 파일을 `.gitignore`에 넣거나, 깨지면 지우고 다시 받으면 그만이라고 생각한다.

**틀린 건 아닌데, state가 무엇의 "진실"인지를 거꾸로 알고 있다.** `.tf` 코드는 "원하는 상태"이고, state는 "지금 실재하는 리소스와 코드 사이의 매핑"이다. 이 매핑이 깨지거나 두 사람이 동시에 건드리면, Terraform은 멀쩡한 리소스를 지워야 할 대상으로 오판한다.

## 실제 원리

### state는 캐시가 아니라 desired-state ↔ real-world 매핑

Terraform이 하는 일은 세 가지를 비교하는 것이다: **(1) `.tf` 코드(원하는 상태)**, **(2) state 파일(내가 만들었다고 기억하는 것)**, **(3) 클라우드 실제 리소스**. `plan`은 이 셋을 diff 한다.

여기서 핵심은 (2)가 없으면 Terraform은 "이 리소스를 내가 만들었는지"를 모른다는 것이다. state에 `aws_db_instance.main = db-abc123`이라는 매핑이 있어야 Terraform이 "저 RDS는 내 관리 대상"이라고 안다. 이 줄이 사라지면 `plan`은 코드에 있는 RDS를 **새로 만들어야 할 것**으로 보고, 실재하는 RDS는 **추적되지 않는 고아**가 된다. 반대로 state엔 있는데 코드에서 지워지면 → `destroy` 대상이 된다.

### 동시 실행이 위험한 이유: state는 read-modify-write다

`apply`의 내부 동작은 이렇다. ① state를 읽는다 → ② plan을 계산한다 → ③ 클라우드 API를 호출해 리소스를 바꾼다 → ④ 새 state를 **통째로 덮어쓴다**. 이건 전형적인 read-modify-write이고, 원자적이지 않다.

두 사람이 동시에 `apply`하면 고전적인 lost update가 난다.

- A가 state(버전 5)를 읽음, B도 버전 5를 읽음
- A가 리소스 추가 후 버전 6으로 덮어씀
- B는 자기가 읽은 버전 5 기준으로 계산한 결과를 그 위에 덮어씀 → A의 변경이 state에서 증발
- 다음 `apply` 때 Terraform은 "state엔 없는데 실재하는 A의 리소스"를 고아로 보거나, 정합성이 깨진 state로 엉뚱한 `destroy`를 만든다

이걸 막는 게 **state locking**이다. Terraform backend는 `apply` 시작 시 락을 잡고(예: DynamoDB에 `LockID` 행을 조건부 PUT), 끝나면 푼다. 락이 있으면 B의 `apply`는 "state is already locked"로 즉시 거부된다. 락은 [[idempotency-key]]처럼 "동시에 한 명만"을 보장하는 장치다.

### 로컬 state의 세 가지 결함

`terraform.tfstate`를 로컬/깃에 두면 세 가지가 동시에 터진다.

1. **락 없음** — 팀원 누구나 동시에 `apply` 가능, 위 lost update 직행
2. **단일 사본** — 노트북이 죽으면 인프라 매핑이 통째로 사라짐 (실 리소스는 살아있지만 Terraform이 "남의 것"으로 인식)
3. **평문 시크릿** — state엔 RDS 비밀번호, 생성된 키 같은 값이 **평문**으로 박힌다. 깃에 올리면 시크릿 유출

그래서 remote backend(S3+DynamoDB, Terraform Cloud, GCS 등)가 선택이 아니라 기본이다. S3는 사본+버저닝([[s3-consistency-evolution]]), DynamoDB는 락을 담당한다.

## 현장 시나리오

스타트업 인프라 팀 2명이 같은 `prod` 워크스페이스를 로컬 state로 운영했다. state는 "혹시 모르니" 공유 드라이브에 복사해 두는 식.

금요일 저녁, 엔지니어 A가 새 캐시 노드를 추가하려 `apply`를 걸었다. 거의 동시에 B가 보안 그룹 규칙을 바꾸려 자기 노트북의 (조금 오래된) state로 `apply`를 걸었다.

- B의 로컬 state엔 A가 최근에 import한 `aws_db_instance.main` 매핑이 **없었다**
- 락이 없으니 두 `apply`가 동시에 진행
- B의 `apply`가 나중에 끝나며 state를 통째로 덮어씀 → RDS 매핑이 사라진 state가 최종본이 됨
- 주말 사이 누군가 `terraform apply`로 정리 작업 → Terraform은 "코드엔 있지만 클라우드엔 내가 모르는 RDS가 있고, state엔 destroy 흔적"으로 plan을 잘못 계산
- `aws_db_instance.main`을 **destroy 후 재생성**하는 plan이 `yes` 한 번에 통과 → 프로덕션 DB가 통째로 날아감

복구는 RDS 자동 스냅샷에서 했지만 **최근 4시간 데이터 손실**. 원인 보고서 한 줄: **"state에 락이 없었고, 진실의 원천이 두 개의 노트북에 갈라져 있었다."**

## 실무 적용 포인트

1. **첫 줄부터 remote backend**: 로컬 state로 시작하지 마라. `backend "s3"`에 `bucket`, `key`, `dynamodb_table`(락용) 지정. 프로젝트 0일차에 박는다.
2. **S3 버킷 버저닝 필수**: `aws s3api put-bucket-versioning --versioning-configuration Status=Enabled`. state를 잘못 덮어써도 이전 버전으로 롤백 가능. 백업의 최후 보루.
3. **DynamoDB 락 테이블**: 파티션 키 `LockID`(String) 하나면 충분. 이게 없으면 S3 backend라도 동시 `apply`가 막히지 않는다.
4. **락이 꼬였을 때만 `force-unlock`**: `apply`가 비정상 종료하면 락이 남는다. `terraform force-unlock <LOCK_ID>`는 **그 apply가 정말 죽었음을 확인한 뒤에만**. 살아있는 apply에 쓰면 위 lost update를 직접 유발.
5. **state는 절대 손으로 편집하지 마라**: 옮길 땐 `terraform state mv`, 가져올 땐 `terraform import`, 뺄 땐 `terraform state rm`. 텍스트 에디터로 JSON을 고치면 매핑 정합성이 깨진다.
6. **시크릿은 state에 남는다고 가정**: backend 버킷은 SSE 암호화 + 버킷 정책으로 접근 최소화. 민감 출력은 `sensitive = true`로 로그만 가리되, state 평문 저장 자체는 막을 수 없음을 전제로 설계.
7. **워크스페이스/환경 분리**: `prod`와 `staging`은 다른 state key로. 하나의 거대 state는 락 경합과 blast radius를 키운다.

## 더 깊은 토끼굴

- Terraform 공식: [State](https://developer.hashicorp.com/terraform/language/state) / [Backend configuration](https://developer.hashicorp.com/terraform/language/backend) — state의 목적과 remote backend·locking 공식 설명
- [[s3-consistency-evolution]]: state 저장소로서 S3의 강한 일관성이 왜 중요한가 (read-after-write)
- [[idempotency-key]]: "동시에 한 명만"을 보장하는 락의 일반 원리
- [[wal-pitr]]: DB가 같은 read-modify-write 손실을 WAL+버저닝으로 푸는 방식
- [[outbox-pattern]]: 상태 변경과 부수효과를 원자적으로 묶는 또 다른 접근
