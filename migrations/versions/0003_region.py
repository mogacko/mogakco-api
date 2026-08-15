"""Replace shared chapter schema with region.

Revision ID: 0003_region
Revises: 0002_community
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_region"
down_revision: str | None = "0002_community"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

REGIONS = (
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
)


def upgrade() -> None:
    region = op.create_table(
        "region",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=20), nullable=False),
        sa.Column("is_enable", sa.Boolean(), nullable=False),
        sa.UniqueConstraint("name", name="uq_region_name"),
    )
    op.bulk_insert(
        region,
        [{"name": name, "is_enable": is_enable} for name, is_enable in REGIONS],
    )

    op.add_column("users", sa.Column("region_id", sa.Integer(), nullable=True))
    op.execute(
        "UPDATE users AS u SET region_id = r.id "
        "FROM chapters AS c JOIN region AS r ON r.name = c.code "
        "WHERE c.id = u.chapter_id"
    )
    op.alter_column("users", "region_id", nullable=False)
    op.create_foreign_key(
        "fk_users_region_id_region", "users", "region", ["region_id"], ["id"]
    )
    op.drop_constraint("fk_users_chapter_id_chapters", "users", type_="foreignkey")
    op.drop_column("users", "chapter_id")

    op.add_column("posts", sa.Column("region_id", sa.Integer(), nullable=True))
    op.execute(
        "UPDATE posts AS p SET region_id = r.id "
        "FROM chapters AS c JOIN region AS r ON r.name = c.code "
        "WHERE c.id = p.chapter_id"
    )
    op.alter_column("posts", "region_id", nullable=False)
    op.create_foreign_key(
        "fk_posts_region_id_region", "posts", "region", ["region_id"], ["id"]
    )
    op.drop_index("ix_posts_chapter_board_created", table_name="posts")
    op.drop_index("ix_posts_chapter_board_category_created", table_name="posts")
    op.drop_constraint("fk_posts_chapter_id_chapters", "posts", type_="foreignkey")
    op.drop_column("posts", "chapter_id")
    op.create_index(
        "ix_posts_region_board_created",
        "posts",
        ["region_id", "board", "created_at"],
    )
    op.create_index(
        "ix_posts_region_board_category_created",
        "posts",
        ["region_id", "board", "category", "created_at"],
    )
    op.drop_table("chapters")


def downgrade() -> None:
    chapters = op.create_table(
        "chapters",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(length=20), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("is_open", sa.Boolean(), nullable=False),
        sa.UniqueConstraint("code", name="uq_chapters_code"),
    )
    op.bulk_insert(
        chapters,
        [
            {"code": name, "sort_order": order, "is_open": is_enable}
            for order, (name, is_enable) in enumerate(REGIONS, start=1)
        ],
    )

    op.add_column("users", sa.Column("chapter_id", sa.Integer(), nullable=True))
    op.execute(
        "UPDATE users AS u SET chapter_id = c.id "
        "FROM region AS r JOIN chapters AS c ON c.code = r.name "
        "WHERE r.id = u.region_id"
    )
    op.alter_column("users", "chapter_id", nullable=False)
    op.create_foreign_key(
        "fk_users_chapter_id_chapters",
        "users",
        "chapters",
        ["chapter_id"],
        ["id"],
    )
    op.drop_constraint("fk_users_region_id_region", "users", type_="foreignkey")
    op.drop_column("users", "region_id")

    op.add_column("posts", sa.Column("chapter_id", sa.Integer(), nullable=True))
    op.execute(
        "UPDATE posts AS p SET chapter_id = c.id "
        "FROM region AS r JOIN chapters AS c ON c.code = r.name "
        "WHERE r.id = p.region_id"
    )
    op.alter_column("posts", "chapter_id", nullable=False)
    op.create_foreign_key(
        "fk_posts_chapter_id_chapters",
        "posts",
        "chapters",
        ["chapter_id"],
        ["id"],
    )
    op.drop_index("ix_posts_region_board_created", table_name="posts")
    op.drop_index("ix_posts_region_board_category_created", table_name="posts")
    op.drop_constraint("fk_posts_region_id_region", "posts", type_="foreignkey")
    op.drop_column("posts", "region_id")
    op.create_index(
        "ix_posts_chapter_board_created",
        "posts",
        ["chapter_id", "board", "created_at"],
    )
    op.create_index(
        "ix_posts_chapter_board_category_created",
        "posts",
        ["chapter_id", "board", "category", "created_at"],
    )
    op.drop_table("region")
