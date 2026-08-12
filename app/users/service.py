from fastapi import HTTPException, status
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.images.model import PROFILE, AssetUsage, MediaAsset
from app.keywords.service import sync_keywords
from app.users.model import User
from app.users.schemas import UserUpdateRequest

VALID_REGIONS = {"SEOUL", "BUSAN"}


def _set_profile_image(db: Session, user: User, asset_id: int | None) -> None:
    """프로필 자리를 갈아끼운다. 자리당 한 장이라 지우고 다시 넣는다."""
    if asset_id is not None:
        asset = db.get(MediaAsset, asset_id)
        if asset is None or asset.owner_id != user.id or asset.status != "READY":
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="사용할 수 없는 이미지입니다.")
    db.execute(delete(AssetUsage).where(AssetUsage.usage_type == PROFILE, AssetUsage.usage_id == user.id))
    if asset_id is not None:
        db.add(AssetUsage(asset_id=asset_id, usage_type=PROFILE, usage_id=user.id))
    # 갈아끼운 자리를 프로퍼티가 다시 읽도록 캐시를 비운다.
    db.expire(user, ["profile_usage"])


def update_user(db: Session, user: User, request: UserUpdateRequest) -> User:
    values = request.model_dump(exclude_unset=True)
    if "activity_region" in values and values["activity_region"] not in VALID_REGIONS:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="활동 지역이 유효하지 않습니다.")
    nickname = values.get("nickname")
    if nickname is not None and nickname != user.nickname:
        existing = db.scalar(select(User.id).where(User.nickname == nickname))
        if existing is not None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="이미 사용 중인 닉네임입니다.")
    if "profile_image_asset_id" in values:
        # 저장 위치가 컬럼이 아니라 사용처 행이라 setattr 대상에서 뺀다. None이면 떼기만 한다.
        _set_profile_image(db, user, values.pop("profile_image_asset_id"))
    for field, value in values.items():
        setattr(user, field, value)
    sync_keywords(db, user.id, values)
    db.commit()
    db.refresh(user)
    return user
