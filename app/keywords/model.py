from sqlalchemy import CheckConstraint, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class UserKeyword(Base):
    """프로필의 분야·스택·관심분야를 키워드 단위로 펼쳐 둔 표. 자동완성 집계의 원천이다."""

    __tablename__ = "user_keywords"
    __table_args__ = (
        UniqueConstraint("user_id", "kind", "keyword", name="uq_user_keywords_user_kind_keyword"),
        CheckConstraint("kind IN ('FIELD', 'STACK', 'INTEREST')", name="ck_user_keywords_kind"),
        Index("ix_user_keywords_kind_keyword", "kind", "keyword"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(10))
    keyword: Mapped[str] = mapped_column(String(100))
    display: Mapped[str] = mapped_column(String(100))
