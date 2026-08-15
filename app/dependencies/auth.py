import os
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User

LOCAL_ENVIRONMENTS = {"local", "development", "test"}


def debug_auth_enabled() -> bool:
    return (
        os.getenv("APP_ENV", "production").lower() in LOCAL_ENVIRONMENTS
        and os.getenv("ENABLE_DEBUG_AUTH", "false").lower() == "true"
    )


def get_current_user(
    db: Annotated[Session, Depends(get_db)],
    debug_user_id: Annotated[
        str | None, Header(alias="X-Debug-User-Id")
    ] = None,
) -> User:
    if not debug_auth_enabled() or debug_user_id is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")

    try:
        user_id = int(debug_user_id)
    except ValueError:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "Not authenticated"
        ) from None

    user = db.get(User, user_id)
    if user is None or user.deleted_at is not None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    return user
