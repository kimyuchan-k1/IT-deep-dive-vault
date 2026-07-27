---
title: AZ 3개에 나눠 깔았는데 AZ 하나 죽자 서비스 전체가 멈췄다
date: 2026-07-27
day: 51
category: cloud
tags: [aws, vpc, availability-zone, nat-gateway, high-availability]
related: ["[[reverse-proxy-l4-l7]]", "[[replication-lag]]", "[[dns-cache-ttl]]", "[[terraform-state]]", "[[circuit-breaker]]"]
difficulty: 3
short_text: |
  🔥 [Day 51] AZ 하나 죽자 3-AZ 구성이 전멸했다
  오해: 멀티 AZ면 알아서 안전하다
  실제: 단일 NAT→아웃바운드 전멸→헬스체크 실패
  📖 https://github.com/kimyuchan-k1/IT-deep-dive-vault/blob/main/posts/2026-07-27-aws-vpc-design.md
---

# AZ 3개에 나눠 깔았는데 AZ 하나 죽자 서비스 전체가 멈췄다

EC2를 `ap-northeast-2a`, `2b`, `2c`에 고르게 깔았다. ALB도 3개 AZ 전부에 붙였다. RDS는 Multi-AZ. 아키텍처 다이어그램상으로는 AZ 하나가 통째로 죽어도 2/3은 살아야 했다. 그런데 실제로 한 AZ에 이슈가 생기자 세 AZ의 인스턴스가 전부 헬스체크에서 떨어졌다.

## 흔한 오해

> "인스턴스를 여러 AZ에 나눠 두면 멀티 AZ 아닌가? AZ 하나 죽어도 나머지가 받아주는 거고."

콘솔에서 AZ 체크박스 세 개를 켜는 게 곧 고가용성이라고 배우는 경우가 많다. 그래서 컴퓨트 계층만 흩뿌려 놓고 끝낸다.

절반만 맞다. AZ에 흩어져야 하는 건 인스턴스가 아니라 **요청이 지나가는 모든 홉**이다. 홉 중 하나라도 특정 AZ에 단 하나만 존재하면, 그 AZ가 죽는 순간 나머지 AZ의 인스턴스도 같이 죽는다. 흩어놓은 것 위에 공유 단일점이 얹혀 있으면 가용성은 가장 약한 홉을 따라간다.

## 실제 원리

### 서브넷은 AZ 하나에 못 박혀 있다

VPC는 리전 단위 리소스지만, **서브넷은 정확히 하나의 AZ에 귀속**된다. 서브넷은 AZ를 넘지 못한다. 그래서 "AZ에 나눈다"는 말의 실제 단위는 서브넷이고, AZ마다 퍼블릭/프라이빗 서브넷이 각각 있어야 계층이 온전히 복제된다.

주의할 점 하나. `ap-northeast-2a`라는 AZ **이름은 계정마다 다른 물리 AZ에 매핑**된다. AWS가 계정별로 이름을 셔플하기 때문이다. 두 계정이 같은 물리 AZ를 쓰는지 확인하려면 이름이 아니라 AZ ID(`apne2-az1` 형태)를 봐야 한다. 장애 공지와 내 리소스를 대조할 때 이걸 모르면 엉뚱한 AZ를 본다.

### 진짜 단일점은 NAT Gateway다

여기가 핵심이다. NAT Gateway는 리전 서비스가 아니라 **특정 서브넷, 즉 특정 AZ에 생성되는 리소스**다. AWS가 그 안에서 이중화해 주는 건 어디까지나 같은 AZ 안에서다. AZ 자체가 나가면 그 NAT도 같이 나간다.

비용을 아끼려고 NAT를 하나만 만들고 세 AZ의 프라이빗 서브넷 라우트 테이블이 전부 그 NAT를 가리키게 하는 구성이 흔하다. 이러면 AZ 하나가 죽었을 때 나머지 두 AZ의 인스턴스는 살아 있지만 **아웃바운드 인터넷이 통째로 끊긴다**. 외부 API 호출, 패키지 다운로드, 라이선스 검증, 퍼블릭 엔드포인트를 쓰는 SDK 호출이 전부 타임아웃으로 매달린다.

그리고 그 타임아웃이 헬스체크를 죽인다. 헬스체크 엔드포인트가 외부 의존성을 하나라도 건드리면 세 AZ 인스턴스가 동시에 unhealthy가 되고, ALB는 정상 타깃이 없다며 503을 뱉는다. 인스턴스 자체는 멀쩡한데 말이다.

### AZ를 넘는 트래픽에는 요금과 지연이 붙는다

같은 리전이라도 AZ를 넘는 트래픽은 EC2 기준 **양방향 각각 GB당 $0.01**이 과금된다. NAT Gateway는 시간당 요금과 별개로 **처리 데이터 GB당 요금**(us-east-1 기준 약 $0.045)을 따로 받는다. 즉 AZ 간 홉과 NAT 홉이 겹치면 같은 1GB에 요금이 두 번 붙는다.

그래서 NAT를 AZ마다 두는 건 가용성뿐 아니라 비용 문제이기도 하다. 단일 NAT 구성은 AZ 요금을 아끼는 게 아니라, 다른 AZ에서 온 트래픽에 AZ 간 요금을 얹은 뒤 NAT 처리 요금까지 물리는 구성이다.

로드밸런서 쪽도 같은 축이다. ALB는 cross-zone 로드밸런싱이 항상 켜져 있고 이 AZ 간 트래픽에 별도 요금이 없다. NLB는 기본이 꺼짐이고, 켜면 AZ 간 데이터 전송 요금이 붙는다. [[reverse-proxy-l4-l7]]에서 L4와 L7의 차이가 여기서 요금표로 드러난다.

## 현장 시나리오

한 서비스가 3-AZ로 구성돼 있었다. EC2 9대가 AZ당 3대씩, ALB는 3개 서브넷에 연결, RDS는 Multi-AZ. 다만 NAT Gateway는 `2a`의 퍼블릭 서브넷에 하나뿐이었고, 프라이빗 서브넷 3개가 모두 같은 라우트 테이블 하나를 공유해 `0.0.0.0/0`을 그 NAT로 보내고 있었다. 라우트 테이블이 하나라 IaC 코드도 깔끔해 보였다.

`2a`에 네트워크 이슈가 발생했다. `2a`의 EC2 3대가 먼저 빠졌다. 여기까지는 설계대로다. 그런데 `2b`, `2c`의 6대도 30초쯤 뒤에 전부 unhealthy로 떨어졌다. 이 서비스의 `/health`는 결제 게이트웨이 상태를 확인하는 외부 HTTPS 호출을 포함하고 있었고, 그 호출이 죽은 NAT로 나가 5초 타임아웃에 걸렸다. 헬스체크 타임아웃 5초에 임계 2회, 즉 unhealthy 판정까지 딱 두 번이면 충분했다.

ALB에 정상 타깃이 0개가 되자 503이 나갔다. 오토스케일링은 unhealthy 인스턴스를 교체하려 새 인스턴스를 띄웠지만, 새 인스턴스의 부트스트랩 스크립트도 같은 NAT로 패키지를 받으러 나가 실패했다. 교체 루프가 돌면서 상황이 더 나빠졌다. AZ 하나의 문제가 전체 장애가 된 경로는 단 하나, **라우트 테이블 하나로 묶인 NAT 하나**였다.

## 실무 적용 포인트

1. NAT Gateway는 **AZ당 1개**, 라우트 테이블도 **AZ당 1개**로 분리한다. 프라이빗 서브넷의 `0.0.0.0/0`은 반드시 같은 AZ의 NAT를 가리켜야 한다. 라우트 테이블 하나를 공유하는 순간 3-AZ는 이름만 3-AZ다.
2. 지금 구성을 한 줄로 검증한다. `aws ec2 describe-nat-gateways --query 'NatGateways[].[NatGatewayId,SubnetId,State]'`로 NAT 개수와 서브넷을 뽑고, 각 프라이빗 라우트 테이블의 기본 경로가 어느 NAT를 향하는지 대조한다. NAT 개수 < AZ 개수면 그게 단일점이다.
3. 헬스체크에서 외부 의존성을 뺀다. `/health`는 프로세스 자체 상태만 보고, 의존성 상태는 `/health/deps` 같은 별도 경로로 나눠 알람용으로만 쓴다. 이 분리는 [[liveness-readiness-startup]]에서 쿠버네티스가 프로브를 나눈 이유와 같다.
4. AWS API를 부르는 트래픽은 VPC 엔드포인트로 NAT를 우회시킨다. S3와 DynamoDB는 **Gateway 엔드포인트가 무료**고 NAT 처리 요금도 안 붙는다. 대개 NAT 트래픽의 상당 부분이 S3라, 이것만으로 요금과 장애 반경이 같이 줄어든다.
5. 서브넷 CIDR는 넉넉히 잡는다. AWS는 서브넷마다 **IP 5개를 예약**한다. `/28`은 사용 가능 IP가 11개뿐이라 EKS 노드나 ENI를 많이 쓰는 워크로드에서 금방 고갈된다. 앱 서브넷은 `/20` 이상을 권장한다.
6. 장애 대조는 AZ 이름이 아니라 AZ ID로 한다. `aws ec2 describe-availability-zones --query 'AvailabilityZones[].[ZoneName,ZoneId]'`로 매핑을 확인해 두고 런북에 박아둔다.
7. AZ 하나를 꺼보는 훈련을 실제로 한다. 해당 AZ의 서브넷 NACL을 전면 차단하거나 ASG에서 AZ를 빼는 방식으로 [[chaos-engineering-intro]] 실험을 돌린다. NAT 단일점은 다이어그램에선 절대 안 보이고, 꺼봐야 보인다.

## 더 깊은 토끼굴

- [[reverse-proxy-l4-l7]] — ALB와 NLB의 cross-zone 동작 차이가 어디서 갈리는가
- [[replication-lag]] — RDS Multi-AZ의 동기 복제가 AZ 간 지연을 어떻게 먹는가
- [[dns-cache-ttl]] — 페일오버 뒤에도 옛 IP로 붙는 클라이언트 문제
- [[liveness-readiness-startup]] — 헬스체크를 나눠야 하는 이유
- [[terraform-state]] — AZ별 서브넷/라우트 테이블을 코드로 강제하는 법

**출처**:
- AWS 공식 문서, NAT gateways: https://docs.aws.amazon.com/vpc/latest/userguide/vpc-nat-gateway.html
- AWS 공식 문서, VPC subnets: https://docs.aws.amazon.com/vpc/latest/userguide/configure-subnets.html
- AWS 공식 문서, AZ IDs for your resources: https://docs.aws.amazon.com/ram/latest/userguide/working-with-az-ids.html
- AWS Well-Architected, Reliability Pillar: https://docs.aws.amazon.com/wellarchitected/latest/reliability-pillar/welcome.html
