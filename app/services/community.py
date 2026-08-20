from collections.abc import Mapping
from datetime import datetime
from typing import TYPE_CHECKING, Any

from redis import Redis
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session, aliased
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
    from app.schemas import CommentResponse, CommentThreadResponse, PostResponse


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
    parent_comment = aliased(Comment)
    comment_counts = (
        select(
            Comment.target_id.label("post_id"),
            func.count(Comment.id).label("comment_count"),
        )
        .outerjoin(
            parent_comment,
            parent_comment.id == Comment.parent_comment_id,
        )
        .where(
            Comment.target_type == CommentTargetType.POST,
            Comment.deleted_at.is_(None),
            or_(
                Comment.parent_comment_id.is_(None),
                and_(
                    parent_comment.parent_comment_id.is_(None),
                    parent_comment.target_type == Comment.target_type,
                    parent_comment.target_id == Comment.target_id,
                ),
            ),
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


def comment_target_exists(
    db: Session, target_type: CommentTargetType, target_id: int
) -> bool:
    if target_type is not CommentTargetType.POST:
        return False
    return db.scalar(
        select(Post.id).where(Post.id == target_id, Post.deleted_at.is_(None))
    ) is not None


def select_comments_for_target(
    target_type: CommentTargetType, target_id: int
) -> Select:
    return (
        select(
            Comment.id,
            Comment.target_type,
            Comment.target_id,
            Comment.parent_comment_id,
            Comment.user_id,
            User.nickname.label("author_nickname"),
            User.deleted_at.label("author_deleted_at"),
            Comment.content,
            Comment.created_at,
            Comment.updated_at,
            Comment.deleted_at,
        )
        .outerjoin(User, User.id == Comment.user_id)
        .where(
            Comment.target_type == target_type,
            Comment.target_id == target_id,
        )
        .order_by(Comment.created_at, Comment.id)
    )


def _comment_response(
    row: Mapping[str, Any], current_user_id: int, *, tombstone: bool = False
) -> "CommentResponse":
    from app.schemas import CommentResponse

    author_active = (
        row["user_id"] is not None and row["author_deleted_at"] is None
    )
    return CommentResponse(
        id=row["id"],
        targetType=row["target_type"],
        targetId=row["target_id"],
        parentId=row["parent_comment_id"],
        body="" if tombstone else row["content"],
        createdAt=row["created_at"],
        editedAt=row["updated_at"],
        isDeleted=row["deleted_at"] is not None,
        isMine=author_active and row["user_id"] == current_user_id,
        **serialize_author(
            row["user_id"], row["author_nickname"], row["author_deleted_at"]
        ),
    )


def comment_threads_from_rows(
    rows: list[Mapping[str, Any]], current_user_id: int
) -> "CommentThreadResponse":
    from app.schemas import CommentThread, CommentThreadResponse

    roots = [row for row in rows if row["parent_comment_id"] is None]
    root_ids = {row["id"] for row in roots}
    replies: dict[int, list[Mapping[str, Any]]] = {root_id: [] for root_id in root_ids}
    for row in rows:
        parent_id = row["parent_comment_id"]
        if parent_id in root_ids and row["deleted_at"] is None:
            replies[parent_id].append(row)

    items = []
    count = 0
    for root in roots:
        visible_replies = replies[root["id"]]
        root_deleted = root["deleted_at"] is not None
        if root_deleted and not visible_replies:
            continue
        count += (not root_deleted) + len(visible_replies)
        items.append(
            CommentThread(
                comment=_comment_response(
                    root, current_user_id, tombstone=root_deleted
                ),
                masked=root_deleted,
                replies=[
                    _comment_response(reply, current_user_id)
                    for reply in visible_replies
                ],
            )
        )
    return CommentThreadResponse(count=count, items=items)


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
