from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class Term(Base):
    __tablename__ = "terms"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(20), unique=True)
    required: Mapped[bool] = mapped_column(Boolean)


class TermVersion(Base):
    __tablename__ = "term_versions"
    __table_args__ = (UniqueConstraint("term_id", "version", name="uq_term_versions_term_version"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    term_id: Mapped[int] = mapped_column(ForeignKey("terms.id", ondelete="CASCADE"), index=True)
    version: Mapped[str] = mapped_column(String(50))
    content: Mapped[str] = mapped_column(Text)
    effective_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class UserTermAgreement(Base):
    __tablename__ = "user_term_agreements"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    term_version_id: Mapped[int] = mapped_column(
        ForeignKey("term_versions.id", ondelete="RESTRICT"), index=True
    )
    agreed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    withdrawn_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
