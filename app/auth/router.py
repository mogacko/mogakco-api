from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.auth.config import AuthSettings, TokenSettings
from app.auth.schemas import RefreshRequest, SignupRequest, TokenExchangeRequest, TokenResponse
from app.auth.service import (
    authorization_url,
    consume_oauth_attempt,
    create_login_code,
    create_oauth_attempt,
    exchange_login_code,
    find_social_user_id,
    logout,
    provider_user_id,
    refresh_tokens,
    signup,
)
from app.db import get_db

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/{provider}/authorize", summary="소셜 로그인 시작")
def get_authorize(provider: str, db: Session = Depends(get_db)) -> RedirectResponse:
    normalized = provider.upper()
    if normalized not in {"GOOGLE", "KAKAO"}:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="지원하지 않는 소셜 로그인 제공자입니다.")
    settings = AuthSettings.from_env()
    state, verifier = create_oauth_attempt(db, normalized, settings.token)
    return RedirectResponse(authorization_url(normalized, state, verifier, settings))


@router.get("/{provider}/callback", summary="소셜 로그인 콜백")
def get_callback(provider: str, code: str, state: str, db: Session = Depends(get_db)) -> RedirectResponse:
    """소셜 제공자 인증 결과를 앱 링크의 일회용 로그인 코드로 전달합니다."""
    normalized = provider.upper()
    if normalized not in {"GOOGLE", "KAKAO"}:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="지원하지 않는 소셜 로그인 제공자입니다.")
    settings = AuthSettings.from_env()
    attempt = consume_oauth_attempt(db, normalized, state)
    external_user_id = provider_user_id(normalized, code, attempt, settings)
    user_id = find_social_user_id(db, normalized, external_user_id)
    login_code = create_login_code(db, user_id, settings.token, normalized, external_user_id)
    suffix = "" if user_id is not None else "&signup_required=true"
    return RedirectResponse(f"{settings.callback.app_link_base_url}/auth/callback?code={login_code}{suffix}")


@router.post("/token", response_model=TokenResponse, summary="로그인 코드로 토큰 발급")
def post_token(request: TokenExchangeRequest, db: Session = Depends(get_db)) -> TokenResponse:
    """앱 링크로 받은 일회용 로그인 코드를 access·refresh token으로 교환합니다."""
    access_token, refresh_token = exchange_login_code(db, request.code, TokenSettings.from_env())
    return TokenResponse(access_token=access_token, refresh_token=refresh_token)


@router.post("/signup", response_model=TokenResponse, summary="소셜 계정 가입 완료")
def post_signup(request: SignupRequest, db: Session = Depends(get_db)) -> TokenResponse:
    """필수 약관 동의와 프로필을 저장한 뒤 로그인 토큰을 발급합니다."""
    values = request.model_dump(exclude={"code", "agreed_term_version_ids"})
    access_token, refresh_token = signup(db, request.code, values, request.agreed_term_version_ids, TokenSettings.from_env())
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
