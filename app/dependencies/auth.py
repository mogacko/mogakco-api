import logging
import os
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Header
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.exceptions import AuthenticationError, ConfigurationError
from app.models import User

LOCAL_ENVIRONMENTS = {"local", "development", "test"}
logger = logging.getLogger(__name__)


def debug_auth_enabled() -> bool:
    return (
        os.getenv("APP_ENV", "production").lower() in LOCAL_ENVIRONMENTS
        and os.getenv("ENABLE_DEBUG_AUTH", "false").lower() == "true"
    )


def get_current_user(
    db: Annotated[Session, Depends(get_db)],
    debug_user_uuid: Annotated[
        str | None,
        Header(
            alias="X-Debug-User-Uuid",
            description="개발 환경에서 사용할 사용자 UUID credential",
        ),
    ] = None,
) -> User:
    if not debug_auth_enabled():
        raise AuthenticationError()

    header_provided = debug_user_uuid is not None
    raw_uuid = (
        debug_user_uuid
        if header_provided
        else os.getenv("DEBUG_DEFAULT_USER_UUID")
    )
    if not raw_uuid:
        raise AuthenticationError()

    try:
        user_uuid = UUID(raw_uuid)
    except ValueError:
        if header_provided:
            raise AuthenticationError() from None
        logger.error("Invalid DEBUG_DEFAULT_USER_UUID configuration")
        raise ConfigurationError() from None

    user = db.scalar(
        select(User).where(
            User.uuid == user_uuid,
            User.deleted_at.is_(None),
        )
    )
    if user is None:
        raise AuthenticationError()
    return user
