"""애플리케이션 예외의 공통 계약과 HTTP 상태별 분류."""

from dataclasses import dataclass
from http import HTTPStatus
from typing import ClassVar


@dataclass(frozen=True, slots=True)
class ErrorSpec:
    """API 오류의 분류, 코드와 메시지를 함께 보관한다."""

    exception_type: type["AppException"]
    code: str
    message: str

    def __post_init__(self) -> None:
        if (
            not isinstance(self.exception_type, type)
            or not issubclass(self.exception_type, AppException)
            or self.exception_type is AppException
        ):
            raise TypeError(
                "exception_type must be an AppException subclass"
            )
        if not self.code or not self.message:
            raise ValueError("error code and message must not be empty")

    @property
    def status_code(self) -> HTTPStatus:
        return self.exception_type.status_code


class AppException(Exception):
    """글로벌 예외 핸들러가 처리하는 최상위 애플리케이션 예외."""

    status_code: ClassVar[HTTPStatus]

    def __init__(self, error: ErrorSpec) -> None:
        if type(self) is not error.exception_type:
            raise TypeError(
                f"{type(self).__name__} cannot use "
                f"{error.exception_type.__name__} error spec"
            )
        super().__init__(error.message)
        self.code = error.code
        self.message = error.message


class BadRequestException(AppException):
    status_code = HTTPStatus.BAD_REQUEST


class AuthenticationException(AppException):
    status_code = HTTPStatus.UNAUTHORIZED


class ForbiddenException(AppException):
    status_code = HTTPStatus.FORBIDDEN


class NotFoundException(AppException):
    status_code = HTTPStatus.NOT_FOUND


class MethodNotAllowedException(AppException):
    status_code = HTTPStatus.METHOD_NOT_ALLOWED


class ConflictException(AppException):
    status_code = HTTPStatus.CONFLICT


class DomainValidationException(AppException):
    status_code = HTTPStatus.UNPROCESSABLE_ENTITY


class TooManyRequestsException(AppException):
    status_code = HTTPStatus.TOO_MANY_REQUESTS


class InternalServerException(AppException):
    status_code = HTTPStatus.INTERNAL_SERVER_ERROR


class ServiceUnavailableException(AppException):
    status_code = HTTPStatus.SERVICE_UNAVAILABLE
