import pytest

from app.auth.config import SettingsError, TokenSettings


def test_token_settings_rejects_short_secret(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AUTH_JWT_SECRET", "short")
    monkeypatch.setenv("AUTH_ACCESS_TOKEN_TTL_SECONDS", "900")
    monkeypatch.setenv("AUTH_LOGIN_CODE_TTL_SECONDS", "60")

    with pytest.raises(SettingsError, match="32자"):
        TokenSettings.from_env()
