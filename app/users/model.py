from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.images.model import MediaUsage


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("activity_region IN ('SEOUL', 'BUSAN')", name="ck_users_activity_region"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    nickname: Mapped[str] = mapped_column(String(30), unique=True, index=True)
    activity_region: Mapped[str] = mapped_column(String(10))
    field: Mapped[str | None] = mapped_column(String(100))
    introduction: Mapped[str | None] = mapped_column(Text)
    organization: Mapped[str | None] = mapped_column(String(100))
    stack: Mapped[str | None] = mapped_column(Text)
    interests: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # media_usages.usage_id에는 외래 키가 없으므로 조인 조건을 직접 적는다.
    profile_usage: Mapped[MediaUsage | None] = relationship(
        primaryjoin="and_(User.id == foreign(MediaUsage.usage_id), MediaUsage.usage_type == 'PROFILE')",
        viewonly=True,
        uselist=False,
        lazy="joined",
    )

    @property
    def profile_image_url(self) -> str | None:
        return self.profile_usage.asset.url if self.profile_usage is not None else None
