---
title: L4 로드밸런서를 L7처럼 쓰려다, 헬스체크는 통과하는데 절반의 요청이 죽었다
date: 2026-07-07
day: 34
category: network
tags: [network, load-balancer, reverse-proxy, l4, l7]
related: ["[[grpc-vs-rest]]", "[[http2-vs-http3]]", "[[cdn-cache-key]]", "[[liveness-readiness-startup]]", "[[websocket-vs-sse-vs-polling]]"]
difficulty: 2
short_text: |
  ⚠️ [Day 34] L4 LB는 요청 아닌 연결을 나눈다

  오해: LB가 요청을 고루 분배
  실제: L4는 커넥션 단위→gRPC 한 서버 몰빵

  "초록불인데 절반이 죽었다"

  📖 https://github.com/kimyuchan-k1/IT-deep-dive-vault/blob/main/posts/2026-07-07-reverse-proxy-l4-l7.md
---

# L4 로드밸런서를 L7처럼 쓰려다, 헬스체크는 통과하는데 절반의 요청이 죽었다

## 흔한 오해

"로드밸런서는 들어오는 요청을 뒤에 있는 서버들한테 골고루 뿌려주는 거잖아. round-robin이면 요청이 1번, 2번, 3번 서버로 순서대로 가고."

대부분 그렇게 그림을 그린다. 요청(request) 하나하나가 공평하게 분배되는 파이프라인. 그래서 "LB를 L4로 쓰든 L7로 쓰든 결국 트래픽 나눠주는 건 똑같은데 뭐가 다르냐"고 묻는다. 클라우드 콘솔에서 NLB랑 ALB 중 아무거나 싸고 빠른 걸 고른다.

**절반만 맞다.** L7 로드밸런서는 정말 요청 단위로 나눈다. 하지만 L4는 요청이 뭔지 아예 모른다. L4가 나누는 건 요청이 아니라 **TCP 커넥션**이다. 이 차이가 HTTP/1.1에서는 안 보이다가, HTTP/2·gRPC·WebSocket을 쓰는 순간 정확히 반대 방향으로 터진다.

## 실제 원리

### L4는 봉투만 보고, L7은 편지를 뜯는다

OSI 계층으로 이름이 붙었다. L4는 전송 계층 — TCP/UDP의 4-tuple(출발지 IP:포트, 목적지 IP:포트)까지만 본다. 패킷 안에 뭐가 들었는지는 안 본다. 봉투 겉면의 주소만 보고 창구를 배정하는 것이다.

L7은 애플리케이션 계층 — 실제 HTTP 요청을 파싱한다. 메서드, 경로(`/api/v1/orders`), Host 헤더, 쿠키, 심지어 body까지 읽는다. 편지를 뜯어서 내용을 보고 어디로 보낼지 결정한다.

여기서 갈리는 핵심: **L4는 커넥션을 열 때 딱 한 번 뒷단 서버를 고른다.** 그 커넥션이 살아있는 동안 오가는 모든 바이트는 무조건 처음 고른 서버로 간다. 반면 L7은 하나의 커넥션 위에서도 요청마다 다른 서버로 라우팅할 수 있다.

### HTTP/1.1에서는 왜 문제가 안 보였나

HTTP/1.1은 커넥션 하나에 요청 하나가 원칙이었다(keep-alive로 재사용은 하지만 요청은 직렬). 클라이언트가 많으면 커넥션도 많다. 커넥션이 많으니 L4가 커넥션을 골고루 나누기만 해도 요청이 골고루 퍼진 것처럼 보인다. **커넥션 수 ≈ 요청 수**였기 때문이다.

### HTTP/2가 이 가정을 깬다

HTTP/2는 정반대다. 커넥션 하나에 요청 수백 개를 멀티플렉싱한다. gRPC 클라이언트는 보통 목적지당 **롱리브드 커넥션 1개**를 열어놓고 그 위로 모든 RPC를 흘려보낸다. 이제 커넥션 수는 1인데 요청은 수천이다.

L4 LB 입장에서 보이는 건 "커넥션 1개"뿐이다. 그 커넥션을 서버 A로 보내면, 그 클라이언트의 모든 요청이 영원히 A로만 간다. 서버가 10대 있어도 한 대만 일한다. `roundrobin`을 걸어놨어도 나눌 커넥션 자체가 하나뿐이라 아무 효과가 없다. 이게 [[grpc-vs-rest]]를 L4 뒤에 두면 안 되는 이유다.

### 헬스체크의 함정

더 교묘한 건 헬스체크다. L4 헬스체크는 보통 "TCP 3-way handshake가 되나" 또는 "포트가 열려있나"만 본다. 애플리케이션이 500을 뿜든 데드락에 빠졌든, TCP 소켓만 살아있으면 초록불이다. 프로세스는 떠 있는데 요청 처리는 못 하는 좀비 상태를 L4는 건강하다고 판단한다. L7 헬스체크는 `GET /healthz`를 실제로 던져서 200을 확인한다 — 이건 [[liveness-readiness-startup]] 프로브와 같은 결의 이야기다.

## 현장 시나리오

주문 API를 gRPC로 재작성한 팀이 있었다. 트래픽을 감당하려고 뒷단 파드를 6대로 늘리고, 앞에 클라우드 L4 로드밸런서(NLB)를 뒀다. 배포 직후엔 멀쩡했다.

문제는 오토스케일이 돌면서 시작됐다. 부하가 올라 파드를 6 → 12대로 늘렸는데, **새로 뜬 6대에는 트래픽이 거의 안 갔다.** 기존 gRPC 클라이언트들이 이미 6개의 롱리브드 커넥션을 오래된 파드 6대에 꽂아둔 상태였고, L4는 커넥션 단위라 그 커넥션을 재분배하지 않는다. 새 파드는 CPU 5%, 기존 파드는 CPU 95%.

기존 파드 중 한 대가 과부하로 응답 지연에 빠졌다. 그런데 TCP 소켓은 살아있으니 NLB 헬스체크는 초록불. LB는 계속 그 좀비 파드로 요청을 밀어넣었다. 그 커넥션에 물린 클라이언트들의 요청 — 전체의 약 절반 — 이 타임아웃으로 죽었다. 대시보드엔 "타깃 6/6 healthy"만 떠 있었다.

인과 사슬: gRPC 롱리브드 커넥션 → L4가 커넥션 단위 분배 → 스케일아웃해도 재분배 안 됨 → 소수 파드 과부하 → TCP는 살아 헬스체크 통과 → 좀비로 요청 계속 유입 → 절반 타임아웃. 수정은 한 줄이었다. **NLB를 걷어내고 L7(ALB) 또는 클라이언트 사이드 LB로 바꾸니** 요청 단위로 12대에 고르게 퍼졌다.

## 실무 적용 포인트

1. **HTTP/2·gRPC·WebSocket은 L7 LB 뒤에 둔다.** AWS면 NLB(L4)가 아니라 ALB(L7), 또는 Envoy/nginx. L4에 gRPC를 물리면 커넥션당 한 서버 몰빵이 기본이다.
2. **꼭 L4를 써야 하면 클라이언트 사이드 LB를 병행한다.** gRPC의 경우 `round_robin` 로드밸런싱 정책 + DNS/xDS로 서버 목록을 받아 클라이언트가 여러 커넥션을 직접 연다. 커넥션이 여러 개여야 L4도 나눌 게 생긴다.
3. **헬스체크는 반드시 L7(HTTP)로.** `GET /healthz`가 200을 반환하는지 확인. TCP 체크만 하면 좀비 프로세스를 못 걸러낸다. 임계값은 `unhealthy_threshold=2`, `interval=5s` 정도가 흔한 출발점.
4. **롱리브드 커넥션에 최대 수명을 건다.** Envoy `max_connection_duration`이나 nginx `keepalive_time`으로 커넥션을 주기적으로 끊어 재분배를 강제한다. 안 그러면 스케일아웃 효과가 기존 커넥션에 막힌다.
5. **L4 vs L7 선택 기준은 "요청 내용으로 라우팅이 필요한가"다.** 경로·호스트 기반 라우팅, TLS termination, 요청 단위 분배가 필요하면 L7. 순수 TCP 처리량과 최저 지연(초당 수백만 커넥션, 게임·DB 프록시)만 필요하면 L4.
6. **L7은 공짜가 아니다.** 요청을 파싱하고 재조립하니 지연이 붙고(보통 수백 µs~수 ms), TLS를 두 번 처리한다. 그래서 [[cdn-cache-key]] 계층처럼 L7과 L4를 계층으로 겹쳐 쓴다 — 바깥은 L4로 받아 흘리고, 안쪽에서 L7으로 라우팅.

## 더 깊은 토끼굴

- [[grpc-vs-rest]] — gRPC의 롱리브드 커넥션이 왜 L4와 상극인지
- [[http2-vs-http3]] — 멀티플렉싱이 커넥션 개수 가정을 어떻게 바꾸나
- [[liveness-readiness-startup]] — TCP 체크 vs HTTP 체크, 좀비를 거르는 프로브 설계
- [[cdn-cache-key]] — L7 프록시 계층에서의 라우팅 키

**1차 출처:**
- Cloudflare — "What is Layer 4 / Layer 7 load balancing?": https://www.cloudflare.com/learning/performance/types-of-load-balancing/
- gRPC 공식 블로그 — "gRPC Load Balancing": https://grpc.io/blog/grpc-load-balancing/
- Kubernetes 공식 문서 — "gRPC load balancing on Kubernetes without Tears": https://kubernetes.io/blog/2018/11/07/grpc-load-balancing-on-kubernetes-without-tears/
