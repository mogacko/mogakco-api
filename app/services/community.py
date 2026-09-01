from collections.abc import Mapping
from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

from redis import Redis
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session, aliased
from sqlalchemy.sql import Select

from app.models import (
    Comment,
    CommentTargetType,
    CommunityPost,
    CommunityPostBoard,
    CommunityPostCategory,
    Region,
    User,
)

if TYPE_CHECKING:
    from app.schemas import (
        CommentResponse,
        CommentThreadResponse,
        CommunityPostListItem,
    )


def validate_community_post_category(
    board: CommunityPostBoard,
    category: CommunityPostCategory | None,
) -> None:
    if board is CommunityPostBoard.TALK and category is None:
        raise ValueError("talk community posts require a category")
    if board is not CommunityPostBoard.TALK and category is not None:
        raise ValueError("only talk community posts accept a category")


def get_region_by_name(db: Session, region_name: str) -> Region | None:
    return db.scalar(select(Region).where(Region.name == region_name))


def select_community_posts_with_stats() -> Select:
    parent_comment = aliased(Comment)
    comment_counts = (
        select(
            Comment.target_id.label("community_post_id"),
            func.count(Comment.id).label("comment_count"),
        )
        .outerjoin(
            parent_comment,
            parent_comment.id == Comment.parent_comment_id,
        )
        .where(
            Comment.target_type == CommentTargetType.COMMUNITY_POST,
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
            CommunityPost.id,
            CommunityPost.uuid,
            CommunityPost.category,
            CommunityPost.title,
            CommunityPost.body,
            CommunityPost.author_id,
            User.uuid.label("author_uuid"),
            User.nickname.label("author_nickname"),
            User.deleted_at.label("author_deleted_at"),
            CommunityPost.created_at,
            CommunityPost.updated_at,
            func.coalesce(comment_counts.c.comment_count, 0).label(
                "comment_count"
            ),
        )
        .outerjoin(User, User.id == CommunityPost.author_id)
        .outerjoin(
            comment_counts,
            comment_counts.c.community_post_id == CommunityPost.id,
        )
        .where(CommunityPost.deleted_at.is_(None))
    )


def resolve_comment_target_id(
    db: Session,
    target_type: CommentTargetType,
    target_uuid: UUID,
) -> int | None:
    if target_type is not CommentTargetType.COMMUNITY_POST:
        return None
    return db.scalar(
        select(CommunityPost.id).where(
            CommunityPost.uuid == target_uuid,
            CommunityPost.deleted_at.is_(None),
        )
    )


def select_comments_for_target(
    target_type: CommentTargetType,
    target_id: int,
) -> Select:
    parent_comment = aliased(Comment)
    return (
        select(
            Comment.id,
            Comment.uuid,
            Comment.parent_comment_id,
            parent_comment.uuid.label("parent_uuid"),
            Comment.user_id,
            User.uuid.label("author_uuid"),
            User.nickname.label("author_nickname"),
            User.deleted_at.label("author_deleted_at"),
            Comment.content,
            Comment.created_at,
            Comment.updated_at,
            Comment.deleted_at,
        )
        .outerjoin(User, User.id == Comment.user_id)
        .outerjoin(parent_comment, parent_comment.id == Comment.parent_comment_id)
        .where(
            Comment.target_type == target_type,
            Comment.target_id == target_id,
        )
        .order_by(Comment.created_at, Comment.id)
    )


def _comment_response(
    row: Mapping[str, Any],
    current_user_id: int,
    *,
    tombstone: bool = False,
) -> "CommentResponse":
    from app.schemas import CommentResponse

    author_active = (
        row["user_id"] is not None and row["author_deleted_at"] is None
    )
    return CommentResponse(
        uuid=row["uuid"],
        parentUuid=row["parent_uuid"],
        authorUuid=row["author_uuid"] if author_active else None,
        authorNickname=row["author_nickname"] if author_active else None,
        authorAvatarUrl=None,
        body="" if tombstone else row["content"],
        createdAt=row["created_at"],
        updatedAt=row["updated_at"],
        isDeleted=row["deleted_at"] is not None,
        isMine=author_active and row["user_id"] == current_user_id,
    )


def comment_threads_from_rows(
    rows: list[Mapping[str, Any]],
    current_user_id: int,
) -> "CommentThreadResponse":
    from app.schemas import CommentThread, CommentThreadResponse

    roots = [row for row in rows if row["parent_comment_id"] is None]
    root_ids = {row["id"] for row in roots}
    replies: dict[int, list[Mapping[str, Any]]] = {
        root_id: [] for root_id in root_ids
    }
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
                    root,
                    current_user_id,
                    tombstone=root_deleted,
                ),
                masked=False,
                replies=[
                    _comment_response(reply, current_user_id)
                    for reply in visible_replies
                ],
            )
        )
    return CommentThreadResponse(count=count, items=items)


def community_post_like_key(community_post_id: int) -> str:
    return f"community_post:like:{community_post_id}:users"


def get_community_post_like_stats(
    redis: Redis,
    community_post_ids: list[int],
    current_user_id: int,
) -> dict[int, tuple[int, bool]]:
    unique_ids = list(dict.fromkeys(community_post_ids))
    if not unique_ids:
        return {}

    pipeline = redis.pipeline(transaction=False)
    for community_post_id in unique_ids:
        key = community_post_like_key(community_post_id)
        pipeline.scard(key)
        pipeline.sismember(key, current_user_id)
    results = pipeline.execute()
    return {
        community_post_id: (int(results[index]), bool(results[index + 1]))
        for index, community_post_id in zip(
            range(0, len(results), 2),
            unique_ids,
        )
    }


def set_community_post_liked(
    redis: Redis,
    community_post_id: int,
    user_id: int,
    *,
    liked: bool,
) -> tuple[int, bool]:
    key = community_post_like_key(community_post_id)
    pipeline = redis.pipeline(transaction=True)
    if liked:
        pipeline.sadd(key, user_id)
    else:
        pipeline.srem(key, user_id)
    pipeline.scard(key)
    _, like_count = pipeline.execute()
    return int(like_count), liked


def community_post_list_item_from_row(
    row: Mapping[str, Any],
    *,
    like_count: int,
    is_liked: bool,
) -> "CommunityPostListItem":
    from app.schemas import CommunityPostListItem

    author_active = (
        row["author_id"] is not None and row["author_deleted_at"] is None
    )
    return CommunityPostListItem(
        uuid=row["uuid"],
        categoryName=row["category"],
        title=row["title"],
        body=row["body"][:60],
        authorNickname=row["author_nickname"] if author_active else None,
        authorAvatarUrl=None,
        createdAt=row["created_at"],
        updatedAt=row["updated_at"],
        likeCount=like_count,
        commentCount=row["comment_count"],
        isLiked=is_liked,
    )
