"""이벤트 목록 API의 응답 스키마와 상태 값."""

from datetime import date, time
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, Field

from app.models import EventCategory


class EventStatus(StrEnum):
    """클라이언트에 노출하는 이벤트의 현재 상태."""

    OPEN = "OPEN"
    FULL = "FULL"
    CANCEL = "CANCEL"
    PARTICIPATING = "PARTICIPATING"
    # 신청 마감일이 지난 이벤트는 EXPIRED, 취소된 이벤트는 CANCEL로 표시한다.
    EXPIRED = "EXPIRED"


class EventListItem(BaseModel):
    """이벤트 목록의 한 항목.

    필드명은 기존 API 계약에 맞춰 camelCase를 그대로 사용한다. ``currentCount``는
    DB에서 원자적으로 관리하고, ``status``는 조회 시 이벤트 정보를 바탕으로 계산한다.
    """

    eventUuid: UUID
    categoryName: EventCategory
    date: date
    startAt: time
    endAt: time
    title: str
    place: str
    price: int = Field(ge=0)
    capacity: int = Field(ge=0)
    currentCount: int = Field(ge=0)
    status: EventStatus
    cancelReason: str | None
    postImageUrl: str | None


class EventDetailResponse(EventListItem):
    """목록 항목에 설명과 신청 마감일을 더한 이벤트 상세 응답."""

    description: str
    dueDate: date
