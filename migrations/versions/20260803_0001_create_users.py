"""create social authentication schema

Revision ID: 20260803_0001
Revises:
Create Date: 2026-08-03
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260803_0001"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("nickname", sa.String(length=30), nullable=False),
        sa.Column("activity_region", sa.String(length=10), nullable=False),
        sa.Column("profile_image_url", sa.String(length=2048), nullable=True),
        sa.Column("field", sa.String(length=100), nullable=True),
        sa.Column("introduction", sa.Text(), nullable=True),
        sa.Column("organization", sa.String(length=100), nullable=True),
        sa.Column("stack", sa.Text(), nullable=True),
        sa.Column("interests", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.CheckConstraint("activity_region IN ('SEOUL', 'BUSAN')", name="ck_users_activity_region"),
    )
    op.create_index("ix_users_nickname", "users", ["nickname"], unique=True)
    op.create_table(
        "social_accounts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("provider", sa.String(length=10), nullable=False),
        sa.Column("provider_user_id", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.CheckConstraint("provider IN ('GOOGLE', 'APPLE', 'KAKAO')", name="ck_social_accounts_provider"),
        sa.UniqueConstraint("provider", "provider_user_id", name="uq_social_accounts_provider_user"),
    )
    op.create_index("ix_social_accounts_user_id", "social_accounts", ["user_id"])
    op.create_table(
        "auth_sessions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("refresh_token_hash", sa.String(length=64), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_auth_sessions_user_id", "auth_sessions", ["user_id"])
    op.create_table(
        "login_codes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=True),
        sa.Column("provider", sa.String(length=10), nullable=True),
        sa.Column("provider_user_id", sa.String(length=255), nullable=True),
        sa.Column("code_hash", sa.String(length=64), nullable=False, unique=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_login_codes_user_id", "login_codes", ["user_id"])
    op.create_index("ix_login_codes_expires_at", "login_codes", ["expires_at"])
    op.create_table(
        "oauth_attempts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("provider", sa.String(length=10), nullable=False),
        sa.Column("state_hash", sa.String(length=64), nullable=False, unique=True),
        sa.Column("code_verifier", sa.String(length=128), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("provider IN ('GOOGLE', 'KAKAO')", name="ck_oauth_attempts_provider"),
    )
    op.create_index("ix_oauth_attempts_expires_at", "oauth_attempts", ["expires_at"])
    op.create_table(
        "terms",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(length=20), nullable=False, unique=True),
        sa.Column("required", sa.Boolean(), nullable=False),
    )
    op.create_table(
        "term_versions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("term_id", sa.Integer(), sa.ForeignKey("terms.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version", sa.String(length=50), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("effective_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("term_id", "version", name="uq_term_versions_term_version"),
    )
    op.create_index("ix_term_versions_term_id", "term_versions", ["term_id"])
    op.create_table(
        "user_term_agreements",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "term_version_id",
            sa.Integer(),
            sa.ForeignKey("term_versions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("agreed_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("withdrawn_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_user_term_agreements_user_id", "user_term_agreements", ["user_id"])
    op.create_index("ix_user_term_agreements_term_version_id", "user_term_agreements", ["term_version_id"])


def downgrade() -> None:
    op.drop_index("ix_user_term_agreements_term_version_id", table_name="user_term_agreements")
    op.drop_index("ix_user_term_agreements_user_id", table_name="user_term_agreements")
    op.drop_table("user_term_agreements")
    op.drop_index("ix_term_versions_term_id", table_name="term_versions")
    op.drop_table("term_versions")
    op.drop_table("terms")
    op.drop_index("ix_oauth_attempts_expires_at", table_name="oauth_attempts")
    op.drop_table("oauth_attempts")
    op.drop_index("ix_login_codes_expires_at", table_name="login_codes")
    op.drop_index("ix_login_codes_user_id", table_name="login_codes")
    op.drop_table("login_codes")
    op.drop_index("ix_auth_sessions_user_id", table_name="auth_sessions")
    op.drop_table("auth_sessions")
    op.drop_index("ix_social_accounts_user_id", table_name="social_accounts")
    op.drop_table("social_accounts")
    op.drop_index("ix_users_nickname", table_name="users")
    op.drop_table("users")
