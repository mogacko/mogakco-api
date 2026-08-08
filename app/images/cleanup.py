"""아무도 쓰지 않는 이미지를 지운다. 배포 환경의 크론에서 주기적으로 부른다.

    uv run python -m app.images.cleanup

업로드 URL만 받고 올리지 않은 `PENDING`과, 프로필 사진을 바꿔서 참조가 끊긴 `READY`가
모두 대상이다. 둘 다 "어느 사용자도 참조하지 않는다"로 걸리므로 상태를 따로 보지 않는다.
"""
import os
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.images.config import StorageSettings
from app.images.model import MediaAsset
from app.images.service import s3_client
from app.users.model import User

# 올린 직후 아직 프로필에 붙이기 전인 이미지를 지우지 않도록 여유를 둔다.
DEFAULT_AGE_HOURS = 24
_S3_DELETE_LIMIT = 1000


def delete_orphan_assets(db: Session, settings: StorageSettings, age_hours: int = DEFAULT_AGE_HOURS) -> int:
    cutoff = datetime.now(UTC) - timedelta(hours=age_hours)
    # 하위 질의에서 NULL을 걸러야 NOT IN이 빈 결과를 내지 않는다.
    referenced = select(User.profile_image_asset_id).where(User.profile_image_asset_id.is_not(None))
    orphans = db.scalars(
        select(MediaAsset).where(MediaAsset.created_at < cutoff, MediaAsset.id.not_in(referenced))
    ).all()
    if not orphans:
        return 0

    client = s3_client(settings)
    for start in range(0, len(orphans), _S3_DELETE_LIMIT):
        chunk = orphans[start : start + _S3_DELETE_LIMIT]
        client.delete_objects(
            Bucket=settings.bucket,
            Delete={"Objects": [{"Key": asset.key} for asset in chunk], "Quiet": True},
        )

    db.execute(delete(MediaAsset).where(MediaAsset.id.in_([asset.id for asset in orphans])))
    db.commit()
    return len(orphans)


if __name__ == "__main__":
    from app.db import SessionLocal

    with SessionLocal() as session:
        removed = delete_orphan_assets(
            session, StorageSettings.from_env(), int(os.getenv("MEDIA_ORPHAN_AGE_HOURS", DEFAULT_AGE_HOURS))
        )
    print(f"지운 이미지 {removed}개")
