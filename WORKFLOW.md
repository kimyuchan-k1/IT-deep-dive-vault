# 매일 작업 워크플로우

Cowork 스케줄이 매일 09:00 KST에 트리거하면 **이 문서 순서대로** 실행한다.
스케줄 프롬프트는 한 줄: "WORKFLOW.md 읽고 그대로 수행해라."

---

## 환경 변수 / 경로

- **vault 루트**: `F:\IT TEAC DATABASE\IT 정보 전달\`
- **bash 경로**: `/sessions/<session>/mnt/IT 정보 전달/`
- **GitHub repo**: https://github.com/kimyuchan-k1/IT-deep-dive-vault
- **GitHub PAT**: (Cowork secrets 또는 환경변수로 보관)
- **카카오톡 발송 도구**: `mcp__<uuid>__KakaotalkChat-MemoChat`

---

## 0. 사전 읽기 (필수, 매번)

다음 파일을 차례로 읽는다:

1. `STYLE_GUIDE.md` — 글쓰기 규칙
2. `EXAMPLES.md` — 모범 글 1편 이상
3. `topics_catalog.md` — 후보 주제 풀
4. `posts/*.md` — 최근 7일 글의 frontmatter (제목/카테고리/태그/related)
5. `index/embeddings.sqlite` — 의미 유사도 검색용
6. `index/graph.json` — 그래프 인접성용 (없으면 빈 그래프)

빠진 파일이 있으면 그 파일은 건너뛰고, 마지막에 보고에 기록.

---

## 1. 주제 선정

### 1-1. 후보 샘플
- `topics_catalog.md`에서 `status: pending`인 항목들 추림
- 카테고리 균형: 최근 7일 글의 카테고리 분포 확인 → 부족한 카테고리 우선
- 난이도 균형: 어제가 4 이상이면 오늘은 2~3, 어제가 1~2면 오늘은 3+
- 후보 3개 선정

### 1-2. 중복 체크 (각 후보별)
- 후보 제목 + 슬러그 임베딩 생성
- `embeddings.sqlite`의 기존 글 임베딩과 코사인 유사도 계산
- **유사도 > 0.85 → 탈락**

### 1-3. 그래프 인접성 체크 (각 후보별)
- `graph.json`에서 후보 주제의 1-hop, 2-hop 이웃 카테고리 분포
- 1-hop 이웃이 모두 같은 카테고리만 있으면 → 후보 우선순위 낮춤 (편향 방지)
- 고립 영역(주변 노드 0~1개)이면 → 우선순위 높임 (지식 공백 메우기)

### 1-4. 최종 1개 확정
- 위 가중치 합산 후 1개 선택
- 선택 사유를 보고에 한 줄 기록 (예: "그래프 공백 영역 + 카테고리 균형")

---

## 2. 본문 작성

`STYLE_GUIDE.md`의 6단계 골격 그대로:

1. 후킹 한 줄 (글 제목)
2. ## 흔한 오해
3. ## 실제 원리 (가장 길게)
4. ## 현장 시나리오
5. ## 실무 적용 포인트 (구체 수치 포함)
6. ## 더 깊은 토끼굴 ([[wikilinks]] + 1차 출처 URL 1개 이상)

작성 시 `EXAMPLES.md`의 모범 글 톤을 모방한다.

### Frontmatter 작성
```yaml
---
title: {후킹 한 줄}
date: {오늘 날짜 YYYY-MM-DD}
day: {지금까지 발행한 글 수 + 1}
category: {topics_catalog의 카테고리}
tags: [...]
related: ["[[slug1]]", "[[slug2]]"]
difficulty: {1~5}
short_text: |
  {200자 카드}
---
```

### 200자 카드 (`short_text`)
공식대로:
```
{이모지} [Day N] {임팩트있는 한 문장}

오해: {짧게}
실제: {화살표 인과}

"{구체 사례 한 줄}"

📖 {GitHub blob URL}
```

**URL은 작성 시점에 commit 전이라 아직 없지만, 예측 가능한 형태로 미리 박는다:**
```
https://github.com/kimyuchan-k1/IT-deep-dive-vault/blob/main/posts/{YYYY-MM-DD}-{slug}.md
```

---

## 3. 자기 검증 (필수)

`STYLE_GUIDE.md`의 9번 체크리스트 9개 항목 모두 ✅ 확인.
하나라도 빠지면 **다시 쓰기** (최대 2회 재시도).

검증 통과한 후에만 다음 단계로 진행.

---

## 4. 파일 저장

- 파일 경로: `posts/{YYYY-MM-DD}-{slug}.md`
- `topics_catalog.md`의 해당 항목 `status: pending` → `status: done` 수정
- `topics_catalog.md` 하단의 진행 현황 (X / Y) 갱신

---

## 5. 인덱스 갱신

`scripts/update_index.py` 실행:
- 새 글의 임베딩 생성 → `index/embeddings.sqlite` insert
- 새 글의 [[wikilinks]] + frontmatter `related` 파싱 → `index/graph.json` 노드/엣지 추가

스크립트가 아직 없으면 직접 수행 (인덱스 형식은 단순 JSON/SQLite).

---

## 6. GitHub commit (REST API)

`scripts/commit_via_api.py`로 다음 파일들을 한 commit에 push:
- `posts/{새 글}.md`
- `topics_catalog.md` (status 갱신)
- `index/embeddings.sqlite` (선택, 너무 크면 제외)
- `index/graph.json`

commit 메시지: `feat: Day N - {title}`

성공 응답에서 commit SHA 추출 → 1초 대기 → URL HEAD 체크 (`HEAD https://github.com/.../blob/main/posts/...md`):
- 200 → 다음 단계
- 404 → 30초 대기 후 1회 재시도 → 그래도 실패면 카톡 발송 중단 + 에러 보고

---

## 7. 카카오톡 발송

`mcp__<uuid>__KakaotalkChat-MemoChat` 도구 호출:
- `message`: 글의 `short_text` 그대로 (이미 URL 포함, 200자 이내 검증됨)

성공 응답 확인. 실패 시:
- 재시도 1회
- 그래도 실패면 보고에 기록 (다음날 묶음 발송 검토)

---

## 8. 최종 보고

스케줄 실행 결과를 다음 형식으로 한 줄 출력:

```
✅ Day N 발송 완료 - {title}
  카테고리: {category}, 난이도: {N}
  선정 사유: {1-4의 선정 사유}
  commit: {SHA 7자리}
  중복 체크: {탈락 후보 N개}
  소요 시간: {M초}
```

또는 실패 시:
```
❌ Day N 실패 - 단계 {N}에서 중단
  사유: {짧게}
  복구: {수동 트리거 권장 / 다음날 묶음 / 무시}
```

---

## 가드레일

- **6번(commit) 실패 시 7번(카톡) 절대 보내지 마라** — 죽은 링크 방지
- **3번(자기 검증) 2회 재시도 후도 실패하면 발송 중단** — 품질 하한선
- **임베딩 API 호출 실패** → 그날은 임베딩 없이 진행, 다음날 재인덱싱
- **PlayMCP 발송 실패** → 다음날 묶음 발송 (vault에는 정상 저장됨)

---

## 첫 7일 운영 모드 (튜닝 기간)

- 매일 도착한 카드에 대해 사용자 피드백 받기
- `STYLE_GUIDE.md`의 다음을 튜닝:
  - 6단계 골격이 너무 길거나 짧지 않은지
  - 톤이 과한지/부족한지
  - 200자 카드 후킹이 잘 먹는지
- 중복 임계값 0.85 → 실제 운영 보고 0.80~0.90 사이 조정
- 카테고리 가중치 균형 검토
