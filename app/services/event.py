"""이벤트 조회와 참가 신청에 필요한 도메인 로직."""

import logging
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import date
from time import sleep
from typing import Any
from uuid import UUID, uuid4

from redis import Redis
from redis.exceptions import RedisError
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.sql import Select

from app.exceptions import (
    BadRequestError,
    ConflictError,
    NotFoundError,
    TooManyRequestsError,
)
from app.models import Event, EventParticipant
from app.schemas import EventDetailResponse, EventListItem, EventStatus
from app.time import kst_now

logger = logging.getLogger(__name__)

EVENT_PARTICIPATION_LOCK_TTL_MS = 5_000
EVENT_PARTICIPATION_LOCK_ATTEMPTS = 3
EVENT_PARTICIPATION_LOCK_RETRY_SECONDS = 0.05
_RELEASE_LOCK_SCRIPT = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
end
return 0
"""


def select_events_with_stats(current_user_id: int) -> Select:
    """활성 이벤트와 참가 통계를 함께 조회하는 SELECT 문을 만든다.

    지역, 카테고리, 날짜, 페이징 조건은 라우터가 이 SELECT 문에 이어 붙인다.
    여기서는 모든 이벤트에 공통인 소프트 삭제 제외, 참가자 수, 현재 사용자의
    참가 여부를 조회한다.
    """

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
            Event.current_count,
            Event.cancel_reason,
            Event.post_image_url,
            is_participating,
        )
        # 삭제된 이벤트는 모든 목록 조회에서 공통으로 제외한다.
        .where(Event.deleted_at.is_(None))
    )


def is_event_expired(event_date: date) -> bool:
    """행사 날짜가 KST 현재 날짜보다 이전인지 확인한다."""

    return event_date < kst_now().date()


def event_status(row: Mapping[str, Any]) -> EventStatus:
    """조회 행을 API 상태로 변환한다.

    우선순위는 취소 > 행사 종료 > 본인 참가 > 정원 마감 > 모집 중이다.
    따라서 종료된 행사는 참가 여부와 관계없이 EXPIRED로 반환한다.
    """

    if row["cancel_reason"] is not None:
        return EventStatus.CANCEL
    if is_event_expired(row["date"]):
        return EventStatus.EXPIRED
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


@contextmanager
def event_participation_lock(
    redis: Redis,
    event_uuid: UUID,
) -> Iterator[None]:
    """이벤트별 Redis 락을 짧게 획득하고 장애 시 DB 보호로 대체한다."""

    key = f"event:participation:{event_uuid}"
    token = uuid4().hex
    try:
        for attempt in range(EVENT_PARTICIPATION_LOCK_ATTEMPTS):
            if redis.set(
                key,
                token,
                nx=True,
                px=EVENT_PARTICIPATION_LOCK_TTL_MS,
            ):
                break
            if attempt + 1 < EVENT_PARTICIPATION_LOCK_ATTEMPTS:
                sleep(EVENT_PARTICIPATION_LOCK_RETRY_SECONDS)
        else:
            raise TooManyRequestsError(
                "EVENT_PARTICIPATION_BUSY",
                "참가 요청이 많습니다. 잠시 후 다시 시도해주세요.",
            )
    except RedisError:
        logger.warning(
            "Redis event participation lock failed; using DB safeguards",
            exc_info=True,
        )
        yield
        return

    try:
        yield
    finally:
        try:
            redis.eval(_RELEASE_LOCK_SCRIPT, 1, key, token)
        except RedisError:
            logger.warning(
                "Redis event participation lock release failed",
                exc_info=True,
            )


def _raise_event_application_error(
    db: Session,
    event_uuid: UUID,
    user_id: int,
) -> None:
    """조건부 갱신 실패 원인을 API 오류 우선순위에 맞춰 판별한다."""

    event = db.execute(
        select(
            Event.id,
            Event.cancel_reason,
            Event.due_date,
            Event.current_count,
            Event.capacity,
        ).where(
            Event.uuid == event_uuid,
            Event.deleted_at.is_(None),
        )
    ).one_or_none()
    if event is None or event.cancel_reason is not None:
        raise NotFoundError(
            "EVENT_NOT_FOUND",
            "존재하지 않거나 취소된 행사입니다.",
        )
    if db.scalar(
        select(EventParticipant.id).where(
            EventParticipant.event_id == event.id,
            EventParticipant.user_id == user_id,
        )
    ) is not None:
        raise ConflictError("ALREADY_APPLIED", "이미 신청한 행사입니다.")
    if event.due_date < kst_now().date():
        raise BadRequestError("EVENT_CLOSED", "모집 기간이 종료되었습니다.")
    if event.current_count >= event.capacity:
        raise ConflictError(
            "EVENT_FULL",
            "정원이 마감되어 신청할 수 없습니다.",
        )
    raise RuntimeError("Event application update failed unexpectedly")


def apply_to_event(db: Session, event_uuid: UUID, user_id: int) -> None:
    """정원이 남은 이벤트의 카운터 증가와 참가 등록을 함께 커밋한다."""

    event_id = db.scalar(
        update(Event)
        .where(
            Event.uuid == event_uuid,
            Event.deleted_at.is_(None),
            Event.cancel_reason.is_(None),
            Event.due_date >= kst_now().date(),
            Event.current_count < Event.capacity,
        )
        .values(current_count=Event.current_count + 1)
        .returning(Event.id)
    )
    if event_id is None:
        _raise_event_application_error(db, event_uuid, user_id)

    db.add(EventParticipant(event_id=event_id, user_id=user_id))
    try:
        db.commit()
    except IntegrityError as error:
        db.rollback()
        already_applied = db.scalar(
            select(EventParticipant.id)
            .join(Event, Event.id == EventParticipant.event_id)
            .where(
                Event.uuid == event_uuid,
                EventParticipant.user_id == user_id,
            )
        )
        if already_applied is not None:
            raise ConflictError(
                "ALREADY_APPLIED",
                "이미 신청한 행사입니다.",
            ) from error
        raise


def _raise_event_cancellation_error(
    db: Session,
    event_uuid: UUID,
    user_id: int,
) -> None:
    """참가 삭제 실패 원인을 이벤트, 신청, 기간 순으로 판별한다."""

    event = db.execute(
        select(
            Event.id,
            Event.date,
            Event.cancel_reason,
            Event.deleted_at,
        ).where(Event.uuid == event_uuid)
    ).one_or_none()
    if (
        event is None
        or event.deleted_at is not None
        or event.cancel_reason is not None
    ):
        raise NotFoundError("EVENT_NOT_FOUND", "존재하지 않는 행사입니다.")
    if db.scalar(
        select(EventParticipant.id).where(
            EventParticipant.event_id == event.id,
            EventParticipant.user_id == user_id,
        )
    ) is None:
        raise BadRequestError(
            "APPLICATION_NOT_FOUND",
            "신청 내역이 존재하지 않거나 이미 취소되었습니다.",
        )
    if is_event_expired(event.date):
        raise BadRequestError(
            "CANCEL_PERIOD_EXPIRED",
            "취소 가능 기간이 지났습니다.",
        )
    raise RuntimeError("Event cancellation delete failed unexpectedly")


def cancel_event_application(
    db: Session,
    event_uuid: UUID,
    user_id: int,
) -> None:
    """참가 관계 삭제와 이벤트 카운터 감소를 함께 커밋한다."""

    cancellable_event_id = (
        select(Event.id)
        .where(
            Event.uuid == event_uuid,
            Event.deleted_at.is_(None),
            Event.cancel_reason.is_(None),
            Event.date >= kst_now().date(),
        )
        .scalar_subquery()
    )
    event_id = db.scalar(
        delete(EventParticipant)
        .where(
            EventParticipant.event_id == cancellable_event_id,
            EventParticipant.user_id == user_id,
        )
        .returning(EventParticipant.event_id)
    )
    if event_id is None:
        _raise_event_cancellation_error(db, event_uuid, user_id)

    updated_event_id = db.scalar(
        update(Event)
        .where(Event.id == event_id, Event.current_count > 0)
        .values(current_count=Event.current_count - 1)
        .returning(Event.id)
    )
    if updated_event_id is None:
        db.rollback()
        raise RuntimeError("Event participant counter is inconsistent")
    db.commit()
