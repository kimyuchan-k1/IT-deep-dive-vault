---
title: SSE는 끊기면 알아서 다시 붙는다. 그래서 주문 2건이 조용히 사라졌다
date: 2026-08-17
day: 71
category: network
tags: [sse, eventsource, http, streaming, nginx, alb]
related: ["[[websocket-vs-sse-vs-polling]]", "[[retry-exponential-backoff-jitter]]", "[[http2-vs-http3]]", "[[reverse-proxy-l4-l7]]", "[[idempotency-key]]"]
difficulty: 3
short_text: |
  🔥 [Day 71] 재연결은 됐는데 주문 2건이 사라졌다
  오해: SSE는 끊기면 알아서 복구된다
  실제: 60초 idle 타임아웃→재연결→Last-Event-ID 무시→유실
  📖 https://github.com/kimyuchan-k1/IT-deep-dive-vault/blob/main/posts/2026-08-17-sse-prod-ops.md
---

# SSE는 끊기면 알아서 다시 붙는다. 그래서 주문 2건이 조용히 사라졌다

## 흔한 오해

> "SSE는 WebSocket보다 훨씬 쉽잖아. 그냥 HTTP고, 브라우저가 끊기면 알아서 재연결해준다며. 서버는 `text/event-stream`으로 계속 쓰기만 하면 되고."

`new EventSource(url)` 한 줄에 `onmessage` 하나 붙이면 끝나니 그렇게 읽힌다. 비교 글들도 "SSE는 자동 재연결이 내장돼 있다"를 장점 칸에 그냥 써놓는다.

틀린 말은 아니다. 다만 **자동 재연결은 계약의 절반이다.** 명세가 브라우저에게 시킨 건 "끊기면 다시 붙고, 마지막으로 받은 id를 헤더에 실어 보내라"까지다. 그 헤더를 읽고 빈 구간을 메우는 건 서버 몫이고 명세는 거기까지 강제하지 않는다. 절반만 구현하면 시스템은 끊긴 적 없는 것처럼 동작한다 — 데이터만 빼고.

## 실제 원리

### 프로토콜은 필드 네 개가 전부다

SSE 스트림은 UTF-8 텍스트고, 파싱 단위는 줄이다. 필드는 `data`, `event`, `id`, `retry` 네 개뿐이고, **빈 줄 하나가 이벤트를 디스패치한다.** `:`로 시작하는 줄은 주석이라 그냥 버려진다.

```
id: 10482
event: order.created
data: {"orderId":10482,
data: "amount":24000}

: keepalive
```

여기서 자주 밟는 지점 셋.

- `data:` 여러 줄은 `\n`으로 이어붙여 **하나의** `event.data`가 된다. 개행이 섞인 JSON도 조각나지 않고 그대로 복원된다.
- 콜론 뒤 공백은 **한 개만** 제거된다. `data:  {"a":1}`의 값은 ` {"a":1}`이다.
- `id:`만 있고 `data:`가 없는 이벤트는 **디스패치되지 않는다.** 그런데 last event ID 버퍼는 갱신된다. 이벤트를 안 보내면서 커서만 미는 게 가능하다는 뜻이다.

### 자동 재연결의 계약서

스트림 도중 연결이 끊기면 브라우저는 잠시 기다렸다가 다시 붙는다. 이때 마지막으로 받은 `id`가 비어 있지 않으면 **`Last-Event-ID` 요청 헤더**에 실어 보낸다. 대기 시간은 서버가 `retry: 5000`(밀리초)으로 지정할 수 있고, 지정하지 않으면 명세상 "구현이 정하는 값"이다. 명세는 재시도가 반복될 때 브라우저가 **더 기다려도 된다**고만 허용한다 — 지수 백오프는 보장이 아니라 선택 사항이다.

여기가 핵심이다. 브라우저는 헤더를 보내주는 데까지만 책임진다. 서버가 `Last-Event-ID`를 읽지 않으면 재연결은 "성공"하고, 스트림은 끊긴 시점이 아니라 **지금**부터 다시 흐른다. 그 사이 구간은 아무 데도 기록되지 않는다. 에러 로그도 없다. 정상 동작이기 때문이다.

### 끊김에도 종류가 있다

- **스트림 도중 연결이 죽음**(프록시 타임아웃, 네트워크 단절, 서버 재시작) → 재연결한다.
- **응답이 200이 아니거나 `Content-Type`이 `text/event-stream`이 아님** → 실패 처리하고 **재연결하지 않는다.** 인증 만료로 401이 떨어지면 스트림은 그 자리에서 영구히 죽는다.
- **204 No Content**는 "이제 그만 붙어라"를 말하는 관용적 방법이다. 정상 종료를 표현할 수단이 이것뿐이다.

`onerror`는 "복구 불가"와 "잠깐 끊김"을 구분해주지 않는다. 상태는 `readyState`로 봐야 한다 — `CONNECTING(0)`이면 재시도 중, `CLOSED(2)`면 영구히 죽은 것이다.

### 스트림 위에 앉아 있는 것들

SSE는 HTTP/1.1에서 `Content-Length` 없는 chunked 응답이다. 그래서 중간 장비들이 "아직 안 끝난 응답"으로 취급하고 자기 방식대로 손댄다.

nginx는 기본이 `proxy_buffering on`이라 업스트림 응답을 버퍼에 모은다. 버퍼가 찰 때까지 클라이언트로 나가지 않는다 — 증상은 "느리다"가 아니라 **"몰아서 온다"**다. `proxy_buffering off;`를 켜거나 응답에 `X-Accel-Buffering: no`를 실어야 한다. gzip도 이벤트 단위 flush가 없으면 같은 이유로 스트림을 뭉갠다.

그리고 **idle 타임아웃**. nginx `proxy_read_timeout` 기본값은 60초, AWS ALB의 기본 idle timeout도 60초다. 이벤트가 뜸한 시간대에는 이 값이 그대로 연결 수명이 된다. 주석 줄 하트비트가 장식이 아니라 필수인 이유다.

마지막으로 연결 하나가 커넥션 하나다. HTTP/1.1에서 브라우저는 오리진당 동시 연결을 보통 6개로 제한하고, SSE는 그중 하나를 **영구히** 점유한다. 탭을 6개 띄우면 여섯 번째부터는 SSE가 아니라 그 페이지의 API 호출까지 같이 멈춘다. 종단 간 HTTP/2면 스트림 다중화라 이 제약은 사실상 사라진다([[http2-vs-http3]]).

## 현장 시나리오

배달 주문 관리 웹 대시보드. 사장님 화면에 새 주문을 SSE로 밀어넣는다. 동시 접속 8,000. 구성은 CloudFront → ALB → nginx → Spring Boot고, `id`에 주문 시퀀스를 박아 보내고 있었다. 서버는 `Last-Event-ID` 헤더를 읽지 않았다. 필요를 못 느꼈다 — 개발 환경에선 연결이 끊긴 적이 없으니까.

새벽 2시 40분. 주문이 뜸해 62초 동안 이벤트가 없었다. ALB 기본 idle timeout 60초가 걸려 연결이 끊겼고, EventSource는 조용히 재연결했다. 그 3초 사이에 주문 2건이 들어왔다. 서버는 새 연결에 "지금부터"의 스트림을 열어줬고, 주문 2건은 어느 화면에도 뜨지 않았다.

콘솔에 에러는 없다. `readyState`는 `OPEN`이다. UI의 연결 표시등은 초록색이다. 사장님은 주문이 안 들어온 줄 알았고 손님은 40분 뒤에 전화를 걸었다. 서버 로그에는 이벤트를 정상 발행했다는 기록만 남아 있었다.

같은 결함이 낮에는 다른 얼굴로 나왔다. 12시 10분 롤링 배포로 8,000개 연결이 한꺼번에 끊겼다. 서버가 `retry:`를 보낸 적이 없어 간격은 브라우저 기본값이었고, 지터가 없으니 8,000개가 거의 같은 시각에 다시 붙었다. 그제야 급히 붙여둔 "놓친 이벤트 조회" 쿼리가 8,000번 동시에 돌아 DB 커넥션 풀 200개가 즉시 고갈됐다. 재연결이 500으로 실패하자 `EventSource`는 명세대로 또 붙었다 — [[retry-exponential-backoff-jitter]]의 그 패턴이 그대로 재현됐다.

원인 한 줄: **재연결은 브라우저가 해주고, 복구는 아무도 안 해준다.**

## 실무 적용 포인트

1. **`Last-Event-ID`를 읽지 않을 거면 `id:`도 보내지 마라.** 보내는 순간 클라이언트는 "재개 가능한 스트림"으로 믿는다. 읽을 거라면 핸들러 첫 줄이 이것이어야 한다 — 헤더의 id를 커서로 삼아 그 이후 이벤트를 리플레이한 뒤에 실시간 스트림으로 넘어간다. 최초 접속(헤더 없음)과 재접속을 다른 코드 경로로 분리해라.

2. **리플레이 버퍼의 보존 한도를 명시적으로 정해라.** Redis Streams나 이벤트 테이블에 최근 N분(예: 10분) 또는 최근 N건만 남기고, 커서가 그 범위를 벗어나면 리플레이 대신 `event: resync`를 한 번 보내 클라이언트가 전체를 다시 조회하게 만든다. 무한 리플레이는 배포 직후 자해다.

3. **하트비트 주기를 가장 짧은 idle 타임아웃의 절반 이하로.** ALB 기본 60초, nginx `proxy_read_timeout` 기본 60초라면 15~20초마다 주석 줄(`: ping\n\n`)을 흘린다. 경로상 프록시가 여럿이면 가장 작은 값이 기준이다. ALB idle timeout을 120초로 올리고 하트비트를 30초로 두면 트래픽이 절반이 된다.

4. **`retry:`를 서버가 직접 정하고 지터를 값으로 섞어라.** 명세는 간격을 늘려주는 걸 브라우저 재량으로만 허용하므로 기대면 안 된다. 연결 수립 시 `retry: {3000~9000 사이 난수}`를 첫 줄로 내려보내면 재연결 시점이 6초 폭으로 흩어진다. 배포는 롤링으로 나눠 끊는다.

5. **프록시 버퍼링을 명시적으로 끄고 스테이징에서 검증해라.** nginx `location`에 `proxy_buffering off;`, `proxy_read_timeout 3600s;`를 두거나 응답에 `X-Accel-Buffering: no`를 실어라. 개발 환경엔 프록시가 없어 절대 재현되지 않는다 — "이벤트 5개를 1초 간격으로 보내고 도착 간격이 1초인가"를 재는 스모크 테스트를 넣는 게 확실하다.

6. **인증 만료를 스트림 죽음으로 두지 마라.** 401은 재연결 없는 영구 종료다. `EventSource`는 커스텀 헤더를 못 붙여 대개 쿠키(`withCredentials: true`)를 쓰는데, `readyState === 2`를 감지해 토큰 재발급 후 새 `EventSource`를 만드는 경로를 따로 둬야 한다. 헤더 인증이 꼭 필요하면 `fetch` + `ReadableStream` 구현으로 갈아타라.

또 하나. 8,000 연결은 블로킹 서블릿 모델에서 스레드 8,000개다. Spring이면 `SseEmitter`나 WebFlux로 가고, 노드당 연결 상한을 먼저 정한 뒤 그 값으로 오토스케일 임계를 잡아라.

## 더 깊은 토끼굴

- [[websocket-vs-sse-vs-polling]] — 애초에 SSE를 고를 자리였는지
- [[retry-exponential-backoff-jitter]] — 8,000개가 동시에 돌아오는 걸 막는 쪽
- [[http2-vs-http3]] — 오리진당 6 연결 제약이 사라지는 지점
- [[reverse-proxy-l4-l7]] — 스트림 위에 앉아 버퍼링하는 그 계층
- [[idempotency-key]] — 리플레이가 중복 처리로 번지지 않게 하는 장치

**출처**
- WHATWG HTML Living Standard, "Server-sent events" — https://html.spec.whatwg.org/multipage/server-sent-events.html (필드 4종, 빈 줄 디스패치, 콜론 뒤 공백 1개 제거, `data` 없으면 미발행, `Last-Event-ID` 헤더, 재연결 시간은 구현 정의, 200/`text/event-stream` 아니면 재연결 없이 실패)
- MDN, "Using server-sent events" — https://developer.mozilla.org/en-US/docs/Web/API/Server-sent_events/Using_server-sent_events (HTTP/1.1 오리진당 연결 제한과 HTTP/2에서의 차이, `readyState`, 커스텀 헤더 불가)
- nginx 공식 문서, `ngx_http_proxy_module` — https://nginx.org/en/docs/http/ngx_http_proxy_module.html (`proxy_buffering` 기본 on, `proxy_read_timeout` 기본 60초)
- AWS 공식 문서, "Application Load Balancer connections" — https://docs.aws.amazon.com/elasticloadbalancing/latest/application/application-load-balancer-connections.html (기본 idle timeout 60초)
