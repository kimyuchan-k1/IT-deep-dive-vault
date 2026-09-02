---
title: 재시도를 앱에서 걷어내 메시에 맡겼다. 장애 하나가 243배로 돌아왔다
date: 2026-09-02
day: 84
category: cloud
tags: [service-mesh, istio, envoy, xds, retry-amplification]
related: ["[[sidecar-tradeoff]]", "[[circuit-breaker]]", "[[retry-exponential-backoff-jitter]]", "[[backpressure-patterns]]"]
difficulty: 3
short_text: |
  🔥 [Day 84] 재시도를 메시에 맡겼더니 장애가 243배로 커졌다

  오해: 재시도는 플랫폼이 알아서
  실제: 기본 3회가 홉마다 곱해짐→5홉이면 3⁵

  📖 https://github.com/kimyuchan-k1/IT-deep-dive-vault/blob/main/posts/2026-09-02-service-mesh-istio.md
---

# 재시도를 앱에서 걷어내 메시에 맡겼다. 장애 하나가 243배로 돌아왔다

## 흔한 오해

> "서비스 메시를 깔면 재시도·타임아웃·서킷 브레이커를 각 언어 라이브러리로 따로 구현할 필요가 없다. 네트워크 신뢰성을 플랫폼이 대신 처리해주니까 앱은 비즈니스 로직만 짜면 된다."

메시 도입 제안서에 거의 항상 이렇게 적힌다. Java는 Resilience4j, Go는 go-retryablehttp로 제각각 짜던 걸 YAML 한 장으로 통일한 건 맞다. 언어별 파편화를 없앤 게 메시의 진짜 성과다.

문제는 그다음이다. "플랫폼이 대신 해준다"를 "플랫폼이 알아서 잘 해준다"로 읽는 순간, **정책이 어디에 몇 벌 존재하는지에 대한 감각이 사라진다.** 라이브러리 재시도는 코드 리뷰에 걸린다. 메시 재시도는 아무도 쓴 적 없는데 이미 켜져 있다.

## 실제 원리

### 메시가 파는 건 프록시가 아니라 컨트롤 플레인이다

Envoy는 그냥 프록시다. 메시의 본체는 **정책을 수천 개 프록시에 뿌리는 xDS 컨트롤 플레인**이다. istiod는 LDS(리스너)·RDS(라우트)·CDS(클러스터)·EDS(엔드포인트)를 gRPC 스트림으로 각 사이드카에 푸시한다.

여기서 구조적 성질 두 개가 나온다.

첫째, **푸시는 원자적이지 않다.** 정책 하나를 바꾸면 프록시마다 도착 시각이 다르다. 그 사이 클러스터는 구정책 프록시와 신정책 프록시가 섞인 상태로 돈다. istiod가 `pilot_proxy_convergence_time`을 히스토그램으로 노출하는 이유가 이거다. 수렴에 걸리는 시간이 지표라는 건, 수렴 안 된 구간이 정상 상태라는 뜻이다.

둘째, **각 프록시는 자기 홉만 본다.** 사이드카는 자기가 보낸 요청이 실패했다는 것만 알지, 그 요청이 이미 세 단계 위에서 두 번 재시도된 결과라는 걸 모른다. 전역 시야가 없다. 이 두 번째 성질이 뒤에서 터진다.

### 아무도 켠 적 없는 재시도가 켜져 있다

Istio는 VirtualService를 하나도 안 만들어도 HTTP 요청에 기본 재시도를 건다. 공식 문서 기준 **기본 attempts는 2**다. 최초 시도까지 합쳐 **요청 1건당 최대 3번**이 나간다.

무엇을 재시도하는지가 더 중요하다. Envoy의 `retryOn` 조건에는 `connect-failure`, `refused-stream`, `unavailable`, `retriable-status-codes`(기본 503)가 들어간다. 그러니까 **업스트림이 과부하로 503을 뱉는 바로 그 상황이 재시도 트리거다.** 죽어가는 서비스에 트래픽을 3배로 부어주는 조건이 기본값이다.

gRPC는 더 나쁘다. 서버가 과부하를 알리는 표준 코드 `UNAVAILABLE`이 재시도 대상에 그대로 들어 있다.

### 홉마다 독립이면 곱해진다

핵심은 여기다. 재시도는 **호출하는 쪽 사이드카**에서 일어난다. 체인이 A → B → C → D → E라면, A의 사이드카도 3번, B의 사이드카도 3번, C의 사이드카도 3번 시도한다. 각자 자기 홉만 보니까 서로의 존재를 모른다.

```
A→B 1회 요청
  B→C 최대 3회
    C→D 각각 최대 3회 = 9
      D→E 각각 최대 3회 = 27
        E 도달 요청 = 3⁴ = 81
5홉이면 3⁵ = 243
```

라이브러리 시절에도 같은 곱셈은 가능했다. 차이는 **가시성**이다. 코드의 `@Retryable`은 grep에 걸린다. 메시 기본값은 어느 저장소에도 없다.

그리고 증폭이 용량을 넘으면 시스템은 **트리거가 사라져도 스스로 복구하지 못한다.** 재시도가 만든 부하가 다시 실패를 만들고, 그 실패가 다시 재시도를 만든다. 원인을 제거해도 나쁜 상태에 눌러앉는 이 성질을 metastable failure라고 부른다. [[circuit-breaker]]가 라이브러리 기능이 아니라 아키텍처 요구사항인 이유다.

## 현장 시나리오

핀테크, 마이크로서비스 40개, Istio 도입 8개월차. 결제 경로는 `gateway → order → payment → ledger → risk` 5홉이다.

`risk` 서비스가 룰 엔진 배포 후 p99가 40ms → 900ms로 늘었다. 타임아웃에 걸린 요청이 503으로 돌아오기 시작했다. 여기까지는 단일 서비스 성능 저하다.

```
risk 응답 900ms → ledger 사이드카 타임아웃 → 503
  → ledger 사이드카가 3회 재시도 (기본값)
  → payment 사이드카도 그 실패를 보고 3회
  → order, gateway도 각각 3회
  → risk 도착 RPS 800 → 실측 19,000
  → risk 커넥션 큐 포화, 정상 요청도 전부 503
  → 롤백 완료 후에도 15분간 복구 안 됨
```

배포 롤백이 8분 만에 끝났는데 장애는 23분이었다. 롤백 후에도 회복이 안 된 구간이 metastable 상태다. 밖에서 들어오는 실트래픽은 800 RPS인데, 메시 안에서 재생산된 재시도가 그보다 큰 부하를 유지시켰다.

멈춘 방법은 스케일아웃이 아니라 `risk`로 가는 VirtualService에 `retries.attempts: 0`을 넣고 xDS 수렴을 기다린 것이었다. 40초 뒤 RPS가 800으로 떨어졌다.

사후에 확인한 게 하나 더 있다. Istio의 DestinationRule `connectionPool.http.maxRetries` **기본값은 2³²-1**이다. 클러스터 전체에서 동시에 떠 있을 수 있는 재시도 수에 사실상 상한이 없었다. 재시도를 막을 마지막 방벽이 열려 있는 상태로 8개월을 돌았다.

## 실무 적용 포인트

1. **내부 홉의 재시도는 명시적으로 끈다.** 내부 VirtualService에 `retries: {attempts: 0}`을 기본으로 박고, 재시도는 **엣지 게이트웨이 1홉에서만** 허용한다. 곱셈을 덧셈으로 되돌리는 유일한 방법이다.
2. **`connectionPool.http.maxRetries`를 반드시 숫자로 지정한다.** 기본 2³²-1은 상한이 없는 것과 같다. 업스트림 파드 수 × 3 정도의 유한값으로 잡는다.
3. **`retryOn`에서 `unavailable`과 503을 뺀다.** 남길 건 `connect-failure`와 `refused-stream`처럼 **요청이 서버에 도달하지 못한 게 확실한** 조건뿐이다. 503은 "서버가 받았고 처리하다 실패했다"일 수 있어서, 재시도하면 [[idempotency-key]] 없이는 중복 처리가 난다.
4. **`perTryTimeout × attempts < 전체 timeout`을 지킨다.** Istio는 재시도 중이라도 라우트 전체 타임아웃이 되면 잘라낸다. 전체 10s, perTry 8s, attempts 2로 두면 두 번째 시도는 실행조차 안 되고 설정만 있는 상태가 된다.
5. **재시도 대신 축출을 쓴다.** `outlierDetection`으로 `consecutive5xxErrors: 5`, `baseEjectionTime: 30s`를 걸면 아픈 인스턴스를 로드밸런싱 풀에서 빼는 쪽이 먼저 동작한다. 같은 인스턴스를 3번 두드리는 것보다 낫다.
6. **증폭 비율과 수렴 지표를 같이 대시보드에 올린다.** 엣지 인그레스 RPS 대비 최말단 서비스 인바운드 RPS 비율은 정상 시 1에 가깝고, 이게 튀는 게 재시도 폭풍의 가장 빠른 신호다 — [[distributed-tracing-otel]]의 스팬 수로도 같은 걸 본다. 옆에 `pilot_proxy_convergence_time` p99와 `pilot_xds_push_errors`를 둔다. 장애 중 정책을 바꿨으면 "적용됐다"가 아니라 수렴 지표를 보고 판단해야 한다.

## 더 깊은 토끼굴

메시가 실제로 풀어준 문제는 재시도 자체가 아니라 **정책의 위치**다. 라이브러리 시절엔 40개 서비스에 40벌의 재시도 코드가 있었고, 하나 고치려면 40번 배포해야 했다. 메시는 그걸 한 곳에서 바꿀 수 있게 만들었다. 대신 **한 곳에서 40개를 동시에 망칠 수 있게** 됐고, 기본값이 안전하지 않으면 그 기본값이 40벌로 복제된다.

사이드카가 데이터 경로에 붙는 비용 자체는 [[sidecar-tradeoff]]에서 다뤘다. 여기서 본 건 그 위층, 컨트롤 플레인이 만드는 문제다. 재시도의 올바른 형태는 [[retry-exponential-backoff-jitter]]로 이어지고, 재시도를 아예 안 하고 부하를 되밀어내는 쪽은 [[backpressure-patterns]]와 [[bulkhead-pattern]]이다. 메시가 자동으로 붙여준다고 광고하는 mTLS의 실제 비용은 [[mtls-zero-trust]]에 있다.

**출처**

- Istio Traffic Management — 재시도 기본 동작과 `HTTPRetry` 필드: https://istio.io/latest/docs/concepts/traffic-management/#retries
- Istio DestinationRule 레퍼런스 — `connectionPool.http.maxRetries` 기본값 2³²-1: https://istio.io/latest/docs/reference/config/networking/destination-rule/#ConnectionPoolSettings-HTTPSettings
- Envoy `x-envoy-retry-on` 조건 목록: https://www.envoyproxy.io/docs/envoy/latest/configuration/http/http_filters/router_filter#x-envoy-retry-on
- Google SRE Book, Ch.22 "Addressing Cascading Failures": https://sre.google/sre-book/addressing-cascading-failures/
- Bronson et al., "Metastable Failures in Distributed Systems" (HotOS '21): https://sigops.org/s/conferences/hotos/2021/papers/hotos21-s11-bronson.pdf
