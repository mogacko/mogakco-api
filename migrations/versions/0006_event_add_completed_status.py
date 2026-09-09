"""add completed event status and active host constraint

Revision ID: 0006_event_completed
Revises: 0002_event
Create Date: 2026-09-07 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0006_event_completed"
down_revision: str | None = "0002_event"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """종료 상태를 추가하고 활성 이벤트에 호스트를 강제한다."""

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


def downgrade() -> None:
    """활성 호스트 제약과 종료 상태를 제거한다."""

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
