from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from redis import Redis
from redis.exceptions import RedisError
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user
from app.models import (
    Comment,
    CommentTargetType,
    CommunityPost,
    CommunityPostBoard,
    CommunityPostCategory,
    Region,
    User,
)
from app.redis_client import get_redis_client
from app.schemas import (
    CommentCreateRequest,
    CommentThreadResponse,
    CommentUpdateRequest,
    CommunityPostCreateRequest,
    CommunityPostDetailResponse,
    CommunityPostPageResponse,
    CommunityPostUpdateRequest,
    LikeResponse,
)
from app.services.community import (
    comment_threads_from_rows,
    community_post_like_key,
    community_post_list_item_from_row,
    get_community_post_like_stats,
    get_region_by_name,
    resolve_comment_target_id,
    select_comments_for_target,
    select_community_posts_with_stats,
    set_community_post_liked,
    validate_community_post_category,
)

router = APIRouter(prefix="/api/v1", tags=["커뮤니티"])


def _enabled_region(db: Session, region_name: str) -> Region:
    region = get_region_by_name(db, region_name)
    if region is None or not region.is_enable:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Region not found")
    return region


def _active_community_post(
    db: Session,
    community_post_uuid: UUID,
) -> CommunityPost:
    community_post = db.scalar(
        select(CommunityPost).where(
            CommunityPost.uuid == community_post_uuid,
            CommunityPost.deleted_at.is_(None),
        )
    )
    if community_post is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "Community post not found",
        )
    return community_post


def _editable_community_post(
    db: Session,
    community_post_uuid: UUID,
    user_id: int,
) -> CommunityPost:
    community_post = _active_community_post(db, community_post_uuid)
    if community_post.author_id != user_id:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "올바르지 않은 접근입니다.",
        )
    return community_post


def _like_stats(
    redis: Redis,
    community_post_ids: list[int],
    current_user_id: int,
) -> dict[int, tuple[int, bool]]:
    try:
        return get_community_post_like_stats(
            redis,
            community_post_ids,
            current_user_id,
        )
    except RedisError as error:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Like service unavailable",
        ) from error


def _region_like_context(
    db: Session,
    redis: Redis,
    region_id: int,
    current_user_id: int,
) -> tuple[dict[int, tuple[int, bool]], set[int]]:
    # ponytail: active Region scan; add a ranked index only when volume demands it.
    candidates = db.execute(
        select(CommunityPost.id, CommunityPost.created_at).where(
            CommunityPost.region_id == region_id,
            CommunityPost.deleted_at.is_(None),
        )
    ).all()
    stats = _like_stats(
        redis,
        [community_post_id for community_post_id, _ in candidates],
        current_user_id,
    )
    popular_ids = {
        community_post_id
        for community_post_id, _ in sorted(
            candidates,
            key=lambda row: (
                stats[row[0]][0],
                row[1],
                row[0],
            ),
            reverse=True,
        )[:3]
    }
    return stats, popular_ids


def _page_response(
    rows: list,
    *,
    offset: int,
    limit: int,
    stats: dict[int, tuple[int, bool]],
    popular_ids: set[int],
) -> CommunityPostPageResponse:
    page_rows = rows[:limit]
    return CommunityPostPageResponse(
        items=[
            community_post_list_item_from_row(
                row,
                like_count=stats[row["id"]][0],
                is_liked=stats[row["id"]][1],
                is_popular=row["id"] in popular_ids,
            )
            for row in page_rows
        ],
        offset=offset,
        limit=limit,
        hasMore=len(rows) > limit,
    )


@router.get(
    "/comments",
    response_model=CommentThreadResponse,
    summary="댓글 목록 조회",
)
def list_comments(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    targetType: Annotated[CommentTargetType, Query()],
    targetUuid: Annotated[UUID, Query()],
) -> CommentThreadResponse:
    target_id = resolve_comment_target_id(db, targetType, targetUuid)
    if target_id is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Target not found")
    rows = db.execute(
        select_comments_for_target(targetType, target_id)
    ).mappings().all()
    return comment_threads_from_rows(rows, current_user.id)


@router.post(
    "/comments",
    status_code=status.HTTP_201_CREATED,
    response_class=Response,
    summary="댓글 작성",
)
def create_comment(
    request: CommentCreateRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> Response:
    target_id = resolve_comment_target_id(
        db,
        request.targetType,
        request.targetUuid,
    )
    if target_id is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Target not found")

    parent_id = None
    if request.parentUuid is not None:
        parent = db.scalar(
            select(Comment).where(
                Comment.uuid == request.parentUuid,
                Comment.deleted_at.is_(None),
            )
        )
        if parent is None:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                "Parent comment not found",
            )
        if (
            parent.parent_comment_id is not None
            or parent.target_type is not request.targetType
            or parent.target_id != target_id
        ):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "올바르지 않은 대상입니다.",
            )
        parent_id = parent.id

    db.add(
        Comment(
            target_type=request.targetType,
            target_id=target_id,
            parent_comment_id=parent_id,
            user_id=current_user.id,
            content=request.body,
        )
    )
    db.commit()
    return Response(status_code=status.HTTP_201_CREATED)


@router.patch(
    "/comments/{commentUuid}",
    status_code=status.HTTP_201_CREATED,
    response_class=Response,
    summary="댓글 수정",
)
def update_comment(
    commentUuid: UUID,
    request: CommentUpdateRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> Response:
    comment = db.scalar(
        select(Comment).where(
            Comment.uuid == commentUuid,
            Comment.deleted_at.is_(None),
        )
    )
    if comment is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Comment not found")
    if comment.user_id != current_user.id:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "올바르지 않은 접근입니다.",
        )

    comment.content = request.body
    comment.updated_at = datetime.now(UTC)
    db.commit()
    return Response(status_code=status.HTTP_201_CREATED)


@router.delete(
    "/comments/{commentUuid}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="댓글 삭제",
)
def delete_comment(
    commentUuid: UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> Response:
    comment = db.scalar(
        select(Comment).where(
            Comment.uuid == commentUuid,
            Comment.deleted_at.is_(None),
        )
    )
    if comment is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Comment not found")
    if comment.user_id != current_user.id:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "올바르지 않은 접근입니다.",
        )

    comment.deleted_at = datetime.now(UTC)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/community-posts",
    response_model=CommunityPostPageResponse,
    summary="게시글 목록 조회",
)
def list_community_posts(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    redis: Annotated[Redis, Depends(get_redis_client)],
    regionName: Annotated[str, Query()],
    boardName: Annotated[CommunityPostBoard, Query()],
    categoryName: Annotated[CommunityPostCategory | None, Query()] = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> CommunityPostPageResponse:
    if (
        categoryName is not None
        and boardName is not CommunityPostBoard.TALK
    ):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "올바르지 않은 메뉴입니다.",
        )

    region = _enabled_region(db, regionName)
    rows = db.execute(
        select_community_posts_with_stats()
        .where(
            CommunityPost.region_id == region.id,
            CommunityPost.board == boardName,
        )
        .where(
            CommunityPost.category == categoryName
            if categoryName is not None
            else True
        )
        .order_by(CommunityPost.created_at.desc(), CommunityPost.id.desc())
        .offset(offset)
        .limit(limit + 1)
    ).mappings().all()
    stats, popular_ids = _region_like_context(
        db,
        redis,
        region.id,
        current_user.id,
    )
    return _page_response(
        rows,
        offset=offset,
        limit=limit,
        stats=stats,
        popular_ids=popular_ids,
    )


@router.get(
    "/community-posts/detail",
    response_model=CommunityPostDetailResponse,
    summary="게시글 상세 조회",
)
def get_community_post_detail(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    redis: Annotated[Redis, Depends(get_redis_client)],
    communityPostUuid: Annotated[UUID, Query()],
) -> CommunityPostDetailResponse:
    row = db.execute(
        select(
            CommunityPost.id,
            CommunityPost.uuid,
            Region.name.label("region_name"),
            CommunityPost.board,
            CommunityPost.title,
            CommunityPost.body,
            CommunityPost.author_id,
            User.uuid.label("author_uuid"),
            User.nickname.label("author_nickname"),
            User.deleted_at.label("author_deleted_at"),
            CommunityPost.created_at,
            CommunityPost.edited_at,
        )
        .join(Region, Region.id == CommunityPost.region_id)
        .outerjoin(User, User.id == CommunityPost.author_id)
        .where(
            CommunityPost.uuid == communityPostUuid,
            CommunityPost.deleted_at.is_(None),
        )
    ).mappings().one_or_none()
    if row is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "Community post not found",
        )

    like_count, is_liked = _like_stats(
        redis,
        [row["id"]],
        current_user.id,
    )[row["id"]]
    author_active = (
        row["author_id"] is not None
        and row["author_deleted_at"] is None
    )
    return CommunityPostDetailResponse(
        uuid=row["uuid"],
        regionName=row["region_name"],
        boardName=row["board"],
        title=row["title"],
        body=row["body"],
        authorUuid=row["author_uuid"] if author_active else None,
        authorNickname=row["author_nickname"] if author_active else None,
        authorAvatarUrl=None,
        createdAt=row["created_at"],
        updatedAt=row["edited_at"],
        likeCount=like_count,
        isLiked=is_liked,
    )


@router.post(
    "/regions/{regionName}/community-posts",
    status_code=status.HTTP_201_CREATED,
    response_class=Response,
    summary="게시글 작성",
)
def create_community_post(
    regionName: str,
    request: CommunityPostCreateRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> Response:
    region = _enabled_region(db, regionName)
    if request.boardName is CommunityPostBoard.NOTICE:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "올바르지 않은 접근입니다.",
        )

    db.add(
        CommunityPost(
            author_id=current_user.id,
            region_id=region.id,
            board=request.boardName,
            category=request.categoryName,
            title=request.title,
            body=request.body,
        )
    )
    db.commit()
    return Response(status_code=status.HTTP_201_CREATED)


@router.get(
    "/community-posts/search",
    response_model=CommunityPostPageResponse,
    summary="게시글 검색",
)
def search_community_posts(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    redis: Annotated[Redis, Depends(get_redis_client)],
    regionName: Annotated[str, Query()],
    q: Annotated[str, Query()],
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> CommunityPostPageResponse:
    if not 1 <= len(q) <= 100:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "올바르지 않은 메뉴입니다.",
        )

    region = _enabled_region(db, regionName)
    pattern = f"%{q}%"
    rows = db.execute(
        select_community_posts_with_stats()
        .where(
            CommunityPost.region_id == region.id,
            or_(
                CommunityPost.title.ilike(pattern),
                CommunityPost.body.ilike(pattern),
                User.deleted_at.is_(None) & User.nickname.ilike(pattern),
            ),
        )
        .order_by(CommunityPost.created_at.desc(), CommunityPost.id.desc())
        .offset(offset)
        .limit(limit + 1)
    ).mappings().all()
    stats, popular_ids = _region_like_context(
        db,
        redis,
        region.id,
        current_user.id,
    )
    return _page_response(
        rows,
        offset=offset,
        limit=limit,
        stats=stats,
        popular_ids=popular_ids,
    )


@router.post(
    "/community-posts/{communityPostUuid}/likes",
    response_model=LikeResponse,
    summary="게시글 좋아요",
)
def like_community_post(
    communityPostUuid: UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    redis: Annotated[Redis, Depends(get_redis_client)],
) -> LikeResponse:
    community_post = _active_community_post(db, communityPostUuid)
    try:
        like_count, is_liked = set_community_post_liked(
            redis,
            community_post.id,
            current_user.id,
            liked=True,
        )
    except RedisError as error:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Like service unavailable",
        ) from error
    return LikeResponse(likeCount=like_count, isLiked=is_liked)


@router.delete(
    "/community-posts/{communityPostUuid}/likes",
    response_model=LikeResponse,
    summary="게시글 좋아요 취소",
)
def unlike_community_post(
    communityPostUuid: UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    redis: Annotated[Redis, Depends(get_redis_client)],
) -> LikeResponse:
    community_post = _active_community_post(db, communityPostUuid)
    try:
        like_count, is_liked = set_community_post_liked(
            redis,
            community_post.id,
            current_user.id,
            liked=False,
        )
    except RedisError as error:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Like service unavailable",
        ) from error
    return LikeResponse(likeCount=like_count, isLiked=is_liked)


@router.patch(
    "/community-posts/{communityPostUuid}",
    status_code=status.HTTP_201_CREATED,
    response_class=Response,
    summary="게시글 수정",
)
def update_community_post(
    communityPostUuid: UUID,
    request: CommunityPostUpdateRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> Response:
    community_post = _editable_community_post(
        db,
        communityPostUuid,
        current_user.id,
    )
    if "categoryName" in request.model_fields_set:
        try:
            validate_community_post_category(
                community_post.board,
                request.categoryName,
            )
        except ValueError as error:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "올바르지 않은 메뉴입니다.",
            ) from error
        community_post.category = request.categoryName
    if "title" in request.model_fields_set:
        community_post.title = request.title
    if "body" in request.model_fields_set:
        community_post.body = request.body
    community_post.edited_at = datetime.now(UTC)
    db.commit()
    return Response(status_code=status.HTTP_201_CREATED)


@router.delete(
    "/community-posts/{communityPostUuid}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="게시글 삭제",
)
def delete_community_post(
    communityPostUuid: UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    redis: Annotated[Redis, Depends(get_redis_client)],
) -> Response:
    community_post = _editable_community_post(
        db,
        communityPostUuid,
        current_user.id,
    )
    community_post.deleted_at = datetime.now(UTC)
    db.commit()
    try:
        redis.delete(community_post_like_key(community_post.id))
    except RedisError:
        pass
    return Response(status_code=status.HTTP_204_NO_CONTENT)
