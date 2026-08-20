import os

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.exc import IntegrityError

from app.models import PostBoard, PostCategory
from app.services.community import validate_post_category

DATABASE_URL = os.environ["DATABASE_URL"]


def test_post_category_rules() -> None:
    validate_post_category(PostBoard.TALK, PostCategory.FREE)
    validate_post_category(PostBoard.NOTICE, None)
    validate_post_category(PostBoard.QUESTION, None)

    with pytest.raises(ValueError):
        validate_post_category(PostBoard.TALK, None)
    with pytest.raises(ValueError):
        validate_post_category(PostBoard.NOTICE, PostCategory.FREE)


def test_community_migration_constraints_and_delete_rules() -> None:
    config = Config("alembic.ini")
    command.upgrade(config, "head")
    engine = sa.create_engine(DATABASE_URL)

    with engine.begin() as connection:
        user_id = connection.scalar(
            sa.text(
                "INSERT INTO users (nickname, region_id) VALUES ('community-user', 1) "
                "RETURNING id"
            )
        )
        post_id = connection.scalar(
            sa.text(
                "INSERT INTO posts (author_id, region_id, board, category, title, body) "
                "VALUES (:user_id, 1, 'talk', 'free', 'title', 'body') RETURNING id"
            ),
            {"user_id": user_id},
        )
        connection.execute(
            sa.text("INSERT INTO post_likes (post_id, user_id) VALUES (:post_id, :user_id)"),
            {"post_id": post_id, "user_id": user_id},
        )
        comment_id = connection.scalar(
            sa.text(
                "INSERT INTO comments (user_id, target_type, target_id, content) "
                "VALUES (:user_id, 'post', :post_id, 'comment') RETURNING id"
            ),
            {"user_id": user_id, "post_id": post_id},
        )

    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(
            sa.text("INSERT INTO post_likes (post_id, user_id) VALUES (:post_id, :user_id)"),
            {"post_id": post_id, "user_id": user_id},
        )

    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(
            sa.text(
                "INSERT INTO comments (target_type, target_id, content) "
                "VALUES ('other', :post_id, 'comment')"
            ),
            {"post_id": post_id},
        )

    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(
            sa.text(
                "INSERT INTO posts (region_id, board, title, body) "
                "VALUES (1, 'question', 'title', :body)"
            ),
            {"body": "x" * 10001},
        )

    with pytest.raises(sa.exc.DataError), engine.begin() as connection:
        connection.execute(
            sa.text(
                "INSERT INTO comments (target_type, target_id, content) "
                "VALUES ('post', :post_id, :content)"
            ),
            {"post_id": post_id, "content": "x" * 301},
        )

    with engine.begin() as connection:
        reply_id = connection.scalar(
            sa.text(
                "INSERT INTO comments "
                "(target_type, target_id, parent_comment_id, content) "
                "VALUES ('post', :post_id, :parent_id, 'reply') RETURNING id"
            ),
            {"post_id": post_id, "parent_id": comment_id},
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
        post_author = connection.scalar(
            sa.text("SELECT author_id FROM posts WHERE id = :post_id"),
            {"post_id": post_id},
        )
        comment_authors = connection.execute(
            sa.text("SELECT user_id FROM comments WHERE id IN (:root, :reply)"),
            {"root": comment_id, "reply": reply_id},
        ).scalars().all()
        like_count = connection.scalar(sa.text("SELECT count(*) FROM post_likes"))

    assert post_author is None
    assert comment_authors == [None, None]
    assert like_count == 0
    assert "deleted_at" not in {
        column["name"] for column in sa.inspect(engine).get_columns("post_likes")
    }
    post_indexes = {index["name"] for index in sa.inspect(engine).get_indexes("posts")}
    assert "ix_posts_region_board_created" in post_indexes
    assert "ix_posts_region_board_category_created" in post_indexes
    assert not any("chapter" in name for name in post_indexes)
    comment_columns = {
        column["name"] for column in sa.inspect(engine).get_columns("comments")
    }
    assert comment_columns == {
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

    with engine.begin() as connection:
        second_user_id = connection.scalar(
            sa.text(
                "INSERT INTO users (nickname, region_id) VALUES ('second-user', 1) "
                "RETURNING id"
            )
        )
        connection.execute(
            sa.text(
                "INSERT INTO post_likes (post_id, user_id) VALUES (:post_id, :user_id)"
            ),
            {"post_id": post_id, "user_id": second_user_id},
        )
        connection.execute(
            sa.text("DELETE FROM posts WHERE id = :post_id"), {"post_id": post_id}
        )
        assert connection.scalar(sa.text("SELECT count(*) FROM post_likes")) == 0

    command.downgrade(config, "0001_core")
    inspector = sa.inspect(engine)
    assert not inspector.has_table("posts")
    assert not inspector.has_table("post_likes")
    assert not inspector.has_table("comments")
    assert inspector.has_table("users")
    command.upgrade(config, "head")
    engine.dispose()
