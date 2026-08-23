from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.routers.community import router as community_router

app = FastAPI(title="mogakco-api")
app.include_router(community_router)


@app.exception_handler(HTTPException)
async def http_exception_response(
    _request: Request,
    error: HTTPException,
) -> JSONResponse:
    content = (
        {}
        if error.status_code in {404, 503}
        else {"message": str(error.detail)}
    )
    return JSONResponse(
        status_code=error.status_code,
        content=content,
        headers=error.headers,
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_response(
    request: Request,
    _error: RequestValidationError,
) -> JSONResponse:
    message = (
        "올바르지 않은 대상입니다."
        if request.url.path.startswith("/api/v1/comments")
        else "올바르지 않은 메뉴입니다."
    )
    return JSONResponse(
        status_code=422,
        content={"message": message},
    )
