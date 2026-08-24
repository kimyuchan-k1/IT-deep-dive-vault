# Topics Catalog (시드 주제 풀)

매일 Claude가 여기서 가중 샘플링해서 후보 생성.
운영하면서 계속 추가/제거/난이도 조정.

## 컬럼 의미
- **slug**: 글 파일명 후보 (날짜 제외)
- **category**: A~H 카테고리
- **difficulty**: 1(기초) ~ 5(전문가)
- **status**: pending(미발행) / done(발행됨) / pinned(다음에 우선)

---

## A. 분산 시스템 이론

| slug | 제목 | difficulty | status |
|---|---|---|---|
| cap-theorem-real-meaning | CAP 정리의 진짜 의미와 PACELC | 4 | **done (Day 21)** |
| crdt-intro | CRDT — 동시 편집을 가능하게 하는 자료구조 | 4 | **done (Day 53)** |
| saga-vs-2pc | Saga 패턴 vs 2PC | 3 | **done (Day 31)** |
| local-first-software | Local-First Software 아키텍처 | 4 | **done (Day 13)** |
| lamport-vs-vector-clock | Lamport Clock vs Vector Clock | 4 | **done (Day 3)** |
| quorum-rw-n | Quorum 합의 (R + W > N) | 3 | pending |
| eventual-vs-strong-consistency | Eventual vs Strong Consistency 비용 | 3 | **done (Day 61)** |
| read-your-writes | Read-your-writes / Monotonic Read | 3 | **done (Day 47)** |
| redlock-debate | 분산 락 — Redlock 논쟁 | 4 | **done (Day 68)** |
| two-generals | Two Generals Problem이 시스템에 미치는 영향 | 3 | **done (Day 33)** |
| paxos-5min | Paxos를 5분만에 직관적으로 | 5 | pending |
| raft-easier-than-paxos | Raft가 Paxos보다 쉬운 이유 | 4 | **done (Day 28)** |
| idempotency-key | Idempotency Key 설계 | 2 | **done (Day 45)** |
| outbox-pattern | 분산 트랜잭션 — Outbox 패턴 | 3 | **done (Day 56)** |
| cdc-debezium | CDC와 Debezium | 3 | **done (Day 11)** |

## B. Redis / 캐시

| slug | 제목 | difficulty | status |
|---|---|---|---|
| redis-bigkey | Redis BigKey가 클러스터를 죽이는 이유 | 3 | **done (Day 1)** |
| redis-sorted-set-queue | Redis Sorted Set으로 선착순 큐 | 3 | **done (Day 39)** |
| redis-rdb-vs-aof | RDB vs AOF | 2 | **done (Day 25)** |
| cache-stampede | Cache Stampede와 PER, XFetch | 4 | **done (Day 59)** |
| cache-aside-vs-write-through | Cache-Aside vs Write-Through vs Write-Behind | 2 | **done (Day 65)** |
| redis-cluster-slot | Redis Cluster의 hash slot 16384의 의미 | 3 | pending |
| redis-pipelining-vs-tx | Pipelining vs Transaction (MULTI/EXEC) | 2 | **done (Day 44)** |
| redis-lua-atomic | Redis Lua로 원자적 연산 | 3 | **done (Day 23)** |
| redis-scan-vs-keys | SCAN을 KEYS 대신 써야 하는 이유 | 1 | pending |
| redis-streams-vs-kafka | Redis Streams vs Kafka | 3 | pending |
| redis-ttl-eviction | TTL과 LRU/LFU eviction | 2 | **done (Day 67)** |
| redis-acl | Redis 6 ACL과 멀티테넌시 | 3 | **done (Day 54)** |
| redis-hotkey | Redis HotKey 문제와 대응 | 3 | pending |
| lazy-freeing | UNLINK / LAZY FREE 동작 원리 | 3 | **done (Day 69)** |

## C. 메시지 큐 / 이벤트 드리븐

| slug | 제목 | difficulty | status |
|---|---|---|---|
| kafka-partition-math | Kafka 파티션과 컨슈머 그룹 수학 | 3 | pending |
| kafka-exactly-once | Kafka Exactly-Once의 진짜 동작 | 4 | **done (Day 4)** |
| dead-letter-queue | Dead Letter Queue 패턴 | 2 | **done (Day 37)** |
| at-least-once-vs-at-most-once | At-least-once vs At-most-once | 2 | **done (Day 29)** |
| rabbitmq-vs-kafka | RabbitMQ vs Kafka 결정 트리 | 3 | pending |
| backpressure-patterns | Backpressure 4가지 패턴 | 4 | **done (Day 49)** |
| event-sourcing-intro | Event Sourcing 입문 | 3 | **done (Day 73)** |
| cqrs-when-needed | CQRS는 언제 진짜 필요한가 | 3 | **done (Day 64)** |
| saga-orchestrator-vs-choreography | Saga Orchestrator vs Choreography | 3 | **done (Day 7)** |
| kafka-connect-schema-registry | Kafka Connect / Schema Registry | 3 | pending |
| sqs-vs-sns-vs-eventbridge | SQS vs SNS vs EventBridge | 2 | **done (Day 17)** |
| pull-vs-push-model | Pull vs Push 모델 | 2 | **done (Day 19)** |

## D. 데이터베이스 내부

| slug | 제목 | difficulty | status |
|---|---|---|---|
| btree-index-internals | B+Tree 인덱스 내부 구조 | 3 | **done (Day 2)** |
| lsm-tree-rocksdb | LSM Tree와 RocksDB 동작 원리 | 4 | **done (Day 74)** |
| mvcc-how | MVCC가 락을 어떻게 줄이나 | 3 | pending |
| phantom-read-isolation | Phantom Read와 격리 수준 | 3 | **done (Day 77)** |
| hash-merge-nested-loop-join | Hash/Merge/Nested Loop Join | 3 | **done (Day 32)** |
| index-skip-scan-covering | Index Skip Scan과 Covering Index | 3 | **done (Day 36)** |
| connection-pool-sizing | Connection Pool 사이즈 결정 공식 | 3 | **done (Day 48)** |
| wal-pitr | WAL과 PITR | 3 | **done (Day 52)** |
| postgres-vacuum-bloat | PostgreSQL VACUUM과 bloat | 4 | **done (Day 60)** |
| innodb-buffer-pool | MySQL InnoDB 버퍼풀 튜닝 | 4 | **done (Day 5)** |
| replication-lag | Read Replica replication lag 다루기 | 3 | **done (Day 41)** |
| sharding-strategies | Sharding 전략 (Range/Hash/Directory) | 3 | **done (Day 50)** |
| upsert-idempotency | UPSERT 패턴과 멱등성 | 2 | pending |
| materialized-view | Materialized View vs 캐시 테이블 | 2 | **done (Day 9)** |
| hyperloglog-approx-counting | 어림 카운팅 (HyperLogLog) | 3 | **done (Day 38)** |

## E. 네트워크 / 프로토콜

| slug | 제목 | difficulty | status |
|---|---|---|---|
| tcp-slow-start | TCP Slow Start와 Congestion Window | 3 | pending |
| http2-vs-http3 | HTTP/2 vs HTTP/3 (QUIC) | 3 | **done (Day 57)** |
| websocket-vs-sse-vs-polling | WebSocket vs SSE vs Long Polling | 2 | **done (Day 62)** |
| tls13-zero-rtt | TLS 1.3 handshake 0-RTT | 4 | pending |
| dns-cache-ttl | DNS 캐시와 TTL 튜닝 | 2 | **done (Day 14)** |
| grpc-vs-rest | gRPC가 REST보다 빠른 진짜 이유 | 2 | pending |
| cdn-cache-key | CDN 캐시 키 설계 | 3 | **done (Day 27)** |
| reverse-proxy-l4-l7 | Reverse Proxy와 L4/L7 LB | 2 | **done (Day 34)** |
| http-idempotency | Idempotency를 위한 HTTP 메서드 설계 | 2 | pending |
| sse-prod-ops | Server-Sent Events 실전 운영 | 3 | **done (Day 71)** |

## F. 클라우드 / 인프라

| slug | 제목 | difficulty | status |
|---|---|---|---|
| k8s-pod-death-5-reasons | Kubernetes Pod이 죽는 흔한 5가지 이유 | 2 | pending |
| liveness-readiness-startup | Liveness/Readiness/Startup Probe | 2 | **done (Day 15)** |
| hpa-internals | HPA의 진짜 동작 | 3 | **done (Day 18)** |
| service-mesh-istio | Service Mesh가 풀어주는 것 | 3 | pending |
| sidecar-tradeoff | Sidecar 패턴 트레이드오프 | 3 | **done (Day 6)** |
| aws-vpc-design | AWS VPC 디자인 — AZ 분리 | 3 | **done (Day 51)** |
| spot-instance-safe | Spot Instance 안전하게 쓰는 법 | 3 | **done (Day 75)** |
| s3-consistency-evolution | S3 일관성 모델 변천사 | 3 | **done (Day 43)** |
| observability-stack | CloudWatch vs Datadog vs Grafana | 2 | **done (Day 70)** |
| helm-vs-kustomize | Helm 차트 vs Kustomize | 2 | **done (Day 72)** |
| terraform-state | Terraform State 백업과 잠금 | 3 | **done (Day 26)** |
| blue-green-canary-rolling | Blue/Green vs Canary vs Rolling | 2 | **done (Day 10)** |

## G. 관측성 / 안정성

| slug | 제목 | difficulty | status |
|---|---|---|---|
| sli-slo-sla | SLI/SLO/SLA의 진짜 차이 | 2 | pending |
| percentile-p99 | p50, p95, p99 어떤 걸 봐야 하나 | 2 | pending |
| distributed-tracing-otel | 분산 트레이싱 (OpenTelemetry) | 3 | **done (Day 42)** |
| structured-logging | Structured Logging이 grep보다 나은 이유 | 2 | **done (Day 46)** |
| circuit-breaker | Circuit Breaker 패턴 | 2 | **done (Day 40)** |
| bulkhead-pattern | Bulkhead 패턴 | 3 | **done (Day 35)** |
| retry-exponential-backoff-jitter | Retry + Exponential Backoff + Jitter | 2 | **done (Day 63)** |
| chaos-engineering-intro | Chaos Engineering 입문 | 3 | **done (Day 24)** |
| blameless-postmortem | Postmortem 잘 쓰는 법 | 2 | **done (Day 66)** |
| error-budget | Error Budget로 배포 속도 결정 | 3 | **done (Day 55)** |

## H. 보안 / 인증

| slug | 제목 | difficulty | status |
|---|---|---|---|
| oauth2-grant-types | OAuth2 4가지 grant type | 3 | **done (Day 16)** |
| jwt-vs-session | JWT vs Session 쿠키 | 3 | **done (Day 22)** |
| mtls-zero-trust | mTLS와 zero-trust | 3 | **done (Day 30)** |
| rate-limit-token-bucket | Rate Limiting (Token vs Leaky Bucket) | 3 | **done (Day 58)** |
| sqli-prepared-stmt | SQL Injection — Prepared Statement 한계 | 3 | **done (Day 20)** |
| csrf-samesite | CSRF와 SameSite 쿠키 | 2 | **done (Day 8)** |
| secret-management | Secret 관리 (Vault vs SSM) | 2 | **done (Day 76)** |
| rbac-abac-rebac | RBAC vs ABAC vs ReBAC | 3 | **done (Day 12)** |

---

## 카테고리별 진행 현황 (자동 갱신 예정)

- A. 분산 시스템 이론: **13 / 15**
- B. Redis / 캐시: **10 / 14**
- C. 메시지 큐: **9 / 12**
- D. 데이터베이스 내부: **13 / 15**
- E. 네트워크: **6 / 10**
- F. 클라우드: **10 / 12**
- G. 관측성: **8 / 10**
- H. 보안: **8 / 8**

**총 94개. 약 3개월치.**
