from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.auth import service
from app.auth.model import SocialAccount
from app.db import get_db
from app.main import app
from app.users.model import User


@pytest.fixture
def client(engine, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AUTH_JWT_SECRET", "12345678901234567890123456789012")
    monkeypatch.setenv("AUTH_ACCESS_TOKEN_TTL_SECONDS", "900")
    monkeypatch.setenv("AUTH_LOGIN_CODE_TTL_SECONDS", "60")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_IDS", "google-web-client-id")
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    monkeypatch.setattr(
        service._google_jwks,
        "get_signing_key_from_jwt",
        lambda _token: SimpleNamespace(key=private_key.public_key()),
    )

    def override_db():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    yield TestClient(app), private_key, engine
    app.dependency_overrides.clear()


def _google_token(private_key, audience: str = "google-web-client-id") -> str:
    return jwt.encode(
        {
            "sub": "google-user",
            "aud": audience,
            "iss": "https://accounts.google.com",
            "exp": datetime.now(UTC) + timedelta(minutes=5),
        },
        private_key,
        algorithm="RS256",
    )


def test_google_social_login_returns_signup_code_or_tokens_and_rejects_wrong_audience(client):
    test_client, private_key, engine = client

    response = test_client.post("/auth/social-login", json={"provider": "GOOGLE", "id_token": _google_token(private_key)})
    assert response.status_code == 200
    assert response.json()["signup_required"] is True
    assert response.json()["code"]
    with Session(engine) as db:
        user = User(nickname="hannah", activity_region="SEOUL")
        db.add(user)
        db.flush()
        db.add(SocialAccount(user_id=user.id, provider="GOOGLE", provider_user_id="google-user"))
        db.commit()
    signed_in = test_client.post("/auth/social-login", json={"provider": "GOOGLE", "id_token": _google_token(private_key)})
    assert signed_in.status_code == 200
    assert signed_in.json()["signup_required"] is False
    assert signed_in.json()["access_token"]
    assert signed_in.json()["refresh_token"]
    assert test_client.post(
        "/auth/social-login",
        json={"provider": "GOOGLE", "id_token": _google_token(private_key, "wrong-client-id")},
    ).status_code == 401
    assert test_client.post("/auth/social-login", json={"provider": "APPLE", "id_token": _google_token(private_key)}).status_code == 404
