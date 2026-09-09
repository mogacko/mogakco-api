"""create event schema

Revision ID: 0002_event
Revises: 0001_initial
Create Date: 2026-09-02 01:40:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from geoalchemy2 import Geography


revision: str = '0002_event'
down_revision: str | None = '0001_initial'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """이벤트 본문과 사용자 참가 관계를 저장할 테이블을 생성한다."""

    # 이벤트 기본 정보와 목록 필터에 쓰이는 지역·카테고리·날짜를 저장한다.
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")
    op.create_table('events',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('uuid', sa.Uuid(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('region_id', sa.Integer(), nullable=False),
    sa.Column('host_id', sa.Integer(), nullable=True),
    sa.Column('status', sa.Enum('PENDING', 'APPROVED', 'REJECTED', 'CANCEL', name='eventstatus', native_enum=False, length=20), server_default=sa.text("'PENDING'"), nullable=False),
    sa.Column('category', sa.Enum('SEMINAR', 'HACKATHON', 'NETWORKING', 'RETROSPECTIVE', 'OTHER', name='eventcategory', native_enum=False, length=20), nullable=False),
    sa.Column('title', sa.String(length=50), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('place_name', sa.String(length=100), nullable=False),
    sa.Column('address', sa.String(length=255), nullable=False),
    sa.Column('detail_address', sa.String(length=100), nullable=True),
    sa.Column('location', Geography(geometry_type='POINT', srid=4326, spatial_index=False), nullable=False),
    sa.Column('kakao_place_id', sa.String(length=50), nullable=True),
    sa.Column('date', sa.Date(), nullable=False),
    sa.Column('due_date', sa.Date(), nullable=False),
    sa.Column('start_at', sa.Time(), nullable=False),
    sa.Column('end_at', sa.Time(), nullable=False),
    sa.Column('price', sa.Integer(), nullable=False),
    sa.Column('capacity', sa.Integer(), nullable=False),
    sa.Column('current_count', sa.Integer(), server_default=sa.text('0'), nullable=False),
    sa.Column('post_image_url', sa.String(length=500), nullable=True),
    sa.Column('cancel_reason', sa.String(length=200), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint('capacity >= 0', name='ck_events_capacity_non_negative'),
    sa.CheckConstraint('current_count >= 0 AND current_count <= capacity', name='ck_events_current_count_range'),
    sa.CheckConstraint('price >= 0', name='ck_events_price_non_negative'),
    sa.CheckConstraint("status IN ('PENDING', 'APPROVED', 'REJECTED', 'CANCEL')", name='ck_events_status'),
    sa.ForeignKeyConstraint(['host_id'], ['users.id'], name=op.f('fk_events_host_id_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['region_id'], ['regions.id'], name=op.f('fk_events_region_id_regions')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_events')),
    sa.UniqueConstraint('uuid', name=op.f('uq_events_uuid'))
    )
    # 자주 사용하는 지역/카테고리/날짜 조회 조합을 인덱스로 지원한다.
    op.create_index('ix_events_region_category_date', 'events', ['region_id', 'category', 'date'], unique=False)
    op.create_index('ix_events_region_date', 'events', ['region_id', 'date'], unique=False)
    op.create_index('ix_events_host_id', 'events', ['host_id'], unique=False)
    # 참가 관계를 별도 테이블에 두어 참가 인원과 사용자 참가 여부를 계산한다.
    op.create_table('event_participants',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('event_id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['event_id'], ['events.id'], name=op.f('fk_event_participants_event_id_events'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_event_participants_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_event_participants')),
    sa.UniqueConstraint('event_id', 'user_id', name='uq_event_participants_event_id_user_id')
    )
    op.create_index('ix_event_participants_user_id', 'event_participants', ['user_id'], unique=False)


def downgrade() -> None:
    """외래 키 의존성의 역순으로 이벤트 스키마를 제거한다."""

    # events를 참조하는 참가 테이블을 먼저 제거해야 외래 키 충돌이 나지 않는다.
    op.drop_index('ix_event_participants_user_id', table_name='event_participants')
    op.drop_table('event_participants')
    op.drop_index('ix_events_host_id', table_name='events')
    op.drop_index('ix_events_region_date', table_name='events')
    op.drop_index('ix_events_region_category_date', table_name='events')
    op.drop_table('events')
