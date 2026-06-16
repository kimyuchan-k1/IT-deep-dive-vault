---
title: DNS 레코드를 바꾸고 TTL 60초를 기다렸는데, 트래픽은 한 시간째 옛 서버로 갔다
date: 2026-06-16
day: 14
category: network
tags: [dns, ttl, caching, resolver, jvm, failover]
related: ["[[cdn-cache-key]]", "[[reverse-proxy-l4-l7]]", "[[blue-green-canary-rolling]]", "[[tls13-zero-rtt]]", "[[circuit-breaker]]", "[[http2-vs-http3]]"]
difficulty: 2
short_text: |
  ⚠️ [Day 14] TTL 60초인데 1시간째 옛 서버로 갔다
  오해: TTL 지나면 알아서 새 IP
  실제: OS·resolver·JVM이 TTL 무시
  "JVM이 IP를 영구 캐시했다"
  📖 https://github.com/kimyuchan-k1/IT-deep-dive-vault/blob/main/posts/2026-06-16-dns-cache-ttl.md
---

# DNS 레코드를 바꾸고 TTL 60초를 기다렸는데, 트래픽은 한 시간째 옛 서버로 갔다

## 흔한 오해

"DNS 레코드의 TTL이 60초면, 레코드를 바꾸고 60초만 지나면 전 세계가 새 IP를 보는 거 아닌가? TTL은 '이 캐시를 몇 초 동안 믿어라'는 약속이니까, 그 시간만 지나면 모두가 다시 물어보고 새 답을 받겠지. 그래서 마이그레이션 전날 TTL을 60초로 낮춰두면 전환은 1분이면 끝난다."

DNS 기반 전환(서버 이전, 페일오버, [[blue-green-canary-rolling]])을 처음 설계할 때 거의 모두가 이렇게 계산한다. 그래서 운영 가이드들도 "전환 전 TTL을 낮춰라" 한 줄로 끝낸다.

**TTL은 상한선을 정하는 권고지, 강제 만료 신호가 아니다.** "최대 이만큼은 캐시해도 된다"는 뜻이지 "이 시간이 지나면 반드시 버려라"가 아니다. 그 사이에 OS, resolver, 그리고 특히 **애플리케이션 런타임**이 각자의 규칙으로 TTL을 늘리거나 아예 무시한다. 그래서 TTL 60초짜리 레코드가 한 시간째 옛 IP로 트래픽을 보낸다.

## 실제 원리

DNS 응답 하나가 클라이언트에 닿기까지 **캐시가 최소 세 겹** 쌓인다. TTL은 각 겹에서 다르게 취급된다.

### 1겹 — Recursive Resolver의 클램핑

앱이 도메인을 물으면 먼저 ISP나 회사의 **recursive resolver**(예: `8.8.8.8`)에 묻는다. 이 resolver가 권위 서버의 TTL을 그대로 따르지 않는다. 많은 resolver가 **최소 TTL(min-cache-ttl)** 과 **최대 TTL(max-cache-ttl)** 을 강제한다. 권위 서버가 TTL 60초를 줘도, resolver가 `min-cache-ttl 300`으로 설정돼 있으면 **5분간** 옛 답을 들고 있는다. 60초는 무시된다.

### 2겹 — Negative Caching(없는 답도 캐시된다)

레코드를 막 만들었거나 옛 이름을 지우면, 그 사이 조회가 `NXDOMAIN`을 받는다. 이 "없음"이라는 답도 캐시된다 — RFC 2308이 정의한 **negative caching**이다. 그리고 negative TTL은 A 레코드의 TTL이 아니라 도메인의 **SOA 레코드 `minimum` 필드**로 정해진다. SOA minimum이 3600이면, 한 번 `NXDOMAIN`을 받은 resolver는 **1시간 동안** "그 이름 없음"을 고집한다. A 레코드 TTL을 아무리 낮춰도 소용없다.

### 3겹 — OS와 런타임의 자체 캐시

여기가 진짜 함정이다. resolver가 새 답을 줘도, **클라이언트 쪽이 안 물어보면** 끝이다.

- **OS 스텁 resolver**: Windows `DNS Client`, systemd-resolved 등이 별도 캐시를 둔다. 보통 TTL을 따르지만, 정지/대기 상태에선 갱신이 늦다.
- **JVM**: 자바는 악명 높다. 보안 매니저가 켜진 환경의 기본값 `networkaddress.cache.ttl = -1` 은 **한 번 해석한 IP를 프로세스가 죽을 때까지 영구 캐시**한다. TTL이 60초든 1초든 JVM은 거들떠보지 않는다. 재시작 전엔 절대 새 IP를 안 본다.
- **커넥션 풀/Keep-Alive**: DNS가 새 IP를 알려줘도, 이미 열린 TCP 커넥션은 **옛 IP에 그대로 붙어 있다.** [[http2-vs-http3]]의 멀티플렉싱처럼 커넥션을 오래 재사용할수록 옛 IP에 더 오래 고착된다.

정리하면, **TTL이 통제하는 건 "resolver가 권위 서버에 다시 물어볼 최대 간격"뿐이다.** resolver의 클램핑, negative cache의 SOA minimum, OS·JVM·커넥션 풀은 TTL 밖의 독립 변수다. 이 네 개가 곱해져서 실제 전환 시간이 결정된다.

## 현장 시나리오

한 커머스가 결제 게이트웨이 서버를 새 인프라로 옮겼다. 전날 A 레코드 TTL을 `3600`에서 `60`으로 낮췄고, 당일 새 IP로 레코드를 바꿨다. "1분이면 다 넘어온다"고 보고했다. 인과 사슬은 이랬다:

- 결제 처리를 담당하는 **자바 서비스**가 게이트웨이 도메인을 호출했다. JVM `networkaddress.cache.ttl`이 기본 `-1`이라, 부팅 때 한 번 해석한 **옛 IP를 영구 캐시**하고 있었다. TTL 60초는 JVM 입장에선 존재하지 않는 값이었다
- 옛 서버는 트래픽을 받아 처리는 했지만, 마이그레이션 정리 단계에서 **DB 커넥션이 끊긴 상태**였다 — 결제가 간헐적으로 실패하기 시작했다
- 옛 IP로 가던 자바 워커들이 한 시간째 옛 서버를 때렸고, **전체 결제의 약 18%**가 그쪽으로 빠져 실패했다
- 운영팀은 `dig`로 새 IP가 잘 나오는 걸 확인하고 "DNS는 정상"이라 판단해 한 시간을 엉뚱한 데서 헤맸다. resolver는 멀쩡했고, 범인은 **앱 프로세스 안의 캐시**였다

복구는 DNS가 아니라 **자바 서비스 롤링 재시작**이었다. 재시작하자 JVM이 새로 해석해 즉시 새 IP로 붙었다. 사후 팀은 모든 자바 서비스에 `networkaddress.cache.ttl=30`을 박았다. 원인은 TTL을 안 낮춰서가 아니라, **TTL이 닿지도 않는 레이어에 캐시가 있다는 걸 몰랐던 것**이었다. `dig`가 보는 세상과 앱이 보는 세상은 다른 캐시다.

## 실무 적용 포인트

1. **전환은 TTL의 "2배 + α"를 기다려라**: TTL 60초여도 resolver 클램핑·전파 지연 때문에 실제는 더 걸린다. 옛 서버를 끄기 전 **최소 TTL의 2~3배**(예: 60초 → 3~5분)는 양쪽을 동시에 띄워둔다. 옛 IP로 오는 잔여 트래픽을 그래프로 0이 될 때까지 본 뒤 끈다.
2. **JVM은 반드시 `networkaddress.cache.ttl`을 명시하라**: 기본 `-1`(영구) 또는 `30`은 환경마다 다르다. `java.security`나 `-Dnetworkaddress.cache.ttl=30`으로 못박아라. 안 하면 페일오버가 재시작 전까지 안 먹는다.
3. **negative caching은 SOA `minimum`으로 통제한다**: 새 이름을 곧 만들 거면 SOA minimum(예: 3600)을 미리 낮춰라. A 레코드 TTL을 낮춰도 `NXDOMAIN` 캐시는 SOA가 지배한다.
4. **`dig`를 믿지 말고 앱 관점으로 확인하라**: `dig @8.8.8.8 도메인`은 resolver 캐시만 본다. 실제 트래픽이 어느 IP로 가는지는 **서버 액세스 로그의 출발 IP 분포**나 양쪽 서버의 요청 수로 봐야 한다.
5. **커넥션 풀과 Keep-Alive를 함께 끊어라**: DNS가 갱신돼도 열린 커넥션은 옛 IP에 붙어 있다. 페일오버 시 풀을 비우거나 connection max-age(예: 60초)를 둬 주기적으로 재연결되게 한다. [[circuit-breaker]]가 옛 IP를 빨리 격리하게 두는 것도 방법.
6. **진짜 빠른 전환이 필요하면 DNS에 의존하지 마라**: 초 단위 페일오버는 DNS 캐시 구조상 보장 못 한다. **고정 VIP + L4 로드밸런서**([[reverse-proxy-l4-l7]])나 anycast로 IP를 안 바꾸고 뒤만 바꾸는 게 정석이다.

## 더 깊은 토끼굴

- RFC 2181 — [Clarifications to the DNS Specification](https://www.rfc-editor.org/rfc/rfc2181): TTL의 정의와 캐싱 규칙의 1차 출처
- RFC 2308 — [Negative Caching of DNS Queries (DNS NCACHE)](https://www.rfc-editor.org/rfc/rfc2308): `NXDOMAIN` 캐시와 SOA `minimum`의 역할
- Oracle — [Java Networking Properties (`networkaddress.cache.ttl`)](https://docs.oracle.com/en/java/javase/17/docs/api/java.base/java/net/doc-files/net-properties.html): JVM이 DNS를 캐시하는 방식과 기본값
- [[reverse-proxy-l4-l7]]: IP를 안 바꾸고 뒤를 바꾸는 진짜 빠른 전환의 토대
- [[blue-green-canary-rolling]]: DNS 전환과 배포 전략이 만나는 지점
- [[cdn-cache-key]]: 또 다른 캐시 레이어 — DNS와 곱해지면 전파가 더 복잡해진다
