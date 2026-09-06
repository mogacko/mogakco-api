"""특정 도메인에 속하지 않는 공통 API 오류."""

from app.exceptions import (
    DomainValidationException,
    ErrorSpec,
    InternalServerException,
    MethodNotAllowedException,
    NotFoundException,
)


class CommonErrors:
    INVALID_REQUEST = ErrorSpec(
        DomainValidationException,
        "INVALID_REQUEST",
        "요청값이 올바르지 않습니다.",
    )
    NOT_FOUND = ErrorSpec(
        NotFoundException,
        "NOT_FOUND",
        "요청한 경로를 찾을 수 없습니다.",
    )
    METHOD_NOT_ALLOWED = ErrorSpec(
        MethodNotAllowedException,
        "METHOD_NOT_ALLOWED",
        "허용되지 않은 요청 방식입니다.",
    )
    INTERNAL_SERVER_ERROR = ErrorSpec(
        InternalServerException,
        "INTERNAL_SERVER_ERROR",
        "서버 오류가 발생했습니다.",
    )
    CONFIGURATION_ERROR = ErrorSpec(
        InternalServerException,
        "CONFIGURATION_ERROR",
        "서버 설정 오류가 발생했습니다.",
    )

    # 프레임워크 예외의 동적 HTTP 상태를 보존하므로 ErrorSpec으로 만들지 않는다.
    HTTP_ERROR_CODE = "HTTP_ERROR"
    HTTP_ERROR_MESSAGE = "요청을 처리할 수 없습니다."
