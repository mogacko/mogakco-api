"""약관의 현재 동의 상태와 마케팅 동의 변경 이력."""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class TermAgreement(Base):
    __tablename__ = "term_agreements"
    __table_args__ = (UniqueConstraint("user_id", "type", "version"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    type: Mapped[str] = mapped_column(String(30))
    version: Mapped[str] = mapped_column(String(50))
    is_required: Mapped[bool] = mapped_column(Boolean, default=False)
    is_agreed: Mapped[bool] = mapped_column(Boolean)
    notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    signed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MarketingConsentHistory(Base):
    __tablename__ = "marketing_consent_history"
    __table_args__ = (
        CheckConstraint("type = 'MARKETING'", name="ck_marketing_consent_history_type"),
        CheckConstraint(
            "previous_is_agreed IS NULL OR previous_is_agreed <> is_agreed",
            name="ck_marketing_consent_history_changed",
        ),
        Index("ix_marketing_consent_history_user_changed", "user_id", "changed_at"),
    )

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    type: Mapped[str] = mapped_column(String(30))
    version: Mapped[str] = mapped_column(String(50))
    previous_is_agreed: Mapped[bool | None] = mapped_column(Boolean)
    is_agreed: Mapped[bool] = mapped_column(Boolean)
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
