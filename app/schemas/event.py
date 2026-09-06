"""이벤트 목록 API의 응답 스키마와 상태 값."""

from datetime import date as Date
from datetime import datetime, time
from enum import StrEnum
from typing import Annotated, Self
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

from app.models import EventCategory, EventStatus


class EventDisplayStatus(StrEnum):
    """공개 조회에서 클라이언트에 노출하는 계산된 이벤트 상태."""

    OPEN = "OPEN"
    FULL = "FULL"
    CANCEL = "CANCEL"
    PARTICIPATING = "PARTICIPATING"
    # 저장된 완료 상태와 아직 갱신되지 않은 지난 이벤트를 EXPIRED로 보여준다.
    EXPIRED = "EXPIRED"


RequiredText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
EventTitle = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=50),
]
PlaceName = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=100),
]
Address = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=255),
]
DetailAddress = Annotated[
    str,
    StringConstraints(strip_whitespace=True, max_length=100),
]
KakaoPlaceId = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=50),
]


class EventCreateRequest(BaseModel):
    """이벤트 등록 신청에 필요한 입력값."""

    model_config = ConfigDict(extra="forbid")

    categoryName: EventCategory
    title: EventTitle
    description: RequiredText
    placeName: PlaceName
    address: Address
    detailAddress: DetailAddress | None = None
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    kakaoPlaceId: KakaoPlaceId | None = None
    date: Date
    startAt: time
    endAt: time
    capacity: int = Field(ge=1)
    dueDate: Date
    postImageUrl: Annotated[str, StringConstraints(max_length=500)] | None = None
    price: int = Field(ge=0)


class EventCreateResponse(BaseModel):
    """등록 신청이 저장된 이벤트의 식별자와 안내 문구."""

    eventUuid: UUID
    message: str


class EventEditRequestBody(BaseModel):
    """승인을 요청할 행사 변경분."""

    model_config = ConfigDict(extra="forbid")

    categoryName: EventCategory | None = None
    title: EventTitle | None = None
    description: RequiredText | None = None
    placeName: PlaceName | None = None
    address: Address | None = None
    detailAddress: DetailAddress | None = None
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    kakaoPlaceId: KakaoPlaceId | None = None
    date: Date | None = None
    startAt: time | None = None
    endAt: time | None = None
    capacity: int | None = Field(default=None, ge=1)
    dueDate: Date | None = None
    postImageUrl: Annotated[
        str, StringConstraints(max_length=500)
    ] | None = None
    price: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_fields(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("at least one field is required")
        required_fields = {
            "categoryName",
            "title",
            "description",
            "placeName",
            "address",
            "latitude",
            "longitude",
            "date",
            "startAt",
            "endAt",
            "capacity",
            "dueDate",
            "price",
        }
        if any(
            getattr(self, field) is None
            for field in self.model_fields_set & required_fields
        ):
            raise ValueError("required event fields cannot be null")
        return self


class EventEditResponse(BaseModel):
    """접수된 행사 수정 요청의 대상과 안내 문구."""

    eventUuid: UUID
    message: str


class OwnedEventListItem(BaseModel):
    """등록자가 자신의 행사를 조회할 때 사용하는 저장 상태 응답."""

    eventUuid: UUID
    categoryName: EventCategory
    title: str
    date: Date
    startAt: time
    endAt: time
    placeName: str
    price: int = Field(ge=0)
    capacity: int = Field(ge=0)
    currentCount: int = Field(ge=0)
    status: EventStatus
    cancelReason: str | None
    postImageUrl: str | None
    createdAt: datetime
    updatedAt: datetime | None


class EventListItem(BaseModel):
    """이벤트 목록의 한 항목.

    필드명은 기존 API 계약에 맞춰 camelCase를 그대로 사용한다. ``currentCount``는
    DB에서 원자적으로 관리하고, ``status``는 조회 시 이벤트 정보를 바탕으로 계산한다.
    """

    eventUuid: UUID
    categoryName: EventCategory
    date: Date
    startAt: time
    endAt: time
    title: str
    placeName: str
    price: int = Field(ge=0)
    capacity: int = Field(ge=0)
    currentCount: int = Field(ge=0)
    status: EventDisplayStatus
    cancelReason: str | None
    postImageUrl: str | None


class EventDetailResponse(EventListItem):
    """목록 항목에 설명과 신청 마감일을 더한 이벤트 상세 응답."""

    description: str
    dueDate: Date
    address: str
    detailAddress: str | None
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    kakaoPlaceId: str | None
