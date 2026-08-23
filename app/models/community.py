from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.core import utc_now


class PostBoard(StrEnum):
    NOTICE = "notice"
    QUESTION = "question"
    TALK = "talk"


class PostCategory(StrEnum):
    FREE = "free"
    RETROSPECTIVE = "retrospective"
    RECRUIT = "recruit"


class CommentTargetType(StrEnum):
    POST = "post"
    EVENT = "event"
    MEETUP = "meetup"


class Post(Base):
    __tablename__ = "posts"
    __table_args__ = (
        CheckConstraint("char_length(body) <= 10000", name="ck_posts_body_length"),
        Index("ix_posts_region_board_created", "region_id", "board", "created_at"),
        Index(
            "ix_posts_region_board_category_created",
            "region_id",
            "board",
            "category",
            "created_at",
        ),
        Index("ix_posts_author_id", "author_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    author_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    region_id: Mapped[int] = mapped_column(ForeignKey("region.id"))
    board: Mapped[PostBoard] = mapped_column(
        Enum(
            PostBoard,
            native_enum=False,
            length=20,
            values_callable=lambda enum: [e.value for e in enum],
        )
    )
    category: Mapped[PostCategory | None] = mapped_column(
        Enum(
            PostCategory,
            native_enum=False,
            length=30,
            values_callable=lambda enum: [e.value for e in enum],
        )
    )
    title: Mapped[str] = mapped_column(String(60))
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )
    edited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Comment(Base):
    __tablename__ = "comments"
    __table_args__ = (
        CheckConstraint(
            "target_type IN ('post', 'event', 'meetup')",
            name="ck_comments_target_type",
        ),
        Index(
            "ix_comments_target_created",
            "target_type",
            "target_id",
            "created_at",
        ),
        UniqueConstraint("uuid", name="uq_comments_uuid"),
        Index("ix_comments_parent_comment_id", "parent_comment_id"),
        Index("ix_comments_user_id", "user_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    uuid: Mapped[UUID] = mapped_column(
        Uuid,
        default=uuid4,
        server_default=text("gen_random_uuid()"),
    )
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    target_type: Mapped[CommentTargetType] = mapped_column(
        Enum(
            CommentTargetType,
            native_enum=False,
            length=20,
            values_callable=lambda enum: [e.value for e in enum],
        )
    )
    target_id: Mapped[int] = mapped_column(Integer)
    parent_comment_id: Mapped[int | None] = mapped_column(
        ForeignKey("comments.id", ondelete="RESTRICT")
    )
    content: Mapped[str] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
