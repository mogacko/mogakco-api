# 프로젝트 지침

아키텍처 또는 애플리케이션 코드를 변경하기 전, 반드시 `docs/decisions/architecture.md`를 읽고 그 결정사항을 따른다.

- 일반 CRUD에는 헥사고날 아키텍처나 `repository.py`를 추가하지 않는다. 포트·어댑터를 둘 기준은 `architecture.md`에 있다.
- 이미지를 쓰는 기능은 `asset_usages`에 사용처 행을 남긴다. 남기지 않으면 정리 배치가 24시간 뒤 그 이미지를 지운다.
- 기능을 추가하거나 고치면 `tests/test_<기능>.py`에 테스트를 함께 쓴다. 표는 `conftest.py`의 `engine` 픽스처가 만든다.
- 커밋 메시지는 영어로 쓴다. `feat(auth): add Google native sign-in`처럼 Conventional Commits 형식을 따른다.

## 명령어

로컬은 SQLite와 `.env.local`을 쓴다. `.env.example`을 복사해서 만든다.

| 목적 | 명령 |
| --- | --- |
| 테스트 | `uv run pytest` |
| 개발 서버 | `uv run --env-file .env.local fastapi dev app/main.py` |
| 마이그레이션 적용 | `uv run --env-file .env.local alembic upgrade head` |
| 마이그레이션 생성 | `uv run --env-file .env.local alembic revision --autogenerate -m "설명"` |
| 고아 이미지 정리 | `uv run --env-file .env.local python -m app.images.cleanup --dry-run` |

- 테스트는 인메모리 SQLite를 직접 만들어 쓰므로 환경 파일이 필요 없다. 운영과 같은 PostgreSQL로 돌리려면 `TEST_DATABASE_URL`을 준다.
- Windows 콘솔(cp949)에서 `fastapi dev`가 배너 이모지 때문에 죽으면 `PYTHONIOENCODING=utf-8`을 앞에 붙인다.

## 문서

`architecture.md` 말고는 전부 해당할 때만 읽는다. 미리 다 읽지 않는다.

| 언제 | 무엇을 |
| --- | --- |
| 계획 문서를 쓰거나 완료 처리할 때 | `docs/decisions/planning-documents.md` |
| 라우트를 추가·수정할 때 | `docs/decisions/api-documentation.md` |
| 인증이 걸린 기능을 만들 때 | `docs/decisions/authentication.md` |
| 환경 변수나 주기 실행을 건드릴 때 | `docs/decisions/environment-configuration.md` |
| 작업과 맞는 진행 중인 계획이 있을 때 | `docs/plan/` |
| 기존 기능의 계약과 동작을 확인할 때 | `docs/complete/` |
| 버킷·캐시 운영 절차가 필요할 때 | `docs/operations/` |
