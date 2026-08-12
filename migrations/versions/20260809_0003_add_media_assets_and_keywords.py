"""add media assets and profile keywords

Revision ID: 20260809_0003
Revises: 20260808_0002
Create Date: 2026-08-09
"""
import sqlalchemy as sa
from alembic import op

revision = "20260809_0003"
down_revision = "20260808_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "media_assets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("owner_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("key", sa.String(length=255), nullable=False, unique=True),
        sa.Column("url", sa.String(length=2048), nullable=False),
        sa.Column("content_type", sa.String(length=50), nullable=False),
        sa.Column("status", sa.String(length=10), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.CheckConstraint("status IN ('PENDING', 'READY')", name="ck_media_assets_status"),
    )
    op.create_index("ix_media_assets_owner_id", "media_assets", ["owner_id"])

    op.create_table(
        "user_keywords",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(length=10), nullable=False),
        sa.Column("keyword", sa.String(length=100), nullable=False),
        sa.Column("display", sa.String(length=100), nullable=False),
        sa.CheckConstraint("kind IN ('FIELD', 'STACK', 'INTEREST')", name="ck_user_keywords_kind"),
        sa.UniqueConstraint("user_id", "kind", "keyword", name="uq_user_keywords_user_kind_keyword"),
    )
    op.create_index("ix_user_keywords_user_id", "user_keywords", ["user_id"])
    op.create_index("ix_user_keywords_kind_keyword", "user_keywords", ["kind", "keyword"])

    # 자유 입력 URL 대신 검증이 끝난 에셋만 프로필에 붙일 수 있게 바꾼다.
    with op.batch_alter_table("users") as batch:
        batch.drop_column("profile_image_url")
        batch.add_column(sa.Column("profile_image_asset_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_users_profile_image_asset_id", "media_assets", ["profile_image_asset_id"], ["id"], ondelete="SET NULL"
        )


def downgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.drop_constraint("fk_users_profile_image_asset_id", type_="foreignkey")
        batch.drop_column("profile_image_asset_id")
        batch.add_column(sa.Column("profile_image_url", sa.String(length=2048), nullable=True))

    op.drop_index("ix_user_keywords_kind_keyword", table_name="user_keywords")
    op.drop_index("ix_user_keywords_user_id", table_name="user_keywords")
    op.drop_table("user_keywords")
    op.drop_index("ix_media_assets_owner_id", table_name="media_assets")
    op.drop_table("media_assets")
