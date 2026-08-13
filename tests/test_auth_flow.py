from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.auth.config import TokenSettings
from app.auth.service import create_login_code
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


def seed_required_term(engine) -> int:
    with Session(engine) as db:
        term = Term(code="SERVICE", required=True)
        db.add(term)
        db.flush()
        version = TermVersion(term_id=term.id, version="v1", content="service", effective_at=datetime.now(UTC))
        db.add(version)
        db.commit()
        return version.id


def test_signup_and_token_rotation(client):
    test_client, engine = client
    term_version_id = seed_required_term(engine)
    code = create_login_code(TokenSettings.from_env(), "GOOGLE", "google-user")

    signup = test_client.post(
        "/auth/signup",
        json={"code": code, "nickname": "hannah", "activity_region": "SEOUL", "agreed_term_version_ids": [term_version_id]},
    )
    assert signup.status_code == 200
    refresh_token = signup.json()["refresh_token"]
    # 가입에 성공한 코드는 소진된다.
    assert test_client.post("/auth/signup", json={"code": code, "nickname": "other", "activity_region": "SEOUL", "agreed_term_version_ids": [term_version_id]}).status_code == 401

    duplicate_code = create_login_code(TokenSettings.from_env(), "GOOGLE", "google-user")
    duplicate = test_client.post(
        "/auth/signup",
        json={"code": duplicate_code, "nickname": "other", "activity_region": "SEOUL", "agreed_term_version_ids": [term_version_id]},
    )
    assert duplicate.status_code == 409

    refreshed = test_client.post("/auth/refresh", json={"refresh_token": refresh_token})
    assert refreshed.status_code == 200
    assert test_client.post("/auth/refresh", json={"refresh_token": refresh_token}).status_code == 401
    assert test_client.post("/auth/logout", json={"refresh_token": refreshed.json()["refresh_token"]}).status_code == 204


def test_signup_rejects_expired_code(client, signup_codes):
    test_client, engine = client
    term_version_id = seed_required_term(engine)

    expired_code = create_login_code(TokenSettings.from_env(), "KAKAO", "expired")
    # 만료는 Redis TTL이 처리하므로 "만료된 상태"는 곧 "키가 없는 상태"다.
    signup_codes.store.clear()

    expired = test_client.post(
        "/auth/signup",
        json={"code": expired_code, "nickname": "hannah", "activity_region": "SEOUL", "agreed_term_version_ids": [term_version_id]},
    )
    assert expired.status_code == 401


def test_login_code_carries_the_configured_ttl(signup_codes, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AUTH_JWT_SECRET", "12345678901234567890123456789012")
    monkeypatch.setenv("AUTH_ACCESS_TOKEN_TTL_SECONDS", "900")
    monkeypatch.setenv("AUTH_LOGIN_CODE_TTL_SECONDS", "45")

    create_login_code(TokenSettings.from_env(), "KAKAO", "kakao-user")

    # 만료를 Redis에 맡긴 이상 TTL을 실제로 걸었는지가 유일하게 검증할 수 있는 지점이다.
    assert list(signup_codes.ttls.values()) == [45]


def test_rejected_signup_keeps_the_code_usable(client):
    test_client, engine = client
    term_version_id = seed_required_term(engine)
    code = create_login_code(TokenSettings.from_env(), "KAKAO", "kakao-user")

    missing_terms = test_client.post(
        "/auth/signup",
        json={"code": code, "nickname": "hannah", "activity_region": "SEOUL", "agreed_term_version_ids": []},
    )
    assert missing_terms.status_code == 422

    # 검증에서 막힌 요청은 코드를 소진하지 않는다. 약관만 채워 같은 코드로 다시 시도할 수 있다.
    retried = test_client.post(
        "/auth/signup",
        json={"code": code, "nickname": "hannah", "activity_region": "SEOUL", "agreed_term_version_ids": [term_version_id]},
    )
    assert retried.status_code == 200
