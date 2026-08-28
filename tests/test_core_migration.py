import os

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.services.community import get_region_by_name

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


def test_initial_migration_creates_final_core_schema() -> None:
    config = Config("alembic.ini")
    engine = sa.create_engine(DATABASE_URL)
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    command.upgrade(config, "head")

    with engine.connect() as connection:
        regions = connection.execute(
            sa.text("SELECT name, is_enabled FROM regions ORDER BY id")
        ).all()

    assert regions == REGIONS
    inspector = sa.inspect(engine)
    assert not inspector.has_table("chapters")
    assert not inspector.has_table("post_likes")
    assert not inspector.has_table("posts")
    assert {column["name"] for column in inspector.get_columns("regions")} == {
        "id",
        "name",
        "is_enabled",
    }
    assert {column["name"] for column in inspector.get_columns("users")} == {
        "id",
        "uuid",
        "nickname",
        "region_id",
        "created_at",
        "updated_at",
        "deleted_at",
    }

    with Session(engine) as session:
        region = get_region_by_name(session, "busan")
        assert region is not None
        assert region.name == "busan"
        assert region.is_enabled is True
        assert get_region_by_name(session, "서울") is None

    with engine.begin() as connection:
        connection.execute(
            sa.text(
                "INSERT INTO regions (name, is_enabled) "
                "VALUES ('new-region', false)"
            )
        )
        assert connection.scalar(sa.text("SELECT count(*) FROM regions")) == 11

    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(
            sa.text("INSERT INTO regions (name, is_enabled) VALUES ('seoul', false)")
        )

    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(
            sa.text("INSERT INTO users (nickname, region_id) VALUES ('no-region', 9999)")
        )

    with engine.begin() as connection:
        connection.execute(
            sa.text("INSERT INTO users (nickname, region_id) VALUES ('unique-user', 1)")
        )

    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(
            sa.text("INSERT INTO users (nickname, region_id) VALUES ('unique-user', 2)")
        )

    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(
            sa.text(
                "INSERT INTO community_posts (region_id, board, title, body) "
                "VALUES (9999, 'question', 'invalid region', 'body')"
            )
        )

    command.downgrade(config, "base")
    inspector = sa.inspect(engine)
    for table in ("regions", "users", "community_posts", "comments"):
        assert not inspector.has_table(table)
    command.upgrade(config, "head")
    engine.dispose()
