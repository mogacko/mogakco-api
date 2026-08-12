from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

# 이미지를 붙일 수 있는 자리. 새 사용처가 생기면 여기와 CHECK 제약에 값을 추가한다.
PROFILE = "PROFILE"
USAGE_TYPES = (PROFILE,)


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


class AssetUsage(Base):
    """에셋이 어디에 붙어 있는지. 이 표에 행이 없는 에셋은 아무도 안 쓰는 것이다.

    `usage_id`는 대상 행의 기본 키다. 대상 표가 여럿이라 외래 키를 걸 수 없다. 그 대가로
    정리 배치가 사용처 종류를 몰라도 되고, 새 사용처가 생겨도 참조 판단을 고치지 않는다.
    """

    __tablename__ = "asset_usages"
    __table_args__ = (
        CheckConstraint(f"usage_type IN ({', '.join(repr(t) for t in USAGE_TYPES)})", name="ck_asset_usages_type"),
        # 한 자리에는 이미지 한 장. 여러 장을 붙이는 사용처가 생기면 그때 푼다.
        UniqueConstraint("usage_type", "usage_id", name="uq_asset_usages_slot"),
        Index("ix_asset_usages_asset_id", "asset_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("media_assets.id", ondelete="CASCADE"))
    usage_type: Mapped[str] = mapped_column(String(30))
    usage_id: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    asset: Mapped[MediaAsset] = relationship(lazy="joined")
