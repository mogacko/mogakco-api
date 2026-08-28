import logging
import os
from collections.abc import Generator
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.database import create_db_engine, get_db
from app.dependencies.auth import get_current_user
from app.exceptions import AppException
from app.main import app_exception_response
from app.models import User
from app.time import kst_now

DATABASE_URL = os.environ["DATABASE_URL"]
AUTH_ERROR = {"code": "AUTH_REQUIRED", "message": "로그인이 필요합니다."}


@pytest.fixture
def auth_app() -> Generator[
    tuple[TestClient, sa.Engine, int, UUID, UUID]
]:
    command.upgrade(Config("alembic.ini"), "head")
    engine = create_db_engine(DATABASE_URL)

    with Session(engine) as session:
        session.execute(sa.delete(User))
        active = User(nickname="active-user", region_id=1)
        deleted = User(
            nickname="deleted-user", region_id=1, deleted_at=kst_now()
        )
        session.add_all([active, deleted])
        session.commit()
        active_id = active.id
        active_uuid = active.uuid
        deleted_uuid = deleted.uuid

    app = FastAPI()
    app.add_exception_handler(AppException, app_exception_response)

    def override_db() -> Generator[Session]:
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_db] = override_db

    @app.post("/protected-write")
    def protected_write(
        current_user: User = Depends(get_current_user),
        db: Session = Depends(get_db),
    ) -> dict[str, int]:
        created = User(nickname=f"created-by-{current_user.id}", region_id=1)
        db.add(created)
        db.commit()
        return {"currentUserId": current_user.id}

    with TestClient(app, raise_server_exceptions=False) as client:
        yield client, engine, active_id, active_uuid, deleted_uuid
    engine.dispose()


def user_count(engine: sa.Engine) -> int:
    with Session(engine) as session:
        return session.scalar(sa.select(sa.func.count()).select_from(User)) or 0


def enable_debug_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("ENABLE_DEBUG_AUTH", "true")
    monkeypatch.delenv("DEBUG_DEFAULT_USER_UUID", raising=False)


def test_valid_debug_user_uuid_is_injected(
    auth_app: tuple[TestClient, sa.Engine, int, UUID, UUID],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, engine, active_id, active_uuid, _ = auth_app
    enable_debug_auth(monkeypatch)

    response = client.post(
        "/protected-write",
        headers={"X-Debug-User-Uuid": str(active_uuid)},
    )

    assert response.status_code == 200
    assert response.json() == {"currentUserId": active_id}
    assert user_count(engine) == 3


@pytest.mark.parametrize(
    "user_uuid", ["empty", "malformed", "missing", "deleted"]
)
def test_invalid_debug_user_uuid_rejects_without_writing(
    auth_app: tuple[TestClient, sa.Engine, int, UUID, UUID],
    monkeypatch: pytest.MonkeyPatch,
    user_uuid: str,
) -> None:
    client, engine, _, _, deleted_uuid = auth_app
    enable_debug_auth(monkeypatch)
    value = {
        "empty": "",
        "malformed": "not-a-uuid",
        "missing": str(uuid4()),
        "deleted": str(deleted_uuid),
    }[user_uuid]
    before = user_count(engine)

    response = client.post(
        "/protected-write",
        headers={"X-Debug-User-Uuid": value},
    )

    assert response.status_code == 401
    assert response.json() == AUTH_ERROR
    assert user_count(engine) == before


def test_missing_header_uses_configured_default_uuid(
    auth_app: tuple[TestClient, sa.Engine, int, UUID, UUID],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, engine, active_id, active_uuid, _ = auth_app
    enable_debug_auth(monkeypatch)
    monkeypatch.setenv("DEBUG_DEFAULT_USER_UUID", str(active_uuid))

    response = client.post("/protected-write")

    assert response.status_code == 200
    assert response.json() == {"currentUserId": active_id}
    assert user_count(engine) == 3


def test_missing_header_and_default_rejects_without_writing(
    auth_app: tuple[TestClient, sa.Engine, int, UUID, UUID],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, engine, _, _, _ = auth_app
    enable_debug_auth(monkeypatch)
    before = user_count(engine)

    response = client.post("/protected-write")

    assert response.status_code == 401
    assert response.json() == AUTH_ERROR
    assert user_count(engine) == before


def test_malformed_default_uuid_is_configuration_error_without_raw_value(
    auth_app: tuple[TestClient, sa.Engine, int, UUID, UUID],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    client, engine, _, _, _ = auth_app
    enable_debug_auth(monkeypatch)
    raw_value = "not-a-valid-default-uuid"
    monkeypatch.setenv("DEBUG_DEFAULT_USER_UUID", raw_value)
    before = user_count(engine)

    logging.getLogger("app.dependencies.auth").disabled = False
    with caplog.at_level(logging.ERROR, logger="app.dependencies.auth"):
        response = client.post("/protected-write")

    assert response.status_code == 500
    assert response.json() == {
        "code": "CONFIGURATION_ERROR",
        "message": "서버 설정 오류가 발생했습니다.",
    }
    assert "DEBUG_DEFAULT_USER_UUID" in caplog.text
    assert raw_value not in caplog.text
    assert user_count(engine) == before


@pytest.mark.parametrize(
    ("app_env", "enabled"), [("local", "false"), ("production", "true")]
)
def test_disabled_or_production_debug_auth_rejects_without_writing(
    auth_app: tuple[TestClient, sa.Engine, int, UUID, UUID],
    monkeypatch: pytest.MonkeyPatch,
    app_env: str,
    enabled: str,
) -> None:
    client, engine, _, active_uuid, _ = auth_app
    monkeypatch.setenv("APP_ENV", app_env)
    monkeypatch.setenv("ENABLE_DEBUG_AUTH", enabled)
    monkeypatch.setenv("DEBUG_DEFAULT_USER_UUID", str(active_uuid))
    before = user_count(engine)

    response = client.post(
        "/protected-write",
        headers={"X-Debug-User-Uuid": str(active_uuid)},
    )

    assert response.status_code == 401
    assert response.json() == AUTH_ERROR
    assert user_count(engine) == before
