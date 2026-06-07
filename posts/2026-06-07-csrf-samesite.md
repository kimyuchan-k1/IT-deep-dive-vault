---
title: SameSite=Lax가 기본값이 됐는데 왜 아직도 CSRF 토큰을 쓰나
date: 2026-06-07
day: 8
category: security
tags: [csrf, samesite, cookie, web-security, session]
related: ["[[jwt-vs-session]]", "[[oauth2-grant-types]]", "[[http-idempotency]]", "[[reverse-proxy-l4-l7]]", "[[mtls-zero-trust]]"]
difficulty: 2
short_text: |
  ⚠️ [Day 8] SameSite=Lax면 CSRF 끝? 아니다
  오해: 기본값이 다 막아준다
  실제: GET 변경+서브도메인=같은 사이트
  "img 태그 한 줄로 비번이 바뀜"
  📖 https://github.com/kimyuchan-k1/IT-deep-dive-vault/blob/main/posts/2026-06-07-csrf-samesite.md
---

# SameSite=Lax가 기본값이 됐는데 왜 아직도 CSRF 토큰을 쓰나

## 흔한 오해

"CSRF는 옛날 문제 아닌가? Chrome이 2020년부터 쿠키 `SameSite` 기본값을 `Lax`로 바꿨잖아. 그럼 다른 사이트에서 보낸 요청엔 세션 쿠키가 안 실리니까, 이제 CSRF 토큰 같은 건 레거시고 안 넣어도 되는 거 아냐?"

웹 보안 입문 글들이 딱 이 지점에서 멈춘다. "브라우저가 알아서 막아주니 끝"이라는 결론으로. 그래서 신규 프로젝트에서 CSRF 토큰을 빼고, `SameSite=Lax` 하나로 안심한다.

**`SameSite=Lax`가 CSRF 공격면을 크게 줄인 건 맞다. "그래서 CSRF가 끝났다"가 틀렸다.** `Lax`는 *교차 사이트 POST/fetch/iframe*만 막는다. 막지 못하는 구멍이 최소 두 개 남는다 — **상태를 바꾸는 GET 요청**, 그리고 **"같은 사이트(same-site)"의 정의가 생각보다 넓다는 것**. 이 둘을 모르면 토큰을 뺀 순간 구멍이 생긴다.

## 실제 원리

### CSRF의 본질: 브라우저가 쿠키를 자동으로 붙인다

CSRF(Cross-Site Request Forgery)는 **인증 정보를 훔치는 공격이 아니다.** 브라우저가 특정 도메인으로 요청을 보낼 때 그 도메인의 쿠키를 *자동으로* 붙여주는 성질을 악용한다. 공격자 사이트 `evil.com`에 들어간 피해자의 브라우저가 `bank.com`으로 요청을 쏘면, 피해자가 `bank.com`에 로그인된 상태라면 세션 쿠키가 **자동으로 같이 실린다.** 공격자는 쿠키 값을 볼 필요도 없다. 그냥 요청을 *대신 트리거*하기만 하면 서버는 "정상 로그인 사용자"로 본다.

그래서 방어의 핵심은 "이 요청이 **우리 사이트에서 의도적으로** 보낸 것인가, 아니면 **남의 사이트가 시킨 것인가**"를 구분하는 일이다. `SameSite` 쿠키 속성과 CSRF 토큰은 이 구분을 *서로 다른 방식*으로 한다.

### SameSite의 세 값과 정확한 경계

`SameSite`는 "이 쿠키를 교차 사이트 요청에도 붙일 것인가"를 정한다.

- **`None`**: 항상 붙인다 (옛날 기본 동작). CSRF 무방비.
- **`Lax`** (현 기본값): **교차 사이트의 안전하지 않은 요청**(POST, PUT, DELETE, fetch, XHR, iframe, img)엔 **안 붙인다.** 단, **사용자가 직접 클릭한 톱레벨 GET 내비게이션**(주소창 이동, 링크 클릭)엔 **붙인다.**
- **`Strict`**: 교차 사이트면 **톱레벨 GET 내비게이션에도** 안 붙인다.

여기가 핵심이다. `Lax`가 GET 내비게이션에 쿠키를 붙이는 건 UX를 위해서다 — 외부 링크로 `bank.com`에 들어갔는데 로그아웃돼 있으면 불편하니까. 그런데 만약 서버가 **GET으로 상태를 바꾸는 엔드포인트**(`GET /account/email?new=evil@x.com` 같은)를 갖고 있으면, 공격자는 그냥 `<a>` 링크나 `<img src=...>`로 그 GET을 트리거할 수 있고 **쿠키는 정상적으로 실린다.** `Lax`는 이걸 못 막는다.

### "Same-site"는 same-origin이 아니다

두 번째 구멍이 더 미묘하다. `SameSite`의 "사이트"는 **등록 가능 도메인(eTLD+1)** 기준이다. **스킴·포트·서브도메인 차이를 무시한다.**

```
origin: scheme + host + port  (https://app.bank.com:443)
site:   eTLD + 1              (bank.com)
```

즉 `evil.bank.com`과 `app.bank.com`은 **origin은 다르지만 same-site다.** 공격자가 `bank.com`의 서브도메인 하나를 장악하거나(서브도메인 takeover, XSS), 사용자 콘텐츠가 `*.bank.com`에서 호스팅되면, 거기서 보낸 요청은 `SameSite` 입장에서 **"같은 사이트"라 쿠키가 붙는다.** 게다가 `https`에서 `http`로의 다운그레이드도 same-site로 친다. `SameSite`는 **사이트 경계는 지키지만 origin 경계는 안 지킨다** — 이게 same-origin policy와 헷갈리면 안 되는 이유다.

### 그래서 토큰이 별개 층인 이유

CSRF 토큰(또는 double-submit, `Origin`/`Referer` 검증)은 **요청 본문/헤더에 예측 불가능한 값**을 요구한다. 공격자 사이트는 그 값을 *읽을 수 없으므로*(same-origin policy가 응답 읽기를 막는다) 위조 요청에 못 담는다. 이건 GET-변경 구멍도, 서브도메인 구멍도 함께 막는다. `SameSite`가 **브라우저 레벨 1차 방어**라면, 토큰은 **애플리케이션 레벨 2차 방어**다. 둘은 대체재가 아니라 **심층 방어(defense in depth)** 의 다른 층이다.

## 현장 시나리오

한 핀테크 스타트업이 신규 대시보드를 만들면서 "이제 `SameSite=Lax`가 기본이니 CSRF 토큰은 레거시"라고 판단하고 토큰 미들웨어를 뺐다. 인증은 세션 쿠키, 대부분 API는 POST + JSON이라 `Lax`로 충분해 보였다. 인과 사슬은 이랬다:

- 사용자 프로필 페이지에 **이메일 변경을 `GET /api/profile/email?value=...`로 처리**하는 옛 엔드포인트가 하나 남아 있었다. 프론트 리팩터링 중 POST로 못 바꾼 잔재
- 공격자가 피싱 메일에 `<img src="https://app.fintech.com/api/profile/email?value=attacker@evil.com">`를 심음
- 피해자가 메일을 열자 브라우저가 그 GET을 발사 → **톱레벨은 아니지만 `Lax`는 img의 GET에 쿠키를…** 막아야 했지만, 문제는 그게 아니라 **회사 마케팅 페이지가 `promo.fintech.com`에서 사용자 제출 HTML을 렌더**하고 있었다는 것
- 공격자는 `promo.fintech.com`에 스크립트를 넣어 `app.fintech.com`으로 fetch를 쐈고, `SameSite=Lax` 기준 **둘은 same-site라 세션 쿠키가 그대로 실렸다**
- 결과: 이메일이 공격자 주소로 바뀌고 → 비밀번호 재설정 링크가 공격자에게 → **계정 탈취.** 수십 건이 같은 패턴으로 터짐

원인은 `SameSite` 버그가 아니었다. **`Lax`를 same-origin 방어로 착각**한 것, 그리고 **상태 변경 GET을 방치**한 것이었다. 팀은 (1) 모든 상태 변경을 POST/PUT/DELETE로 강제하고, (2) **CSRF 토큰 미들웨어를 다시 넣고**, (3) 민감 쿠키를 `__Host-` 접두사 + `Secure`로 잠그고, (4) 서버에서 `Origin` 헤더를 화이트리스트와 대조하게 했다. 공격 패턴은 사라졌다. **브라우저가 1차로 막아준다고 해서, 앱이 2차를 안 깔아도 되는 건 아니었다.**

## 실무 적용 포인트

1. **상태 변경은 절대 GET으로 두지 마라**: `Lax`가 톱레벨 GET에 쿠키를 붙이므로 GET 변경 엔드포인트는 그 자체로 CSRF 구멍이다. 모든 변경을 POST/PUT/PATCH/DELETE로 강제 → [[http-idempotency]]의 안전한 메서드 분류를 따른다.
2. **민감 작업엔 CSRF 토큰을 유지하라**: 로그인·결제·이메일/비번 변경처럼 치명적인 작업엔 `SameSite`에 더해 동기화 토큰(synchronizer token) 또는 double-submit 쿠키를 둔다. SPA면 `XSRF-TOKEN` 쿠키 → `X-XSRF-TOKEN` 헤더 패턴.
3. **`Strict`가 가능한 쿠키는 `Strict`로**: 외부 링크 진입에서 로그인 상태가 필요 없는 쿠키(예: 결제 전용 토큰)는 `Strict`로 GET 내비게이션 구멍까지 막는다. 일반 세션은 UX 때문에 보통 `Lax`.
4. **`__Host-` 접두사 + `Secure`로 쿠키를 잠가라**: `Set-Cookie: __Host-session=...; Secure; Path=/; SameSite=Lax`. `__Host-` 접두사는 `Domain` 지정을 금지해 **서브도메인이 쿠키를 덮어쓰는 경로**를 차단한다 — same-site 구멍 완화.
5. **서버에서 `Origin`/`Referer`를 검증하라**: 변경 요청의 `Origin` 헤더를 허용 도메인 화이트리스트와 대조한다. 토큰을 보완하는 값싼 층. 단, `Referer`는 누락될 수 있으니 `Origin` 우선.
6. **서브도메인을 신뢰 경계로 착각하지 마라**: 사용자 콘텐츠·마케팅 페이지를 메인 앱과 **다른 등록 도메인**(`*.fintech-usercontent.com`)에 두면 same-site 우산에서 빠진다. 같은 eTLD+1 안에 두면 `SameSite` 보호가 무력해진다 → [[mtls-zero-trust]]의 "내부도 안 믿는다" 원칙.

## 더 깊은 토끼굴

- OWASP 공식: [Cross-Site Request Forgery Prevention Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html) — 토큰·SameSite·Origin 검증을 층으로 쌓는 정석
- MDN: [Set-Cookie: SameSite](https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Set-Cookie/SameSite) — Lax/Strict/None의 정확한 전송 규칙
- web.dev: [SameSite cookies explained](https://web.dev/articles/samesite-cookies-explained) — Chrome 기본값 변경과 same-site vs same-origin 구분
- [[jwt-vs-session]]: 세션 쿠키 vs 토큰 — CSRF 노출이 갈리는 지점
- [[http-idempotency]]: 안전한 메서드/멱등 메서드 분류, GET을 왜 변경에 쓰면 안 되나
- [[oauth2-grant-types]]: state 파라미터로 OAuth 콜백의 CSRF를 막는 그 원리
- [[mtls-zero-trust]]: 네트워크 내부조차 신뢰하지 않는 경계 설계
