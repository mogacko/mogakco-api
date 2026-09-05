"""이벤트와 이벤트 참가 정보를 저장하는 SQLAlchemy 모델."""

from datetime import date, datetime, time
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Time,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.time import kst_now


class EventCategory(StrEnum):
    """이벤트 목록 필터와 DB 저장에 공통으로 사용하는 카테고리."""

    SEMINAR = "SEMINAR"
    HACKATHON = "HACKATHON"
    NETWORKING = "NETWORKING"
    RETROSPECTIVE = "RETROSPECTIVE"
    OTHER = "OTHER"


class EventStatus(StrEnum):
    """DB에 저장하는 이벤트의 심사 및 취소 상태."""

    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    CANCEL = "CANCEL"


class Event(Base):
    """지역별 이벤트의 일정, 장소, 등록자와 심사 상태를 저장한다.

    레코드는 실제로 삭제하지 않고 ``deleted_at``을 채우는 소프트 삭제 방식을
    사용한다. 승인된 행사의 취소는 ``status``와 ``cancel_reason``으로 나타낸다.
    """

    __tablename__ = "events"
    __table_args__ = (
        # 금액과 정원은 애플리케이션 검증을 우회해도 음수가 저장되지 않게 한다.
        CheckConstraint("price >= 0", name="ck_events_price_non_negative"),
        CheckConstraint(
            "capacity >= 0",
            name="ck_events_capacity_non_negative",
        ),
        CheckConstraint(
            "current_count >= 0 AND current_count <= capacity",
            name="ck_events_current_count_range",
        ),
        CheckConstraint(
            "status IN ('PENDING', 'APPROVED', 'REJECTED', 'CANCEL')",
            name="ck_events_status",
        ),
        Index(
            "ix_events_region_category_date",
            "region_id",
            "category",
            "date",
        ),
        Index("ix_events_region_date", "region_id", "date"),
        Index("ix_events_host_id", "host_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    uuid: Mapped[UUID] = mapped_column(
        Uuid,
        unique=True,
        default=uuid4,
        server_default=text("gen_random_uuid()"),
    )
    region_id: Mapped[int] = mapped_column(ForeignKey("regions.id"))
    # 기존 행사는 등록자를 역추적할 수 없으므로 마이그레이션 이후에도 NULL을 허용한다.
    host_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    status: Mapped[EventStatus] = mapped_column(
        Enum(
            EventStatus,
            native_enum=False,
            length=20,
            values_callable=lambda enum: [e.value for e in enum],
        ),
        default=EventStatus.PENDING,
        server_default=text("'PENDING'"),
    )
    category: Mapped[EventCategory] = mapped_column(
        Enum(
            EventCategory,
            native_enum=False,
            length=20,
            values_callable=lambda enum: [e.value for e in enum],
        )
    )
    title: Mapped[str] = mapped_column(String(50))
    description: Mapped[str] = mapped_column(Text)
    place: Mapped[str] = mapped_column(String(100))
    date: Mapped[date] = mapped_column(Date)
    due_date: Mapped[date] = mapped_column(Date)
    start_at: Mapped[time] = mapped_column(Time)
    end_at: Mapped[time] = mapped_column(Time)
    price: Mapped[int] = mapped_column(Integer)
    capacity: Mapped[int] = mapped_column(Integer)
    current_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
        server_default=text("0"),
    )
    post_image_url: Mapped[str | None] = mapped_column(String(500))
    cancel_reason: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=kst_now, server_default=func.now()
    )
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EventParticipant(Base):
    """사용자의 이벤트 참가 관계를 나타내는 연결 모델.

    같은 사용자가 같은 이벤트에 두 번 참가하지 못하도록 복합 유니크 제약을
    두며, 사용자별 참가 이력 조회를 위해 별도 인덱스를 둔다.
    """

    __tablename__ = "event_participants"
    __table_args__ = (
        UniqueConstraint(
            "event_id",
            "user_id",
            name="uq_event_participants_event_id_user_id",
        ),
        Index("ix_event_participants_user_id", "user_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[int] = mapped_column(
        ForeignKey("events.id", ondelete="CASCADE")
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=kst_now, server_default=func.now()
    )
