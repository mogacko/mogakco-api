"""사용자별 소속, 기술 스택과 관심 분야."""

from enum import StrEnum

from sqlalchemy import (
    CheckConstraint,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class UserAttributeType(StrEnum):
    AFFILIATION = "AFFILIATION"
    STACK = "STACK"
    INTEREST = "INTEREST"


class UserAttribute(Base):
    __tablename__ = "user_attributes"
    __table_args__ = (
        UniqueConstraint("user_id", "type", "value_normalized"),
        CheckConstraint(
            "type IN ('AFFILIATION', 'STACK', 'INTEREST')",
            name="ck_user_attributes_type",
        ),
        CheckConstraint(
            "char_length(value) BETWEEN 1 AND 50",
            name="ck_user_attributes_value_length",
        ),
        CheckConstraint(
            "type <> 'AFFILIATION' OR char_length(value) <= 20",
            name="ck_user_attributes_affiliation_length",
        ),
        Index(
            "uq_user_attributes_affiliation_user_id",
            "user_id",
            unique=True,
            postgresql_where=text("type = 'AFFILIATION'"),
            sqlite_where=text("type = 'AFFILIATION'"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    type: Mapped[UserAttributeType] = mapped_column(
        Enum(
            UserAttributeType,
            native_enum=False,
            length=20,
            values_callable=lambda enum: [item.value for item in enum],
        )
    )
    value: Mapped[str] = mapped_column(String(50))
    value_normalized: Mapped[str] = mapped_column(String(150))
