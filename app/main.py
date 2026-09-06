import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.common.errors import CommonErrors
from app.exceptions import AppException
from app.routers.community import router as community_router
from app.routers.event import router as event_router

logger = logging.getLogger(__name__)

app = FastAPI(title="mogakco-api")
app.include_router(community_router)
app.include_router(event_router)


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
    error = CommonErrors.INVALID_REQUEST
    return JSONResponse(
        status_code=error.status_code,
        content={"code": error.code, "message": error.message},
    )


@app.exception_handler(StarletteHTTPException)
async def framework_http_exception_response(
    _request: Request,
    error: StarletteHTTPException,
) -> JSONResponse:
    fixed_error = {
        404: CommonErrors.NOT_FOUND,
        405: CommonErrors.METHOD_NOT_ALLOWED,
    }.get(error.status_code)
    code = (
        fixed_error.code
        if fixed_error is not None
        else CommonErrors.HTTP_ERROR_CODE
    )
    message = (
        fixed_error.message
        if fixed_error is not None
        else CommonErrors.HTTP_ERROR_MESSAGE
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
    internal_error = CommonErrors.INTERNAL_SERVER_ERROR
    return JSONResponse(
        status_code=internal_error.status_code,
        content={
            "code": internal_error.code,
            "message": internal_error.message,
        },
    )
