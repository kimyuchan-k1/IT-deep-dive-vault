---
title: Prepared Statement만 쓰면 SQL Injection은 끝났다고 믿었는데, 정렬 컬럼 한 줄로 통째로 뚫렸다
date: 2026-06-22
day: 20
category: security
tags: [sql-injection, prepared-statement, parameterized-query, orm, security]
related: ["[[jwt-vs-session]]", "[[rbac-abac-rebac]]", "[[csrf-samesite]]", "[[connection-pool-sizing]]", "[[btree-index-internals]]", "[[secret-management]]"]
difficulty: 3
short_text: |
  ⚠️ [Day 20] Prepared Statement도 SQLi 못 막는다
  오해: 바인딩 쓰면 끝
  실제: 식별자·ORDER BY는 바인딩 불가→조립
  "정렬 컬럼으로 뚫렸다"
  📖 https://github.com/kimyuchan-k1/IT-deep-dive-vault/blob/main/posts/2026-06-22-sqli-prepared-stmt.md
---

# Prepared Statement만 쓰면 SQL Injection은 끝났다고 믿었는데, 정렬 컬럼 한 줄로 통째로 뚫렸다

## 흔한 오해

"SQL Injection? 그건 옛날 얘기 아닌가. `"SELECT * FROM users WHERE id = " + id`처럼 문자열로 쿼리를 이어 붙이니까 뚫린 거고, Prepared Statement(바인딩 파라미터)만 쓰면 끝이다. `?`에 값을 넣으면 드라이버가 알아서 이스케이프해주니까, 사용자 입력에 `' OR 1=1 --`를 넣어도 그냥 문자열 값으로 취급돼서 안전하다. ORM(JPA, Prepared Statement 래퍼)을 쓰면 자동으로 다 바인딩되니 신경 쓸 일도 없다."

SQLi를 한 번 배운 사람은 거의 다 이렇게 정리한다. 그리고 절반은 맞다. **값(value) 자리의 인젝션은 Prepared Statement가 거의 완벽하게 막는다.** OWASP도 첫 번째 방어로 parameterized query를 꼽는다. 그래서 "바인딩 = SQLi 끝"이라는 공식이 정설처럼 돈다.

**문제는 SQL에는 파라미터로 바인딩할 수 없는 자리가 있다는 것이다.** 그리고 그 자리는 결국 문자열로 조립할 수밖에 없어서, 바인딩을 아무리 철저히 써도 거기서 뚫린다.

## 실제 원리

Prepared Statement가 안전한 이유부터 정확히 짚자. 핵심은 이스케이프가 아니라 **"쿼리 구조와 데이터를 분리해서 컴파일한다"**는 데 있다.

### 바인딩이 막는 것 — 데이터와 코드의 분리

Prepared Statement는 `?` 자리를 비운 채 쿼리를 먼저 DB에 보내 **파싱·플랜 컴파일**을 끝낸다. 그 다음 값을 따로 보낸다. 이 순간 쿼리의 **문법 트리(구조)는 이미 확정**돼 있다. 나중에 들어온 값이 `' OR 1=1 --`이든 뭐든, 그건 트리의 한 리프 노드(상수)로만 박힌다. **값이 새로운 SQL 토큰(연산자, 키워드)으로 재해석될 길 자체가 닫혀 있다.** 이게 이스케이프(따옴표 escaping)보다 본질적으로 강한 이유다 — 이스케이프는 "위험 문자를 잘 가공한다"는 약속이고, 바인딩은 "값은 절대 코드가 되지 않는다"는 구조적 보장이다.

### 바인딩이 못 막는 것 — 식별자 자리

여기가 핵심이다. **파라미터는 오직 "값(literal)" 자리에만 들어간다.** SQL 표준에서 `?`로 바인딩 가능한 건 WHERE의 비교값, INSERT의 VALUES, LIMIT 인자 같은 **데이터**뿐이다. 다음 자리들은 **문법적으로 바인딩이 불가능**하다:

- **컬럼명·테이블명** (식별자): `ORDER BY ?`는 동작하지 않거나, 된다 해도 의미가 다르다
- **`ORDER BY` 정렬 컬럼**, **`ASC`/`DESC` 방향**
- **`LIMIT`/`OFFSET`을 일부 드라이버에서** (구버전)
- **`IN (?, ?, ...)`의 가변 개수** — 개수만큼 `?`를 동적으로 만들어야 함
- **SQL 키워드·연산자** 일체

왜 안 되나. 식별자는 **쿼리 구조의 일부**이기 때문이다. 어떤 컬럼으로 정렬하느냐는 실행 플랜([[btree-index-internals]]의 인덱스 선택)을 바꾼다. 즉 식별자는 "데이터"가 아니라 "코드"라서, 컴파일이 끝나기 전에 확정돼야 한다. 그래서 드라이버는 식별자 자리에 `?`를 허용하지 않는다.

### 그래서 어떻게 뚫리나

식별자를 동적으로 바꿔야 하는 화면(정렬 가능한 테이블, 동적 검색)은 결국 이렇게 짠다:

```java
// 값은 바인딩, 그런데 정렬 컬럼은 문자열로 조립
String sql = "SELECT * FROM orders WHERE user_id = ? ORDER BY " + sortColumn;
```

`user_id`는 안전하게 바인딩됐다. 그런데 `sortColumn`이 사용자 입력(`?sort=created_at`)에서 그대로 왔다면, **그 자리는 1세대 SQLi와 똑같이 벌거벗은 문자열 연결**이다. 바인딩을 99% 잘 써도, 식별자 1%에서 전체가 무너진다.

## 현장 시나리오

한 커머스 사의 주문 목록 API가 있었다. 코드 리뷰에서 "모든 쿼리가 Prepared Statement"라고 확인돼 SQLi 항목은 통과 처리됐다. 인과 사슬은 이랬다:

- 목록 화면에 **정렬 기능**이 있었다. `GET /orders?sort=created_at&dir=desc`. 백엔드는 `WHERE user_id = ?`는 바인딩하고, `ORDER BY ${sort} ${dir}`는 **문자열로 붙였다.** 바인딩이 안 되는 자리라 어쩔 수 없다고 판단했다
- 한 공격자가 `sort` 파라미터에 서브쿼리를 넣었다: `?sort=(CASE WHEN (SELECT substr(password,1,1) FROM admins LIMIT 1)='a' THEN id ELSE user_id END)`
- 이 쿼리는 에러를 안 낸다. **정렬 순서만 바뀐다.** 결과 목록의 순서가 바뀌느냐로, 공격자는 관리자 비밀번호 해시를 **한 글자씩 참/거짓으로 추출**했다 (Blind SQLi, boolean-based)
- WAF는 `' OR 1=1 --` 같은 고전 패턴만 보고 있었는데, 이건 멀쩡한 컬럼식처럼 생겨서 통과했다. 응답도 200이라 로그·알람에 안 걸렸다
- 며칠에 걸쳐 자동화 스크립트가 정렬 순서 변화를 이진 탐색하며 관리자 테이블을 통째로 덤프했다

수정은 **정렬 컬럼을 입력으로 받지 않는 것**이었다. 허용된 컬럼명을 서버의 **화이트리스트 맵**(`{"date": "created_at", "amount": "total_price"}`)에 두고, 사용자 입력은 그 맵의 **키 조회**로만 쓰게 바꿨다. 입력값이 맵에 없으면 기본 컬럼으로 폴백했다. 원인은 "바인딩을 안 했다"가 아니라, **바인딩이 불가능한 자리를 사용자 입력으로 채웠다**는 것이었다. Prepared Statement는 값 자리만 지킨다 — 식별자 자리는 개발자가 화이트리스트로 지켜야 한다.

## 실무 적용 포인트

1. **식별자(컬럼·테이블·정렬)는 절대 입력을 직접 쓰지 말고 화이트리스트 매핑**: 허용 컬럼을 서버 코드의 고정된 `Set`/`Map`에 두고, 입력은 **키 조회**로만 받아라. 매칭 실패 시 예외 또는 기본값 폴백. 이스케이프로 때우려 하지 마라 — 식별자엔 표준 이스케이프 규칙이 없다.
2. **`ORDER BY`/`LIMIT`도 정수·열거형으로 강제**: 정렬 방향은 `dir == "desc" ? "DESC" : "ASC"`처럼 **코드 분기**로 결정하라. `OFFSET`/`LIMIT`은 `Integer.parseInt`로 파싱해 정수임을 보장한 뒤 넣어라.
3. **ORM도 raw fragment에선 안 막아준다**: JPA `@Query`의 native SQL, JPQL의 문자열 연결, QueryDSL `Expressions.stringTemplate`, Hibernate `createNativeQuery`에 입력을 이어붙이면 똑같이 뚫린다. ORM = 안전이 아니라, **바인딩을 쓴 경로만** 안전이다.
4. **`IN` 절은 동적 `?` 생성으로**: `IN (${list})`로 붙이지 말고, 리스트 크기만큼 `?`를 만들어(`IN (?,?,?)`) 각각 바인딩하라. 대량이면 임시 테이블·배열 타입(Postgres `= ANY(?)`)을 써라.
5. **최소 권한 DB 계정으로 폭발 반경 차단**: 앱 계정에 `admins` 테이블 `SELECT` 권한을 안 주면, 식별자 SQLi가 나도 추출 대상이 없다([[rbac-abac-rebac]]의 최소 권한 원칙을 DB 계정에도). 읽기 전용 화면은 읽기 전용 계정으로.
6. **Blind SQLi는 응답 코드로 안 잡힌다 — 쿼리 단위 모니터링**: 200 OK에 정렬만 바뀌는 공격은 WAF·상태코드 알람을 우회한다. 동일 엔드포인트에 정렬 파라미터가 **비정상적으로 자주 변하는 패턴**, 쿼리 길이 급증을 DB 프록시 레벨에서 봐라.

## 더 깊은 토끼굴

- OWASP 공식 — [SQL Injection Prevention Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/SQL_Injection_Prevention_Cheat_Sheet.html): parameterized query를 1순위 방어로, 식별자는 화이트리스트로 다루라는 1차 근거
- OWASP 공식 — [Testing for SQL Injection (WSTG)](https://owasp.org/www-project-web-security-testing-guide/latest/4-Web_Application_Security_Testing/07-Input_Validation_Testing/05-Testing_for_SQL_Injection): Blind/boolean-based 추출 기법의 표준 분류
- PostgreSQL 공식 — [PREPARE](https://www.postgresql.org/docs/current/sql-prepare.html): 파라미터가 "값" 자리에만 허용되는 이유(플랜 컴파일 분리)
- [[btree-index-internals]]: 정렬 컬럼이 왜 실행 플랜을 바꾸는가 — 식별자가 "코드"인 이유
- [[rbac-abac-rebac]]: DB 계정 최소 권한으로 SQLi의 폭발 반경을 줄이는 인가 설계
- [[jwt-vs-session]]: 인증을 뚫는 또 다른 입력 경로 — 토큰 검증의 함정
