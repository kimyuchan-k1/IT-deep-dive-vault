---
title: 요청마다 정확히 40ms씩 늦었다. 범인은 write() 두 번과 서로를 기다린 두 알고리즘이었다
date: 2026-10-05
day: 104
category: network
tags: [tcp, nagle, delayed-ack, tcp-nodelay, latency]
related: ["[[tcp-slow-start]]", "[[connection-pool-sizing]]", "[[redis-pipelining-vs-tx]]", "[[grpc-vs-rest]]", "[[keepalive-timeout-mismatch]]"]
difficulty: 4
short_text: |
  🔥 [Day 104] 요청마다 정확히 40ms 늦었다

  오해: 작은 패킷은 바로 나간다
  실제: write 2번→Nagle 대기→상대 ACK 지연→40ms 정지

  📖 https://github.com/kimyuchan-k1/IT-deep-dive-vault/blob/main/posts/2026-10-05-nagle-delayed-ack.md
---

# 요청마다 정확히 40ms씩 늦었다. 범인은 write() 두 번과 서로를 기다린 두 알고리즘이었다

## 흔한 오해

> "`write()`를 호출하면 데이터는 바로 네트워크로 나간다. 작은 메시지일수록 더 빨리 간다. 지연이 생기면 네트워크나 서버가 느린 거다."

소켓 API는 `write()`가 리턴하면 끝난 것처럼 보이게 만든다. 그래서 지연을 보면 먼저 RTT, 서버 처리 시간, GC를 의심한다.

실제로 커널은 작은 데이터를 **일부러 붙잡고 있을 수 있다**. 상대편 커널도 ACK를 **일부러 늦게 보낼 수 있다**. 둘 다 1980년대에 대역폭을 아끼려고 만든 최적화다. 각각은 무해하다. 둘이 같은 연결에서 만나면 서로를 기다리며 타이머가 터질 때까지 멈춘다. 그 타이머가 Linux에서 보통 40ms다.

## 실제 원리

### 1. Nagle: "ACK 안 온 데이터가 있으면 작은 건 모아라"

RFC 896(1984)의 규칙은 한 줄이다. **아직 ACK를 못 받은 데이터가 전송 중이면, 새로 들어온 작은 데이터는 보내지 말고 버퍼에 쌓는다.** ACK가 오거나 MSS만큼 차면 그때 보낸다.

텔넷처럼 키 하나에 1바이트를 보내던 시절, 40바이트 헤더에 1바이트 페이로드 패킷이 망을 채우는 걸 막으려는 장치였다. 핵심은 Nagle이 **시간이 아니라 ACK에 묶여 있다**는 점이다. ACK가 늦으면 전송도 늦다.

### 2. Delayed ACK: "응답에 얹어 보낼 테니 잠깐 기다려라"

RFC 1122 §4.2.3.2는 수신 측이 ACK를 지연시키는 것을 허용한다. 곧 보낼 응답 데이터에 ACK를 얹으면(piggyback) 패킷 하나를 아낀다. 조건은 두 개다. 지연은 **0.5초를 넘으면 안 되고**, 풀사이즈 세그먼트 **두 개마다는 반드시** ACK를 보내야 한다.

Linux 구현은 지연 타이머(ATO)를 최소 40ms, 최대 200ms 범위에서 동적으로 잡는다. 대화형 트래픽에서는 대부분 하한인 40ms에 붙는다. `ss -ti`로 보면 연결마다 `ato:40`이 찍혀 있다.

### 3. 둘이 만나면: write-write-read 교착

문제 패턴은 **작은 write 두 번 → 그다음 read**다.

```
클라이언트(Nagle ON)                       서버(Delayed ACK)
write(header 16B) ──────────────────────▶  수신. 아직 응답 못 만듦(바디 필요)
write(body 300B)   → 버퍼에 보류              ACK를 응답에 얹으려고 대기
   (header가 아직 un-ACKed라서)              ...
          ... 40ms 동안 양쪽 모두 정지 ...
                                    ◀────  delayed ACK 타이머 만료 → ACK
body 전송 ───────────────────────────────▶  이제 요청 완성 → 처리 → 응답
```

클라이언트는 "ACK 오면 보낼게"라고 기다린다. 서버는 "응답 보낼 때 ACK 얹을게"라고 기다린다. 서버가 응답을 만들려면 바디가 필요하다. 바디는 ACK가 와야 나간다. 데드락은 아니다. 타이머가 결국 풀어 준다. 그래서 장애가 아니라 **모든 요청에 정확히 같은 상수가 더해지는** 형태로 나타난다.

### 4. 왜 테스트에선 안 보였나

Linux 수신 측은 연결 초반과 특정 상황에서 **quickack 모드**로 즉시 ACK를 보낸다. 새 연결의 처음 몇 요청은 멀쩡하고, 커넥션 풀이 연결을 재사용하는 운영 환경에서만 40ms가 붙는다. 그리고 루프백(`localhost`) 벤치마크는 RTT가 수 μs라 다른 효과에 묻힌다.

## 현장 시나리오

결제 승인 게이트웨이. Java로 짠 자체 바이너리 프로토콜 클라이언트가 카드사 중계 서버와 연결 32개짜리 풀을 유지한다. 프레임 인코더가 이렇게 생겼다.

```java
out.write(header);   // 16바이트: 길이, 타입, 요청 ID
out.write(body);     // 200~400바이트
out.flush();         // BufferedOutputStream이 아니라 SocketOutputStream 직결
```

1. 리팩터링에서 `BufferedOutputStream` 래퍼가 빠짐 → `write()` 두 번이 각각 시스템 콜이 됨
2. Java `Socket`은 `TCP_NODELAY` 기본값이 false → Nagle ON
3. header 전송 후 body는 header의 ACK를 기다리며 보류
4. 중계 서버(Linux)는 바디 없이는 응답 불가 → delayed ACK 40ms 후 ACK
5. 승인 API p50이 6ms → 46ms. 연결 하나가 초당 처리할 수 있는 요청이 약 20건으로 떨어짐
6. 풀 32개 × 20 = 초당 640건이 상한 → 저녁 피크(초당 900건)에서 풀 대기열이 쌓이고 타임아웃 연쇄([[connection-pool-sizing]])

서버 CPU도, 카드사 응답 시간도 정상이었다. `tcpdump`에서 header 패킷과 body 패킷 사이 간격이 **매번 40.0ms 근처**로 찍힌 걸 보고서야 원인이 잡혔다. 지연 분포가 넓게 퍼지지 않고 한 점에 뭉쳐 있으면 타이머를 의심해야 한다.

## 실무 적용 포인트

1. **요청-응답형 프로토콜은 `TCP_NODELAY`를 켠다.** Java `socket.setTcpNoDelay(true)`, C `setsockopt(fd, IPPROTO_TCP, TCP_NODELAY, &one, sizeof(one))`. Go의 `net.TCPConn`은 기본값이 이미 true다. Redis·nginx(`tcp_nodelay on`, keepalive 연결 기본 on)도 켜고 쓴다.

2. **NODELAY보다 먼저 write를 합친다.** NODELAY만 켜면 header 16B, body 300B가 패킷 두 개로 나간다. 버퍼에 모아 한 번에 쓰거나 `writev()`로 보내면 패킷 하나, 시스템 콜 하나로 끝난다. [[redis-pipelining-vs-tx]]의 파이프라이닝이 빠른 이유와 같다.

3. **대량 전송은 반대로 `TCP_CORK`(Linux)를 쓴다.** cork를 걸면 커널이 MSS 단위로 꽉 채워 보내고, 해제 시점에 남은 걸 내보낸다. 파일 헤더 + `sendfile()` 조합이 대표 사례다. cork는 최대 200ms 후 자동 플러시된다.

4. **수신 측만 고칠 수 있다면 `TCP_QUICKACK`.** 다만 이 플래그는 영구적이지 않다. 커널이 다시 delayed ACK 모드로 돌아가므로 매 `recv()` 후에 다시 설정해야 한다. 라우트 단위로는 `ip route change <dst> ... quickack 1`로 고정할 수 있다.

5. **진단은 세 단계로.** `ss -ti dst <ip>`로 `ato` 값 확인 → `tcpdump -i any -nn host <ip> -ttt`로 패킷 간 간격 확인 → 간격이 40ms/200ms 근처에 뭉치면 Nagle×delayed ACK 확정. Windows 상대라면 기본 delayed ACK가 200ms라 간격이 200ms로 찍힌다.

6. **지연 히스토그램에서 "한 점에 뭉친 상수"를 경보로 본다.** p50과 p99가 둘 다 같은 값(+40ms)만큼 올랐다면 부하 문제가 아니라 타이머 문제다. 부하 문제는 꼬리가 늘어나지 중앙값이 통째로 평행 이동하지 않는다.

## 더 깊은 토끼굴

- [[tcp-slow-start]] — 같은 연결에서 cwnd가 작을 때 작은 세그먼트가 어떻게 전송을 제약하는지.
- [[connection-pool-sizing]] — 요청당 고정 지연이 풀 처리량 상한을 어떻게 끌어내리는지.
- [[redis-pipelining-vs-tx]] — write를 합쳐 왕복을 줄이는 같은 원리의 애플리케이션 계층 버전.
- [[grpc-vs-rest]] — HTTP/2 프레이밍이 작은 프레임을 어떻게 다루는지.
- [[keepalive-timeout-mismatch]] — 재사용 연결에서만 나타나는 또 다른 문제.

**1차 출처**
- RFC 896 — Congestion Control in IP/TCP Internetworks (Nagle 알고리즘): https://www.rfc-editor.org/rfc/rfc896
- RFC 1122 §4.2.3.2 — When to Send an ACK Segment (0.5초 상한, 2세그먼트 규칙): https://www.rfc-editor.org/rfc/rfc1122
- Linux man-pages tcp(7) — `TCP_NODELAY`, `TCP_CORK`(200ms), `TCP_QUICKACK`(비영구): https://man7.org/linux/man-pages/man7/tcp.7.html
- Go net 패키지 — `TCPConn.SetNoDelay` (기본값 true): https://pkg.go.dev/net#TCPConn.SetNoDelay
