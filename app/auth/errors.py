"""인증 도메인의 API 오류."""

from app.exceptions import (
    AuthenticationException,
    ErrorSpec,
    ForbiddenException,
)


class AuthErrors:
    REQUIRED = ErrorSpec(
        AuthenticationException,
        "AUTH_REQUIRED",
        "로그인이 필요합니다.",
    )
    FORBIDDEN = ErrorSpec(
        ForbiddenException,
        "FORBIDDEN",
        "권한이 없습니다.",
    )
