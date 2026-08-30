---
title: DELETE는 멱등이라 재시도해도 안전한 줄 알았다. 쿠폰 재고가 두 번 깎였다
date: 2026-08-30
day: 81
category: network
tags: [http, idempotency, retry, rfc9110]
related: ["[[idempotency-key]]", "[[retry-exponential-backoff-jitter]]", "[[reverse-proxy-l4-l7]]"]
difficulty: 2
short_text: |
  ⚠️ [Day 81] DELETE 재시도가 재고를 두 번 깎았다
  오해: 메서드가 멱등이면 안전하다
  실제: 핸들러가 count-1 → 프록시 자동 재시도에 이중 차감
  📖 https://github.com/kimyuchan-k1/IT-deep-dive-vault/blob/main/posts/2026-08-30-http-idempotency.md
---

프록시 설정은 건드린 적도 없었다. 그런데 쿠폰 재고가 하루에 두 번씩 깎였다.

## 흔한 오해

"GET, PUT, DELETE는 멱등하다. 그러니까 타임아웃 나면 그냥 재시도해도 된다. 조심할 건 POST뿐이다."

입문서와 면접 질문지가 딱 이 표를 준다. safe = GET/HEAD/OPTIONS, idempotent = safe + PUT/DELETE, 나머지는 아님. 표 자체는 맞다. 그래서 클라이언트 재시도 정책도, nginx 같은 리버스 프록시의 업스트림 재시도도 이 표를 기준으로 짠다.

빠진 게 하나 있다. **그 표는 메서드가 지켜야 할 약속이지, 당신의 핸들러가 지키고 있다는 증거가 아니다.**

## 실제 원리

RFC 9110은 멱등을 이렇게 정의한다 — "동일한 요청을 여러 번 보냈을 때 **서버에 의도된 효과**가 한 번 보냈을 때와 같다". 여기서 두 가지가 갈린다.

**첫째, 멱등은 응답이 아니라 상태에 대한 약속이다.** `DELETE /coupons/9f2`를 두 번 보내면 첫 번째는 `204`, 두 번째는 `404`가 온다. 응답 코드는 다르지만 서버 상태는 같으니 멱등이 맞다. 문제는 클라이언트다. 재시도 로직이 `404`를 실패로 분류하면, 성공한 삭제가 실패로 보고되고 상위 로직이 보상 트랜잭션을 돌린다. 멱등한 API가 멱등하지 않은 시스템을 만든다.

**둘째, 멱등성은 메서드 이름이 아니라 핸들러의 연산 종류에서 나온다.** 절대값을 쓰면 멱등, 상대값을 쓰면 아니다.

```sql
UPDATE coupons SET stock = 40      WHERE id = ?;  -- 몇 번을 해도 40. 멱등.
UPDATE coupons SET stock = stock-1 WHERE id = ?;  -- 두 번 하면 38. 멱등 아님.
```

두 번째 쿼리를 `DELETE /coupons/9f2/stock` 뒤에 숨겨두면, HTTP 스펙상으로는 멱등한 엔드포인트인데 실제로는 호출 횟수만큼 효과가 쌓인다. 그리고 이 거짓말을 믿는 건 사람이 아니라 **중간 경로의 기계들**이다.

RFC 9110은 자동 재시도가 멱등성에 기대어 동작한다고 명시한다. nginx도 똑같다. `proxy_next_upstream`은 업스트림이 죽거나 타임아웃일 때 다음 서버로 요청을 넘기는데, **이미 업스트림에 전송된 비멱등 요청(POST 등)은 넘기지 않는다.** 넘기게 하려면 `non_idempotent` 파라미터를 명시적으로 켜야 한다. 뒤집으면, `DELETE`나 `PUT`은 그 플래그 없이도 **기본 설정에서 재전송 대상**이라는 뜻이다. 아무도 재시도 코드를 안 짰는데 요청이 두 번 가는 경로가 여기서 생긴다 — [[reverse-proxy-l4-l7]] 계층이 조용히 하는 일이다.

`PATCH`는 또 다른 함정이다. 스펙상 멱등이 아니다. JSON Patch(`{"op":"move"}`, 배열 `add`)는 순서와 현재 상태에 의존하니 두 번 적용하면 결과가 달라진다. 반면 JSON Merge Patch는 필드 치환이라 사실상 멱등하다. 같은 `PATCH` 메서드인데 페이로드 포맷에 따라 성질이 갈린다.

## 현장 시나리오

쿠폰 발급 API를 운영하던 팀. 구조는 `클라이언트 → nginx → 앱 서버 3대`.

엔드포인트는 `DELETE /coupons/{code}/stock` 하나. "재고를 하나 소진한다"는 의미로 만들었고, 내부 구현은 `UPDATE coupons SET stock = stock - 1`. 리뷰에서 아무도 안 걸었다. DELETE니까 멱등이라고 다들 생각했다.

앱 서버 한 대의 GC가 길어지면서 응답이 `proxy_read_timeout` 60초를 넘겼다. nginx는 그 요청을 **2번 서버로 그대로 재전송**했다. 첫 서버는 죽은 게 아니라 느렸을 뿐이라 GC가 끝난 뒤 `stock - 1`을 커밋했고, 2번 서버도 `stock - 1`을 커밋했다. 한 사용자의 요청 하나로 재고 2 차감.

인과 사슬: **GC 폴즈 → 60초 타임아웃 → nginx가 멱등 메서드라 판단해 자동 재전송 → 상대값 UPDATE가 양쪽에서 커밋 → 재고 이중 차감.** 앱 로그에는 `204`가 두 줄 찍혔지만 access log의 요청 ID는 하나였다. GC가 잦은 날에만 터져서, 재현이 안 된다는 이유로 2주간 "DB 정합성 이슈"로 분류돼 있었다.

수정은 쿼리 한 줄이었다. `stock = stock - 1`을 없애고, 발급 레코드를 `INSERT`한 뒤 `stock`을 **발급 건수로부터 계산**하도록 바꿨다. 발급 레코드에는 `(coupon_code, user_id)` unique 제약. 재전송이 와도 두 번째 INSERT는 충돌로 죽는다.

## 실무 적용 포인트

1. **메서드 라벨 말고 SQL을 봐라.** `SET x = 5`는 멱등, `SET x = x - 1`은 아니다. 멱등 메서드(`PUT`/`DELETE`) 핸들러에 상대값 연산이 있으면 그 자리에서 버그다.
2. **nginx `proxy_next_upstream`을 지금 확인한다.** 기본값은 `error timeout`이고, 비멱등 요청만 재전송에서 빠진다. 멱등 메서드는 기본 재전송 대상이다. 상한을 걸려면 `proxy_next_upstream_tries 2;`, `proxy_next_upstream_timeout 10s;`.
3. **PUT은 전체 표현을 실어야 한다.** 델타를 `PUT` 바디에 담는 순간 멱등이 깨진다. 부분 갱신은 `PATCH` + `If-Match: {etag}`로 잃어버린 갱신(lost update)까지 같이 막는다.
4. **`PATCH`에 JSON Patch를 쓴다면 멱등이 아니라고 문서에 박아라.** 멱등이 필요하면 JSON Merge Patch(`application/merge-patch+json`)로 간다.
5. **DELETE의 두 번째 호출을 팀 규약으로 정한다.** `404`를 주고 클라이언트가 성공 처리하든, 항상 `204`를 주든 — 정하지 않으면 재시도 코드가 알아서 오해한다.
6. **부수효과 있는 `POST`/`PATCH`에는 `Idempotency-Key` 헤더.** 메서드 성질로 해결이 안 되는 구간은 이걸로 덮는다 — [[idempotency-key]].
7. **클라이언트 타임아웃 > 서버 p99 + 여유.** 타임아웃이 짧으면 서버는 멀쩡히 처리 중인데 재시도가 겹친다. 백오프에는 지터를 넣는다 — [[retry-exponential-backoff-jitter]].

멱등 메서드 표는 "이 메서드는 재시도해도 된다"는 표가 아니다. "이 메서드로 만들 거면 재시도돼도 괜찮게 구현하라"는 요구사항 표다. 프록시와 브라우저는 이미 그 요구사항을 지켰다고 가정하고 움직이고 있다.

## 더 깊은 토끼굴

- [[idempotency-key]] — 메서드 성질로 못 막는 POST 중복 차단
- [[retry-exponential-backoff-jitter]] — 재시도가 겹치지 않게 만드는 법
- [[at-least-once-vs-at-most-once]] — 중복이 태생적으로 생기는 이유
- [[upsert-idempotency]] — DB 레벨에서 멱등 쓰기 만들기
- [[reverse-proxy-l4-l7]] — 요청을 조용히 재전송하는 계층

**출처**
- [RFC 9110 §9.2.2 — Idempotent Methods](https://www.rfc-editor.org/rfc/rfc9110.html#section-9.2.2) (멱등 정의와 자동 재시도 근거)
- [nginx — `proxy_next_upstream`](https://nginx.org/en/docs/http/ngx_http_proxy_module.html#proxy_next_upstream) (`non_idempotent` 파라미터 동작)
