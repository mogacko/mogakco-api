# 환경 설정 결정

동일 코드에 환경변수만 주입한다. 로컬은 SQLite와 `.env.local`을 `uv run --env-file`로 읽고, 스테이징·운영은 PostgreSQL과 배포 환경의 비밀값 저장소를 사용한다.

`AUTH_API_BASE_URL`은 HTTPS를 기본으로 하되 로컬의 `localhost`·`127.0.0.1` HTTP만 허용한다. `AUTH_APP_LINK_BASE_URL`은 Universal Link/App Link 검증을 위해 항상 HTTPS여야 한다. 환경 파일에는 비밀값을 커밋하지 않는다.
