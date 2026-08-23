"""Drop the legacy PostgreSQL post likes table.

Revision ID: 0005_drop_post_likes
Revises: 0004_comment_spec
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_drop_post_likes"
down_revision: str | None = "0004_comment_spec"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index("ix_post_likes_user_id", table_name="post_likes")
    op.drop_table("post_likes")


def downgrade() -> None:
    op.create_table(
        "post_likes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "post_id",
            sa.Integer(),
            sa.ForeignKey("posts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "post_id", "user_id", name="uq_post_likes_post_user"
        ),
    )
    op.create_index("ix_post_likes_user_id", "post_likes", ["user_id"])
