from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from redis import Redis
from redis.exceptions import RedisError
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user
from app.models import (
    Comment,
    CommentTargetType,
    Post,
    PostBoard,
    PostCategory,
    Region,
    User,
)
from app.redis_client import get_redis_client
from app.schemas import (
    CommentCreateRequest,
    CommentResponse,
    CommentThreadResponse,
    CommentUpdateRequest,
    LikeResponse,
    PopularPostsResponse,
    PostCreateRequest,
    PostPageResponse,
    PostResponse,
    PostUpdateRequest,
)
from app.services.community import (
    comment_target_exists,
    comment_threads_from_rows,
    created_comment_response,
    get_region_by_chapter_code,
    get_post_like_stats,
    post_like_key,
    post_response_from_row,
    select_comments_for_target,
    select_posts_with_stats,
    set_post_liked,
    validate_post_category,
)

router = APIRouter(prefix="/api/v1", tags=["커뮤니티"])


def _enabled_region(db: Session, chapter_code: str) -> Region:
    region = get_region_by_chapter_code(db, chapter_code)
    if region is None or not region.is_enable:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Chapter not found")
    return region


def _editable_post(db: Session, post_id: int, user_id: int) -> Post:
    post = db.scalar(
        select(Post).where(Post.id == post_id, Post.deleted_at.is_(None))
    )
    if post is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Post not found")
    if post.author_id != user_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Not the post author")
    return post


def _like_stats(
    redis: Redis, post_ids: list[int], current_user_id: int
) -> dict[int, tuple[int, bool]]:
    try:
        return get_post_like_stats(redis, post_ids, current_user_id)
    except RedisError as error:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Like service unavailable"
        ) from error


def _post_responses(
    rows: list, redis: Redis, current_user_id: int
) -> list[PostResponse]:
    stats = _like_stats(redis, [row["id"] for row in rows], current_user_id)
    return [
        post_response_from_row(
            row,
            like_count=stats[row["id"]][0],
            is_liked=stats[row["id"]][1],
        )
        for row in rows
    ]


def _page_response(
    rows: list,
    *,
    offset: int,
    limit: int,
    total: int,
    redis: Redis,
    current_user_id: int,
    board_total: int | None = None,
    category_counts: dict[PostCategory, int] | None = None,
) -> PostPageResponse:
    return PostPageResponse(
        items=_post_responses(rows, redis, current_user_id),
        offset=offset,
        limit=limit,
        total=total,
        hasMore=offset + len(rows) < total,
        boardTotal=board_total,
        categoryCounts=category_counts,
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
    targetId: Annotated[int, Query(gt=0)],
) -> CommentThreadResponse:
    if not comment_target_exists(db, targetType, targetId):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Target not found")
    rows = db.execute(
        select_comments_for_target(targetType, targetId)
    ).mappings().all()
    return comment_threads_from_rows(rows, current_user.id)


@router.post(
    "/comments",
    response_model=CommentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="댓글 작성",
)
def create_comment(
    request: CommentCreateRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> CommentResponse:
    if not comment_target_exists(db, request.targetType, request.targetId):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Target not found")

    if request.parentId is not None:
        parent = db.get(Comment, request.parentId)
        if parent is None or parent.deleted_at is not None:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, "Parent comment not found"
            )
        if (
            parent.parent_comment_id is not None
            or parent.target_type is not request.targetType
            or parent.target_id != request.targetId
        ):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "Invalid comment parent",
            )

    comment = Comment(
        target_type=request.targetType,
        target_id=request.targetId,
        parent_comment_id=request.parentId,
        user_id=current_user.id,
        content=request.body,
    )
    db.add(comment)
    db.flush()
    response = created_comment_response(comment, current_user)
    db.commit()
    return response


@router.patch(
    "/comments/{commentId}",
    response_model=CommentResponse,
    summary="댓글 수정",
)
def update_comment(
    commentId: int,
    request: CommentUpdateRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> CommentResponse:
    comment = db.scalar(
        select(Comment).where(
            Comment.id == commentId,
            Comment.deleted_at.is_(None),
        )
    )
    if comment is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Comment not found")
    if comment.user_id != current_user.id:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Not the comment author"
        )

    comment.content = request.body
    comment.updated_at = datetime.now(UTC)
    db.flush()
    response = created_comment_response(comment, current_user)
    db.commit()
    return response


@router.delete(
    "/comments/{commentId}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="댓글 삭제",
)
def delete_comment(
    commentId: int,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> Response:
    comment = db.scalar(
        select(Comment).where(
            Comment.id == commentId,
            Comment.deleted_at.is_(None),
        )
    )
    if comment is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Comment not found")
    if comment.user_id != current_user.id:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Not the comment author"
        )

    comment.deleted_at = datetime.now(UTC)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/chapters/{chapterCode}/posts",
    response_model=PostPageResponse,
    summary="게시글 목록 조회",
)
def list_posts(
    chapterCode: str,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    redis: Annotated[Redis, Depends(get_redis_client)],
    boardCode: Annotated[PostBoard | None, Query()] = None,
    categoryCode: Annotated[PostCategory | None, Query()] = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> PostPageResponse:
    if categoryCode is not None and boardCode is not PostBoard.TALK:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "categoryCode requires boardCode=talk",
        )

    region = _enabled_region(db, chapterCode)
    filters = [Post.region_id == region.id, Post.deleted_at.is_(None)]
    if boardCode is not None:
        filters.append(Post.board == boardCode)
    board_filters = list(filters)
    if categoryCode is not None:
        filters.append(Post.category == categoryCode)

    total = db.scalar(select(func.count()).select_from(Post).where(*filters)) or 0
    board_total = None
    if boardCode is not None:
        board_total = (
            db.scalar(select(func.count()).select_from(Post).where(*board_filters)) or 0
        )

    category_counts = None
    if boardCode is PostBoard.TALK:
        counts = db.execute(
            select(Post.category, func.count())
            .where(*board_filters)
            .group_by(Post.category)
        ).all()
        category_counts = {category: 0 for category in PostCategory}
        category_counts.update({category: count for category, count in counts})

    rows = db.execute(
        select_posts_with_stats()
        .where(Post.region_id == region.id)
        .where(Post.board == boardCode if boardCode is not None else True)
        .where(Post.category == categoryCode if categoryCode is not None else True)
        .order_by(Post.created_at.desc(), Post.id.desc())
        .offset(offset)
        .limit(limit)
    ).mappings().all()
    return _page_response(
        rows,
        offset=offset,
        limit=limit,
        total=total,
        redis=redis,
        current_user_id=current_user.id,
        board_total=board_total,
        category_counts=category_counts,
    )


@router.post(
    "/chapters/{chapterCode}/posts",
    response_model=PostResponse,
    status_code=status.HTTP_201_CREATED,
    summary="게시글 작성",
)
def create_post(
    chapterCode: str,
    request: PostCreateRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    redis: Annotated[Redis, Depends(get_redis_client)],
) -> PostResponse:
    region = _enabled_region(db, chapterCode)
    if request.boardCode is PostBoard.NOTICE:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Notice posts require staff permission"
        )

    post = Post(
        author_id=current_user.id,
        region_id=region.id,
        board=request.boardCode,
        category=request.categoryCode,
        title=request.title,
        body=request.body,
    )
    db.add(post)
    db.flush()
    row = db.execute(
        select_posts_with_stats().where(Post.id == post.id)
    ).mappings().one()
    response = _post_responses([row], redis, current_user.id)[0]
    db.commit()
    return response


@router.get(
    "/chapters/{chapterCode}/posts/search",
    response_model=PostPageResponse,
    summary="게시글 검색",
)
def search_posts(
    chapterCode: str,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    redis: Annotated[Redis, Depends(get_redis_client)],
    q: Annotated[str, Query()],
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> PostPageResponse:
    query = q.strip()
    if not 1 <= len(query) <= 100:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "q must be between 1 and 100 characters after trimming",
        )

    region = _enabled_region(db, chapterCode)
    pattern = f"%{query}%"
    search_filter = or_(
        Post.title.ilike(pattern),
        Post.body.ilike(pattern),
        User.deleted_at.is_(None) & User.nickname.ilike(pattern),
    )
    base = select_posts_with_stats().where(
        Post.region_id == region.id, search_filter
    )
    total = db.scalar(
        select(func.count()).select_from(base.order_by(None).subquery())
    ) or 0
    rows = db.execute(
        base.order_by(Post.created_at.desc(), Post.id.desc())
        .offset(offset)
        .limit(limit)
    ).mappings().all()
    return _page_response(
        rows,
        offset=offset,
        limit=limit,
        total=total,
        redis=redis,
        current_user_id=current_user.id,
    )


@router.get(
    "/chapters/{chapterCode}/posts/popular",
    response_model=PopularPostsResponse,
    summary="인기 게시글 조회",
)
def popular_posts(
    chapterCode: str,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    redis: Annotated[Redis, Depends(get_redis_client)],
) -> PopularPostsResponse:
    region = _enabled_region(db, chapterCode)
    rows = db.execute(
        select_posts_with_stats()
        .where(
            Post.region_id == region.id,
            Post.board != PostBoard.NOTICE,
            Post.created_at >= datetime.now(UTC) - timedelta(days=7),
        )
    ).mappings().all()
    stats = _like_stats(redis, [row["id"] for row in rows], current_user.id)
    rows = sorted(
        (
            row
            for row in rows
            if stats[row["id"]][0] + row["comment_count"] * 2 >= 20
        ),
        key=lambda row: (
            stats[row["id"]][0] + row["comment_count"] * 2,
            row["created_at"],
            row["id"],
        ),
        reverse=True,
    )[:3]
    return PopularPostsResponse(
        items=[
            post_response_from_row(
                row,
                like_count=stats[row["id"]][0],
                is_liked=stats[row["id"]][1],
            )
            for row in rows
        ]
    )


@router.get(
    "/posts/{postId}",
    response_model=PostResponse,
    summary="게시글 상세 조회",
)
def get_post(
    postId: int,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    redis: Annotated[Redis, Depends(get_redis_client)],
) -> PostResponse:
    row = db.execute(
        select_posts_with_stats().where(Post.id == postId)
    ).mappings().one_or_none()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Post not found")
    return _post_responses([row], redis, current_user.id)[0]


@router.post(
    "/posts/{postId}/likes",
    response_model=LikeResponse,
    summary="게시글 좋아요",
)
def like_post(
    postId: int,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    redis: Annotated[Redis, Depends(get_redis_client)],
) -> LikeResponse:
    post = db.scalar(
        select(Post).where(Post.id == postId, Post.deleted_at.is_(None))
    )
    if post is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Post not found")

    try:
        like_count, is_liked = set_post_liked(
            redis, postId, current_user.id, liked=True
        )
    except RedisError as error:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Like service unavailable"
        ) from error
    return LikeResponse(likeCount=like_count, isLiked=is_liked)


@router.delete(
    "/posts/{postId}/likes",
    response_model=LikeResponse,
    summary="게시글 좋아요 취소",
)
def unlike_post(
    postId: int,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    redis: Annotated[Redis, Depends(get_redis_client)],
) -> LikeResponse:
    post = db.scalar(
        select(Post).where(Post.id == postId, Post.deleted_at.is_(None))
    )
    if post is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Post not found")

    try:
        like_count, is_liked = set_post_liked(
            redis, postId, current_user.id, liked=False
        )
    except RedisError as error:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Like service unavailable"
        ) from error
    return LikeResponse(likeCount=like_count, isLiked=is_liked)


@router.patch(
    "/posts/{postId}",
    response_model=PostResponse,
    summary="게시글 수정",
)
def update_post(
    postId: int,
    request: PostUpdateRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    redis: Annotated[Redis, Depends(get_redis_client)],
) -> PostResponse:
    post = _editable_post(db, postId, current_user.id)
    if "categoryCode" in request.model_fields_set:
        try:
            validate_post_category(post.board, request.categoryCode)
        except ValueError as error:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)
            ) from error
        post.category = request.categoryCode
    if "title" in request.model_fields_set:
        post.title = request.title
    if "body" in request.model_fields_set:
        post.body = request.body
    post.edited_at = datetime.now(UTC)

    db.flush()
    row = db.execute(
        select_posts_with_stats().where(Post.id == post.id)
    ).mappings().one()
    response = _post_responses([row], redis, current_user.id)[0]
    db.commit()
    return response


@router.delete(
    "/posts/{postId}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="게시글 삭제",
)
def delete_post(
    postId: int,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    redis: Annotated[Redis, Depends(get_redis_client)],
) -> Response:
    post = _editable_post(db, postId, current_user.id)
    post.deleted_at = datetime.now(UTC)
    db.commit()
    try:
        redis.delete(post_like_key(postId))
    except RedisError:
        pass
    return Response(status_code=status.HTTP_204_NO_CONTENT)
