import hashlib
import secrets
import base64
from urllib.parse import urlencode

import httpx
import jwt
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException, status
from jwt.exceptions import InvalidTokenError, PyJWKClientConnectionError, PyJWKClientError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.config import AppleNativeSettings, AuthSettings, GoogleNativeSettings, KakaoNativeSettings, TokenSettings
from app.auth.model import AuthSession, LoginCode, OAuthAttempt, SocialAccount
from app.auth.token import create_access_token
from app.keywords.service import sync_keywords
from app.terms.service import current_term_versions
from app.users.model import User


_google_jwks = jwt.PyJWKClient("https://www.googleapis.com/oauth2/v3/certs", cache_keys=True)
_apple_jwks = jwt.PyJWKClient("https://appleid.apple.com/auth/keys", cache_keys=True)
_kakao_jwks = jwt.PyJWKClient("https://kauth.kakao.com/.well-known/jwks.json", cache_keys=True)


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _new_refresh_token() -> str:
    return secrets.token_urlsafe(48)


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def create_oauth_attempt(db: Session, provider: str, settings: TokenSettings) -> tuple[str, str]:
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    db.add(OAuthAttempt(provider=provider, state_hash=_hash(state), code_verifier=verifier, expires_at=datetime.now(UTC) + timedelta(seconds=settings.login_code_ttl_seconds)))
    db.commit()
    return state, verifier


def authorization_url(provider: str, state: str, verifier: str, settings: AuthSettings) -> str:
    callback = f"{settings.callback.api_base_url}/auth/{provider.lower()}/callback"
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    if provider == "GOOGLE":
        base, client_id, scope = "https://accounts.google.com/o/oauth2/v2/auth", settings.providers.google_client_id, "openid profile"
    else:
        base, client_id, scope = "https://kauth.kakao.com/oauth/authorize", settings.providers.kakao_client_id, "profile_nickname"
    return f"{base}?{urlencode({'client_id': client_id, 'redirect_uri': callback, 'response_type': 'code', 'state': state, 'code_challenge': challenge, 'code_challenge_method': 'S256', 'scope': scope})}"


def consume_oauth_attempt(db: Session, provider: str, state: str) -> OAuthAttempt:
    attempt = db.scalar(select(OAuthAttempt).where(OAuthAttempt.state_hash == _hash(state)).with_for_update())
    if attempt is None or attempt.provider != provider or attempt.used_at is not None or _as_utc(attempt.expires_at) <= datetime.now(UTC):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="유효하지 않은 OAuth 요청입니다.")
    attempt.used_at = datetime.now(UTC)
    db.commit()
    return attempt


def provider_user_id(provider: str, authorization_code: str, attempt: OAuthAttempt, settings: AuthSettings) -> str:
    callback = f"{settings.callback.api_base_url}/auth/{provider.lower()}/callback"
    if provider == "GOOGLE":
        token_url, client_id, client_secret = "https://oauth2.googleapis.com/token", settings.providers.google_client_id, settings.providers.google_client_secret
        profile_url, identifier = "https://openidconnect.googleapis.com/v1/userinfo", "sub"
    else:
        token_url, client_id, client_secret = "https://kauth.kakao.com/oauth/token", settings.providers.kakao_client_id, settings.providers.kakao_client_secret
        profile_url, identifier = "https://kapi.kakao.com/v2/user/me", "id"
    token_response = httpx.post(token_url, data={"grant_type": "authorization_code", "client_id": client_id, "client_secret": client_secret, "redirect_uri": callback, "code": authorization_code, "code_verifier": attempt.code_verifier}, timeout=10.0)
    if token_response.is_error:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="소셜 인증 코드 교환에 실패했습니다.")
    access_token = token_response.json().get("access_token")
    if not access_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="소셜 인증 응답이 올바르지 않습니다.")
    profile_response = httpx.get(profile_url, headers={"Authorization": f"Bearer {access_token}"}, timeout=10.0)
    if profile_response.is_error or profile_response.json().get(identifier) is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="소셜 사용자 정보를 확인할 수 없습니다.")
    return str(profile_response.json()[identifier])


def find_social_user_id(db: Session, provider: str, external_user_id: str) -> int | None:
    return db.scalar(select(SocialAccount.user_id).where(SocialAccount.provider == provider, SocialAccount.provider_user_id == external_user_id))


def google_user_id(id_token: str, settings: GoogleNativeSettings) -> str:
    try:
        signing_key = _google_jwks.get_signing_key_from_jwt(id_token)
        claims = jwt.decode(
            id_token,
            signing_key.key,
            algorithms=["RS256"],
            audience=settings.oauth_client_ids,
            issuer=["accounts.google.com", "https://accounts.google.com"],
            options={"require": ["aud", "exp", "iss", "sub"]},
        )
    except PyJWKClientConnectionError as error:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="소셜 로그인 확인을 잠시 사용할 수 없습니다.") from error
    except (InvalidTokenError, PyJWKClientError) as error:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="유효하지 않은 Google ID token입니다.") from error
    subject = claims["sub"]
    if not isinstance(subject, str) or not subject:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="유효하지 않은 Google ID token입니다.")
    return subject


def apple_user_id(id_token: str, settings: AppleNativeSettings, nonce: str | None = None) -> str:
    try:
        signing_key = _apple_jwks.get_signing_key_from_jwt(id_token)
        claims = jwt.decode(
            id_token,
            signing_key.key,
            algorithms=["RS256"],
            audience=settings.client_ids,
            issuer="https://appleid.apple.com",
            options={"require": ["aud", "exp", "iss", "sub"]},
        )
    except PyJWKClientConnectionError as error:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="소셜 로그인을 잠시 사용할 수 없습니다.") from error
    except (InvalidTokenError, PyJWKClientError) as error:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="유효하지 않은 Apple ID token입니다.") from error
    subject = claims["sub"]
    if not isinstance(subject, str) or not subject or (nonce is not None and claims.get("nonce") not in {nonce, _hash(nonce)}):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="유효하지 않은 Apple ID token입니다.")
    return subject


def kakao_user_id(id_token: str, nonce: str, settings: KakaoNativeSettings) -> str:
    try:
        signing_key = _kakao_jwks.get_signing_key_from_jwt(id_token)
        claims = jwt.decode(
            id_token,
            signing_key.key,
            algorithms=["RS256"],
            audience=settings.native_app_key,
            issuer="https://kauth.kakao.com",
            options={"require": ["aud", "exp", "iss", "nonce", "sub"]},
        )
    except PyJWKClientConnectionError as error:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="소셜 로그인을 잠시 사용할 수 없습니다.") from error
    except (InvalidTokenError, PyJWKClientError) as error:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="유효하지 않은 Kakao ID token입니다.") from error
    subject = claims["sub"]
    if claims["nonce"] != nonce or not isinstance(subject, str) or not subject:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="유효하지 않은 Kakao ID token입니다.")
    return subject


def create_login_code(db: Session, user_id: int | None, settings: TokenSettings, provider: str | None = None, provider_user_id: str | None = None) -> str:
    code = secrets.token_urlsafe(32)
    db.add(
        LoginCode(
            user_id=user_id,
            provider=provider,
            provider_user_id=provider_user_id,
            code_hash=_hash(code),
            expires_at=datetime.now(UTC) + timedelta(seconds=settings.login_code_ttl_seconds),
        )
    )
    db.commit()
    return code


def social_login(db: Session, provider: str, provider_user_id: str, settings: TokenSettings) -> tuple[tuple[str, str] | None, str | None]:
    user_id = find_social_user_id(db, provider, provider_user_id)
    if user_id is None:
        return None, create_login_code(db, None, settings, provider, provider_user_id)
    tokens = _issue_tokens(db, user_id, settings)
    db.commit()
    return tokens, None


def _issue_tokens(db: Session, user_id: int, settings: TokenSettings) -> tuple[str, str]:
    refresh_token = _new_refresh_token()
    db.add(AuthSession(user_id=user_id, refresh_token_hash=_hash(refresh_token), last_used_at=datetime.now(UTC)))
    return create_access_token(user_id, settings), refresh_token


def exchange_login_code(db: Session, code: str, settings: TokenSettings) -> tuple[str, str]:
    login_code = db.scalar(select(LoginCode).where(LoginCode.code_hash == _hash(code)).with_for_update())
    now = datetime.now(UTC)
    if login_code is None or login_code.used_at is not None or _as_utc(login_code.expires_at) <= now:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="유효하지 않은 로그인 코드입니다.")
    if login_code.user_id is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="가입 완료가 필요합니다.")
    login_code.used_at = now
    tokens = _issue_tokens(db, login_code.user_id, settings)
    db.commit()
    return tokens


def refresh_tokens(db: Session, refresh_token: str, settings: TokenSettings) -> tuple[str, str]:
    session = db.scalar(select(AuthSession).where(AuthSession.refresh_token_hash == _hash(refresh_token)).with_for_update())
    if session is None or session.revoked_at is not None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="유효하지 않은 refresh token입니다.")
    session.revoked_at = datetime.now(UTC)
    tokens = _issue_tokens(db, session.user_id, settings)
    db.commit()
    return tokens


def logout(db: Session, refresh_token: str) -> None:
    session = db.scalar(select(AuthSession).where(AuthSession.refresh_token_hash == _hash(refresh_token)).with_for_update())
    if session is not None and session.revoked_at is None:
        session.revoked_at = datetime.now(UTC)
        db.commit()


def signup(db: Session, code: str, values: dict, term_version_ids: list[int], settings: TokenSettings) -> tuple[str, str]:
    login_code = db.scalar(select(LoginCode).where(LoginCode.code_hash == _hash(code)).with_for_update())
    now = datetime.now(UTC)
    if login_code is None or login_code.used_at is not None or _as_utc(login_code.expires_at) <= now or login_code.user_id is not None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="유효하지 않은 가입 코드입니다.")
    if login_code.provider is None or login_code.provider_user_id is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="소셜 계정 정보가 없습니다.")
    required_ids = {version.id for term, version in current_term_versions(db) if term.required}
    if not required_ids.issubset(term_version_ids):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="필수 약관 동의가 필요합니다.")
    if values["activity_region"] not in {"SEOUL", "BUSAN"}:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="활동 지역이 유효하지 않습니다.")
    if db.scalar(select(User.id).where(User.nickname == values["nickname"])) is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="이미 사용 중인 닉네임입니다.")
    if db.scalar(
        select(SocialAccount.id).where(
            SocialAccount.provider == login_code.provider,
            SocialAccount.provider_user_id == login_code.provider_user_id,
        )
    ) is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="이미 연결된 소셜 계정입니다.")
    user = User(**values)
    db.add(user); db.flush()
    sync_keywords(db, user.id, values)
    db.add(SocialAccount(user_id=user.id, provider=login_code.provider, provider_user_id=login_code.provider_user_id))
    from app.terms.model import UserTermAgreement
    for version_id in set(term_version_ids):
        db.add(UserTermAgreement(user_id=user.id, term_version_id=version_id))
    login_code.used_at = now
    tokens = _issue_tokens(db, user.id, settings)
    db.commit()
    return tokens
