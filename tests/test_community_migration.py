import os

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.exc import IntegrityError

from app.models import CommunityPostBoard, CommunityPostCategory
from app.services.community import validate_community_post_category

DATABASE_URL = os.environ["DATABASE_URL"]


def test_community_post_category_rules() -> None:
    validate_community_post_category(
        CommunityPostBoard.TALK,
        CommunityPostCategory.FREE,
    )
    validate_community_post_category(CommunityPostBoard.NOTICE, None)
    validate_community_post_category(CommunityPostBoard.QUESTION, None)

    with pytest.raises(ValueError):
        validate_community_post_category(CommunityPostBoard.TALK, None)
    with pytest.raises(ValueError):
        validate_community_post_category(
            CommunityPostBoard.NOTICE,
            CommunityPostCategory.FREE,
        )


def test_initial_migration_community_constraints_and_delete_rules() -> None:
    config = Config("alembic.ini")
    command.upgrade(config, "head")
    engine = sa.create_engine(DATABASE_URL)

    with engine.begin() as connection:
        user_id = connection.scalar(
            sa.text(
                "INSERT INTO users (nickname, region_id) "
                "VALUES ('community-user', 1) RETURNING id"
            )
        )
        community_post_id = connection.scalar(
            sa.text(
                "INSERT INTO community_post "
                "(author_id, region_id, board, category, title, body) "
                "VALUES (:user_id, 1, 'talk', 'free', 'title', 'body') "
                "RETURNING id"
            ),
            {"user_id": user_id},
        )
        comment_id = connection.scalar(
            sa.text(
                "INSERT INTO comments "
                "(user_id, target_type, target_id, content) "
                "VALUES (:user_id, 'COMMUNITY_POST', :community_post_id, 'comment') "
                "RETURNING id"
            ),
            {"user_id": user_id, "community_post_id": community_post_id},
        )

    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(
            sa.text(
                "INSERT INTO comments (target_type, target_id, content) "
                "VALUES ('other', :community_post_id, 'comment')"
            ),
            {"community_post_id": community_post_id},
        )

    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(
            sa.text(
                "INSERT INTO community_post (region_id, board, title, body) "
                "VALUES (1, 'question', 'title', :body)"
            ),
            {"body": "x" * 3001},
        )

    with pytest.raises(sa.exc.DataError), engine.begin() as connection:
        connection.execute(
            sa.text(
                "INSERT INTO comments (target_type, target_id, content) "
                "VALUES ('COMMUNITY_POST', :community_post_id, :content)"
            ),
            {"community_post_id": community_post_id, "content": "x" * 301},
        )

    with engine.begin() as connection:
        reply_id = connection.scalar(
            sa.text(
                "INSERT INTO comments "
                "(target_type, target_id, parent_comment_id, content) "
                "VALUES ('COMMUNITY_POST', :community_post_id, :parent_id, 'reply') "
                "RETURNING id"
            ),
            {
                "community_post_id": community_post_id,
                "parent_id": comment_id,
            },
        )

    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(
            sa.text("DELETE FROM comments WHERE id = :comment_id"),
            {"comment_id": comment_id},
        )

    with engine.begin() as connection:
        connection.execute(
            sa.text("DELETE FROM users WHERE id = :user_id"), {"user_id": user_id}
        )
        community_post_author = connection.scalar(
            sa.text(
                "SELECT author_id FROM community_post "
                "WHERE id = :community_post_id"
            ),
            {"community_post_id": community_post_id},
        )
        comment_authors = connection.execute(
            sa.text("SELECT user_id FROM comments WHERE id IN (:root, :reply)"),
            {"root": comment_id, "reply": reply_id},
        ).scalars().all()

    assert community_post_author is None
    assert comment_authors == [None, None]
    inspector = sa.inspect(engine)
    assert not inspector.has_table("post_likes")
    community_post_indexes = {
        index["name"]
        for index in inspector.get_indexes("community_post")
    }
    assert "ix_community_post_region_board_created" in community_post_indexes
    assert (
        "ix_community_post_region_board_category_created"
        in community_post_indexes
    )
    assert not any("chapter" in name for name in community_post_indexes)
    assert {column["name"] for column in inspector.get_columns("community_post")} == {
        "id",
        "uuid",
        "author_id",
        "region_id",
        "board",
        "category",
        "title",
        "body",
        "created_at",
        "edited_at",
        "deleted_at",
    }
    assert {column["name"] for column in inspector.get_columns("comments")} == {
        "id",
        "uuid",
        "target_type",
        "user_id",
        "content",
        "parent_comment_id",
        "target_id",
        "updated_at",
        "created_at",
        "deleted_at",
    }
    check_sql = " ".join(
        constraint["sqltext"]
        for constraint in inspector.get_check_constraints("comments")
    )
    for target_type in ("COMMUNITY_POST", "MOGACKO", "EVENT"):
        assert target_type in check_sql

    command.downgrade(config, "base")
    inspector = sa.inspect(engine)
    assert not inspector.has_table("community_post")
    assert not inspector.has_table("comments")
    command.upgrade(config, "head")
    engine.dispose()
