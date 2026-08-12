"""move profile image reference into asset usages

Revision ID: 20260813_0004
Revises: 20260809_0003
Create Date: 2026-08-13
"""
import sqlalchemy as sa
from alembic import op

revision = "20260813_0004"
down_revision = "20260809_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "asset_usages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("asset_id", sa.Integer(), sa.ForeignKey("media_assets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("usage_type", sa.String(length=30), nullable=False),
        sa.Column("usage_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.CheckConstraint("usage_type IN ('PROFILE')", name="ck_asset_usages_type"),
        sa.UniqueConstraint("usage_type", "usage_id", name="uq_asset_usages_slot"),
    )
    op.create_index("ix_asset_usages_asset_id", "asset_usages", ["asset_id"])

    # 이미 붙어 있는 프로필 사진을 사용처 행으로 옮긴다.
    op.execute(
        """
        INSERT INTO asset_usages (asset_id, usage_type, usage_id)
        SELECT profile_image_asset_id, 'PROFILE', id FROM users
        WHERE profile_image_asset_id IS NOT NULL
        """
    )

    # users가 media_assets를 더 이상 참조하지 않으므로 순환 외래 키가 사라진다.
    with op.batch_alter_table("users") as batch:
        batch.drop_constraint("fk_users_profile_image_asset_id", type_="foreignkey")
        batch.drop_column("profile_image_asset_id")


def downgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("profile_image_asset_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_users_profile_image_asset_id", "media_assets", ["profile_image_asset_id"], ["id"], ondelete="SET NULL"
        )

    # 자리당 한 장이라 되돌릴 때 손실이 없다.
    op.execute(
        """
        UPDATE users SET profile_image_asset_id = (
            SELECT asset_id FROM asset_usages
            WHERE asset_usages.usage_type = 'PROFILE' AND asset_usages.usage_id = users.id
        )
        """
    )

    op.drop_index("ix_asset_usages_asset_id", table_name="asset_usages")
    op.drop_table("asset_usages")
