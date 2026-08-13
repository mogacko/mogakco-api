from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from app.auth.config import AppleNativeSettings, GoogleNativeSettings, KakaoNativeSettings, TokenSettings
from app.auth.schemas import RefreshRequest, SignupRequest, SocialLoginRequest, SocialLoginResponse, TokenResponse
from app.auth.service import (
    apple_user_id,
    google_user_id,
    kakao_user_id,
    logout,
    refresh_tokens,
    social_login,
    signup,
)
from app.db import get_db
from app.keywords.cache import refresh_after_save

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/social-login", response_model=SocialLoginResponse, summary="네이티브 소셜 로그인")
def post_social_login(request: SocialLoginRequest, db: Session = Depends(get_db)) -> SocialLoginResponse:
    if request.provider == "GOOGLE":
        provider_user_id = google_user_id(request.id_token, GoogleNativeSettings.from_env())
    elif request.provider == "APPLE":
        provider_user_id = apple_user_id(request.id_token, AppleNativeSettings.from_env(), request.nonce)
    elif request.provider == "KAKAO":
        if request.nonce is None:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Kakao 로그인에는 nonce가 필요합니다.")
        provider_user_id = kakao_user_id(request.id_token, request.nonce, KakaoNativeSettings.from_env())
    else:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="지원하지 않는 소셜 로그인 제공자입니다.")
    tokens, code = social_login(db, request.provider, provider_user_id, TokenSettings.from_env())
    if code is not None:
        return SocialLoginResponse(signup_required=True, code=code)
    assert tokens is not None
    access_token, refresh_token = tokens
    return SocialLoginResponse(signup_required=False, access_token=access_token, refresh_token=refresh_token, token_type="bearer")


@router.post("/signup", response_model=TokenResponse, summary="소셜 계정 가입 완료")
def post_signup(request: SignupRequest, background_tasks: BackgroundTasks, db: Session = Depends(get_db)) -> TokenResponse:
    """필수 약관 동의와 프로필을 저장한 뒤 로그인 토큰을 발급합니다."""
    values = request.model_dump(exclude={"code", "agreed_term_version_ids"})
    access_token, refresh_token = signup(db, request.code, values, request.agreed_term_version_ids, TokenSettings.from_env())
    background_tasks.add_task(refresh_after_save)
    return TokenResponse(access_token=access_token, refresh_token=refresh_token)


@router.post("/refresh", response_model=TokenResponse, summary="토큰 재발급")
def post_refresh(request: RefreshRequest, db: Session = Depends(get_db)) -> TokenResponse:
    """refresh token을 교체하고 새 토큰 쌍을 발급합니다."""
    access_token, refresh_token = refresh_tokens(db, request.refresh_token, TokenSettings.from_env())
    return TokenResponse(access_token=access_token, refresh_token=refresh_token)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT, summary="로그아웃")
def post_logout(request: RefreshRequest, db: Session = Depends(get_db)) -> Response:
    logout(db, request.refresh_token)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
