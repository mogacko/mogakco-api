from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Uuid, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


def utc_now() -> datetime:
    return datetime.now(UTC)


class Region(Base):
    __tablename__ = "region"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(20), unique=True)
    is_enable: Mapped[bool] = mapped_column(Boolean)


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
    region_id: Mapped[int] = mapped_column(ForeignKey("region.id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        server_default=func.now(),
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
