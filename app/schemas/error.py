"""공통 오류 응답 스키마와 OpenAPI 등록 도우미."""

from pydantic import BaseModel

from app.exceptions import ErrorSpec


class ErrorResponse(BaseModel):
    code: str
    message: str


def error_responses(*errors: ErrorSpec) -> dict[int, dict[str, object]]:
    """오류 스펙을 상태별 OpenAPI 응답 예시로 등록한다."""

    grouped: dict[int, list[ErrorSpec]] = {}
    seen_codes: set[str] = set()
    for error in errors:
        if error.code in seen_codes:
            raise ValueError(f"duplicate error code: {error.code}")
        seen_codes.add(error.code)
        grouped.setdefault(int(error.status_code), []).append(error)

    responses: dict[int, dict[str, object]] = {}
    for status_code, status_errors in grouped.items():
        examples = {
            error.code: {
                "summary": error.code,
                "value": {"code": error.code, "message": error.message},
            }
            for error in status_errors
        }
        responses[status_code] = {
            "model": ErrorResponse,
            "content": {"application/json": {"examples": examples}},
        }

    return responses
