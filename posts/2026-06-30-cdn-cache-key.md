---
title: 로그인한 사용자에게 옆 사람의 장바구니가 그대로 떴다
date: 2026-06-30
day: 27
category: network
tags: [cdn, cache-key, vary, http-caching, cache-poisoning]
related: ["[[dns-cache-ttl]]", "[[cache-aside-vs-write-through]]", "[[http-idempotency]]", "[[reverse-proxy-l4-l7]]", "[[cache-stampede]]"]
difficulty: 3
short_text: |
  ⚠️ [Day 27] CDN이 옆 사람 장바구니를 그대로 보여줬다
  오해: CDN은 URL로 캐싱한다
  실제: 캐시키=호스트+경로+쿼리. 쿠키 무시→한 사람 응답이 전체에 공유
  📖 https://github.com/kimyuchan-k1/IT-deep-dive-vault/blob/main/posts/2026-06-30-cdn-cache-key.md
---

# 로그인한 사용자에게 옆 사람의 장바구니가 그대로 떴다

## 흔한 오해

"CDN은 URL로 캐싱하는 거 아닌가? 같은 주소면 같은 파일, 다른 주소면 다른 파일. 그래서 정적 파일만 CDN에 올리면 되지."

그래서 입문 글들이 "CDN = URL → 파일 매핑"으로 가르친다. `/static/logo.png`를 요청하면 엣지에 캐시되고, 다음 사람은 원본 서버를 안 거친다 — 여기까지는 맞다. 문제는 이 모델이 머릿속에 박히면 **"같은 URL인데 사람마다 다른 응답"** 이라는 경우를 아예 상상하지 못한다는 것이다.

**틀린 건 아닌데, "URL"이 곧 "캐시 키"라고 착각한 것이다.** CDN이 캐시를 찾을 때 쓰는 진짜 열쇠는 URL이 아니라 **캐시 키(cache key)** 라는 별도의 합성 문자열이다. 이걸 어떻게 구성하느냐가 적중률과 보안을 동시에 가른다.

## 실제 원리

### 캐시 키는 URL이 아니라 합성된 식별자다

엣지 노드는 응답을 저장할 때 키-값으로 저장한다. 이때 키는 기본적으로 이렇게 조립된다:

```
cache_key = scheme + host + path + (정규화된) query string
```

즉 `https://shop.com/item?id=42`와 `https://shop.com/item?id=43`은 다른 키다. 여기까지는 URL과 거의 같아 보인다. 핵심은 **이 조립 규칙을 운영자가 바꿀 수 있다는 것**, 그리고 **기본 규칙은 쿠키도 대부분의 헤더도 키에 넣지 않는다는 것**이다.

여기가 핵심이다. 같은 `/cart` URL을 두 사용자가 요청하면, 쿠키(세션)가 키에 안 들어 있는 한 CDN에겐 **완전히 동일한 요청**이다. 첫 번째 사용자의 개인화된 응답이 캐시되면, 두 번째 사용자는 원본 서버에 닿지도 못하고 그 캐시를 받는다.

### 키가 너무 좁으면 오염, 너무 넓으면 파편화

캐시 키 설계는 양쪽 절벽 사이의 외줄타기다.

- **키가 너무 좁다(구분 안 함)** → 서로 다른 응답이 같은 키를 공유 → 한 사람 응답이 모두에게 새어 나간다. 개인정보 유출이거나 [캐시 오염](https://portswigger.net/web-security/web-cache-poisoning)이다.
- **키가 너무 넓다(과하게 구분)** → 사실상 같은 콘텐츠가 매번 다른 키로 저장 → 적중률(hit ratio)이 바닥 → 원본 서버로 요청이 줄줄 새는 cache miss storm.

전형적인 파편화 사례가 **추적용 쿼리 파라미터**다. `?utm_source=...`, `?fbclid=...`는 콘텐츠를 1바이트도 안 바꾸지만, 쿼리 스트링이 키에 들어가면 같은 페이지가 수천 개의 키로 쪼개진다. 광고 캠페인 한 번에 적중률이 90%에서 20%로 떨어지는 일이 여기서 난다.

### Vary 헤더: 원본이 "이 기준으로 갈라라"라고 지시하는 법

같은 URL인데 응답이 갈라져야 할 때(언어, 압축 방식 등), HTTP에는 [`Vary`](https://developer.mozilla.org/en-US/docs/Web/HTTP/Headers/Vary) 헤더가 있다. 원본이 `Vary: Accept-Encoding`을 주면, CDN은 그 요청 헤더 값까지 키에 포함한다. gzip을 받는 사용자와 br(brotli)을 받는 사용자에게 각각 다른 압축본을 캐시하기 위해서다.

함정은 `Vary: User-Agent`다. User-Agent 문자열은 사실상 무한대로 다양하다. 이걸 키에 넣으면 같은 페이지가 브라우저 버전마다 쪼개져 적중률이 붕괴한다. `Vary: Cookie`도 마찬가지로 세션 쿠키가 사람마다 다르니 캐시가 사실상 무력화된다 — 의도가 그거라면 맞지만, 모르고 켜면 CDN을 끈 것과 같다.

### 정규화(normalization)가 적중률을 만든다

좋은 캐시 키 설계의 핵심은 **의미 없는 차이를 키에서 제거하는 것**이다. 쿼리 파라미터를 알파벳순 정렬(`?b=2&a=1` → `?a=1&b=2`), 추적 파라미터 제거, 대소문자 정규화. 이렇게 "같은 콘텐츠는 같은 키"로 모아야 적중률이 올라간다. 캐시는 [[cache-aside-vs-write-through]]와 같은 고민을 공유한다 — 무엇을 같은 것으로 볼 것인가가 전부다.

## 현장 시나리오

이커머스 사이트가 트래픽이 늘자 "응답 빠르게 하자"며 CDN을 전면에 깔았다. 정적 파일만 캐시할 생각이었는데, 설정 실수로 **`Cache-Control` 헤더가 없는 응답도 엣지가 기본 TTL로 캐시**하도록 룰이 들어갔다.

- 사용자 A가 로그인 후 `/cart`를 열었다. 원본 서버는 A의 장바구니가 담긴 HTML을 반환했는데, 이 응답에 `Cache-Control: private`이 빠져 있었다
- CDN 기본 캐시 키는 `host + path`뿐 — **세션 쿠키는 키에 없었다**
- 엣지는 A의 개인화된 `/cart` 응답을 그 키로 저장 (TTL 60초)
- 30초 뒤 사용자 B가 `/cart`를 열었다. CDN 입장에서 키가 동일 → 원본에 가지 않고 **A의 장바구니를 그대로 반환**
- B 화면에 A의 이름, 담긴 상품, 배송지 일부가 노출 → 개인정보 사고

원인 보고서 한 줄: **"캐시 키에 세션을 안 넣었는데, 개인화 응답에 `private`도 안 붙였다."** 키를 좁게 둔 채(쿠키 무시) 캐시 불가 응답까지 캐시한 것이 겹쳐 터진 사고다.

## 실무 적용 포인트

1. **개인화 응답엔 반드시 `Cache-Control: private, no-store`**: 로그인·장바구니·결제 등 사용자별 응답은 엣지가 절대 공유 캐시하지 못하게 막는다. CDN 키 설정보다 이 헤더가 1차 방어선이다.
2. **추적 파라미터는 키에서 제거**: `utm_*`, `fbclid`, `gclid`는 캐시 키 정규화에서 빼라. Cloudflare는 Cache Key 규칙으로 `ignore_query_strings_order`와 특정 파라미터 제외를 지원한다. 적중률이 즉시 회복된다.
3. **`Vary: User-Agent`를 켜지 마라**: 디바이스 분기가 꼭 필요하면 User-Agent 원문 대신 `mobile/desktop` 2~3개로 정규화한 커스텀 키를 써라. 원문을 키에 넣으면 카디널리티 폭발.
4. **쿠키는 필요한 것만 키에 포함**: "쿠키 전체로 Vary"는 사실상 캐시 끄기. A/B 테스트 버킷 쿠키 하나처럼 콘텐츠를 실제로 가르는 키만 골라 포함.
5. **기본 TTL로 전체 캐시하는 룰 금지**: `Cache-Control`이 없는 응답을 엣지가 임의 TTL로 캐시하지 않게 한다. 캐시 여부는 원본이 헤더로 명시(opt-in)하게 강제.
6. **퍼지(purge)는 키 단위로 정확히**: 콘텐츠를 갱신하면 `cache-tag`/surrogate key로 묶어 한 번에 무효화. URL만으로 퍼지하면 정규화된 키 변형들이 안 지워져 stale이 남는다.

## 더 깊은 토끼굴

- RFC 9111 [HTTP Caching](https://www.rfc-editor.org/rfc/rfc9111) — `Cache-Control`, `Vary`, 공유 캐시의 동작을 정의한 1차 표준
- [PortSwigger: Web Cache Poisoning](https://portswigger.net/web-security/web-cache-poisoning) — 캐시 키 밖의 입력(unkeyed input)으로 캐시를 오염시키는 공격 원리
- [[dns-cache-ttl]]: 같은 "엣지에서의 캐싱"이지만 이름 해석 계층의 TTL 트레이드오프
- [[reverse-proxy-l4-l7]]: CDN 엣지가 L7에서 무엇을 보고 키를 만드는가
- [[cache-stampede]]: 캐시 키가 만료되는 순간 원본으로 몰리는 thundering herd
