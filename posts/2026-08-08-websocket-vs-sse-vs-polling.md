---
title: SSE로 갈아탔더니 탭 7개째부터 실시간이 멈췄다
date: 2026-08-08
day: 62
category: network
tags: [sse, websocket, long-polling, http2, eventsource, proxy]
related: ["[[http2-vs-http3]]", "[[sse-prod-ops]]", "[[retry-exponential-backoff-jitter]]", "[[reverse-proxy-l4-l7]]", "[[backpressure-patterns]]"]
difficulty: 3
short_text: |
  🔥 [Day 62] 탭 7개째 실시간이 멈췄다

  오해: SSE는 커넥션이 싸다
  실제: HTTP/1.1은 도메인당 6개

  "7번째 탭은 계속 pending"

  📖 https://github.com/kimyuchan-k1/IT-deep-dive-vault/blob/main/posts/2026-08-08-websocket-vs-sse-vs-polling.md
---

# SSE로 갈아탔더니 탭 7개째부터 실시간이 멈췄다

WebSocket을 걷어내고 SSE로 바꾼 결정 자체는 옳았다. 단방향 푸시였고, 코드는 절반으로 줄었고, 재연결은 브라우저가 알아서 해줬다. 장애는 그 다음 주에 왔다. 관제사가 탭을 여섯 개 넘게 열면 일곱 번째 화면이 영원히 빈 채로 남았다. 서버 에러율은 0%였다.

## 흔한 오해

> "WebSocket은 무겁고 SSE는 그냥 HTTP잖아. 커넥션 하나 더 여는 게 뭐가 문제야?"

비교표들이 이 인상을 만든다. WebSocket은 핸드셰이크·프레이밍·하트비트를 직접 다뤄야 하고, SSE는 `text/event-stream` 하나 뱉으면 끝난다. 그래서 "SSE = 가벼운 HTTP 요청"으로 읽힌다.

앞부분은 맞다. SSE는 진짜 HTTP GET이고 프로토콜 스위치도 없다. 틀린 건 **"HTTP 요청이니까 다른 HTTP 요청처럼 끝난다"**는 부분이다. SSE는 끝나지 않는 응답이다. 끝나지 않는 응답은 요청이 아니라 **점유된 커넥션**이고, 브라우저·프록시·로드밸런서는 전부 커넥션 개수와 유휴 시간에 한도를 걸어둔 채로 서 있다.

## 실제 원리

### 세 방식의 진짜 차이는 "커넥션을 몇 개, 얼마나 오래 잡느냐"다

- **Long Polling** — 요청이 이벤트가 생길 때까지 대기하다 응답하고 닫힌다. 사이클마다 요청 헤더 전체(쿠키 포함이면 수 KB)를 다시 보내고, 대기 중에도 커넥션은 점유 상태다.
- **SSE** — 응답이 닫히지 않고 `data: ...\n\n` 청크가 계속 흘러간다. 커넥션 1개를 서비스 수명 내내 잡는다. 이벤트당 오버헤드는 `data: ` 6바이트 수준.
- **WebSocket** — `Upgrade: websocket`으로 HTTP를 벗어난다. 이후 프레임당 헤더 2~14바이트. 양방향이고, 클라이언트→서버 프레임은 RFC 6455가 **마스킹을 의무화**한다(32비트 키 XOR). 성능이 아니라 중간 프록시가 프레임을 HTTP 요청으로 오독해 캐시를 오염시키는 걸 막으려는 장치다.

여기가 핵심이다. 셋 다 "커넥션을 오래 잡는다"는 성질을 공유하는데, 그 성질에 한도를 거는 주체가 애플리케이션 바깥에 세 겹 있다.

### 첫 번째 한도: 브라우저의 오리진당 커넥션 수

HTTP/1.1에서 브라우저는 **한 오리진당 동시 커넥션을 6개로 제한**한다. 이건 SSE만의 예산이 아니라 그 오리진으로 나가는 모든 요청이 나눠 쓰는 예산이다. 탭 6개가 각각 SSE를 열면 예산은 소진되고, 일곱 번째 SSE는 물론 **같은 오리진의 평범한 API 호출까지 큐에 걸린다**. 요청이 나가질 않으니 서버 로그에도 에러율에도 아무것도 안 남는다.

HTTP/2로 올라가면 이 한도가 사라진다. TCP 커넥션 하나 위에 스트림을 다중화하고, 동시 스트림 상한은 서버가 광고하는 `SETTINGS_MAX_CONCURRENT_STREAMS`가 정한다(흔한 기본값 100). SSE의 확장성은 SSE 자체가 아니라 **오리진이 h2로 서빙되느냐**에 달려 있다. 대신 TCP 위 다중화이므로 패킷 손실 시 head-of-line 블로킹은 남는다 — [[http2-vs-http3]]에서 QUIC이 걷어낸 그 문제다.

WebSocket은 이 계산에서 빠진다. `Upgrade` 핸드셰이크가 HTTP/1.1 전용이라 6개 한도를 애초에 적용받지 않는다. HTTP/2 위에서 쓰려면 RFC 8441의 확장 CONNECT(`:protocol` 의사 헤더)가, HTTP/3용으로는 RFC 9220이 필요하다. 다만 프록시·서버 지원이 고르지 않다 — 클라이언트 쪽 HTTP/2를 종료하고 업스트림엔 HTTP/1.1로 붙는 흔한 구조에서는 이 확장의 이득이 그대로 오지 않는다.

### 두 번째 한도: 중간 장비의 유휴 타임아웃

SSE 커넥션은 이벤트가 없으면 조용하다. 조용한 커넥션은 중간 장비 눈에 **죽은 커넥션**으로 보인다. nginx `proxy_read_timeout` 기본값은 60초, AWS ALB 유휴 타임아웃 기본값도 60초다. 애플리케이션이 아무 잘못을 안 해도 60초마다 스트림이 끊긴다.

그래서 SSE 명세에는 주석 라인이 있다. 콜론으로 시작하는 줄(`: keepalive`)은 이벤트로 파싱되지 않고 버려지지만, **바이트는 흐른다**. 하트비트는 선택이 아니라 타임아웃 상수에 종속된 필수 설정이다.

### 세 번째 한도: 프록시 버퍼링

nginx는 기본적으로 업스트림 응답을 버퍼에 모았다가 내려보낸다(`proxy_buffering on`). SSE에는 치명적이다. 이벤트가 버퍼가 찰 때까지 고여 있다가 한꺼번에 쏟아진다. 증상이 "느리다"가 아니라 **"묶여서 온다"**라서 네트워크 문제로 오진하기 쉽다. 끄는 길은 둘 — 서버가 `X-Accel-Buffering: no` 헤더를 보내거나, 프록시에서 `proxy_buffering off`를 걸거나. 응답 압축이 켜져 있으면 압축 버퍼에서 한 번 더 고인다. [[reverse-proxy-l4-l7]]에서 L7 프록시가 하는 일이 여기서는 전부 방해가 된다.

### 재연결은 공짜가 아니다

EventSource의 자동 재연결은 SSE의 최대 장점인데, 그게 곧 최대 위험이다. 재연결 대기 시간은 명세상 구현 정의값이고 서버가 `retry:` 필드로 덮어쓸 수 있다.

문제는 두 가지다. 첫째, 재연결 사이에 발생한 이벤트는 그냥 사라진다 — 서버가 `id:` 필드를 붙이고 재연결 요청의 `Last-Event-ID` 헤더를 읽어 그 지점부터 다시 보내야 메워진다. 둘째, 끊는 주체가 공통 인프라면 **모두가 동시에 끊기고 동시에 재연결한다**. 지터가 없으면 이건 재연결이 아니라 [[retry-exponential-backoff-jitter]]가 막으려는 그 동기화된 폭풍이다.

## 현장 시나리오

물류 배차 관제 SaaS. 관제사 400명이 브라우저로 실시간 차량 위치를 본다. WebSocket을 걷어내고 SSE로 옮긴 게 3주 전이다. 구성은 ALB → nginx → Node 앱, 오리진은 `dashboard.internal`, HTTP/2는 설정되지 않아 **HTTP/1.1**로 서빙되고 있었다.

첫 번째 증상은 이랬다. 관제사가 권역별 탭 5개를 띄우고 알림 위젯 1개를 켠다 → 같은 오리진 SSE 6개로 브라우저 커넥션 예산이 소진된다 → 일곱 번째 탭의 SSE도, 지도 타일을 받아오는 평범한 XHR도 전부 pending에 걸린다 → 화면은 흰색 그대로다 → 관제사는 "서버가 죽었다"고 신고한다 → 대시보드의 5xx는 0%, p99도 정상이다. **요청이 서버에 도착조차 안 했으니 서버 지표에는 아무 흔적이 없다.**

두 번째 증상은 새벽에 나왔다. 배차가 없는 시간대엔 이벤트가 60초 넘게 안 생긴다 → nginx `proxy_read_timeout` 60초가 스트림을 끊는다 → 하트비트가 없으니 앱은 끊긴 줄도 모른다 → 400명 × 6스트림 = **2,400개 커넥션이 60초 주기로 한꺼번에 재연결**된다 → 재연결마다 초기 스냅샷 쿼리가 나간다 → DB 커넥션 풀이 주기적으로 고갈된다([[connection-pool-sizing]]) → `id:`를 붙이지 않아 재연결 공백 동안의 배차 이벤트는 복구되지 않는다 → 아침에 "차량 상태가 실제와 다르다"는 티켓이 쌓인다.

고친 건 세 줄이었다. 오리진에 HTTP/2를 켜고, 15초마다 `: ping` 주석을 흘리고, 이벤트마다 `id:`를 붙여 `Last-Event-ID`를 처리했다. 프로토콜 선택은 처음부터 옳았다. 틀렸던 건 **끝나지 않는 응답이 지나가는 경로에 몇 개의 한도가 걸려 있는지 세지 않은 것**이다.

## 실무 적용 포인트

1. **SSE를 쓰기로 했으면 오리진이 HTTP/2인지부터 확인해라.** HTTP/1.1이면 탭·위젯 합쳐 오리진당 6개가 상한이고, 초과분은 SSE가 아닌 요청까지 막는다. 서브도메인 샤딩은 h2 전환 전까지의 임시방편으로만 쓴다.

2. **하트비트 주기를 인프라 타임아웃의 절반 이하로 잡아라.** nginx `proxy_read_timeout`과 ALB 유휴 타임아웃이 기본 60초라면 15~25초마다 `: keepalive\n\n`을 보낸다. 주석 라인이라 클라이언트 파서는 무시하고 바이트만 흐른다.

3. **경로 전체에서 버퍼링을 꺼라.** 응답에 `X-Accel-Buffering: no`를 넣고 프록시에 `proxy_buffering off; proxy_http_version 1.1;`을 건다. `curl -N`으로 이벤트가 한 줄씩 즉시 떨어지지 않으면 중간 어딘가가 아직 모으고 있는 것이다.

4. **`id:` 없는 SSE는 "대충 실시간"이다.** 이벤트마다 단조 증가 ID를 붙이고, 서버는 재연결 요청의 `Last-Event-ID` 헤더를 읽어 그 이후부터 재전송해야 한다. 이게 없으면 재연결 = 유실이다.

5. **재연결에 지터를 넣어라.** 공통 인프라가 끊으면 전 클라이언트가 같은 순간 재연결한다. `retry:`로 기본 대기를 지정하되 ±30% 랜덤을 얹고, 재연결 직후 스냅샷 쿼리에는 [[backpressure-patterns]]의 부하 제어를 건다.

6. **WebSocket은 "클라이언트→서버 메시지가 잦을 때"만 골라라.** 단방향 푸시면 SSE가 재연결·프록시 친화성에서 앞선다. 협업 편집·게임처럼 상행이 많으면 WebSocket이다. 단 SSE는 UTF-8 텍스트 전용이라 바이너리는 base64로 약 33% 부풀려 실어야 한다. Long Polling은 폴백으로만 두고 서버 대기를 인프라 타임아웃보다 짧게(25~30초) 자체 제한한다.

## 더 깊은 토끼굴

- [[http2-vs-http3]] — 6커넥션 한도를 없앤 다중화와, 그래도 남는 TCP head-of-line
- [[sse-prod-ops]] — 하트비트·재연결·스냅샷을 실제로 배선하는 법
- [[retry-exponential-backoff-jitter]] — 2,400개가 동시에 재연결하지 않게 만드는 것
- [[reverse-proxy-l4-l7]] — 스트리밍에서는 L7의 친절이 방해가 되는 지점
- [[backpressure-patterns]] — 재연결 폭풍을 서버 쪽에서 흡수하기

**출처**

- WHATWG HTML Standard, *Server-sent events* — https://html.spec.whatwg.org/multipage/server-sent-events.html
- RFC 6455, *The WebSocket Protocol* — https://www.rfc-editor.org/rfc/rfc6455
- RFC 8441, *Bootstrapping WebSockets with HTTP/2* — https://www.rfc-editor.org/rfc/rfc8441
- MDN, *EventSource* — https://developer.mozilla.org/en-US/docs/Web/API/EventSource
- nginx, *ngx_http_proxy_module* (`proxy_buffering`, `proxy_read_timeout`) — https://nginx.org/en/docs/http/ngx_http_proxy_module.html

정리하면, WebSocket·SSE·Long Polling의 선택은 API 모양의 문제가 아니라 **커넥션을 몇 개 잡고 얼마나 오래 조용히 있을 수 있느냐**의 문제다. 그 한도는 애플리케이션 코드가 아니라 브라우저·프록시·로드밸런서의 기본값에 적혀 있다. 실시간이 끊긴 게 아니었다. 브라우저가 일곱 번째 커넥션을 열어주지 않았을 뿐이다.
