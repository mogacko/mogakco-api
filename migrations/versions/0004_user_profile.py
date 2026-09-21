"""Add profile fields, user attributes, and marketing consent history.

Revision ID: 0004_user_profile
Revises: 0003_event_owner
"""

import sqlalchemy as sa
from alembic import op

revision = "0004_user_profile"
down_revision = "0003_event_owner"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 기존 사용자의 미설정 분야는 NULL로 보존한다.
    op.add_column("users", sa.Column("field", sa.String(50), nullable=True))
    op.add_column("users", sa.Column("bio", sa.String(60), nullable=True))
    op.add_column(
        "users",
        sa.Column(
            "is_staff", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "marketing_consent",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "marketing_consent_changed_at", sa.DateTime(timezone=True), nullable=True
        ),
    )
    op.create_table(
        "user_attributes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("type", sa.String(20), nullable=False),
        sa.Column("value", sa.String(50), nullable=False),
        sa.Column("value_normalized", sa.String(150), nullable=False),
        sa.UniqueConstraint("user_id", "type", "value_normalized"),
        sa.CheckConstraint(
            "type IN ('AFFILIATION', 'STACK', 'INTEREST')",
            name="ck_user_attributes_type",
        ),
        sa.CheckConstraint(
            "char_length(value) BETWEEN 1 AND 50",
            name="ck_user_attributes_value_length",
        ),
        sa.CheckConstraint(
            "type <> 'AFFILIATION' OR char_length(value) <= 20",
            name="ck_user_attributes_affiliation_length",
        ),
    )
    op.create_index(
        "uq_user_attributes_affiliation_user_id",
        "user_attributes",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("type = 'AFFILIATION'"),
    )

    op.create_table(
        "term_agreements",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("type", sa.String(30), nullable=False),
        sa.Column("version", sa.String(50), nullable=False),
        sa.Column("is_required", sa.Boolean(), nullable=False),
        sa.Column("is_agreed", sa.Boolean(), nullable=False),
        sa.Column("notified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("signed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("user_id", "type", "version"),
    )
    op.create_table(
        "marketing_consent_history",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("type", sa.String(30), nullable=False),
        sa.Column("version", sa.String(50), nullable=False),
        sa.Column("previous_is_agreed", sa.Boolean(), nullable=True),
        sa.Column("is_agreed", sa.Boolean(), nullable=False),
        sa.Column("changed_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "type = 'MARKETING'", name="ck_marketing_consent_history_type"
        ),
        sa.CheckConstraint(
            "previous_is_agreed IS NULL OR previous_is_agreed <> is_agreed",
            name="ck_marketing_consent_history_changed",
        ),
    )
    op.create_index(
        "ix_marketing_consent_history_user_changed",
        "marketing_consent_history",
        ["user_id", "changed_at"],
    )
    # 기존 users 동의 값은 보존한다. 확인되지 않은 과거 버전/이력을 생성하지 않는다.


def downgrade() -> None:
    op.drop_table("marketing_consent_history")
    op.drop_table("term_agreements")
    op.drop_table("user_attributes")
    for name in (
        "marketing_consent_changed_at",
        "marketing_consent",
        "is_staff",
        "bio",
        "field",
    ):
        op.drop_column("users", name)
