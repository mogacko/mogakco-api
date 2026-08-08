from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class MediaAsset(Base):
    """업로드한 이미지 한 장. PENDING은 발급만 된 상태, READY는 실제 파일 검증까지 끝난 상태다."""

    __tablename__ = "media_assets"
    __table_args__ = (CheckConstraint("status IN ('PENDING', 'READY')", name="ck_media_assets_status"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    key: Mapped[str] = mapped_column(String(255), unique=True)
    url: Mapped[str] = mapped_column(String(2048))
    content_type: Mapped[str] = mapped_column(String(50))
    status: Mapped[str] = mapped_column(String(10))
    size_bytes: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
