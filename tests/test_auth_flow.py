from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.config import TokenSettings
from app.auth.service import create_login_code
from app.auth.model import LoginCode
from app.db import get_db
from app.main import app
from app.terms.model import Term, TermVersion


@pytest.fixture
def client(engine, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AUTH_JWT_SECRET", "12345678901234567890123456789012")
    monkeypatch.setenv("AUTH_ACCESS_TOKEN_TTL_SECONDS", "900")
    monkeypatch.setenv("AUTH_LOGIN_CODE_TTL_SECONDS", "60")

    def override_db():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    yield TestClient(app), engine
    app.dependency_overrides.clear()


def test_signup_and_token_rotation(client):
    test_client, engine = client
    with Session(engine) as db:
        term = Term(code="SERVICE", required=True)
        db.add(term)
        db.flush()
        version = TermVersion(term_id=term.id, version="v1", content="service", effective_at=datetime.now(UTC))
        db.add(version)
        db.commit()
        code = create_login_code(db, None, TokenSettings.from_env(), "GOOGLE", "google-user")
        term_version_id = version.id

    signup = test_client.post(
        "/auth/signup",
        json={"code": code, "nickname": "hannah", "activity_region": "SEOUL", "agreed_term_version_ids": [term_version_id]},
    )
    assert signup.status_code == 200
    refresh_token = signup.json()["refresh_token"]
    assert test_client.post("/auth/signup", json={"code": code, "nickname": "other", "activity_region": "SEOUL", "agreed_term_version_ids": [term_version_id]}).status_code == 401

    with Session(engine) as db:
        duplicate_code = create_login_code(db, None, TokenSettings.from_env(), "GOOGLE", "google-user")
    duplicate = test_client.post(
        "/auth/signup",
        json={"code": duplicate_code, "nickname": "other", "activity_region": "SEOUL", "agreed_term_version_ids": [term_version_id]},
    )
    assert duplicate.status_code == 409

    refreshed = test_client.post("/auth/refresh", json={"refresh_token": refresh_token})
    assert refreshed.status_code == 200
    assert test_client.post("/auth/refresh", json={"refresh_token": refresh_token}).status_code == 401
    assert test_client.post("/auth/logout", json={"refresh_token": refreshed.json()["refresh_token"]}).status_code == 204


def test_signup_rejects_missing_required_terms_and_expired_code(client):
    test_client, engine = client
    with Session(engine) as db:
        term = Term(code="SERVICE", required=True)
        db.add(term); db.flush()
        version = TermVersion(term_id=term.id, version="v1", content="service", effective_at=datetime.now(UTC))
        db.add(version); db.commit()
        code = create_login_code(db, None, TokenSettings.from_env(), "KAKAO", "kakao-user")
        expired_code = create_login_code(db, None, TokenSettings.from_env(), "KAKAO", "expired")
        expired = db.scalar(select(LoginCode).where(LoginCode.provider_user_id == "expired"))
        assert expired is not None
        expired.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        db.commit()

    missing_terms = test_client.post("/auth/signup", json={"code": code, "nickname": "hannah", "activity_region": "SEOUL", "agreed_term_version_ids": []})
    assert missing_terms.status_code == 422
    assert test_client.post("/auth/token", json={"code": expired_code}).status_code == 401
