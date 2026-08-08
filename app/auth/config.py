import os
from dataclasses import dataclass
from urllib.parse import urlparse


class SettingsError(ValueError):
    """필수 인증 설정이 없거나 형식이 맞지 않을 때 발생한다."""


def _required(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise SettingsError(f"{name} 환경 변수가 필요합니다.")
    return value


def _positive_int(name: str) -> int:
    value = _required(name)
    try:
        parsed = int(value)
    except ValueError as error:
        raise SettingsError(f"{name}은 정수여야 합니다.") from error
    if parsed <= 0:
        raise SettingsError(f"{name}은 0보다 커야 합니다.")
    return parsed


def _https_url(name: str) -> str:
    value = _required(name).rstrip("/")
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.netloc or parsed.params or parsed.query or parsed.fragment:
        raise SettingsError(f"{name}은 경로·쿼리 없는 HTTPS URL이어야 합니다.")
    return value


def _api_base_url() -> str:
    name = "AUTH_API_BASE_URL"
    value = _required(name).rstrip("/")
    parsed = urlparse(value)
    local_http = parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1"}
    if (parsed.scheme != "https" and not local_http) or not parsed.netloc or parsed.params or parsed.query or parsed.fragment:
        raise SettingsError(f"{name}은 HTTPS URL 또는 localhost HTTP URL이어야 합니다.")
    return value


@dataclass(frozen=True)
class TokenSettings:
    jwt_secret: str
    access_token_ttl_seconds: int
    login_code_ttl_seconds: int

    @classmethod
    def from_env(cls) -> "TokenSettings":
        jwt_secret = _required("AUTH_JWT_SECRET")
        if len(jwt_secret) < 32:
            raise SettingsError("AUTH_JWT_SECRET은 32자 이상이어야 합니다.")
        return cls(
            jwt_secret=jwt_secret,
            access_token_ttl_seconds=_positive_int("AUTH_ACCESS_TOKEN_TTL_SECONDS"),
            login_code_ttl_seconds=_positive_int("AUTH_LOGIN_CODE_TTL_SECONDS"),
        )


@dataclass(frozen=True)
class CallbackSettings:
    api_base_url: str
    app_link_base_url: str

    @classmethod
    def from_env(cls) -> "CallbackSettings":
        return cls(
            api_base_url=_api_base_url(),
            app_link_base_url=_https_url("AUTH_APP_LINK_BASE_URL"),
        )


@dataclass(frozen=True)
class ProviderSettings:
    google_client_id: str
    google_client_secret: str
    kakao_client_id: str
    kakao_client_secret: str

    @classmethod
    def from_env(cls) -> "ProviderSettings":
        return cls(
            google_client_id=_required("GOOGLE_CLIENT_ID"),
            google_client_secret=_required("GOOGLE_CLIENT_SECRET"),
            kakao_client_id=_required("KAKAO_CLIENT_ID"),
            kakao_client_secret=_required("KAKAO_CLIENT_SECRET"),
        )


@dataclass(frozen=True)
class AuthSettings:
    token: TokenSettings
    callback: CallbackSettings
    providers: ProviderSettings

    @classmethod
    def from_env(cls) -> "AuthSettings":
        return cls(
            token=TokenSettings.from_env(),
            callback=CallbackSettings.from_env(),
            providers=ProviderSettings.from_env(),
        )
