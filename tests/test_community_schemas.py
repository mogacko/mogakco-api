import os
from datetime import UTC, datetime

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.models import Comment, CommentTargetType, Post, PostBoard, PostLike, User
from app.schemas import CommentCreateRequest, PostCreateRequest, PostUpdateRequest
from app.services.community import post_response_from_row, select_posts_with_stats

DATABASE_URL = os.environ["DATABASE_URL"]


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
        CommentCreateRequest(targetType="post", targetId=1, body="   ")


def test_post_query_ignores_legacy_rdb_likes_and_masks_deleted_authors() -> None:
    command.upgrade(Config("alembic.ini"), "head")
    engine = sa.create_engine(DATABASE_URL)

    with Session(engine) as session:
        session.execute(sa.delete(Comment))
        session.execute(sa.delete(PostLike))
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
        session.add(PostLike(post_id=active_post.id, user_id=viewer_id))
        session.add_all(
            [
                Comment(
                    user_id=viewer_id,
                    target_type=CommentTargetType.POST,
                    target_id=active_post.id,
                    content="one",
                ),
                Comment(
                    user_id=viewer_id,
                    target_type=CommentTargetType.POST,
                    target_id=active_post.id,
                    content="two",
                ),
                Comment(
                    user_id=viewer_id,
                    target_type=CommentTargetType.POST,
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
