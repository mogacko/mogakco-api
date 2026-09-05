"""이벤트 목록 API의 응답 스키마와 상태 값."""

from datetime import date, time
from enum import StrEnum
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.models import EventCategory


class EventDisplayStatus(StrEnum):
    """공개 조회에서 클라이언트에 노출하는 계산된 이벤트 상태."""

    OPEN = "OPEN"
    FULL = "FULL"
    CANCEL = "CANCEL"
    PARTICIPATING = "PARTICIPATING"
    # 행사 날짜가 지난 이벤트는 DB 갱신 없이 EXPIRED로 계산한다.
    EXPIRED = "EXPIRED"


RequiredText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
EventTitle = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=50),
]
EventPlace = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=100),
]


class EventCreateRequest(BaseModel):
    """이벤트 등록 신청에 필요한 입력값."""

    model_config = ConfigDict(extra="forbid")

    categoryName: EventCategory
    title: EventTitle
    description: RequiredText
    place: EventPlace
    date: date
    startAt: time
    endAt: time
    capacity: int = Field(ge=1)
    dueDate: date
    postImageUrl: Annotated[str, StringConstraints(max_length=500)] | None = None
    price: int = Field(ge=0)


class EventCreateResponse(BaseModel):
    """등록 신청이 저장된 이벤트의 식별자와 안내 문구."""

    eventUuid: UUID
    message: str


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
    status: EventDisplayStatus
    cancelReason: str | None
    postImageUrl: str | None


class EventDetailResponse(EventListItem):
    """목록 항목에 설명과 신청 마감일을 더한 이벤트 상세 응답."""

    description: str
    dueDate: date
