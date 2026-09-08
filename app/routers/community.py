import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status
from redis import Redis
from redis.exceptions import RedisError
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.auth.errors import AuthErrors
from app.common.errors import CommonErrors
from app.community.errors import CommunityErrors
from app.database import get_db
from app.dependencies import get_current_user
from app.exceptions import (
    DomainValidationException,
    ForbiddenException,
    NotFoundException,
    ServiceUnavailableException,
)
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
from app.region.errors import RegionErrors
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
from app.schemas.error import error_responses
from app.services.community import (
    comment_threads_from_rows,
    community_post_like_key,
    community_post_list_item_from_row,
    get_community_post_like_stats,
    resolve_comment_target_id,
    select_comments_for_target,
    select_community_posts_with_stats,
    set_community_post_liked,
    validate_community_post_category,
    validate_community_post_filter,
)
from app.services.region import enabled_region
from app.time import kst_now

router = APIRouter(prefix="/api/v1", tags=["커뮤니티"])
logger = logging.getLogger(__name__)

_COMMON_ERRORS = (
    AuthErrors.REQUIRED,
    CommonErrors.INVALID_REQUEST,
    CommonErrors.INTERNAL_SERVER_ERROR,
    CommonErrors.CONFIGURATION_ERROR,
)


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
        raise NotFoundException(CommunityErrors.POST_NOT_FOUND)
    return community_post


def _editable_community_post(
    db: Session,
    community_post_uuid: UUID,
    user_id: int,
) -> CommunityPost:
    community_post = _active_community_post(db, community_post_uuid)
    if community_post.author_id != user_id:
        raise ForbiddenException(AuthErrors.FORBIDDEN)
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
        logger.warning("Redis like stats lookup failed", exc_info=True)
        raise ServiceUnavailableException(
            CommunityErrors.LIKE_SERVICE_UNAVAILABLE
        ) from error


def _page_response(
    rows: list,
    *,
    offset: int,
    limit: int,
    redis: Redis,
    current_user_id: int,
) -> CommunityPostPageResponse:
    page_rows = rows[:limit]
    stats = _like_stats(
        redis,
        [row["id"] for row in page_rows],
        current_user_id,
    )
    return CommunityPostPageResponse(
        items=[
            community_post_list_item_from_row(
                row,
                like_count=stats[row["id"]][0],
                is_liked=stats[row["id"]][1],
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
    responses=error_responses(
        *_COMMON_ERRORS,
        CommunityErrors.COMMENT_TARGET_NOT_FOUND,
    ),
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
        raise NotFoundException(CommunityErrors.COMMENT_TARGET_NOT_FOUND)
    rows = db.execute(
        select_comments_for_target(targetType, target_id)
    ).mappings().all()
    return comment_threads_from_rows(rows, current_user.id)


@router.post(
    "/comments",
    status_code=status.HTTP_201_CREATED,
    response_class=Response,
    responses=error_responses(
        *_COMMON_ERRORS,
        CommunityErrors.COMMENT_TARGET_NOT_FOUND,
        CommunityErrors.PARENT_COMMENT_NOT_FOUND,
        CommunityErrors.INVALID_COMMENT_PARENT,
    ),
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
        raise NotFoundException(CommunityErrors.COMMENT_TARGET_NOT_FOUND)

    parent_id = None
    if request.parentUuid is not None:
        parent = db.scalar(
            select(Comment).where(
                Comment.uuid == request.parentUuid,
                Comment.deleted_at.is_(None),
            ).with_for_update()
        )
        if parent is None:
            raise NotFoundException(CommunityErrors.PARENT_COMMENT_NOT_FOUND)
        if (
            parent.parent_comment_id is not None
            or parent.target_type is not request.targetType
            or parent.target_id != target_id
        ):
            raise DomainValidationException(
                CommunityErrors.INVALID_COMMENT_PARENT
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
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    responses=error_responses(
        *_COMMON_ERRORS,
        AuthErrors.FORBIDDEN,
        CommunityErrors.COMMENT_NOT_FOUND,
    ),
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
        raise NotFoundException(CommunityErrors.COMMENT_NOT_FOUND)
    if comment.user_id != current_user.id:
        raise ForbiddenException(AuthErrors.FORBIDDEN)

    comment.content = request.body
    comment.updated_at = kst_now()
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete(
    "/comments/{commentUuid}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=error_responses(
        *_COMMON_ERRORS,
        AuthErrors.FORBIDDEN,
        CommunityErrors.COMMENT_NOT_FOUND,
    ),
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
        raise NotFoundException(CommunityErrors.COMMENT_NOT_FOUND)
    if comment.user_id != current_user.id:
        raise ForbiddenException(AuthErrors.FORBIDDEN)

    comment.deleted_at = kst_now()
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/community-posts",
    response_model=CommunityPostPageResponse,
    responses=error_responses(
        *_COMMON_ERRORS,
        RegionErrors.NOT_FOUND,
        CommunityErrors.INVALID_MENU,
        CommunityErrors.LIKE_SERVICE_UNAVAILABLE,
    ),
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
    validate_community_post_filter(boardName, categoryName)

    region = enabled_region(db, regionName)
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
    return _page_response(
        rows,
        offset=offset,
        limit=limit,
        redis=redis,
        current_user_id=current_user.id,
    )


@router.get(
    "/community-posts/detail",
    response_model=CommunityPostDetailResponse,
    responses=error_responses(
        *_COMMON_ERRORS,
        CommunityErrors.POST_NOT_FOUND,
        CommunityErrors.LIKE_SERVICE_UNAVAILABLE,
    ),
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
            CommunityPost.updated_at,
        )
        .join(Region, Region.id == CommunityPost.region_id)
        .outerjoin(User, User.id == CommunityPost.author_id)
        .where(
            CommunityPost.uuid == communityPostUuid,
            CommunityPost.deleted_at.is_(None),
        )
    ).mappings().one_or_none()
    if row is None:
        raise NotFoundException(CommunityErrors.POST_NOT_FOUND)

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
        updatedAt=row["updated_at"],
        likeCount=like_count,
        isLiked=is_liked,
    )


@router.post(
    "/regions/{regionName}/community-posts",
    status_code=status.HTTP_201_CREATED,
    response_class=Response,
    responses=error_responses(
        *_COMMON_ERRORS,
        AuthErrors.FORBIDDEN,
        RegionErrors.NOT_FOUND,
        CommunityErrors.INVALID_MENU,
    ),
    summary="게시글 작성",
)
def create_community_post(
    regionName: str,
    request: CommunityPostCreateRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> Response:
    region = enabled_region(db, regionName)
    validate_community_post_category(request.boardName, request.categoryName)
    if request.boardName is CommunityPostBoard.NOTICE:
        raise ForbiddenException(AuthErrors.FORBIDDEN)

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
    responses=error_responses(
        *_COMMON_ERRORS,
        RegionErrors.NOT_FOUND,
        CommunityErrors.LIKE_SERVICE_UNAVAILABLE,
    ),
    summary="게시글 검색",
)
def search_community_posts(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    redis: Annotated[Redis, Depends(get_redis_client)],
    regionName: Annotated[str, Query()],
    q: Annotated[str, Query(min_length=1, max_length=100)],
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> CommunityPostPageResponse:
    region = enabled_region(db, regionName)
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
    return _page_response(
        rows,
        offset=offset,
        limit=limit,
        redis=redis,
        current_user_id=current_user.id,
    )


@router.post(
    "/community-posts/{communityPostUuid}/likes",
    response_model=LikeResponse,
    responses=error_responses(
        *_COMMON_ERRORS,
        CommunityErrors.POST_NOT_FOUND,
        CommunityErrors.LIKE_SERVICE_UNAVAILABLE,
    ),
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
        logger.warning("Redis like write failed", exc_info=True)
        raise ServiceUnavailableException(
            CommunityErrors.LIKE_SERVICE_UNAVAILABLE
        ) from error
    return LikeResponse(likeCount=like_count, isLiked=is_liked)


@router.delete(
    "/community-posts/{communityPostUuid}/likes",
    response_model=LikeResponse,
    responses=error_responses(
        *_COMMON_ERRORS,
        CommunityErrors.POST_NOT_FOUND,
        CommunityErrors.LIKE_SERVICE_UNAVAILABLE,
    ),
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
        logger.warning("Redis unlike write failed", exc_info=True)
        raise ServiceUnavailableException(
            CommunityErrors.LIKE_SERVICE_UNAVAILABLE
        ) from error
    return LikeResponse(likeCount=like_count, isLiked=is_liked)


@router.patch(
    "/community-posts/{communityPostUuid}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    responses=error_responses(
        *_COMMON_ERRORS,
        AuthErrors.FORBIDDEN,
        CommunityErrors.POST_NOT_FOUND,
        CommunityErrors.INVALID_MENU,
    ),
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
        validate_community_post_category(
            community_post.board,
            request.categoryName,
        )
        community_post.category = request.categoryName
    if "title" in request.model_fields_set:
        community_post.title = request.title
    if "body" in request.model_fields_set:
        community_post.body = request.body
    community_post.updated_at = kst_now()
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete(
    "/community-posts/{communityPostUuid}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=error_responses(
        *_COMMON_ERRORS,
        AuthErrors.FORBIDDEN,
        CommunityErrors.POST_NOT_FOUND,
    ),
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
    community_post.deleted_at = kst_now()
    db.commit()
    try:
        redis.delete(community_post_like_key(community_post.id))
    except RedisError:
        logger.exception("Redis like cleanup failed after post deletion")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
