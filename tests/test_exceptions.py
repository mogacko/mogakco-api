"""애플리케이션 예외와 오류 카탈로그의 공통 계약을 검증한다."""

from http import HTTPStatus

import pytest

from app.auth.errors import AuthErrors
from app.common.errors import CommonErrors
from app.community.errors import CommunityErrors
from app.event.errors import EventErrors
from app.exceptions import (
    AppException,
    BadRequestException,
    ErrorSpec,
    NotFoundException,
)
from app.region.errors import RegionErrors
from app.schemas.error import error_responses


def test_error_spec_requires_concrete_app_exception_and_nonempty_text() -> None:
    with pytest.raises(TypeError):
        ErrorSpec(str, "INVALID", "invalid")  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        ErrorSpec(AppException, "INVALID", "invalid")
    with pytest.raises(ValueError):
        ErrorSpec(NotFoundException, "", "invalid")
    with pytest.raises(ValueError):
        ErrorSpec(NotFoundException, "INVALID", "")


def test_exception_and_error_spec_categories_must_match() -> None:
    assert EventErrors.NOT_FOUND.status_code is HTTPStatus.NOT_FOUND
    error = NotFoundException(EventErrors.NOT_FOUND)
    assert error.status_code is HTTPStatus.NOT_FOUND
    assert error.code == "EVENT_NOT_FOUND"

    with pytest.raises(TypeError):
        BadRequestException(EventErrors.NOT_FOUND)


def test_catalog_error_codes_are_unique_and_messages_are_consistent() -> None:
    catalogs = (
        CommonErrors,
        AuthErrors,
        RegionErrors,
        EventErrors,
        CommunityErrors,
    )
    specs = [
        value
        for catalog in catalogs
        for value in vars(catalog).values()
        if isinstance(value, ErrorSpec)
    ]
    codes = [spec.code for spec in specs]
    assert len(codes) == len(set(codes))


def test_error_responses_groups_examples_by_status() -> None:
    responses = error_responses(
        EventErrors.INVALID_DATE,
        EventErrors.INVALID_TIME,
        EventErrors.NOT_FOUND,
    )

    assert set(responses) == {400, 404}
    examples = responses[400]["content"]["application/json"]["examples"]
    assert set(examples) == {"INVALID_EVENT_DATE", "INVALID_EVENT_TIME"}
    assert examples["INVALID_EVENT_DATE"]["value"] == {
        "code": "INVALID_EVENT_DATE",
        "message": "신청 마감일은 오늘부터 행사 날짜 이전이어야 합니다.",
    }


def test_error_responses_rejects_duplicate_codes() -> None:
    with pytest.raises(ValueError):
        error_responses(EventErrors.NOT_FOUND, EventErrors.NOT_FOUND)
