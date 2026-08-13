import hashlib
import os
import secrets
import jwt
from datetime import UTC, datetime

import redis
from fastapi import HTTPException, status
from jwt.exceptions import InvalidTokenError, PyJWKClientConnectionError, PyJWKClientError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.config import AppleNativeSettings, GoogleNativeSettings, KakaoNativeSettings, TokenSettings
from app.auth.model import AuthSession, SocialAccount
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


# 가입 코드는 Redis가 원본이다. 없어져도 앱이 소셜 로그인을 다시 하면 새로 생기므로
# 재구축할 원천이 필요 없다. 대신 저장할 곳이 아예 없으면 가입을 시작할 수 없다.
_CODE_KEY = "signup:v1:{digest}"
_REDIS: redis.Redis | None = None


def _redis() -> redis.Redis:
    global _REDIS
    if _REDIS is None:
        url = os.getenv("REDIS_URL")
        if not url:
            raise RuntimeError("REDIS_URL이 없어 가입 코드를 저장할 수 없다.")
        _REDIS = redis.Redis.from_url(url, decode_responses=True)
    return _REDIS


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


def create_login_code(settings: TokenSettings, provider: str, provider_user_id: str) -> str:
    """가입 대기 코드. ID token 검증 결과를 앱이 가입 화면을 띄우는 동안 붙들어 둔다.

    만료는 TTL이 처리한다. provider에는 콜론이 없으므로 값은 콜론 하나로 나눈다.
    """
    code = secrets.token_urlsafe(32)
    _redis().setex(
        _CODE_KEY.format(digest=_hash(code)),
        settings.login_code_ttl_seconds,
        f"{provider}:{provider_user_id}",
    )
    return code


def social_login(db: Session, provider: str, provider_user_id: str, settings: TokenSettings) -> tuple[tuple[str, str] | None, str | None]:
    user_id = find_social_user_id(db, provider, provider_user_id)
    if user_id is None:
        return None, create_login_code(settings, provider, provider_user_id)
    tokens = _issue_tokens(db, user_id, settings)
    db.commit()
    return tokens, None


def _issue_tokens(db: Session, user_id: int, settings: TokenSettings) -> tuple[str, str]:
    refresh_token = _new_refresh_token()
    db.add(AuthSession(user_id=user_id, refresh_token_hash=_hash(refresh_token), last_used_at=datetime.now(UTC)))
    return create_access_token(user_id, settings), refresh_token


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
    # 만료·미발급·이미 쓴 코드가 모두 "키가 없다" 하나로 걸린다.
    key = _CODE_KEY.format(digest=_hash(code))
    stored = _redis().get(key)
    if stored is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="유효하지 않은 가입 코드입니다.")
    provider, provider_user_id = stored.split(":", 1)
    required_ids = {version.id for term, version in current_term_versions(db) if term.required}
    if not required_ids.issubset(term_version_ids):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="필수 약관 동의가 필요합니다.")
    if values["activity_region"] not in {"SEOUL", "BUSAN"}:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="활동 지역이 유효하지 않습니다.")
    if db.scalar(select(User.id).where(User.nickname == values["nickname"])) is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="이미 사용 중인 닉네임입니다.")
    if db.scalar(
        select(SocialAccount.id).where(
            SocialAccount.provider == provider,
            SocialAccount.provider_user_id == provider_user_id,
        )
    ) is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="이미 연결된 소셜 계정입니다.")
    user = User(**values)
    db.add(user); db.flush()
    sync_keywords(db, user.id, values)
    db.add(SocialAccount(user_id=user.id, provider=provider, provider_user_id=provider_user_id))
    from app.terms.model import UserTermAgreement
    for version_id in set(term_version_ids):
        db.add(UserTermAgreement(user_id=user.id, term_version_id=version_id))
    tokens = _issue_tokens(db, user.id, settings)
    db.commit()
    # 커밋한 뒤에 지운다. 먼저 지우면 커밋이 실패했을 때 코드만 날아간다. 위 검증에서 막힌
    # 요청은 코드를 소진하지 않으므로 닉네임만 고쳐 다시 시도할 수 있다. 삭제가 실패해도
    # 남은 코드로 다시 가입하면 social_accounts 유니크 제약이 막는다.
    _redis().delete(key)
    return tokens
