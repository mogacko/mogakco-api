"""add event detail fields

Revision ID: 0003_event_detail
Revises: 0002_event
Create Date: 2026-09-04 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0003_event_detail"
down_revision: str | None = "0002_event"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """상세 조회에 필요한 설명과 신청 마감일을 추가한다."""

    op.add_column("events", sa.Column("description", sa.Text(), nullable=True))
    op.add_column("events", sa.Column("due_date", sa.Date(), nullable=True))
    op.execute(
        sa.text(
            "UPDATE events "
            "SET description = COALESCE(description, ''), "
            "due_date = COALESCE(due_date, date)"
        )
    )
    op.alter_column("events", "description", nullable=False)
    op.alter_column("events", "due_date", nullable=False)


def downgrade() -> None:
    """이벤트 상세 필드를 제거한다."""

    op.drop_column("events", "due_date")
    op.drop_column("events", "description")
