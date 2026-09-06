"""restrict deletion of users with event participation

Revision ID: 0007_participant_restrict
Revises: 0006_event_completed
Create Date: 2026-09-07 00:00:00.000000
"""

from collections.abc import Sequence

from alembic import op


revision: str = "0007_participant_restrict"
down_revision: str | None = "0006_event_completed"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """참가 이력이 남은 사용자의 물리 삭제를 차단한다."""

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


def downgrade() -> None:
    """사용자 삭제 시 참가 행을 함께 삭제하는 기존 정책으로 복구한다."""

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
