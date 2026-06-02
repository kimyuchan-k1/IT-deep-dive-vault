# PlayMCP 연결 가이드

목표: 카카오 PlayMCP를 Cowork(현재 이 Claude 세션)에 Custom Connector로 등록하여 카카오톡 "나와의 채팅"으로 메시지를 보낼 수 있게 한다.

소요 시간: **약 10분**

---

## 사전 준비

- ☐ 카카오 계정 (평소 쓰시는 것)
- ☐ Cowork (현재 세션 그대로)

이게 전부. 카카오 디벨로퍼스 가입/앱 등록 모두 **불필요**.

---

## 단계별

### 1단계 — PlayMCP 사이트 접속 & 로그인 (2분)

1. https://playmcp.kakao.com/ 접속
2. 카카오 계정으로 로그인 (평소 카톡 쓰는 계정)
3. 첫 로그인이면 약관 동의

### 2단계 — "나와의 채팅" 도구 활성화 (2분)

1. 상단/사이드 메뉴에서 **"도구함"** 또는 **"Toolbox"** 찾기
2. 카카오 공식 도구 목록에서 **"카카오톡 나와의 채팅"** 찾기
3. **활성화/추가** 버튼 클릭
4. 권한 동의 (메시지 전송 권한)

### 3단계 — Cowork에 PlayMCP를 Custom Connector로 등록 (5분)

> ⚠️ Cowork의 Custom Connector 메뉴 위치는 버전에 따라 다를 수 있음. 아래는 일반적인 흐름.

**옵션 A — PlayMCP 사이트에서 직접 연결 (있는 경우)**:
1. PlayMCP "도구함" 페이지에서 **"Claude/Cowork에 연결"** 버튼 찾기
2. PlayMCP가 발급하는 **연결 URL** 복사
3. Cowork 설정에서 Custom Connector로 붙여넣기

**옵션 B — 수동 등록**:
1. Cowork 우측 상단 ⚙️ 또는 메뉴 → **Settings / 설정**
2. **Connectors** 또는 **MCP 서버** 항목
3. **"Add Custom Connector"** 또는 **"커스텀 커넥터 추가"**
4. PlayMCP의 MCP 서버 URL 입력
   - 보통 형식: `https://playmcp.kakao.com/mcp/<your-user-token>` 같은 형태
   - 정확한 URL은 PlayMCP 본인 페이지에서 발급받음
5. 연결 인증 (OAuth 팝업)
6. 연결 성공 확인

### 4단계 — Cowork 세션에서 도구 인식 확인 (1분)

연결되면 Cowork에서 사용 가능한 MCP 도구 목록에 카카오톡 관련 도구가 보여야 함. 예시:
- `kakaotalk.send_to_me`
- `mcp__playmcp__send_message_to_me`
- 또는 비슷한 이름

확인 방법: 저(Claude)에게 "지금 쓸 수 있는 카카오톡 도구가 뭐가 있어?" 물어보면 확인 가능.

---

## 첫 발송 PoC (5분)

연결 후 즉시 검증:

### 테스트 1 — 짧은 텍스트 발송
```
저에게 요청: "안녕 카카오톡!" 라고 나와의 채팅으로 보내줘
```
→ 카카오톡 "나와의 채팅"에 메시지 도착 확인

### 테스트 2 — 긴 본문 + 마크다운 발송
```
저에게 요청: posts/2026-06-01-redis-bigkey.md 파일을 읽어서 
            그 본문을 카카오톡 나와의 채팅으로 보내줘
```
→ 어떻게 보이는지 확인:
- ☐ 한 메시지로 다 들어가는가? (잘리지 않는가?)
- ☐ 마크다운 헤더/굵게/링크가 렌더되는가?
- ☐ `[[wikilinks]]`는 어떻게 표시되는가?

### 테스트 3 — 링크 + 짧은 설명
```
저에게 요청: "오늘의 글: Redis BigKey가 클러스터를 죽이는 이유 
            👉 https://github.com/{username}/{repo}/blob/main/posts/2026-06-01-redis-bigkey.md"
            라고 카톡 보내줘
```
→ URL 미리보기/카드 펼침 여부 확인

---

## 검증 결과별 다음 행동

| PlayMCP 발송 한계 | 다음 행동 |
|---|---|
| ✅ 마크다운 + 긴 텍스트 OK | **본문 통째 발송**. GitHub Pages 불필요. 가장 단순. |
| ⚠️ 텍스트만 가능, 마크다운 ❌ | 본문은 GitHub raw URL로, 카톡엔 제목+요약+URL만 |
| ⚠️ 짧은 텍스트만 가능 | GitHub Pages 셋업 후 카드+링크 (v2 방식과 유사) |
| ❌ 발송 실패 | 비공식 [kakao-bot-mcp-server](https://github.com/inspirit941/kakao-bot-mcp-server) 또는 v2의 디벨로퍼스 직접 연동으로 fallback |

---

## 트러블슈팅

| 증상 | 원인 가능성 | 해결 |
|---|---|---|
| PlayMCP 로그인 실패 | 카카오 계정 이슈 | https://accounts.kakao.com 에서 직접 로그인 확인 |
| Custom Connector 등록 안 됨 | Cowork 버전 이슈 | Cowork 업데이트 또는 옵션 A로 시도 |
| Cowork에서 도구 인식 안 됨 | 연결 인증 미완료 | Settings에서 PlayMCP 커넥터 재인증 |
| 메시지 발송 시 401/403 | OAuth 토큰 만료 | PlayMCP에서 도구 재활성화 → Cowork에서 커넥터 재인증 |
| 메시지 안 옴 (에러 없음) | 권한 누락 | PlayMCP 도구함에서 "나와의 채팅" 권한 확인 |

---

## 진행 체크리스트

- [ ] PlayMCP 로그인 완료
- [ ] "나와의 채팅" 도구 활성화 완료
- [ ] Cowork에 Custom Connector 등록 완료
- [ ] Cowork 세션에서 도구 인식 확인
- [ ] 테스트 1 (짧은 텍스트) 성공
- [ ] 테스트 2 (긴 본문) 결과 기록: ______
- [ ] 테스트 3 (URL 카드) 결과 기록: ______
- [ ] 검증 결과 → v3 설계서 8번 결정사항 확정

여기까지 끝나면 → 단계 4 (인덱스 빌더 작성)로 진행.
