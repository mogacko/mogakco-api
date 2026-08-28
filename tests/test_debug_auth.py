import os
from collections.abc import Generator

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.database import create_db_engine, get_db
from app.dependencies.auth import get_current_user
from app.models import User
from app.time import kst_now

DATABASE_URL = os.environ["DATABASE_URL"]


@pytest.fixture
def auth_app() -> Generator[tuple[TestClient, sa.Engine, int, int]]:
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
        active_id, deleted_id = active.id, deleted.id

    app = FastAPI()

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

    yield TestClient(app), engine, active_id, deleted_id
    engine.dispose()


def user_count(engine: sa.Engine) -> int:
    with Session(engine) as session:
        return session.scalar(sa.select(sa.func.count()).select_from(User)) or 0


def test_valid_debug_user_is_injected(
    auth_app: tuple[TestClient, sa.Engine, int, int], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, engine, active_id, _ = auth_app
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("ENABLE_DEBUG_AUTH", "true")

    response = client.post(
        "/protected-write", headers={"X-Debug-User-Id": str(active_id)}
    )

    assert response.status_code == 200
    assert response.json() == {"currentUserId": active_id}
    assert user_count(engine) == 3


@pytest.mark.parametrize("header", [None, "not-an-int", "999999", "deleted"])
def test_invalid_debug_user_rejects_without_writing(
    auth_app: tuple[TestClient, sa.Engine, int, int],
    monkeypatch: pytest.MonkeyPatch,
    header: str | None,
) -> None:
    client, engine, _, deleted_id = auth_app
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("ENABLE_DEBUG_AUTH", "true")
    headers = {} if header is None else {"X-Debug-User-Id": header}
    if header == "deleted":
        headers["X-Debug-User-Id"] = str(deleted_id)
    before = user_count(engine)

    response = client.post("/protected-write", headers=headers)

    assert response.status_code == 401
    assert user_count(engine) == before


@pytest.mark.parametrize(
    ("app_env", "enabled"), [("local", "false"), ("production", "true")]
)
def test_disabled_or_production_debug_auth_rejects_without_writing(
    auth_app: tuple[TestClient, sa.Engine, int, int],
    monkeypatch: pytest.MonkeyPatch,
    app_env: str,
    enabled: str,
) -> None:
    client, engine, active_id, _ = auth_app
    monkeypatch.setenv("APP_ENV", app_env)
    monkeypatch.setenv("ENABLE_DEBUG_AUTH", enabled)
    before = user_count(engine)

    response = client.post(
        "/protected-write", headers={"X-Debug-User-Id": str(active_id)}
    )

    assert response.status_code == 401
    assert user_count(engine) == before
