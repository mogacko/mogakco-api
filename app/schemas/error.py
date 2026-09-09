"""공통 오류 응답 스키마와 OpenAPI 등록 도우미."""

from pydantic import BaseModel


class ErrorResponse(BaseModel):
    code: str
    message: str


def error_responses(*status_codes: int) -> dict[int, dict[str, object]]:
    """지정한 HTTP 상태 코드에 공통 오류 응답 모델을 등록한다."""

    return {status_code: {"model": ErrorResponse} for status_code in status_codes}
