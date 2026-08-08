from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("activity_region IN ('SEOUL', 'BUSAN')", name="ck_users_activity_region"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    nickname: Mapped[str] = mapped_column(String(30), unique=True, index=True)
    activity_region: Mapped[str] = mapped_column(String(10))
    profile_image_url: Mapped[str | None] = mapped_column(String(2048))
    field: Mapped[str | None] = mapped_column(String(100))
    introduction: Mapped[str | None] = mapped_column(Text)
    organization: Mapped[str | None] = mapped_column(String(100))
    stack: Mapped[str | None] = mapped_column(Text)
    interests: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
