# 프로필 이미지와 키워드 자동완성

## 이미지 업로드 계약

파일은 서버를 거치지 않고 클라이언트에서 S3로 바로 올라간다. 그래서 검증은 업로드가 끝난 뒤에 한다.

1. `POST /images/upload-url` — `content_type`(`image/jpeg`·`image/png`·`image/webp`)을 받아 `PENDING` 에셋을 만들고 presigned POST를 돌려준다. 정책에 `content-length-range`를 담아 용량 초과는 S3가 거절한다.
2. 클라이언트가 `upload_url`에 `fields`를 붙여 multipart POST로 올린다.
3. `POST /images/{asset_id}/complete` — 서버가 S3에서 앞 512바이트만 `Range`로 받아 파일 시그니처를 확인한다. 통과하면 `READY`, 아니면 S3 객체와 행을 지우고 422 "이미지 파일이 아닙니다."를 반환한다.

확장자와 `Content-Type`은 위조할 수 있으므로 신뢰하지 않는다. JPEG·PNG는 앞 바이트로, WEBP는 RIFF 컨테이너라 `[0:4]`와 `[8:12]`를 함께 본다.

`users.profile_image_url`은 `profile_image_asset_id` 외래 키로 바뀌었다. 검증이 끝난 본인 소유 에셋만 붙일 수 있고, 응답의 `profile_image_url`은 에셋에서 읽어 내려준다. 업로드에 인증이 필요하므로 가입 요청에서는 이미지를 받지 않는다. 가입 후 `PATCH /me`로 연결한다.

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
        API->>DB: UPDATE users.profile_image_asset_id
        API-->>App: 200 프로필
    end
```

## 자동완성 계약

`GET /keywords/suggest?kind=FIELD|STACK|INTEREST&prefix=&limit=` — 접두사로 찾고 많이 쓰인 순으로 돌려준다.

프로필의 분야·스택·관심분야는 콤마로 나누고 공백을 정리해 `user_keywords`에 사용자 단위로 펼쳐 둔다. 소문자로 맞춰 저장하므로 `React`·`react`·`REACT`는 한 키워드로 합쳐진다. 노출 기준은 **서로 다른 사용자 5명 이상**이다. 한 사람이 여러 번 저장해도 행이 하나라 기준을 넘지 못한다.

```mermaid
sequenceDiagram
    autonumber
    participant App as Flutter 앱
    participant API as FastAPI
    participant DB as PostgreSQL

    Note over App,DB: 쓰기 — 프로필 저장 시 사용자 단위로 펼쳐 기록

    App->>API: PATCH /me<br/>{field, stack, interests, …}
    activate API
    Note right of API: exclude_unset — 요청에 담겨 온<br/>항목만 다시 기록한다
    loop field→FIELD, stack→STACK, interests→INTEREST
        Note right of API: 콤마 분리 → 공백 정리 → 100자 컷<br/>소문자 키로 중복 제거<br/>keyword=소문자, display=원본 표기
        API->>DB: DELETE user_keywords<br/>WHERE user_id AND kind
        API->>DB: INSERT user_keywords (재기록)
    end
    API->>DB: COMMIT (프로필 수정과 한 트랜잭션)
    API-->>App: 200 프로필
    deactivate API

    Note over App,DB: 읽기 — 조회 시점에 집계 (인증 불필요)

    App->>API: GET /keywords/suggest<br/>?kind=STACK&prefix=py&limit=10
    activate API
    Note right of API: prefix도 공백 정리 + 소문자<br/>startswith(autoescape) — %, _ 이스케이프
    API->>DB: SELECT max(display)<br/>WHERE kind AND keyword LIKE 'py%'<br/>GROUP BY keyword<br/>HAVING count(*) >= 5<br/>ORDER BY count(*) DESC, keyword<br/>LIMIT 10
    Note right of DB: 행이 사용자당 1개라<br/>count(*) = 서로 다른 사용자 수<br/>5명 미만 키워드는 노출 안 함
    DB-->>API: ["Python", "PyTorch"]
    API-->>App: 200 ["Python", "PyTorch"]
    deactivate API
```

별도 카운트 컬럼을 두지 않고 조회할 때마다 집계한다. 동기화가 어긋날 자리가 없다. 한계와 업그레이드 경로는 `app/keywords/service.py`의 `ponytail:` 주석에 적어 뒀다.

## 구현 체크리스트

- [x] `media_assets`, `user_keywords` 표와 마이그레이션 `20260809_0003` (up·down 모두 SQLite에서 확인)
- [x] presigned POST 발급, `Range` 검증, 실패 시 S3 객체 정리
- [x] `users.profile_image_asset_id` 전환과 소유·상태 검증
- [x] 가입·프로필 수정 시 키워드 동기화
- [x] 참조 없는 이미지 정리 배치 `app.images.cleanup` (DB 행과 S3 객체를 함께 지움)
- [x] 단위 테스트: 시그니처 판별, 5명 기준, 콤마 분리, 대소문자 통합, 와일드카드 이스케이프

## 검증 결과

테스트는 기본이 인메모리 SQLite이고, `TEST_DATABASE_URL`을 주면 같은 스위트가 PostgreSQL에서 돈다.

```
docker run -d --name mogakco-pg-test -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=mogakco_test -p 55432:5432 postgres:16-alpine
TEST_DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:55432/mogakco_test uv run pytest
```

PostgreSQL 16에서 확인한 것:

- 스위트 20개 통과, 마이그레이션 `upgrade head` → `downgrade base` → 재적용까지 성공
- `alembic check`로 모델과 마이그레이션 사이 드리프트 없음
- `users` ↔ `media_assets` 순환 외래 키가 `ON DELETE SET NULL`로 생성됨. 에셋을 지우면 프로필에서 실제로 떨어진다
- 사용자를 지우면 에셋과 키워드가 `CASCADE`로 함께 지워진다
- `status`·`kind` `CHECK`와 `(user_id, kind, keyword)` 유니크가 실제로 거절한다

S3는 `botocore.stub.Stubber`로 대역을 세워 요청 파라미터(`Bucket`·`Key`·`Range`)까지 확인했다. 테스트가 실제로 잡는지 보려고 시그니처 검사, 삭제 호출, 소유 검증, 정규화, 노출 기준 등 11군데를 일부러 깨뜨려 전부 실패하는 것을 확인했다.

**SQLite의 `LIKE`는 기본이 대소문자 무시라 PostgreSQL과 다르다.** 검색어 정규화가 빠져도 SQLite에서는 통과해 버리므로, 테스트 픽스처에서 `PRAGMA case_sensitive_like = ON`으로 기준을 맞춘다.

## 남은 검증

실제 S3 버킷으로 발급 → 업로드 → 완료 검증 종단 간 확인. 스텁으로는 다음을 확인할 수 없다.

- presigned POST 정책을 S3가 실제로 받아주는지. `Conditions` 형식이 틀리면 모든 업로드가 403이 된다
- `content-length-range`로 용량 초과가 실제로 거절되는지
- IAM 권한 세 가지
- 실제 `ContentRange` 응답 형식

버킷 설정은 [이미지 버킷 운영](../operations/media-bucket.md)에 정리했다. 클라이언트가 Flutter Android·iOS 앱뿐이라 CORS는 설정하지 않는다.

## 보류한 것

- **Redis 캐시** — 사용자 수천 명 규모에서 인덱스 조회로 충분하다. 조회가 실제로 느려지면 그때 넣는다.
- **자동완성 시드 목록** — 초기에는 5명 기준을 넘는 키워드가 없어 결과가 비어 있다. 감수하기로 했다.
- **사용처 표(`asset_usages`)** — 지금 이미지를 쓰는 곳이 프로필뿐이다. 행사 썸네일 같은 두 번째 사용처가 생기면 추가한다.
- **해시 중복 제거** — 중복 이미지가 실제로 쌓이면 검토한다. S3 E-태그는 멀티파트 업로드에서 MD5가 아니므로 그대로 쓸 수 없다.
- **CDN 도메인 확정** — 에셋의 `url`이 `S3_PUBLIC_BASE_URL`로 만들어져 DB에 저장된다. 나중에 바꾸면 이미 저장된 URL과 어긋나므로 버킷을 만들기 전에 정해야 한다.
