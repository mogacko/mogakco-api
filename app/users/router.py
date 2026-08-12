from fastapi import APIRouter, BackgroundTasks, Depends
from sqlalchemy.orm import Session

from app.auth.token import get_current_user
from app.db import get_db
from app.keywords.cache import refresh_after_save
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
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> User:
    user = update_user(db, current_user, request)
    # 저장이 끝난 뒤에 건다. 캐시가 죽어 있어도 프로필 저장은 이미 성공한 상태다.
    background_tasks.add_task(refresh_after_save)
    return user
