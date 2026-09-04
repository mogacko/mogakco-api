"""이벤트 목록 조회 쿼리와 응답 변환 로직."""

from collections.abc import Mapping
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.sql import Select

from app.models import Event, EventParticipant
from app.schemas import EventDetailResponse, EventListItem, EventStatus


def select_events_with_stats(current_user_id: int) -> Select:
    """활성 이벤트와 참가 통계를 함께 조회하는 SELECT 문을 만든다.

    지역, 카테고리, 날짜, 페이징 조건은 라우터가 이 SELECT 문에 이어 붙인다.
    여기서는 모든 이벤트에 공통인 소프트 삭제 제외, 참가자 수, 현재 사용자의
    참가 여부만 계산한다.
    """

    # 참가자 테이블을 이벤트별로 먼저 집계해 본문 조회에서 행이 중복되지 않게 한다.
    participant_counts = (
        select(
            EventParticipant.event_id.label("event_id"),
            func.count(EventParticipant.id).label("current_count"),
        )
        .group_by(EventParticipant.event_id)
        .subquery()
    )
    # EXISTS를 사용해 참가자 전체를 로드하지 않고 현재 사용자의 참가 여부만 구한다.
    is_participating = (
        select(1)
        .select_from(EventParticipant)
        .where(
            EventParticipant.event_id == Event.id,
            EventParticipant.user_id == current_user_id,
        )
        .correlate(Event)
        .exists()
        .label("is_participating")
    )
    return (
        select(
            Event.uuid,
            Event.category,
            Event.date,
            Event.start_at,
            Event.end_at,
            Event.title,
            Event.place,
            Event.price,
            Event.capacity,
            func.coalesce(participant_counts.c.current_count, 0).label(
                "current_count"
            ),
            Event.cancel_reason,
            Event.post_image_url,
            is_participating,
        )
        .outerjoin(
            participant_counts,
            participant_counts.c.event_id == Event.id,
        )
        # 삭제된 이벤트는 모든 목록 조회에서 공통으로 제외한다.
        .where(Event.deleted_at.is_(None))
    )


def event_status(row: Mapping[str, Any]) -> EventStatus:
    """조회 행을 API 상태로 변환한다.

    우선순위는 취소 > 본인 참가 > 정원 마감 > 모집 중이다. 따라서 취소된 행사나
    본인이 이미 참가한 행사는 정원이 찼더라도 더 구체적인 상태를 반환한다.
    """

    if row["cancel_reason"] is not None:
        return EventStatus.CANCEL
    if row["is_participating"]:
        return EventStatus.PARTICIPATING
    if row["current_count"] >= row["capacity"]:
        return EventStatus.FULL
    return EventStatus.OPEN


def event_list_item_from_row(row: Mapping[str, Any]) -> EventListItem:
    """SQLAlchemy 매핑 행을 외부 API 응답 모델로 변환한다."""

    return EventListItem(
        eventUuid=row["uuid"],
        categoryName=row["category"],
        date=row["date"],
        startAt=row["start_at"],
        endAt=row["end_at"],
        title=row["title"],
        place=row["place"],
        price=row["price"],
        capacity=row["capacity"],
        currentCount=row["current_count"],
        status=event_status(row),
        cancelReason=row["cancel_reason"],
        postImageUrl=row["post_image_url"],
    )


def event_detail_from_row(row: Mapping[str, Any]) -> EventDetailResponse:
    """목록용 공통 필드에 상세 설명과 신청 마감일을 더한다."""

    item = event_list_item_from_row(row)
    return EventDetailResponse(
        **item.model_dump(),
        description=row["description"],
        dueDate=row["due_date"],
    )
