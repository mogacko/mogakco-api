from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.auth.token import get_current_user
from app.db import get_db
from app.users.model import User
from app.users.schemas import UserResponse, UserUpdateRequest
from app.users.service import update_user

router = APIRouter(tags=["users"])


@router.get("/me", response_model=UserResponse, summary="내 프로필 조회")
def get_me(current_user: User = Depends(get_current_user)) -> User:
    return current_user


@router.patch("/me", response_model=UserResponse, summary="내 프로필 수정")
def patch_me(
    request: UserUpdateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> User:
    return update_user(db, current_user, request)
