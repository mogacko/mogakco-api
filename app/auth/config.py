import os
from dataclasses import dataclass


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


def _csv(name: str) -> tuple[str, ...]:
    values = tuple(value.strip() for value in _required(name).split(",") if value.strip())
    if not values:
        raise SettingsError(f"{name}에는 하나 이상의 값이 필요합니다.")
    return values


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
class GoogleNativeSettings:
    oauth_client_ids: tuple[str, ...]

    @classmethod
    def from_env(cls) -> "GoogleNativeSettings":
        return cls(oauth_client_ids=_csv("GOOGLE_OAUTH_CLIENT_IDS"))


@dataclass(frozen=True)
class AppleNativeSettings:
    client_ids: tuple[str, ...]

    @classmethod
    def from_env(cls) -> "AppleNativeSettings":
        return cls(client_ids=_csv("APPLE_CLIENT_IDS"))


@dataclass(frozen=True)
class KakaoNativeSettings:
    native_app_key: str

    @classmethod
    def from_env(cls) -> "KakaoNativeSettings":
        return cls(native_app_key=_required("KAKAO_NATIVE_APP_KEY"))

