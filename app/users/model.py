from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.images.model import MediaAsset


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("activity_region IN ('SEOUL', 'BUSAN')", name="ck_users_activity_region"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    nickname: Mapped[str] = mapped_column(String(30), unique=True, index=True)
    activity_region: Mapped[str] = mapped_column(String(10))
    # 두 표가 서로를 참조해 순환이 생기므로 제약을 나중에 거는 것으로 표시한다.
    profile_image_asset_id: Mapped[int | None] = mapped_column(
        ForeignKey(
            "media_assets.id",
            ondelete="SET NULL",
            use_alter=True,
            name="fk_users_profile_image_asset_id",
        )
    )
    field: Mapped[str | None] = mapped_column(String(100))
    introduction: Mapped[str | None] = mapped_column(Text)
    organization: Mapped[str | None] = mapped_column(String(100))
    stack: Mapped[str | None] = mapped_column(Text)
    interests: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # 두 표가 서로를 참조하므로 어느 외래 키를 따라갈지 명시한다.
    profile_image: Mapped[MediaAsset | None] = relationship(
        foreign_keys=[profile_image_asset_id], lazy="joined"
    )

    @property
    def profile_image_url(self) -> str | None:
        return self.profile_image.url if self.profile_image is not None else None
