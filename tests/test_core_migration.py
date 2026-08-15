import os

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.services.community import get_region_by_chapter_code

DATABASE_URL = os.environ["DATABASE_URL"]

REGIONS = [
    ("seoul", True),
    ("busan", True),
    ("gyeonggi", False),
    ("incheon", False),
    ("daejeon", False),
    ("daegu", False),
    ("gwangju", False),
    ("ulsan", False),
    ("gangwon", False),
    ("jeju", False),
]


def test_region_corrective_migration_preserves_data_and_constraints() -> None:
    config = Config("alembic.ini")
    engine = sa.create_engine(DATABASE_URL)
    command.downgrade(config, "base")
    command.upgrade(config, "0002_community")

    with engine.begin() as connection:
        user_id = connection.scalar(
            sa.text(
                "INSERT INTO users (nickname, chapter_id) VALUES ('legacy-user', 1) "
                "RETURNING id"
            )
        )
        post_id = connection.scalar(
            sa.text(
                "INSERT INTO posts (author_id, chapter_id, board, title, body) "
                "VALUES (:user_id, 1, 'question', 'legacy', 'body') RETURNING id"
            ),
            {"user_id": user_id},
        )

    command.upgrade(config, "head")
    command.upgrade(config, "head")

    with engine.connect() as connection:
        regions = connection.execute(
            sa.text("SELECT name, is_enable FROM region ORDER BY id")
        ).all()
        user_region = connection.scalar(
            sa.text(
                "SELECT r.name FROM users u JOIN region r ON r.id = u.region_id "
                "WHERE u.id = :user_id"
            ),
            {"user_id": user_id},
        )
        post_region = connection.scalar(
            sa.text(
                "SELECT r.name FROM posts p JOIN region r ON r.id = p.region_id "
                "WHERE p.id = :post_id"
            ),
            {"post_id": post_id},
        )

    assert regions == REGIONS
    assert user_region == "seoul"
    assert post_region == "seoul"
    inspector = sa.inspect(engine)
    assert not inspector.has_table("chapters")
    assert {column["name"] for column in inspector.get_columns("region")} == {
        "id",
        "name",
        "is_enable",
    }
    assert "chapter_id" not in {
        column["name"] for column in inspector.get_columns("users")
    }
    assert "chapter_id" not in {
        column["name"] for column in inspector.get_columns("posts")
    }

    with Session(engine) as session:
        region = get_region_by_chapter_code(session, "busan")
        assert region is not None
        assert region.name == "busan"
        assert region.is_enable is True
        assert get_region_by_chapter_code(session, "서울") is None

    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(
            sa.text("INSERT INTO region (name, is_enable) VALUES ('seoul', false)")
        )

    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(
            sa.text("INSERT INTO users (nickname, region_id) VALUES ('no-region', 9999)")
        )

    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(
            sa.text("INSERT INTO users (nickname, region_id) VALUES ('legacy-user', 2)")
        )

    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(
            sa.text(
                "INSERT INTO posts (region_id, board, title, body) "
                "VALUES (9999, 'question', 'invalid region', 'body')"
            )
        )

    command.downgrade(config, "0002_community")
    with engine.connect() as connection:
        restored = connection.scalar(
            sa.text(
                "SELECT c.code FROM users u JOIN chapters c ON c.id = u.chapter_id "
                "WHERE u.id = :user_id"
            ),
            {"user_id": user_id},
        )
    assert restored == "seoul"
    assert not sa.inspect(engine).has_table("region")

    command.upgrade(config, "head")
    command.downgrade(config, "base")
    inspector = sa.inspect(engine)
    assert not inspector.has_table("region")
    assert not inspector.has_table("users")
    engine.dispose()
