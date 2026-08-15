import os

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.exc import IntegrityError

DATABASE_URL = os.environ["DATABASE_URL"]


def test_core_migration_seed_and_constraints() -> None:
    config = Config("alembic.ini")
    engine = sa.create_engine(DATABASE_URL)

    command.upgrade(config, "head")
    command.upgrade(config, "head")

    with engine.connect() as connection:
        chapters = connection.execute(
            sa.text("SELECT code, sort_order, is_open FROM chapters ORDER BY sort_order")
        ).all()
    assert chapters == [
        ("seoul", 1, True),
        ("busan", 2, True),
        ("gyeonggi", 3, False),
        ("incheon", 4, False),
        ("daejeon", 5, False),
        ("daegu", 6, False),
        ("gwangju", 7, False),
        ("ulsan", 8, False),
        ("gangwon", 9, False),
        ("jeju", 10, False),
    ]

    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(
            sa.text(
                "INSERT INTO chapters (code, sort_order, is_open) "
                "VALUES ('seoul', 11, false)"
            )
        )

    with engine.begin() as connection:
        connection.execute(
            sa.text("INSERT INTO users (nickname, chapter_id) VALUES ('tester', 1)")
        )

    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(
            sa.text("INSERT INTO users (nickname, chapter_id) VALUES ('tester', 2)")
        )

    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(
            sa.text(
                "INSERT INTO users (nickname, chapter_id) VALUES ('no-chapter', 9999)"
            )
        )

    command.downgrade(config, "base")
    assert not sa.inspect(engine).has_table("chapters")
    assert not sa.inspect(engine).has_table("users")
    engine.dispose()