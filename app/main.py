from fastapi import FastAPI

from app.auth.router import router as auth_router
from app.images.router import router as images_router
from app.keywords.router import router as keywords_router
from app.users.router import router as users_router
from app.terms.router import router as terms_router

app = FastAPI(
    title="모각코 API",
    description="모각코 앱의 소셜 로그인, 프로필, 약관 API입니다.",
    openapi_tags=[
        {"name": "auth", "description": "소셜 로그인, 가입, 토큰과 세션 관리"},
        {"name": "users", "description": "내 프로필 조회와 수정"},
        {"name": "terms", "description": "약관 조회와 마케팅 동의 관리"},
        {"name": "images", "description": "이미지 업로드 URL 발급과 완료 검증"},
        {"name": "keywords", "description": "분야·스택·관심분야 자동완성"},
    ],
)
app.include_router(auth_router)
app.include_router(users_router)
app.include_router(terms_router)
app.include_router(images_router)
app.include_router(keywords_router)


@app.get("/health", summary="헬스 체크")
def health() -> dict[str, bool]:
    return {"ok": True}
