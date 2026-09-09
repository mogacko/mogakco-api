"""add event owner management schema

Revision ID: 0003_event_owner
Revises: 0002_event
Create Date: 2026-09-09 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "0003_event_owner"
down_revision: str | None = "0002_event"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """행사 생명주기와 수정 요청 스키마를 추가한다."""

    op.add_column(
        "events",
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "events",
        sa.Column("rejected_reason", sa.String(length=200), nullable=True),
    )
    op.add_column(
        "events",
        sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.drop_constraint("ck_events_status", "events", type_="check")
    op.create_check_constraint(
        "ck_events_status",
        "events",
        "status IN "
        "('PENDING', 'APPROVED', 'COMPLETED', 'REJECTED', 'CANCEL')",
    )
    op.execute(
        sa.text(
            "UPDATE events SET status = 'COMPLETED' "
            "WHERE status = 'APPROVED' "
            "AND date + end_at <= timezone('Asia/Seoul', now())"
        )
    )
    op.execute(
        sa.text(
            "UPDATE events "
            "SET status = 'REJECTED', "
            "rejected_reason = '행사 시작 시각까지 승인되지 않음', "
            "rejected_at = now() "
            "WHERE status = 'PENDING' "
            "AND date + start_at <= timezone('Asia/Seoul', now())"
        )
    )
    op.execute(
        sa.text(
            "UPDATE events "
            "SET status = 'CANCEL', "
            "cancel_reason = COALESCE(cancel_reason, '호스트 정보 없음'), "
            "cancelled_at = now() "
            "WHERE host_id IS NULL "
            "AND status IN ('PENDING', 'APPROVED')"
        )
    )
    op.create_check_constraint(
        "ck_events_active_host_required",
        "events",
        "host_id IS NOT NULL "
        "OR status IN ('COMPLETED', 'REJECTED', 'CANCEL')",
    )

    op.drop_constraint(
        op.f("fk_event_participants_user_id_users"),
        "event_participants",
        type_="foreignkey",
    )
    op.create_foreign_key(
        op.f("fk_event_participants_user_id_users"),
        "event_participants",
        "users",
        ["user_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    op.create_table(
        "event_edit_requests",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("event_id", sa.Integer(), nullable=False),
        sa.Column("changes", postgresql.JSONB(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "PENDING",
                "APPROVED",
                "REJECTED",
                name="eventeditrequeststatus",
                native_enum=False,
                length=20,
            ),
            server_default=sa.text("'PENDING'"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "jsonb_typeof(changes) = 'object' AND changes <> '{}'::jsonb",
            name="ck_event_edit_requests_changes_object",
        ),
        sa.CheckConstraint(
            "status IN ('PENDING', 'APPROVED', 'REJECTED')",
            name="ck_event_edit_requests_status",
        ),
        sa.ForeignKeyConstraint(
            ["event_id"],
            ["events.id"],
            name=op.f("fk_event_edit_requests_event_id_events"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_event_edit_requests")),
    )
    op.create_index(
        "uq_event_edit_requests_pending_event_id",
        "event_edit_requests",
        ["event_id"],
        unique=True,
        postgresql_where=sa.text("status = 'PENDING'"),
    )


def downgrade() -> None:
    """행사 소유자 관리 스키마를 제거한다."""

    op.drop_index(
        "uq_event_edit_requests_pending_event_id",
        table_name="event_edit_requests",
    )
    op.drop_table("event_edit_requests")

    op.drop_constraint(
        op.f("fk_event_participants_user_id_users"),
        "event_participants",
        type_="foreignkey",
    )
    op.create_foreign_key(
        op.f("fk_event_participants_user_id_users"),
        "event_participants",
        "users",
        ["user_id"],
        ["id"],
        ondelete="CASCADE",
    )

    op.drop_constraint(
        "ck_events_active_host_required",
        "events",
        type_="check",
    )
    op.execute(
        "UPDATE events SET status = 'APPROVED' "
        "WHERE status = 'COMPLETED'"
    )
    op.drop_constraint("ck_events_status", "events", type_="check")
    op.create_check_constraint(
        "ck_events_status",
        "events",
        "status IN ('PENDING', 'APPROVED', 'REJECTED', 'CANCEL')",
    )
    op.drop_column("events", "rejected_at")
    op.drop_column("events", "rejected_reason")
    op.drop_column("events", "cancelled_at")
