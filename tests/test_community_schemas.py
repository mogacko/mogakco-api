import os
from datetime import UTC, datetime

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.main import app
from app.models import Comment, CommentTargetType, Post, PostBoard, User
from app.schemas import CommentCreateRequest, PostCreateRequest, PostUpdateRequest
from app.services.community import post_response_from_row, select_posts_with_stats

DATABASE_URL = os.environ["DATABASE_URL"]


def test_openapi_community_contract() -> None:
    schema = app.openapi()
    paths = schema["paths"]
    summaries = {
        ("GET", "/api/v1/chapters/{chapterCode}/posts"): "게시글 목록 조회",
        ("POST", "/api/v1/chapters/{chapterCode}/posts"): "게시글 작성",
        ("GET", "/api/v1/chapters/{chapterCode}/posts/search"): "게시글 검색",
        ("GET", "/api/v1/chapters/{chapterCode}/posts/popular"): "인기 게시글 조회",
        ("GET", "/api/v1/posts/{postId}"): "게시글 상세 조회",
        ("PATCH", "/api/v1/posts/{postId}"): "게시글 수정",
        ("DELETE", "/api/v1/posts/{postId}"): "게시글 삭제",
        ("POST", "/api/v1/posts/{postId}/likes"): "게시글 좋아요",
        ("DELETE", "/api/v1/posts/{postId}/likes"): "게시글 좋아요 취소",
        ("GET", "/api/v1/comments"): "댓글 목록 조회",
        ("POST", "/api/v1/comments"): "댓글 작성",
        ("PATCH", "/api/v1/comments/{commentId}"): "댓글 수정",
        ("DELETE", "/api/v1/comments/{commentId}"): "댓글 삭제",
    }
    assert {
        (method.upper(), path)
        for path, operations in paths.items()
        for method in operations
    } >= summaries.keys()
    for (method, path), summary in summaries.items():
        operation = paths[path][method.lower()]
        assert operation["summary"] == summary
        assert operation["tags"] == ["커뮤니티"]

    for path, method in (
        ("/api/v1/posts/{postId}", "delete"),
        ("/api/v1/comments/{commentId}", "delete"),
    ):
        operation = paths[path][method]
        assert "requestBody" not in operation
        assert "content" not in operation["responses"]["204"]
    for method in ("post", "delete"):
        assert "requestBody" not in paths["/api/v1/posts/{postId}/likes"][method]

    schemas = schema["components"]["schemas"]
    assert schemas["PostBoard"]["enum"] == ["notice", "question", "talk"]
    assert schemas["PostCategory"]["enum"] == [
        "free",
        "retrospective",
        "recruit",
    ]
    assert schemas["CommentTargetType"]["enum"] == [
        "COMMUNITY_POST",
        "MOGACKO",
        "EVENT",
    ]
    assert "운영자" in schemas["CommentThread"]["properties"]["masked"][
        "description"
    ]
    assert schemas["PostUpdateRequest"]["minProperties"] == 1
    assert schemas["PostUpdateRequest"]["additionalProperties"] is False
    assert set(schemas["PostUpdateRequest"]["properties"]) == {
        "title",
        "body",
        "categoryCode",
    }
    assert schemas["CommentUpdateRequest"]["additionalProperties"] is False
    assert set(schemas["CommentUpdateRequest"]["properties"]) == {"body"}
    for response_schema in ("PostResponse", "CommentResponse"):
        assert "authorAvatarUrl" in schemas[response_schema]["required"]


def test_request_schemas_trim_and_enforce_contract() -> None:
    request = PostCreateRequest(
        boardCode="talk", categoryCode="free", title="  제목  ", body="  본문  "
    )
    assert request.title == "제목"
    assert request.body == "본문"

    with pytest.raises(ValidationError):
        PostCreateRequest(
            boardCode="talk",
            categoryCode="free",
            title="제목",
            body="본문",
            authorId=1,
        )
    with pytest.raises(ValidationError):
        PostCreateRequest(boardCode="talk", title="제목", body="본문")
    with pytest.raises(ValidationError):
        PostCreateRequest(
            boardCode="question", categoryCode="free", title="제목", body="본문"
        )
    with pytest.raises(ValidationError):
        PostUpdateRequest()
    with pytest.raises(ValidationError):
        PostUpdateRequest(title=None)
    with pytest.raises(ValidationError):
        CommentCreateRequest(
            targetType="COMMUNITY_POST", targetId=1, body="   "
        )


def test_post_query_masks_deleted_authors() -> None:
    command.upgrade(Config("alembic.ini"), "head")
    engine = sa.create_engine(DATABASE_URL)

    with Session(engine) as session:
        session.execute(sa.delete(Comment))
        session.execute(sa.delete(Post))
        session.execute(sa.delete(User))

        viewer = User(nickname="viewer", region_id=1)
        soft_deleted = User(
            nickname="soft-deleted", region_id=1, deleted_at=datetime.now(UTC)
        )
        hard_deleted = User(nickname="hard-deleted", region_id=1)
        session.add_all([viewer, soft_deleted, hard_deleted])
        session.flush()
        viewer_id = viewer.id

        active_post = Post(
            author_id=viewer_id,
            region_id=1,
            board=PostBoard.QUESTION,
            title="active",
            body="body",
        )
        soft_post = Post(
            author_id=soft_deleted.id,
            region_id=1,
            board=PostBoard.QUESTION,
            title="soft",
            body="body",
        )
        hard_post = Post(
            author_id=hard_deleted.id,
            region_id=1,
            board=PostBoard.QUESTION,
            title="hard",
            body="body",
        )
        session.add_all([active_post, soft_post, hard_post])
        session.flush()
        session.add_all(
            [
                Comment(
                    user_id=viewer_id,
                    target_type=CommentTargetType.COMMUNITY_POST,
                    target_id=active_post.id,
                    content="one",
                ),
                Comment(
                    user_id=viewer_id,
                    target_type=CommentTargetType.COMMUNITY_POST,
                    target_id=active_post.id,
                    content="two",
                ),
                Comment(
                    user_id=viewer_id,
                    target_type=CommentTargetType.COMMUNITY_POST,
                    target_id=active_post.id,
                    content="deleted",
                    deleted_at=datetime.now(UTC),
                ),
            ]
        )
        session.delete(hard_deleted)
        session.commit()

        statement_count = 0

        def count_statement(*_args: object) -> None:
            nonlocal statement_count
            statement_count += 1

        sa.event.listen(engine, "before_cursor_execute", count_statement)
        try:
            rows = session.execute(
                select_posts_with_stats().order_by(Post.id)
            ).mappings().all()
        finally:
            sa.event.remove(engine, "before_cursor_execute", count_statement)

        responses = [post_response_from_row(row) for row in rows]

    assert statement_count == 1
    assert len(responses) == 3
    assert responses[0].chapterCode == "seoul"
    assert responses[0].likeCount == 0
    assert responses[0].commentCount == 2
    assert responses[0].isLiked is False
    assert responses[0].authorNickname == "viewer"
    assert responses[1].authorId is None
    assert responses[1].authorNickname == "탈퇴한 사용자"
    assert responses[2].authorId is None
    assert responses[2].authorNickname == "탈퇴한 사용자"
    engine.dispose()
