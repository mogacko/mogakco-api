class AppException(Exception):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


class AuthenticationError(AppException):
    def __init__(self) -> None:
        super().__init__(401, "AUTH_REQUIRED", "로그인이 필요합니다.")


class ForbiddenError(AppException):
    def __init__(self) -> None:
        super().__init__(403, "FORBIDDEN", "권한이 없습니다.")


class NotFoundError(AppException):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(404, code, message)


class DomainValidationError(AppException):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(422, code, message)


class ServiceUnavailableError(AppException):
    def __init__(self) -> None:
        super().__init__(
            503,
            "LIKE_SERVICE_UNAVAILABLE",
            "좋아요 서비스를 이용할 수 없습니다.",
        )


class ConfigurationError(AppException):
    def __init__(self) -> None:
        super().__init__(
            500,
            "CONFIGURATION_ERROR",
            "서버 설정 오류가 발생했습니다.",
        )
