import logging

import pytest
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.main import (
    framework_http_exception_response,
    unexpected_exception_response,
    validation_exception_response,
)


def error_test_client() -> TestClient:
    test_app = FastAPI()
    test_app.add_exception_handler(
        RequestValidationError,
        validation_exception_response,
    )
    test_app.add_exception_handler(
        StarletteHTTPException,
        framework_http_exception_response,
    )
    test_app.add_exception_handler(Exception, unexpected_exception_response)

    @test_app.get("/items/{item_id}")
    def get_item(item_id: int) -> dict[str, int]:
        return {"itemId": item_id}

    @test_app.get("/boom")
    def boom() -> None:
        raise RuntimeError("sensitive runtime detail")

    return TestClient(test_app, raise_server_exceptions=False)


def test_framework_404_405_and_validation_use_error_envelope() -> None:
    with error_test_client() as client:
        missing = client.get("/unknown")
        unsupported = client.post("/items/1")
        invalid = client.get("/items/not-an-integer")

    assert missing.status_code == 404
    assert missing.json() == {
        "code": "NOT_FOUND",
        "message": "요청한 경로를 찾을 수 없습니다.",
    }
    assert unsupported.status_code == 405
    assert unsupported.json() == {
        "code": "METHOD_NOT_ALLOWED",
        "message": "허용되지 않은 요청 방식입니다.",
    }
    assert invalid.status_code == 422
    assert invalid.json() == {
        "code": "INVALID_REQUEST",
        "message": "요청값이 올바르지 않습니다.",
    }


def test_unexpected_exception_is_hidden_and_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    logging.getLogger("app.main").disabled = False
    with caplog.at_level(logging.ERROR, logger="app.main"):
        with error_test_client() as client:
            response = client.get("/boom")

    assert response.status_code == 500
    assert response.json() == {
        "code": "INTERNAL_SERVER_ERROR",
        "message": "서버 오류가 발생했습니다.",
    }
    assert "sensitive runtime detail" not in response.text
    assert "Unhandled exception while processing request" in caplog.text
    assert "RuntimeError: sensitive runtime detail" in caplog.text
