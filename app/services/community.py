from collections.abc import Mapping
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session
from sqlalchemy.sql import Select

from app.models import (
    Comment,
    CommentTargetType,
    Post,
    PostBoard,
    PostCategory,
    PostLike,
    Region,
    User,
)

if TYPE_CHECKING:
    from app.schemas import PostResponse


def validate_post_category(
    board: PostBoard, category: PostCategory | None
) -> None:
    if board is PostBoard.TALK and category is None:
        raise ValueError("talk posts require a category")
    if board is not PostBoard.TALK and category is not None:
        raise ValueError("only talk posts accept a category")


def get_region_by_chapter_code(db: Session, chapter_code: str) -> Region | None:
    return db.scalar(select(Region).where(Region.name == chapter_code))


def serialize_author(
    author_id: int | None,
    nickname: str | None,
    deleted_at: datetime | None,
) -> dict[str, int | str | None]:
    if author_id is None or deleted_at is not None:
        return {
            "authorId": None,
            "authorNickname": "탈퇴한 사용자",
            "authorAvatarUrl": None,
        }
    return {
        "authorId": author_id,
        "authorNickname": nickname,
        "authorAvatarUrl": None,
    }


def select_posts_with_stats(current_user_id: int) -> Select:
    like_counts = (
        select(
            PostLike.post_id.label("post_id"),
            func.count(PostLike.id).label("like_count"),
        )
        .group_by(PostLike.post_id)
        .subquery()
    )
    comment_counts = (
        select(
            Comment.target_id.label("post_id"),
            func.count(Comment.id).label("comment_count"),
        )
        .where(
            Comment.target_type == CommentTargetType.POST,
            Comment.deleted_at.is_(None),
        )
        .group_by(Comment.target_id)
        .subquery()
    )
    liked_posts = (
        select(PostLike.post_id.label("post_id"))
        .where(PostLike.user_id == current_user_id)
        .subquery()
    )

    return (
        select(
            Post.id,
            Region.name.label("chapter_code"),
            Post.board,
            Post.category,
            Post.title,
            Post.body,
            Post.author_id,
            User.nickname.label("author_nickname"),
            User.deleted_at.label("author_deleted_at"),
            Post.created_at,
            Post.edited_at,
            func.coalesce(like_counts.c.like_count, 0).label("like_count"),
            func.coalesce(comment_counts.c.comment_count, 0).label("comment_count"),
            liked_posts.c.post_id.is_not(None).label("is_liked"),
        )
        .join(Region, Region.id == Post.region_id)
        .outerjoin(User, User.id == Post.author_id)
        .outerjoin(like_counts, like_counts.c.post_id == Post.id)
        .outerjoin(comment_counts, comment_counts.c.post_id == Post.id)
        .outerjoin(liked_posts, liked_posts.c.post_id == Post.id)
        .where(Post.deleted_at.is_(None))
    )


def post_response_from_row(row: Mapping[str, Any]) -> "PostResponse":
    from app.schemas import PostResponse

    return PostResponse(
        id=row["id"],
        chapterCode=row["chapter_code"],
        boardCode=row["board"],
        categoryCode=row["category"],
        title=row["title"],
        body=row["body"],
        createdAt=row["created_at"],
        editedAt=row["edited_at"],
        likeCount=row["like_count"],
        commentCount=row["comment_count"],
        isLiked=row["is_liked"],
        **serialize_author(
            row["author_id"], row["author_nickname"], row["author_deleted_at"]
        ),
    )
