"""create event edit requests

Revision ID: 0008_event_edit_requests
Revises: 0007_participant_restrict
Create Date: 2026-09-07 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "0008_event_edit_requests"
down_revision: str | None = "0007_participant_restrict"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """행사 원본과 분리된 수정 요청을 저장한다."""

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
    """행사 수정 요청 저장소를 제거한다."""

    op.drop_index(
        "uq_event_edit_requests_pending_event_id",
        table_name="event_edit_requests",
    )
    op.drop_table("event_edit_requests")
