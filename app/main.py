import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.exceptions import AppException
from app.routers.community import router as community_router

logger = logging.getLogger(__name__)

app = FastAPI(title="mogakco-api")
app.include_router(community_router)


@app.exception_handler(AppException)
async def app_exception_response(
    _request: Request,
    error: AppException,
) -> JSONResponse:
    return JSONResponse(
        status_code=error.status_code,
        content={"code": error.code, "message": error.message},
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_response(
    _request: Request,
    _error: RequestValidationError,
) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={
            "code": "INVALID_REQUEST",
            "message": "요청값이 올바르지 않습니다.",
        },
    )


@app.exception_handler(StarletteHTTPException)
async def framework_http_exception_response(
    _request: Request,
    error: StarletteHTTPException,
) -> JSONResponse:
    code, message = {
        404: ("NOT_FOUND", "요청한 경로를 찾을 수 없습니다."),
        405: ("METHOD_NOT_ALLOWED", "허용되지 않은 요청 방식입니다."),
    }.get(
        error.status_code,
        ("HTTP_ERROR", "요청을 처리할 수 없습니다."),
    )
    return JSONResponse(
        status_code=error.status_code,
        content={"code": code, "message": message},
        headers=error.headers,
    )


@app.exception_handler(Exception)
async def unexpected_exception_response(
    _request: Request,
    error: Exception,
) -> JSONResponse:
    logger.exception(
        "Unhandled exception while processing request",
        exc_info=(type(error), error, error.__traceback__),
    )
    return JSONResponse(
        status_code=500,
        content={
            "code": "INTERNAL_SERVER_ERROR",
            "message": "서버 오류가 발생했습니다.",
        },
    )
