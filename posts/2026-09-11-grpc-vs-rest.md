---
title: REST를 gRPC로 바꿨다. 파드 12개 중 3개만 일했다
date: 2026-09-11
day: 93
category: network
tags: [grpc, http2, protobuf, load-balancing, kubernetes]
related: ["[[http2-vs-http3]]", "[[reverse-proxy-l4-l7]]", "[[tcp-slow-start]]", "[[hpa-internals]]", "[[service-mesh-istio]]"]
difficulty: 2
short_text: |
  ⚠️ [Day 93] gRPC로 바꾸자 파드 12개 중 3개만 일했다

  오해: gRPC가 빠른 건 protobuf 덕
  실제: 연결 1개에 요청 다중화→L4는 연결 단위 분배→쏠림

  📖 https://github.com/kimyuchan-k1/IT-deep-dive-vault/blob/main/posts/2026-09-11-grpc-vs-rest.md
---

# REST를 gRPC로 바꿨다. 파드 12개 중 3개만 일했다

## 흔한 오해

> "gRPC가 빠른 건 protobuf 때문이다. JSON은 텍스트라 크고 파싱이 느리고, protobuf는 바이너리라 작고 빠르다. 그러니 내부 API는 gRPC로 바꾸면 그만큼 빨라진다."

틀린 말은 아니다. protobuf는 실제로 작다. 그런데 이게 gRPC 이득의 주인공은 아니다. 진짜 차이는 **전송 계층**에 있다. 그리고 그 차이가 그대로 로드밸런싱을 망가뜨리는 원인이 된다. 속도의 원천과 장애의 원천이 같은 곳에 있다.

## 실제 원리

### 1. protobuf가 작은 건 사실이다. 그런데 몇 µs 싸움이다

`{"id":12345,"name":"kim","active":true}`는 JSON으로 39바이트다. 같은 메시지를 protobuf로 인코딩하면 10바이트다.

```
08 B9 60        field 1 (varint)  id = 12345   ← 태그 1B + 값 2B
12 03 6B 69 6D  field 2 (len)     name = "kim" ← 태그 1B + 길이 1B + 3B
18 01           field 3 (varint)  active = true
```

필드 이름이 사라지고 번호만 남는다. 태그 한 바이트는 `(field_number << 3) | wire_type`이라 필드 번호 1~15는 1바이트에 들어간다. 정수는 7비트씩 끊어 담는 varint라 12345가 2바이트다.

여기가 핵심이다. 사내망 RPC 한 번의 비용은 대개 밀리초 단위다. 네트워크 왕복, DB 조회, 비즈니스 로직. 작은 메시지의 직렬화는 그 안에서 µs 단위다. 39바이트가 10바이트가 돼도 응답 시간에 찍히는 차이는 오차 범위다. 메시지가 수백 KB로 커지고 초당 수만 건일 때 비로소 CPU와 대역폭 차이가 체감된다.

### 2. 진짜 이득: HTTP/1.1은 연결 하나에 요청 하나다

REST는 보통 HTTP/1.1 위에서 돈다. keep-alive로 연결을 재사용해도 **한 연결 위에서 동시에 진행되는 요청은 1개**다. 응답이 끝나야 다음 요청을 보낸다. 파이프라이닝은 스펙에 있지만 응답 순서가 강제되는 문제 때문에 사실상 쓰이지 않는다.

그래서 동시 요청 100개를 보내려면 연결 100개가 필요하다. 연결마다 3-way 핸드셰이크, TLS 핸드셰이크, 그리고 [[tcp-slow-start]]가 `cwnd` 10부터 다시 시작한다. 헤더도 매 요청마다 텍스트로 통째로 다시 보낸다.

gRPC는 HTTP/2 위에서 돈다. RPC 하나가 **스트림** 하나이고, 스트림 수백 개가 TCP 연결 하나 위에서 프레임 단위로 섞여 흐른다. RFC 9113은 동시 스트림 한도(`SETTINGS_MAX_CONCURRENT_STREAMS`)를 100 이상으로 두기를 권한다. 헤더는 HPACK으로 테이블에 등록돼 두 번째 요청부터 인덱스 몇 바이트로 줄어든다.

결과가 이렇다. 연결 하나가 계속 달궈진 상태로 유지되니 핸드셰이크 비용이 0이고, `cwnd`도 크게 자란 채로 남는다. gRPC가 빠른 진짜 이유는 이 **장수하는 단일 연결**이다.

### 3. 그 단일 연결이 L4 로드밸런서를 무력화한다

쿠버네티스 `ClusterIP` Service는 kube-proxy가 iptables(또는 IPVS) 규칙으로 구현한다. 이 계층은 L4다. 패킷 안의 HTTP 요청을 보지 못한다. **새 TCP 연결이 들어올 때 백엔드 파드를 한 번 고르고**, conntrack에 기록된 뒤로는 그 연결의 모든 패킷이 같은 파드로 간다.

HTTP/1.1 REST에서는 이게 문제가 안 된다. 클라이언트가 연결을 수십 개 열고 수시로 닫으니 통계적으로 고르게 퍼진다.

gRPC 클라이언트는 채널 하나에 연결 하나를 열고 몇 시간이고 유지한다. L4 입장에서는 연결이 1개뿐이니 파드도 1개만 고른다. 그 위로 흐르는 초당 수천 개의 RPC가 전부 한 파드에 꽂힌다. **분산 단위가 요청이 아니라 연결이기 때문이다.** 차이는 [[reverse-proxy-l4-l7]]에서 다룬 그 경계 그대로다.

## 현장 시나리오

배달 플랫폼. 주문 서비스(파드 4개)가 재고 서비스(파드 12개)를 초당 약 6,000회 호출한다. REST 시절 재고 서비스 p99는 80ms, 파드별 CPU는 30~40%로 고르게 분포했다.

지연을 줄이려 gRPC로 전환했다. 배포 직후부터 인과가 이렇게 이어졌다.

1. 주문 파드 4개가 각자 gRPC 채널 1개, 즉 TCP 연결 1개를 연다.
2. kube-proxy가 연결 4개를 파드 12개 중 무작위로 배정한다. 둘이 같은 파드에 겹쳐 **3개 파드**에만 연결이 붙었다.
3. 3개 파드가 초당 6,000회를 나눠 받는다. 파드당 2,000회, CPU 90% 이상. 나머지 9개는 3%.
4. HPA는 평균 CPU를 본다. `(90×3 + 3×9) / 12 ≈ 25%`. 목표 60% 한참 아래라 **스케일아웃하지 않는다.**
5. 수동으로 파드를 20개로 늘렸다. 새 파드 8개는 **요청을 한 건도 받지 못했다.** 기존 연결은 여전히 원래 3개 파드에 붙어 있었다.
6. p99는 80ms에서 410ms로 올랐다. 과부하 파드 하나가 OOM으로 재시작하자 그 연결이 끊기며 새 파드로 옮겨 붙었고, 그제서야 부하가 흩어졌다.

파드별 RPS 그래프를 나란히 놓고서야 원인이 보였다. 원인 한 줄: 요청은 6,000개였지만 로드밸런서가 본 건 연결 4개였다. [[hpa-internals]]의 평균 CPU 지표는 이 쏠림을 정확히 가려버렸다.

## 실무 적용 포인트

1. **서버에 `MaxConnectionAge`를 걸어라.** 연결을 주기적으로 끊어 클라이언트가 재연결하면서 파드를 다시 고르게 만든다. 서버가 GOAWAY를 보내 진행 중 RPC는 마무리시킨다. 30초~5분 사이가 흔한 선택이다.

   ```go
   grpc.NewServer(grpc.KeepaliveParams(keepalive.ServerParameters{
       MaxConnectionAge:      60 * time.Second,
       MaxConnectionAgeGrace: 10 * time.Second,
   }))
   ```
   Java는 `NettyServerBuilder.maxConnectionAge(60, TimeUnit.SECONDS)`.

2. **클라이언트 측 로드밸런싱으로 요청 단위 분산을 만든다.** `clusterIP: None`인 헤드리스 Service로 파드 IP 목록을 DNS로 받고, 채널 타깃을 `dns:///inventory.default.svc.cluster.local:50051`로, 서비스 설정을 `{"loadBalancingConfig":[{"round_robin":{}}]}`로 둔다. gRPC의 기본 정책 `pick_first`는 이름 그대로 첫 주소 하나에만 붙는다.

3. **헤드리스 + round_robin만으로는 새 파드를 못 본다.** DNS 재조회는 연결 실패나 GOAWAY 때 일어난다. 1번의 `MaxConnectionAge`를 같이 걸어야 스케일아웃된 파드가 60초 안에 목록에 들어온다.

4. **L7 프록시를 두는 방법도 있다.** Envoy, Linkerd, Istio 사이드카는 HTTP/2 스트림을 보고 요청 단위로 분산한다. 코드 변경 없이 해결되지만 홉 하나와 사이드카 비용이 추가된다. 트레이드오프는 [[service-mesh-istio]].

5. **쏠림은 파드별 지표로만 보인다.** `sum by (pod) (rate(grpc_server_handled_total[1m]))`를 대시보드에 둬라. 파드 간 최대/최소 비율이 3배를 넘으면 연결 쏠림을 먼저 의심한다. HPA 평균 CPU는 쏠림을 숨긴다.

6. **gRPC로 옮기기 전에 병목부터 재라.** 응답 시간 중 직렬화가 차지하는 몫이 1%라면 protobuf로 얻는 건 1% 이하다. 연결 수, 핸드셰이크 횟수, 동시 요청 수가 병목일 때 gRPC가 이긴다. REST를 HTTP/2로만 올려도 다중화 이득 상당 부분을 가져간다.

## 더 깊은 토끼굴

- [[http2-vs-http3]] — 단일 TCP 연결의 대가인 TCP 레벨 HOL 블로킹, QUIC이 이를 푸는 방식
- [[reverse-proxy-l4-l7]] — 연결 단위 분산과 요청 단위 분산이 갈리는 경계
- [[tcp-slow-start]] — 장수 연결이 `cwnd`를 유지해서 얻는 이득의 정체
- [[hpa-internals]] — 평균 지표가 쏠림을 가리는 이유
- [[service-mesh-istio]] — L7 프록시로 gRPC 분산을 해결하는 쪽의 비용

**출처**

- Kubernetes Blog, gRPC Load Balancing on Kubernetes without Tears (L4 Service에서 HTTP/2 연결이 한 파드에 고정되는 문제): https://kubernetes.io/blog/2018/11/07/grpc-load-balancing-on-kubernetes-without-tears/
- gRPC Blog, gRPC Load Balancing (프록시 방식 vs 클라이언트 측 분산, `pick_first`/`round_robin`): https://grpc.io/blog/grpc-load-balancing/
- Protocol Buffers, Encoding (varint, 태그 = field_number << 3 | wire_type): https://protobuf.dev/programming-guides/encoding/
- RFC 9113, HTTP/2 (스트림 다중화, `SETTINGS_MAX_CONCURRENT_STREAMS`): https://www.rfc-editor.org/rfc/rfc9113
- gRPC over HTTP/2 프로토콜 명세: https://github.com/grpc/grpc/blob/master/doc/PROTOCOL-HTTP2.md
