from collections.abc import Mapping
from datetime import datetime
from typing import TYPE_CHECKING, Any

from redis import Redis
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from sqlalchemy.sql import Select

from app.models import (
    Comment,
    CommentTargetType,
    Post,
    PostBoard,
    PostCategory,
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


def select_posts_with_stats() -> Select:
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
            func.coalesce(comment_counts.c.comment_count, 0).label("comment_count"),
        )
        .join(Region, Region.id == Post.region_id)
        .outerjoin(User, User.id == Post.author_id)
        .outerjoin(comment_counts, comment_counts.c.post_id == Post.id)
        .where(Post.deleted_at.is_(None))
    )


def post_like_key(post_id: int) -> str:
    return f"post:like:{post_id}:users"


def get_post_like_stats(
    redis: Redis, post_ids: list[int], current_user_id: int
) -> dict[int, tuple[int, bool]]:
    unique_ids = list(dict.fromkeys(post_ids))
    if not unique_ids:
        return {}

    pipeline = redis.pipeline(transaction=False)
    for post_id in unique_ids:
        key = post_like_key(post_id)
        pipeline.scard(key)
        pipeline.sismember(key, current_user_id)
    results = pipeline.execute()
    return {
        post_id: (int(results[index]), bool(results[index + 1]))
        for index, post_id in zip(range(0, len(results), 2), unique_ids)
    }


def set_post_liked(
    redis: Redis, post_id: int, user_id: int, *, liked: bool
) -> tuple[int, bool]:
    key = post_like_key(post_id)
    pipeline = redis.pipeline(transaction=True)
    if liked:
        pipeline.sadd(key, user_id)
    else:
        pipeline.srem(key, user_id)
    pipeline.scard(key)
    _, like_count = pipeline.execute()
    return int(like_count), liked


def post_response_from_row(
    row: Mapping[str, Any], *, like_count: int = 0, is_liked: bool = False
) -> "PostResponse":
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
        likeCount=like_count,
        commentCount=row["comment_count"],
        isLiked=is_liked,
        **serialize_author(
            row["author_id"], row["author_nickname"], row["author_deleted_at"]
        ),
    )
