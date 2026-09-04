"""add atomic event participant counter

Revision ID: 0004_event_count
Revises: 0003_event_detail
Create Date: 2026-09-04 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0004_event_count"
down_revision: str | None = "0003_event_detail"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """기존 참가 인원을 채우고 원자적 갱신용 카운터를 추가한다."""

    op.add_column(
        "events",
        sa.Column(
            "current_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
    )
    op.execute(
        sa.text(
            "UPDATE events AS e "
            "SET current_count = counts.value, "
            "capacity = GREATEST(e.capacity, counts.value) "
            "FROM ("
            "SELECT event_id, count(*)::integer AS value "
            "FROM event_participants GROUP BY event_id"
            ") AS counts "
            "WHERE e.id = counts.event_id"
        )
    )
    op.create_check_constraint(
        "ck_events_current_count_range",
        "events",
        "current_count >= 0 AND current_count <= capacity",
    )
    op.execute(
        "ALTER TABLE event_participants "
        "RENAME CONSTRAINT uq_event_participants_event_id "
        "TO uq_event_participants_event_id_user_id"
    )


def downgrade() -> None:
    """참가 카운터를 제거하고 기존 제약 이름을 복구한다."""

    op.execute(
        "ALTER TABLE event_participants "
        "RENAME CONSTRAINT uq_event_participants_event_id_user_id "
        "TO uq_event_participants_event_id"
    )
    op.drop_constraint(
        "ck_events_current_count_range",
        "events",
        type_="check",
    )
    op.drop_column("events", "current_count")
