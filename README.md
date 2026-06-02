# IT Deep-Dive Vault

매일 오전 카카오톡으로 받는 백엔드/인프라 딥다이브 글들을 누적하는 개인 RAG vault.

## 무엇인가

- 매일 09:00 KST에 Claude가 새로운 백엔드/인프라 주제를 골라 글을 쓰고
- 이 vault에 저장하고
- 카카오톡 "나에게 보내기"로 카드를 보낸다
- 이미 다룬 주제는 임베딩 + 그래프로 중복 감지 → 자동으로 피한다

## 구조

| 디렉터리 | 역할 |
|---|---|
| `posts/` | Obsidian Vault 형식 MD. **진실의 원천**. |
| `attachments/` | 이미지/도식 |
| `.obsidian/` | Obsidian 앱 설정 (그래프 뷰) |
| `index/` | 임베딩 SQLite, 그래프 JSON (파생, 재빌드 가능) |
| `site/` | GitHub Pages 빌드 결과 |
| `scripts/` | 자동화 스크립트 (Cowork이 호출) |

## 어떻게 읽는가

- **카카오톡 카드**: 매일 도착. 짧은 후킹 + 버튼 → 딥다이브 페이지
- **Obsidian 데스크탑/모바일 앱**: 이 폴더를 vault로 열면 그래프 뷰 자동
- **GitHub Pages**: 정적 사이트로 공개 (남에게 공유 가능)
- **여기 GitHub repo**: 검색, 이력, 백업

## 설계서

- [v1](./00_프로젝트_설계서_v1.md) — 첫 설계
- [v2](./01_프로젝트_설계서_v2.md) — **현재**. Cowork 스케줄 + GitHub 백업 + Obsidian Vault + personal RAG
