from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.images.model import MediaAsset
from app.keywords.service import sync_keywords
from app.users.model import User
from app.users.schemas import UserUpdateRequest

VALID_REGIONS = {"SEOUL", "BUSAN"}


def update_user(db: Session, user: User, request: UserUpdateRequest) -> User:
    values = request.model_dump(exclude_unset=True)
    if "activity_region" in values and values["activity_region"] not in VALID_REGIONS:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="활동 지역이 유효하지 않습니다.")
    nickname = values.get("nickname")
    if nickname is not None and nickname != user.nickname:
        existing = db.scalar(select(User.id).where(User.nickname == nickname))
        if existing is not None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="이미 사용 중인 닉네임입니다.")
    if values.get("profile_image_asset_id") is not None:
        asset = db.get(MediaAsset, values["profile_image_asset_id"])
        if asset is None or asset.owner_id != user.id or asset.status != "READY":
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="사용할 수 없는 이미지입니다.")
    for field, value in values.items():
        setattr(user, field, value)
    sync_keywords(db, user.id, values)
    db.commit()
    db.refresh(user)
    return user
