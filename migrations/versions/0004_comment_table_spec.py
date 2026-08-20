"""Align comments with the current inline table specification.

Revision ID: 0004_comment_spec
Revises: 0003_region
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_comment_spec"
down_revision: str | None = "0003_region"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "comments",
        sa.Column(
            "uuid",
            sa.Uuid(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
    )
    op.create_unique_constraint("uq_comments_uuid", "comments", ["uuid"])

    op.drop_index("ix_comments_author_id", table_name="comments")
    op.drop_index("ix_comments_parent_id", table_name="comments")
    op.drop_constraint(
        "fk_comments_author_id_users", "comments", type_="foreignkey"
    )
    op.drop_constraint(
        "fk_comments_parent_id_comments", "comments", type_="foreignkey"
    )
    op.alter_column("comments", "author_id", new_column_name="user_id")
    op.alter_column("comments", "parent_id", new_column_name="parent_comment_id")
    op.alter_column("comments", "body", new_column_name="content")
    op.alter_column("comments", "edited_at", new_column_name="updated_at")
    op.create_foreign_key(
        "fk_comments_user_id_users",
        "comments",
        "users",
        ["user_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_comments_parent_comment_id_comments",
        "comments",
        "comments",
        ["parent_comment_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index("ix_comments_user_id", "comments", ["user_id"])
    op.create_index(
        "ix_comments_parent_comment_id", "comments", ["parent_comment_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_comments_parent_comment_id", table_name="comments")
    op.drop_index("ix_comments_user_id", table_name="comments")
    op.drop_constraint(
        "fk_comments_parent_comment_id_comments",
        "comments",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_comments_user_id_users", "comments", type_="foreignkey"
    )
    op.alter_column("comments", "updated_at", new_column_name="edited_at")
    op.alter_column("comments", "content", new_column_name="body")
    op.alter_column("comments", "parent_comment_id", new_column_name="parent_id")
    op.alter_column("comments", "user_id", new_column_name="author_id")
    op.create_foreign_key(
        "fk_comments_parent_id_comments",
        "comments",
        "comments",
        ["parent_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_comments_author_id_users",
        "comments",
        "users",
        ["author_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_comments_parent_id", "comments", ["parent_id"])
    op.create_index("ix_comments_author_id", "comments", ["author_id"])
    op.drop_constraint("uq_comments_uuid", "comments", type_="unique")
    op.drop_column("comments", "uuid")
