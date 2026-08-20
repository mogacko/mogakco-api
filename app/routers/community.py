from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user
from app.models import Post, PostBoard, PostCategory, Region, User
from app.schemas import (
    PopularPostsResponse,
    PostCreateRequest,
    PostPageResponse,
    PostResponse,
    PostUpdateRequest,
)
from app.services.community import (
    get_region_by_chapter_code,
    post_response_from_row,
    select_posts_with_stats,
    validate_post_category,
)

router = APIRouter(prefix="/api/v1", tags=["community"])


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


def _page_response(
    rows: list,
    *,
    offset: int,
    limit: int,
    total: int,
    board_total: int | None = None,
    category_counts: dict[PostCategory, int] | None = None,
) -> PostPageResponse:
    return PostPageResponse(
        items=[post_response_from_row(row) for row in rows],
        offset=offset,
        limit=limit,
        total=total,
        hasMore=offset + len(rows) < total,
        boardTotal=board_total,
        categoryCounts=category_counts,
    )


@router.get("/chapters/{chapterCode}/posts", response_model=PostPageResponse)
def list_posts(
    chapterCode: str,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
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
        select_posts_with_stats(current_user.id)
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
        board_total=board_total,
        category_counts=category_counts,
    )


@router.post(
    "/chapters/{chapterCode}/posts",
    response_model=PostResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_post(
    chapterCode: str,
    request: PostCreateRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
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
        select_posts_with_stats(current_user.id).where(Post.id == post.id)
    ).mappings().one()
    response = post_response_from_row(row)
    db.commit()
    return response


@router.get("/chapters/{chapterCode}/posts/search", response_model=PostPageResponse)
def search_posts(
    chapterCode: str,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
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
    base = select_posts_with_stats(current_user.id).where(
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
    return _page_response(rows, offset=offset, limit=limit, total=total)


@router.get("/chapters/{chapterCode}/posts/popular", response_model=PopularPostsResponse)
def popular_posts(
    chapterCode: str,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> PopularPostsResponse:
    region = _enabled_region(db, chapterCode)
    posts = (
        select_posts_with_stats(current_user.id)
        .where(
            Post.region_id == region.id,
            Post.board != PostBoard.NOTICE,
            Post.created_at >= datetime.now(UTC) - timedelta(days=7),
        )
        .subquery()
    )
    score = posts.c.like_count + posts.c.comment_count * 2
    rows = db.execute(
        select(posts)
        .where(score >= 20)
        .order_by(score.desc(), posts.c.created_at.desc(), posts.c.id.desc())
        .limit(3)
    ).mappings().all()
    return PopularPostsResponse(items=[post_response_from_row(row) for row in rows])


@router.get("/posts/{postId}", response_model=PostResponse)
def get_post(
    postId: int,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> PostResponse:
    row = db.execute(
        select_posts_with_stats(current_user.id).where(Post.id == postId)
    ).mappings().one_or_none()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Post not found")
    return post_response_from_row(row)


@router.patch("/posts/{postId}", response_model=PostResponse)
def update_post(
    postId: int,
    request: PostUpdateRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
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
        select_posts_with_stats(current_user.id).where(Post.id == post.id)
    ).mappings().one()
    response = post_response_from_row(row)
    db.commit()
    return response


@router.delete("/posts/{postId}", status_code=status.HTTP_204_NO_CONTENT)
def delete_post(
    postId: int,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> Response:
    post = _editable_post(db, postId, current_user.id)
    post.deleted_at = datetime.now(UTC)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
