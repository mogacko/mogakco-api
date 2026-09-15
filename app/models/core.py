from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Uuid, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.time import kst_now


class Region(Base):
    __tablename__ = "regions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(20), unique=True)
    is_enabled: Mapped[bool] = mapped_column(Boolean)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    uuid: Mapped[UUID] = mapped_column(
        Uuid,
        unique=True,
        default=uuid4,
        server_default=text("gen_random_uuid()"),
    )
    nickname: Mapped[str] = mapped_column(String(30), unique=True)
    region_id: Mapped[int] = mapped_column(ForeignKey("regions.id"))
    # 기존 개발 사용자는 분야를 아직 설정하지 않았을 수 있다.
    field: Mapped[str | None] = mapped_column(String(50))
    bio: Mapped[str | None] = mapped_column(String(60))
    is_staff: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false")
    )
    marketing_consent: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false")
    )
    marketing_consent_changed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=kst_now, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=kst_now,
        onupdate=kst_now,
        server_default=func.now(),
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
