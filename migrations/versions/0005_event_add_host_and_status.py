"""add event host and persisted status

Revision ID: 0005_event_host_status
Revises: 0004_event_count
Create Date: 2026-09-04 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0005_event_host_status"
down_revision: str | None = "0004_event_count"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """기존 행사는 승인 상태로 보존하고 등록자와 상태 컬럼을 추가한다."""

    event_status = sa.Enum(
        "PENDING",
        "APPROVED",
        "REJECTED",
        "CANCEL",
        name="eventstatus",
        native_enum=False,
        length=20,
    )
    op.add_column("events", sa.Column("host_id", sa.Integer(), nullable=True))
    op.add_column(
        "events",
        sa.Column(
            "status",
            event_status,
            server_default=sa.text("'APPROVED'"),
            nullable=False,
        ),
    )
    op.execute(
        sa.text(
            "UPDATE events SET status = 'CANCEL' "
            "WHERE cancel_reason IS NOT NULL"
        )
    )
    op.alter_column("events", "status", server_default=sa.text("'PENDING'"))
    op.create_check_constraint(
        "ck_events_status",
        "events",
        "status IN ('PENDING', 'APPROVED', 'REJECTED', 'CANCEL')",
    )
    op.create_foreign_key(
        op.f("fk_events_host_id_users"),
        "events",
        "users",
        ["host_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_events_host_id", "events", ["host_id"], unique=False)


def downgrade() -> None:
    """이벤트 상태와 등록자 참조를 제거한다."""

    op.drop_index("ix_events_host_id", table_name="events")
    op.drop_constraint(
        op.f("fk_events_host_id_users"),
        "events",
        type_="foreignkey",
    )
    # 개발 중 먼저 적용된 초안 DB에도 안전하게 downgrade할 수 있게 한다.
    op.execute("ALTER TABLE events DROP CONSTRAINT IF EXISTS ck_events_status")
    op.drop_column("events", "status")
    op.drop_column("events", "host_id")
