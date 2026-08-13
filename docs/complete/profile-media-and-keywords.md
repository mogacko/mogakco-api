# 프로필 이미지와 키워드 자동완성

## 이미지 업로드 계약

파일은 서버를 거치지 않고 클라이언트에서 S3로 바로 올라간다. 그래서 검증은 업로드가 끝난 뒤에 한다.

1. `POST /images/upload-url` — `content_type`(`image/jpeg`·`image/png`·`image/webp`)을 받아 `PENDING` 에셋을 만들고 presigned POST를 돌려준다. 정책에 `content-length-range`를 담아 용량 초과는 S3가 거절한다.
2. 클라이언트가 `upload_url`에 `fields`를 붙여 multipart POST로 올린다.
3. `POST /images/{asset_id}/complete` — 서버가 S3에서 앞 512바이트만 `Range`로 받아 파일 시그니처를 확인한다. 통과하면 `READY`, 아니면 S3 객체와 행을 지우고 422 "이미지 파일이 아닙니다."를 반환한다.

확장자와 `Content-Type`은 위조할 수 있으므로 신뢰하지 않는다. JPEG·PNG는 앞 바이트로, WEBP는 RIFF 컨테이너라 `[0:4]`와 `[8:12]`를 함께 본다.

검증이 끝난 본인 소유 에셋만 프로필에 붙일 수 있고, 응답의 `profile_image_url`은 에셋에서 읽어 내려준다. 업로드에 인증이 필요하므로 가입 요청에서는 이미지를 받지 않는다. 가입 후 `PATCH /me`로 연결한다.

## 사용처 표

에셋이 어디에 붙어 있는지는 `media_usages`가 갖는다. 프로필 컬럼(`users.profile_image_asset_id`)은 없앴다.

```
media_usages(asset_id, usage_type, usage_id)
    UNIQUE (usage_type, usage_id)          자리당 이미지 한 장
    asset_id → media_assets ON DELETE CASCADE
```

`usage_id`는 대상 행의 기본 키다. 대상 표가 여럿(`users`, 나중에 행사 등)이라 **외래 키를 걸 수 없다.** 그 대가로 정리 배치가 사용처 종류를 몰라도 된다.

표 이름은 `media_assets`에 맞춰 `media_usages`지만 **컬럼은 `asset_id` 그대로다.** `usage_id`가 이미 대상 쪽 이름이라 이미지 쪽에 쓸 수 없고, `asset_id`는 가리키는 표의 기본 키 이름을 따른다.

```python
referenced = select(MediaUsage.asset_id)   # 새 사용처가 생겨도 그대로다
```

이게 표를 둔 이유다. 컬럼 방식에서는 사용처를 추가하는 사람이 `cleanup.py`의 참조 판단을 같이 고쳐야 했고, 잊으면 **아직 쓰는 이미지가 조용히 지워졌다.**

`users`가 `media_assets`를 더 이상 참조하지 않아 두 표 사이의 순환 외래 키도 사라졌다.

외래 키가 없어서 생기는 구멍은 하나다. **대상이 지워질 때 사용처 행이 남는다.** 지금은 본인 소유 에셋만 붙일 수 있어서, 사용자를 지우면 그 사람의 에셋이 지워지고 사용처 행도 `asset_id` CASCADE로 함께 사라진다. 구멍이 실제로 열리는 것은 둘 중 하나가 생길 때다.

- 남의 에셋을 가리키는 사용처(행사 썸네일 등) — 그 대상을 지우는 코드가 사용처 행도 지운다
- 사용자 간 해시 중복 제거 — 그때 정리 배치에 "대상이 없어진 사용처 행 청소"를 더한다

여러 장을 붙이는 사용처(사진첩 등)가 생기면 `UNIQUE (usage_type, usage_id)`를 푸는 마이그레이션이 필요하다. 지금 아는 사용처는 전부 한 장이라 제약을 건 채로 둔다.

```mermaid
sequenceDiagram
    autonumber
    participant App as Flutter 앱
    participant API as FastAPI
    participant DB as PostgreSQL
    participant S3

    Note over App,S3: 1단계 — 업로드 URL 발급

    App->>API: POST /images/upload-url<br/>{content_type} + Bearer
    activate API
    alt content_type이 jpeg/png/webp가 아님
        API-->>App: 422 "이미지 파일이 아닙니다."
    else 허용 타입
        API->>DB: INSERT media_assets<br/>key=images/{uuid}.{ext}, status=PENDING
        Note right of API: generate_presigned_post는 로컬 서명이라<br/>S3 호출이 아니다<br/>Conditions: Content-Type,<br/>content-length-range 1..max_bytes
        API-->>App: {asset_id, upload_url, fields}
    end
    deactivate API

    Note over App,S3: 2단계 — 클라이언트가 S3로 직접 업로드

    App->>S3: POST multipart<br/>fields 전부 → file 마지막
    activate S3
    alt 정책 위반 (타입 불일치 / 용량 초과)
        S3-->>App: 403
    else 통과
        S3-->>App: 204 No Content
    end
    deactivate S3

    Note over App,S3: 3단계 — 서버가 실제 파일을 검증

    App->>API: POST /images/{asset_id}/complete + Bearer
    activate API
    API->>DB: SELECT media_assets WHERE id
    alt 없음 또는 owner_id ≠ 요청자
        API-->>App: 404 "업로드를 찾을 수 없습니다."
    else 이미 READY
        API-->>App: 200 (멱등, 재검증 없음)
    else PENDING
        API->>S3: GET Object Range: bytes=0-511
        alt 객체 없음 (ClientError)
            S3-->>API: NoSuchKey
            API-->>App: 409 "업로드된 파일이 없습니다."
        else 앞 512바이트 수신
            S3-->>API: 206 + head bytes + ContentRange
            Note right of API: 매직 넘버 검사<br/>JPEG FF D8 FF / PNG 89 50 4E 47…<br/>WEBP는 RIFF + 8~12바이트 "WEBP"
            alt 시그니처 불일치
                API->>S3: DeleteObject
                API->>DB: DELETE media_assets
                API-->>App: 422 "이미지 파일이 아닙니다."
            else 이미지 확인
                API->>DB: UPDATE status=READY,<br/>size_bytes=ContentRange 총 크기
                API-->>App: 200 {id, url, status}
            end
        end
    end
    deactivate API

    Note over App,S3: 4단계 — 프로필에 연결

    App->>API: PATCH /me<br/>{profile_image_asset_id}
    API->>DB: SELECT media_assets
    alt owner_id ≠ 본인 또는 status ≠ READY
        API-->>App: 422 "사용할 수 없는 이미지입니다."
    else
        API->>DB: DELETE + INSERT media_usages<br/>(PROFILE, user_id) 자리를 갈아끼운다
        API-->>App: 200 프로필
    end
```

## 자동완성 계약

`GET /keywords/suggest?kind=FIELD|STACK|INTEREST&prefix=&limit=` — 접두사로 찾고 많이 쓰인 순으로 돌려준다.

프로필의 분야·스택·관심분야는 콤마로 나눠 `user_keywords`에 사용자 단위로 펼쳐 둔다. 노출 기준은 **서로 다른 사용자 5명 이상**이다. 한 사람이 여러 번 저장해도 행이 하나라 기준을 넘지 못한다.

키는 소문자로 맞추고 **구분자(공백·`-`·`_`·`.`)를 지운다.** `Spring Boot`·`SPRINGBOOT`·`spring-boot`·`Spring_Boot`·`spring.boot`가 모두 `springboot` 하나로 모인다. 화면에 보여줄 표기(`display`)는 사용자가 친 그대로 남긴다.

**특수문자를 전부 지우지는 않는다.** 그러면 `C`·`C#`·`C++`가 셋 다 `c`가 되어 버린다. 개발 키워드에서 `#`와 `+`는 뜻이 있다.

검색어도 같은 규칙으로 정규화한다. 그래야 `spring b`가 `springboot`를 찾는다. 구분자만 있는 입력(`---`)은 키가 비어 아무 접두사에나 걸리므로 저장하지 않는다.

임계값 5, 접두사 매칭, 사용자 수 내림차순 정렬, 개수 1~20(기본 10)으로 확정한다. 전부 값만 바꾸면 되는 것들이라 바뀌면 그때 고친다.

**초기에는 결과가 비어 있다.** 서로 다른 5명을 넘겨야 노출이 시작된다. 구분자를 지워 표기 차이는 상당 부분 흡수하지만 **언어가 다르면 못 합친다.** `Spring Boot`와 `스프링부트`는 문자열 규칙으로 이어지지 않는다. 롱테일 키워드는 기준을 오래 못 넘길 수 있다. 감수한다.

한글·영문 별칭은 별칭 표가 있어야 풀린다. 지금 만들면 안 쓰이는 별칭만 쌓이므로, 실제로 갈린 쌍이 데이터에 보이면 그때 넣는다. 그전까지는 둘 다 각각 기준을 넘으면 둘 다 노출된다. 자동완성 목록이 차면 사용자가 기존 표기를 골라 저절로 수렴하는 힘도 있다.

조회는 Redis에서 한다. `user_keywords`는 그대로 원본으로 두고, 노출 대상 집계만 kind별 키 하나에 사본으로 담는다. 정상 상태에서는 자동완성이 PostgreSQL을 조회하지 않는다.

**캐시가 없어도 답은 나온다.** 미스·Redis 장애·`REDIS_URL` 미설정을 구분하지 않고 모두 PostgreSQL 재집계로 넘어간다. Redis가 죽었을 때 자동완성이 통째로 빈 결과가 되는 쪽은 택하지 않았다. 폴백은 조용히 일어나지 않고 경고 로그를 남긴다.

```mermaid
sequenceDiagram
    autonumber
    participant App as Flutter 앱
    participant API as FastAPI
    participant Redis
    participant DB as PostgreSQL

    Note over App,DB: 쓰기 — 저장은 PostgreSQL, 캐시 갱신은 응답을 보낸 뒤

    App->>API: PATCH /me 또는 POST /auth/signup
    activate API
    Note right of API: exclude_unset — 요청에 담겨 온 항목만 다시 기록한다
    loop field→FIELD, stack→STACK, interests→INTEREST
        Note right of API: 콤마 분리 → 공백 정리 → 100자 컷<br/>keyword=소문자에서 구분자 제거 (springboot)<br/>display=사용자가 친 그대로 (Spring Boot)
        API->>DB: DELETE + INSERT user_keywords
    end
    API->>DB: COMMIT (프로필 수정과 한 트랜잭션)
    API-->>App: 200
    deactivate API

    Note over API,Redis: 여기부터 BackgroundTasks — 응답은 이미 나갔다<br/>캐시가 죽어도 저장을 되돌릴 수 없다
    API->>DB: 세 kind 재집계<br/>(요청 세션은 닫혔으므로 새로 연다)
    alt Redis 정상
        API->>Redis: SETEX keywords:v1:{kind} (TTL 24h)
    else Redis 장애
        Note right of API: 경고 로그만 남긴다<br/>다음 저장이나 TTL 만료 때 맞춰진다
    end

    Note over App,DB: 읽기 — 정상 상태에서는 PostgreSQL을 조회하지 않는다 (인증 불필요)

    App->>API: GET /keywords/suggest<br/>?kind=STACK&prefix=py&limit=10
    activate API
    API->>Redis: GET keywords:v1:STACK
    alt 캐시 적중
        Redis-->>API: [[keyword, display], …]
    else 미스 · Redis 장애 · REDIS_URL 미설정
        Note right of API: 셋을 구분하지 않는다<br/>어느 쪽이든 재집계가 답이다
        API->>DB: GROUP BY keyword<br/>HAVING count(*) >= 5<br/>ORDER BY count(*) DESC, keyword
        Note right of DB: 행이 사용자당 1개라<br/>count(*) = 서로 다른 사용자 수
        API->>Redis: SETEX (장애면 건너뛴다)
    end
    Note right of API: 접두사 필터와 limit 절단은 Python에서 한다<br/>정렬은 목록에 이미 반영돼 있다
    API-->>App: 200 ["Python", "PyTorch"]
    deactivate API
```

별도 카운트 컬럼을 두지 않는다. 캐시 갱신도 증분이 아니라 전체 재집계다. 저장 후 갱신과 유실 후 재구축이 같은 동작이라 `app/keywords/cache.py`의 `rebuild()` 하나가 세 곳(백그라운드 갱신, 캐시 미스, 운영 복구 커맨드)에 쓰인다. 동기화가 어긋날 자리를 만들지 않으려는 것이다. 한계와 업그레이드 경로는 그 파일의 `ponytail:` 주석에 적어 뒀다.

운영 절차는 [자동완성 캐시 운영](../operations/keyword-cache.md)에 있다.

## 구현 체크리스트

- [x] `media_assets`, `user_keywords` 표와 마이그레이션 `20260809_0003` (up·down 모두 SQLite에서 확인)
- [x] `media_usages` 표와 마이그레이션 `20260813_0004` (기존 프로필 사진 이관 포함, PostgreSQL에서 up·down 확인)
- [x] presigned POST 발급, `Range` 검증, 실패 시 S3 객체 정리
- [x] 프로필 사진 연결과 소유·상태 검증
- [x] 가입·프로필 수정 시 키워드 동기화
- [x] 참조 없는 이미지 정리 배치 `app.images.cleanup` (DB 행과 S3 객체를 함께 지움, `--dry-run`으로 대상만 확인 가능)
- [x] 미니PC용 systemd 타이머 `deploy/systemd/` (실행은 미검증 — Linux 장비에서 확인 필요)
- [x] 자동완성 Redis 캐시 `app.keywords.cache` (저장 후 백그라운드 갱신, 미스·장애 시 PostgreSQL 폴백, 재구축 커맨드)
- [x] 단위 테스트: 시그니처 판별, 5명 기준, 콤마 분리, 구분자 정규화와 `C#`·`C++` 보존, 접두사에 섞인 와일드카드 문자, 캐시 적중·미스·장애

## 검증 결과

테스트는 기본이 인메모리 SQLite이고, `TEST_DATABASE_URL`을 주면 같은 스위트가 PostgreSQL에서 돈다.

```
docker run -d --name mogakco-pg-test -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=mogakco_test -p 55432:5432 postgres:16-alpine
TEST_DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:55432/mogakco_test uv run pytest
```

PostgreSQL 16에서 확인한 것:

- 스위트 39개 통과, 마이그레이션 `upgrade head` → `downgrade base` → 재적용까지 성공
- `--dry-run`이 S3 자격 증명 없이 고아만 골라낸다. 사용 중인 이미지는 목록에 안 나온다
- `alembic check`로 모델과 마이그레이션 사이 드리프트 없음
- `0004`가 기존 프로필 사진을 사용처 행으로 옮긴다. 사진이 없던 사용자는 행이 생기지 않고, `downgrade`하면 컬럼 값이 그대로 돌아온다
- 에셋을 지우면 사용처 행이 `CASCADE`로 사라져 프로필에서 실제로 떨어진다
- 사용자를 지우면 에셋과 키워드가 `CASCADE`로 함께 지워진다
- `status`·`kind` `CHECK`와 `(user_id, kind, keyword)` 유니크가 실제로 거절한다

S3는 `botocore.stub.Stubber`로 대역을 세워 요청 파라미터(`Bucket`·`Key`·`Range`)까지 확인했다. 테스트가 실제로 잡는지 보려고 시그니처 검사, 삭제 호출, 소유 검증, 정규화, 노출 기준 등 11군데를 일부러 깨뜨려 전부 실패하는 것을 확인했다.

**SQLite는 기본값이 PostgreSQL과 달라 그대로 두면 테스트가 헛돈다.** 픽스처에서 두 가지를 맞춘다.

- `PRAGMA case_sensitive_like = ON` — SQLite의 `LIKE`는 기본이 대소문자 무시라 검색어 정규화가 빠져도 통과해 버린다
- `PRAGMA foreign_keys = ON` — 외래 키가 기본이 꺼져 있어 `ON DELETE CASCADE`가 조용히 무시된다. 켜기 전에는 에셋 삭제가 프로필에서 떨어지는지를 SQLite에서 검증할 수 없었다

Redis는 `get`·`setex` 둘만 쓰므로 `fakeredis`를 붙이지 않고 dict 기반 스텁으로 대역을 세웠다. 여기서도 폴백 제거, 저장 후 갱신 제거, 노출 기준 제거 세 군데를 일부러 깨뜨려 각각 테스트가 실패하는 것을 확인했다.

실제 Redis 7로도 확인했다.

- 미스 → 재집계 후 응답, 그 다음 요청은 `db=None`을 넘겨도 답이 나온다. PostgreSQL을 조회하지 않는다는 증명이다
- 키 내용이 `[["kotlin", "Kotlin"], ["spring boot", "Spring Boot"]]`로 (사용자 수 내림차순, 키워드순) 정렬돼 들어간다. `TTL`은 86400에서 흘러간 만큼
- 닫힌 포트를 `REDIS_URL`로 주면 경고 로그를 남기고 PostgreSQL 결과를 그대로 반환한다

`REDIS_URL`이 없으면 캐시를 아예 쓰지 않으므로 **기존 스위트가 그대로 폴백 경로의 회귀 테스트가 된다.**

## 남은 검증

실제 S3 버킷으로 발급 → 업로드 → 완료 검증 종단 간 확인. 스텁으로는 다음을 확인할 수 없다.

- presigned POST 정책을 S3가 실제로 받아주는지. `Conditions` 형식이 틀리면 모든 업로드가 403이 된다
- `content-length-range`로 용량 초과가 실제로 거절되는지
- IAM 권한 세 가지
- 실제 `ContentRange` 응답 형식

버킷 설정은 [이미지 버킷 운영](../operations/media-bucket.md)에 정리했다. 클라이언트가 Flutter Android·iOS 앱뿐이라 CORS는 설정하지 않는다.

## 보류한 것

- **자동완성 시드 목록** — 초기 빈 결과를 감수하기로 했다.
- **해시 중복 제거** — 중복 이미지가 실제로 쌓이면 검토한다. 우리 업로드는 presigned POST 단일 파트에 상한 5MB라 **S3 E-태그가 곧 MD5**이고, `complete`가 이미 부르는 `GetObject` 응답에 들어 있어 추가 요청이 없다. 버킷 기본 암호화가 SSE-KMS면 E-태그가 MD5가 아니므로 그때는 쓸 수 없다. 사용자 간 중복 제거를 하면 사용처 행이 남는 문제가 같이 오므로 정리 배치도 함께 손봐야 한다.
- **CDN 도메인 확정** — 에셋의 `url`이 `S3_PUBLIC_BASE_URL`로 만들어져 DB에 저장된다. 나중에 바꾸면 이미 저장된 URL과 어긋나므로 버킷을 만들기 전에 정해야 한다.
