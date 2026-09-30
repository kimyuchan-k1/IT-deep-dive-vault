---
title: 링크 미리보기 기능 하나로 AWS 임시 자격증명이 밖으로 나갔다
date: 2026-09-30
day: 101
category: security
tags: [ssrf, imds, aws, cloud-security, egress]
related: ["[[secret-management]]", "[[aws-vpc-design]]", "[[mtls-zero-trust]]", "[[rbac-abac-rebac]]", "[[sqli-prepared-stmt]]"]
difficulty: 3
short_text: |
  🔥 [Day 101] 링크 미리보기로 AWS 키 유출

  오해: 내부 IP 막으면 SSRF 끝
  실제: 302→169.254.169.254→IAM 임시키→외부서 S3 조회

  📖 https://github.com/kimyuchan-k1/IT-deep-dive-vault/blob/main/posts/2026-09-30-ssrf-metadata-endpoint.md
---

# 링크 미리보기 기능 하나로 AWS 임시 자격증명이 밖으로 나갔다

## 흔한 오해

> "SSRF는 서버가 내부망 주소를 요청하게 만드는 공격이다. 입력 URL에서 `localhost`, `127.0.0.1`, `10.x`만 막으면 된다."

그래서 URL을 받는 기능마다 문자열 블록리스트 몇 줄이 붙는다. 입문 자료도 "사용자 입력 URL을 검증하라"에서 멈춘다.

이 방어에는 두 가지 빈칸이 있다. **검증하는 문자열과 실제로 연결하는 주소가 다를 수 있다.** 그리고 클라우드에서 가장 비싼 표적은 내부 서비스가 아니라 **링크 로컬 주소 `169.254.169.254`의 메타데이터 엔드포인트**다. 여기에는 인스턴스에 붙은 IAM 역할의 임시 키가 평문으로 있다.

## 실제 원리

### 1. 메타데이터 엔드포인트는 "인증 없는 비밀 저장소"다

EC2 인스턴스는 링크 로컬 주소 `169.254.169.254`로 자기 정보를 조회한다. 하이퍼바이저가 응답하므로 VPC 보안 그룹이나 NACL을 거치지 않는다.

```
GET http://169.254.169.254/latest/meta-data/iam/security-credentials/
  → app-role
GET http://169.254.169.254/latest/meta-data/iam/security-credentials/app-role
  → { "AccessKeyId": "ASIA...", "SecretAccessKey": "...", "Token": "...", "Expiration": "..." }
```

IMDSv1에서는 이 GET 두 번이 전부다. 인스턴스 안에서 나간 요청이면 누가 보냈는지 묻지 않는다. 여기서 핵심은 **"인스턴스 안에서 나간 요청"에 SSRF로 만든 요청도 포함된다**는 점이다. 서버가 공격자 대신 GET을 보내 주면 공격자가 키를 받는다.

이 키는 인스턴스 밖에서도 쓸 수 있다. `Expiration`까지는 공격자 노트북에서 `aws s3 ls`가 그대로 동작한다.

### 2. IMDSv2는 세션 토큰으로 SSRF의 전형적인 모양을 막는다

IMDSv2는 먼저 `PUT`으로 토큰을 받고, 이후 요청마다 헤더로 토큰을 싣게 한다.

```
PUT /latest/api/token
  X-aws-ec2-metadata-token-ttl-seconds: 21600      → 토큰
GET /latest/meta-data/...
  X-aws-ec2-metadata-token: <토큰>
```

왜 이게 막히나 보면 SSRF 대부분의 형태 때문이다. 공격자는 보통 **URL만** 조종한다. 메서드는 GET이고 임의 헤더를 못 넣는다. PUT도, 커스텀 헤더도 없으니 토큰을 못 받는다. 추가 장치가 둘 더 있다.

- `X-Forwarded-For` 헤더가 붙은 토큰 요청은 거부한다. 오픈 리버스 프록시를 경유한 요청을 걸러낸다.
- 토큰 응답 패킷의 IP TTL이 `HttpPutResponseHopLimit`(기본 1)다. 한 홉만 넘어가도 응답이 버려진다. 컨테이너 네트워크 브리지를 한 번 더 타는 파드는 이 값이 1이면 토큰을 못 받는다.

GCP는 `Metadata-Flavor: Google`, Azure는 `Metadata: true` 헤더를 필수로 요구한다. 같은 발상이다. **헤더를 못 넣는 SSRF는 막고, 헤더까지 조종하는 SSRF는 못 막는다.**

### 3. 문자열 블록리스트는 표기법과 시간 차로 뚫린다

`169.254.169.254`를 문자열로 막아도 같은 주소를 가리키는 표기가 많다.

| 표기 | 값 |
|---|---|
| 10진수 | `http://2852039166/` |
| 16진수 | `http://0xA9FEA9FE/` |
| IPv4-mapped IPv6 | `http://[::ffff:169.254.169.254]/` |
| DNS | `meta.attacker.example` → A 레코드 169.254.169.254 |

더 근본적인 구멍은 **검증과 연결 사이의 시간 차**다. 검증 코드가 호스트를 한 번 resolve해서 공인 IP를 확인하고, HTTP 클라이언트가 연결할 때 다시 resolve한다. TTL 0짜리 DNS가 두 번째 조회에 169.254.169.254를 돌려주면 끝이다(DNS rebinding). HTTP 클라이언트가 `302`를 자동으로 따라가도 같다. 검증은 첫 URL만 봤고 연결은 리다이렉트 목적지로 간다.

그래서 검증 대상은 문자열이 아니라 **소켓이 실제로 연결하는 IP**여야 한다.

## 현장 시나리오

사내 협업 툴 SaaS. 채팅에 URL을 붙이면 서버가 페이지를 가져와 `og:title`과 이미지를 카드로 만든다. 서버는 EC2 위 컨테이너, 인스턴스 역할 `chat-app-role`에는 첨부파일 버킷 `s3:GetObject`, `s3:ListBucket`. IMDS는 v1/v2 둘 다 허용(`HttpTokens=optional`) 상태였다. 방어는 URL 문자열에 `localhost`, `127.`, `10.`, `192.168.`가 있으면 거부하는 정규식 하나.

```
공격자가 https://preview.attacker.example/a 를 채팅에 붙임
  → 문자열 검증 통과 (공인 도메인)
  → 서버 HTTP 클라이언트가 GET, 응답은 302 Location: http://169.254.169.254/latest/meta-data/iam/security-credentials/chat-app-role
  → 클라이언트가 리다이렉트를 자동 추종, 목적지는 재검증 안 함
  → IMDSv1이 JSON으로 임시 키 응답
  → og:title이 없어 "본문 앞 200자"를 카드 제목으로 표시 → 키 일부가 채팅창에 노출
  → 공격자는 경로를 바꿔 가며 반복, SecretAccessKey와 Token 전체 확보
  → 공격자 IP에서 ListBucket → GetObject 수천 건
  → 3시간 뒤 GuardDuty가 인스턴스 자격증명이 AWS 밖에서 쓰였다는 탐지를 올림
```

원인은 세 겹이었다. 리다이렉트 목적지를 검증하지 않았다. IMDSv1이 열려 있었다. 역할 권한이 첨부파일 전체였다. 어느 하나만 막혔어도 키는 나가지 않았거나 쓸모가 없었다.

2019년 Capital One 사고가 같은 경로(설정이 잘못된 WAF의 SSRF → IMDS 역할 자격증명 → S3)로 알려져 있고, AWS가 그해 11월 IMDSv2를 내놓은 배경이다.

## 실무 적용 포인트

1. **IMDSv2를 강제한다.** `aws ec2 modify-instance-metadata-options --instance-id i-xxx --http-tokens required --http-put-response-hop-limit 1`. 새 인스턴스는 Launch Template에 `HttpTokens=required`를 박는다. 전환 전 CloudWatch 지표 `MetadataNoToken`으로 아직 v1을 부르는 프로세스를 찾는다. 이 값이 0으로 며칠 유지되면 강제로 바꾼다.

2. **컨테이너가 노드 역할 키를 못 보게 한다.** EKS에서는 파드에 IRSA 또는 EKS Pod Identity로 역할을 따로 주고, 노드 hop limit은 1로 둬 파드에서 노드 IMDS 토큰이 안 나오게 한다. NetworkPolicy로 `169.254.169.254/32` 이그레스를 차단하면 이중 방어가 된다.

3. **검증은 resolve된 IP로, 연결도 그 IP로 한다.** 호스트를 한 번 resolve → IP가 `127.0.0.0/8`, `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`, `169.254.0.0/16`, `::1`, `fc00::/7`, `fe80::/10`, `fd00:ec2::254`에 속하면 거부 → 그 IP로 소켓을 열고 `Host` 헤더만 원래 이름으로 보낸다. 두 번 resolve하지 않는다.

4. **리다이렉트는 끄거나 홉마다 위 3번 검증을 다시 한다.** 자동 추종을 끄고 최대 3홉까지 수동으로 따라가며 각 `Location`을 재검증한다. 응답 크기 1MB, 타임아웃 5초 상한도 건다.

5. **외부 URL을 가져오는 기능은 격리된 이그레스로 뺀다.** 미리보기·웹훅·이미지 프록시 워커를 IAM 역할이 없는 별도 서브넷/태스크에 두고, 아웃바운드는 프록시 한 곳만 허용한다([[aws-vpc-design]]). 키가 없는 곳에서는 SSRF가 성공해도 가져갈 게 없다.

6. **응답 본문을 사용자에게 되돌리지 않는다.** 파싱 실패 시 "미리보기 없음"으로 끝낸다. 에러 메시지나 폴백 제목에 원문을 섞으면 blind SSRF가 full-read SSRF로 바뀐다.

## 더 깊은 토끼굴

- [[secret-management]] — 장기 키 대신 임시 자격증명을 쓰는 이유와, 임시 키도 유출되면 만료 전까지는 유효하다는 한계.
- [[aws-vpc-design]] — 이그레스 경로를 서브넷 단위로 나누는 설계.
- [[mtls-zero-trust]] — "내부에서 온 요청은 믿는다"는 가정이 IMDSv1과 같은 구조의 취약점이다.
- [[rbac-abac-rebac]] — 역할 권한을 버킷 단위가 아니라 prefix·태그 조건으로 좁히는 방법.
- [[sqli-prepared-stmt]] — 문자열 검증 대신 구조로 막는다는 같은 원칙.
- [[dns-rebinding]] — 검증-연결 시간 차 공격의 상세.

**1차 출처**
- AWS — Configure the instance metadata service options (IMDSv2, hop limit): https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/configuring-instance-metadata-service.html
- AWS Security Blog — Add defense in depth against open firewalls, reverse proxies, and SSRF vulnerabilities with enhancements to the EC2 Instance Metadata Service: https://aws.amazon.com/blogs/security/defense-in-depth-open-firewalls-reverse-proxies-ssrf-vulnerabilities-ec2-instance-metadata-service/
- OWASP — Server-Side Request Forgery Prevention Cheat Sheet: https://cheatsheetseries.owasp.org/cheatsheets/Server_Side_Request_Forgery_Prevention_Cheat_Sheet.html
- Google Cloud — About VM metadata (`Metadata-Flavor: Google`): https://cloud.google.com/compute/docs/metadata/overview
