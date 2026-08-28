import os
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.database import create_db_engine
from app.main import app
from app.models import (
    Comment,
    CommentTargetType,
    CommunityPost,
    CommunityPostBoard,
    User,
)
from app.schemas import (
    CommentCreateRequest,
    CommunityPostCreateRequest,
    CommunityPostUpdateRequest,
)
from app.services.community import (
    community_post_list_item_from_row,
    select_community_posts_with_stats,
)
from app.time import kst_now

DATABASE_URL = os.environ["DATABASE_URL"]


def test_openapi_community_contract() -> None:
    schema = app.openapi()
    paths = schema["paths"]
    summaries = {
        ("GET", "/api/v1/community-posts"): "게시글 목록 조회",
        ("GET", "/api/v1/community-posts/detail"): "게시글 상세 조회",
        ("POST", "/api/v1/regions/{regionName}/community-posts"): "게시글 작성",
        ("GET", "/api/v1/community-posts/search"): "게시글 검색",
        ("PATCH", "/api/v1/community-posts/{communityPostUuid}"): "게시글 수정",
        ("DELETE", "/api/v1/community-posts/{communityPostUuid}"): "게시글 삭제",
        (
            "POST",
            "/api/v1/community-posts/{communityPostUuid}/likes",
        ): "게시글 좋아요",
        (
            "DELETE",
            "/api/v1/community-posts/{communityPostUuid}/likes",
        ): "게시글 좋아요 취소",
        ("GET", "/api/v1/comments"): "댓글 목록 조회",
        ("POST", "/api/v1/comments"): "댓글 작성",
        ("PATCH", "/api/v1/comments/{commentUuid}"): "댓글 수정",
        ("DELETE", "/api/v1/comments/{commentUuid}"): "댓글 삭제",
    }
    operations = {
        (method.upper(), path)
        for path, path_operations in paths.items()
        for method in path_operations
    }
    assert operations == summaries.keys()
    for (method, path), summary in summaries.items():
        operation = paths[path][method.lower()]
        assert operation["summary"] == summary
        assert operation["tags"] == ["커뮤니티"]

    header_parameters = [
        parameter
        for path_operations in paths.values()
        for operation in path_operations.values()
        for parameter in operation.get("parameters", [])
        if parameter["in"] == "header"
    ]
    assert all(
        parameter["name"] != "X-Debug-User-Id"
        for parameter in header_parameters
    )
    assert header_parameters
    assert all(
        parameter["name"] == "X-Debug-User-Uuid"
        for parameter in header_parameters
    )
    assert all(
        any(
            option.get("type") == "string"
            for option in parameter["schema"].get(
                "anyOf", [parameter["schema"]]
            )
        )
        and "UUID" in parameter["description"]
        for parameter in header_parameters
    )

    assert not any("/posts" in path for path in paths)
    assert not any("postId" in path for path in paths)
    for path, method, status_code in (
        ("/api/v1/community-posts/{communityPostUuid}", "delete", "204"),
        ("/api/v1/comments/{commentUuid}", "delete", "204"),
        ("/api/v1/regions/{regionName}/community-posts", "post", "201"),
        ("/api/v1/community-posts/{communityPostUuid}", "patch", "201"),
        ("/api/v1/comments", "post", "201"),
        ("/api/v1/comments/{commentUuid}", "patch", "201"),
    ):
        operation = paths[path][method]
        if method == "delete":
            assert "requestBody" not in operation
        assert "content" not in operation["responses"][status_code]
    like_path = "/api/v1/community-posts/{communityPostUuid}/likes"
    for method in ("post", "delete"):
        assert "requestBody" not in paths[like_path][method]

    schemas = schema["components"]["schemas"]
    assert set(schemas["ErrorResponse"]["properties"]) == {"code", "message"}
    assert set(schemas["ErrorResponse"]["required"]) == {"code", "message"}
    assert schemas["CommunityPostBoard"]["enum"] == [
        "notice",
        "question",
        "talk",
    ]
    assert schemas["CommunityPostCategory"]["enum"] == [
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
    assert schemas["CommunityPostUpdateRequest"]["minProperties"] == 1
    assert (
        schemas["CommunityPostUpdateRequest"]["additionalProperties"] is False
    )
    assert set(schemas["CommunityPostUpdateRequest"]["properties"]) == {
        "title",
        "body",
        "categoryName",
    }
    assert set(schemas["CommentCreateRequest"]["properties"]) == {
        "targetType",
        "targetUuid",
        "parentUuid",
        "body",
    }
    assert schemas["CommentUpdateRequest"]["additionalProperties"] is False
    assert set(schemas["CommentUpdateRequest"]["properties"]) == {"body"}
    assert "authorAvatarUrl" in schemas["CommentResponse"]["required"]
    assert "authorAvatarUrl" in schemas["CommunityPostListItem"]["required"]
    assert "isPopular" in schemas["CommunityPostListItem"]["properties"]
    assert set(schemas["CommunityPostDetailResponse"]["properties"]) == {
        "uuid",
        "regionName",
        "boardName",
        "title",
        "body",
        "authorUuid",
        "authorNickname",
        "authorAvatarUrl",
        "createdAt",
        "updatedAt",
        "likeCount",
        "isLiked",
    }

    for path, method, expected_statuses in (
        (
            "/api/v1/community-posts",
            "get",
            {"401", "404", "422", "500", "503"},
        ),
        (
            "/api/v1/community-posts/{communityPostUuid}",
            "patch",
            {"401", "403", "404", "422", "500"},
        ),
        (
            "/api/v1/comments/{commentUuid}",
            "patch",
            {"401", "403", "404", "422", "500"},
        ),
    ):
        responses = paths[path][method]["responses"]
        assert expected_statuses <= responses.keys()
        for status_code in expected_statuses:
            assert responses[status_code]["content"]["application/json"][
                "schema"
            ] == {"$ref": "#/components/schemas/ErrorResponse"}


def test_request_schemas_preserve_raw_text_and_enforce_contract() -> None:
    request = CommunityPostCreateRequest(
        boardName="talk",
        categoryName="free",
        title="  제목  ",
        body="  본문  ",
    )
    assert request.title == "  제목  "
    assert request.body == "  본문  "
    assert CommunityPostCreateRequest(
        boardName="question",
        title=" ",
        body=" ",
    ).body == " "

    with pytest.raises(ValidationError):
        CommunityPostCreateRequest(
            boardName="talk",
            categoryName="free",
            title="제목",
            body="본문",
            authorUuid=uuid4(),
        )
    with pytest.raises(ValidationError):
        CommunityPostCreateRequest(
            boardName="talk",
            title="제목",
            body="본문",
        )
    with pytest.raises(ValidationError):
        CommunityPostCreateRequest(
            boardName="question",
            categoryName="free",
            title="제목",
            body="본문",
        )
    with pytest.raises(ValidationError):
        CommunityPostCreateRequest(
            boardName="question",
            title="x" * 26,
            body="본문",
        )
    with pytest.raises(ValidationError):
        CommunityPostCreateRequest(
            boardName="question",
            title="제목",
            body="x" * 3001,
        )
    with pytest.raises(ValidationError):
        CommunityPostUpdateRequest()
    with pytest.raises(ValidationError):
        CommunityPostUpdateRequest(title=None)

    target_uuid = uuid4()
    comment = CommentCreateRequest(
        targetType="COMMUNITY_POST",
        targetUuid=target_uuid,
        body="   ",
    )
    assert comment.targetUuid == target_uuid
    assert comment.body == "   "


def test_community_post_query_masks_deleted_authors() -> None:
    command.upgrade(Config("alembic.ini"), "head")
    engine = create_db_engine(DATABASE_URL)

    with Session(engine) as session:
        session.execute(sa.delete(Comment))
        session.execute(sa.delete(CommunityPost))
        session.execute(sa.delete(User))

        viewer = User(nickname="viewer", region_id=1)
        soft_deleted = User(
            nickname="soft-deleted",
            region_id=1,
            deleted_at=kst_now(),
        )
        hard_deleted = User(nickname="hard-deleted", region_id=1)
        session.add_all([viewer, soft_deleted, hard_deleted])
        session.flush()

        active_community_post = CommunityPost(
            author_id=viewer.id,
            region_id=1,
            board=CommunityPostBoard.QUESTION,
            title="active",
            body="x" * 80,
        )
        soft_community_post = CommunityPost(
            author_id=soft_deleted.id,
            region_id=1,
            board=CommunityPostBoard.QUESTION,
            title="soft",
            body="body",
        )
        hard_community_post = CommunityPost(
            author_id=hard_deleted.id,
            region_id=1,
            board=CommunityPostBoard.QUESTION,
            title="hard",
            body="body",
        )
        session.add_all(
            [
                active_community_post,
                soft_community_post,
                hard_community_post,
            ]
        )
        session.flush()
        session.add_all(
            [
                Comment(
                    user_id=viewer.id,
                    target_type=CommentTargetType.COMMUNITY_POST,
                    target_id=active_community_post.id,
                    content="one",
                ),
                Comment(
                    user_id=viewer.id,
                    target_type=CommentTargetType.COMMUNITY_POST,
                    target_id=active_community_post.id,
                    content="two",
                ),
                Comment(
                    user_id=viewer.id,
                    target_type=CommentTargetType.COMMUNITY_POST,
                    target_id=active_community_post.id,
                    content="deleted",
                    deleted_at=kst_now(),
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
                select_community_posts_with_stats().order_by(
                    CommunityPost.id
                )
            ).mappings().all()
        finally:
            sa.event.remove(engine, "before_cursor_execute", count_statement)

        responses = [
            community_post_list_item_from_row(
                row,
                like_count=0,
                is_liked=False,
                is_popular=False,
            )
            for row in rows
        ]

    assert statement_count == 1
    assert len(responses) == 3
    assert isinstance(responses[0].uuid, UUID)
    assert responses[0].body == "x" * 60
    assert responses[0].commentCount == 2
    assert responses[0].authorNickname == "viewer"
    assert responses[1].authorNickname is None
    assert responses[2].authorNickname is None
    engine.dispose()
