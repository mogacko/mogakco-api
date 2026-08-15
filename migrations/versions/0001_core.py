"""Create chapters and users.

Revision ID: 0001_core
Revises:
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_core"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CHAPTERS = (
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
            {"code": code, "sort_order": order, "is_open": is_open}
            for order, (code, is_open) in enumerate(CHAPTERS, start=1)
        ],
    )
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("nickname", sa.String(length=30), nullable=False),
        sa.Column("chapter_id", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["chapter_id"], ["chapters.id"], name="fk_users_chapter_id_chapters"
        ),
        sa.UniqueConstraint("nickname", name="uq_users_nickname"),
    )


def downgrade() -> None:
    op.drop_table("users")
    op.drop_table("chapters")