"""이벤트 등록, 조회와 참가 신청에 필요한 도메인 로직."""

import logging
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from time import sleep
from typing import Any
from uuid import UUID, uuid4

from geoalchemy2.elements import WKTElement
from redis import Redis
from redis.exceptions import RedisError
from sqlalchemy import and_, delete, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.sql import Select

from app.event.errors import EventErrors
from app.exceptions import (
    BadRequestException,
    ConflictException,
    DomainValidationException,
    ForbiddenException,
    NotFoundException,
    TooManyRequestsException,
)
from app.models import (
    Event,
    EventEditRequest as EventEditRequestModel,
    EventEditRequestStatus,
    EventParticipant,
    EventStatus,
    User,
)
from app.schemas import (
    EventCreateRequest,
    EventDetailResponse,
    EventDisplayStatus,
    EventEditRequestBody,
    EventListItem,
    OwnedEventListItem,
)
from app.common.errors import CommonErrors
from app.time import KST, kst_now

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
            Event.place_name,
            Event.price,
            Event.capacity,
            Event.current_count,
            Event.status,
            Event.cancel_reason,
            Event.post_image_url,
            is_participating,
        )
        # 삭제된 이벤트는 모든 목록 조회에서 공통으로 제외한다.
        .where(
            Event.deleted_at.is_(None),
            Event.status.in_(
                (
                    EventStatus.APPROVED,
                    EventStatus.COMPLETED,
                    EventStatus.CANCEL,
                )
            ),
        )
    )


def is_event_expired(event_date: date) -> bool:
    """행사 날짜가 KST 현재 날짜보다 이전인지 확인한다."""

    return event_date < kst_now().date()


def event_status(row: Mapping[str, Any]) -> EventDisplayStatus:
    """조회 행을 API 상태로 변환한다.

    우선순위는 취소 > 행사 종료 > 본인 참가 > 정원 마감 > 모집 중이다.
    따라서 종료된 행사는 참가 여부와 관계없이 EXPIRED로 반환한다.
    """

    if row["status"] is EventStatus.CANCEL:
        return EventDisplayStatus.CANCEL
    if row["status"] is EventStatus.COMPLETED:
        return EventDisplayStatus.EXPIRED
    if is_event_expired(row["date"]):
        return EventDisplayStatus.EXPIRED
    if row["is_participating"]:
        return EventDisplayStatus.PARTICIPATING
    if row["current_count"] >= row["capacity"]:
        return EventDisplayStatus.FULL
    return EventDisplayStatus.OPEN


def register_event(
    db: Session,
    request: EventCreateRequest,
    current_user: User,
) -> Event:
    """행사와 호스트 참가 관계를 하나의 트랜잭션으로 등록한다."""

    today = kst_now().date()
    if request.dueDate < today or request.dueDate >= request.date:
        raise BadRequestException(EventErrors.INVALID_DATE)
    if request.startAt >= request.endAt:
        raise BadRequestException(EventErrors.INVALID_TIME)

    event = Event(
        region_id=current_user.region_id,
        host_id=current_user.id,
        status=EventStatus.PENDING,
        category=request.categoryName,
        title=request.title,
        description=request.description,
        place_name=request.placeName,
        address=request.address,
        detail_address=request.detailAddress or None,
        location=WKTElement(
            f"POINT({request.longitude} {request.latitude})",
            srid=4326,
        ),
        kakao_place_id=request.kakaoPlaceId,
        date=request.date,
        due_date=request.dueDate,
        start_at=request.startAt,
        end_at=request.endAt,
        price=request.price,
        capacity=request.capacity,
        current_count=1,
        post_image_url=request.postImageUrl,
    )
    db.add(event)
    db.flush()
    db.add(EventParticipant(event_id=event.id, user_id=current_user.id))
    db.commit()
    return event


def list_owned_events(db: Session, user_id: int) -> list[OwnedEventListItem]:
    """등록자가 올린 삭제되지 않은 행사를 최신순으로 반환한다."""

    events = db.scalars(
        select(Event)
        .where(Event.host_id == user_id, Event.deleted_at.is_(None))
        .order_by(Event.created_at.desc(), Event.id.desc())
    ).all()
    return [
        OwnedEventListItem(
            eventUuid=event.uuid,
            categoryName=event.category,
            title=event.title,
            date=event.date,
            startAt=event.start_at,
            endAt=event.end_at,
            placeName=event.place_name,
            price=event.price,
            capacity=event.capacity,
            currentCount=event.current_count,
            status=event.status,
            cancelReason=event.cancel_reason,
            postImageUrl=event.post_image_url,
            createdAt=event.created_at,
            updatedAt=event.updated_at,
        )
        for event in events
    ]


def cancel_owned_event(db: Session, event_uuid: UUID, user_id: int) -> None:
    """등록자가 올린 행사의 상태만 CANCEL로 전환한다."""

    event = db.scalar(
        select(Event)
        .where(Event.uuid == event_uuid, Event.deleted_at.is_(None))
        .with_for_update()
    )
    if event is None:
        raise NotFoundException(EventErrors.NOT_FOUND)
    if event.host_id != user_id:
        raise ForbiddenException(EventErrors.NOT_OWNER)
    if event.status is EventStatus.CANCEL:
        db.commit()
        return
    if event.status not in (EventStatus.PENDING, EventStatus.APPROVED):
        raise ConflictException(EventErrors.NOT_CANCELLABLE)

    now = kst_now()
    event.status = EventStatus.CANCEL
    event.cancel_reason = "등록자 요청"
    event.cancelled_at = now
    event.updated_at = now
    db.commit()


def request_event_edit(
    db: Session,
    event_uuid: UUID,
    user_id: int,
    request: EventEditRequestBody,
) -> None:
    """행사 원본을 바꾸지 않고 검증된 변경분을 승인 대기로 저장한다."""

    event = db.scalar(
        select(Event)
        .where(Event.uuid == event_uuid, Event.deleted_at.is_(None))
        .with_for_update()
    )
    if event is None:
        raise NotFoundException(EventErrors.NOT_FOUND)
    if event.host_id != user_id:
        raise ForbiddenException(EventErrors.NOT_OWNER)
    if event.status not in (EventStatus.PENDING, EventStatus.APPROVED):
        raise ConflictException(EventErrors.NOT_EDITABLE)
    if db.scalar(
        select(EventEditRequestModel.id).where(
            EventEditRequestModel.event_id == event.id,
            EventEditRequestModel.status == EventEditRequestStatus.PENDING,
        )
    ) is not None:
        raise ConflictException(EventErrors.EDIT_ALREADY_PENDING)

    fields = request.model_fields_set
    if {"date", "dueDate"} & fields:
        event_date = request.date if "date" in fields else event.date
        due_date = request.dueDate if "dueDate" in fields else event.due_date
        if due_date < kst_now().date() or due_date >= event_date:
            raise DomainValidationException(CommonErrors.INVALID_REQUEST)
    if {"startAt", "endAt"} & fields:
        start_at = request.startAt if "startAt" in fields else event.start_at
        end_at = request.endAt if "endAt" in fields else event.end_at
        if start_at >= end_at:
            raise DomainValidationException(CommonErrors.INVALID_REQUEST)
    if "capacity" in fields and request.capacity < event.current_count:
        raise DomainValidationException(CommonErrors.INVALID_REQUEST)

    changes = request.model_dump(mode="json", exclude_unset=True)
    if changes.get("detailAddress") == "":
        changes["detailAddress"] = None
    db.add(EventEditRequestModel(event_id=event.id, changes=changes))
    try:
        db.commit()
    except IntegrityError as error:
        db.rollback()
        raise ConflictException(EventErrors.EDIT_ALREADY_PENDING) from error


def event_list_item_from_row(row: Mapping[str, Any]) -> EventListItem:
    """SQLAlchemy 매핑 행을 외부 API 응답 모델로 변환한다."""

    return EventListItem(
        eventUuid=row["uuid"],
        categoryName=row["category"],
        date=row["date"],
        startAt=row["start_at"],
        endAt=row["end_at"],
        title=row["title"],
        placeName=row["place_name"],
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
        address=row["address"],
        detailAddress=row["detail_address"],
        latitude=row["latitude"],
        longitude=row["longitude"],
        kakaoPlaceId=row["kakao_place_id"],
    )


def advance_event_lifecycle(db: Session, now: datetime | None = None) -> int:
    """시간이 지난 승인·대기 이벤트를 종료 상태로 전환한다."""

    current = (now or kst_now()).astimezone(KST)
    current_time = current.time().replace(tzinfo=None)
    completed = db.execute(
        update(Event)
        .where(
            Event.status == EventStatus.APPROVED,
            or_(
                Event.date < current.date(),
                and_(
                    Event.date == current.date(),
                    Event.end_at <= current_time,
                ),
            ),
        )
        .values(status=EventStatus.COMPLETED)
    ).rowcount
    rejected = db.execute(
        update(Event)
        .where(
            Event.status == EventStatus.PENDING,
            or_(
                Event.date < current.date(),
                and_(
                    Event.date == current.date(),
                    Event.start_at <= current_time,
                ),
            ),
        )
        .values(
            status=EventStatus.REJECTED,
            rejected_reason="행사 시작 시각까지 승인되지 않음",
            rejected_at=current,
        )
    ).rowcount
    db.commit()
    return completed + rejected


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
            raise TooManyRequestsException(EventErrors.PARTICIPATION_BUSY)
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
            Event.status,
            Event.due_date,
            Event.current_count,
            Event.capacity,
        ).where(
            Event.uuid == event_uuid,
            Event.deleted_at.is_(None),
        )
    ).one_or_none()
    if event is None or event.status is not EventStatus.APPROVED:
        raise NotFoundException(EventErrors.NOT_FOUND)
    if db.scalar(
        select(EventParticipant.id).where(
            EventParticipant.event_id == event.id,
            EventParticipant.user_id == user_id,
        )
    ) is not None:
        raise ConflictException(EventErrors.ALREADY_APPLIED)
    if event.due_date < kst_now().date():
        raise BadRequestException(EventErrors.CLOSED)
    if event.current_count >= event.capacity:
        raise ConflictException(EventErrors.FULL)
    raise RuntimeError("Event application update failed unexpectedly")


def apply_to_event(db: Session, event_uuid: UUID, user_id: int) -> None:
    """정원이 남은 이벤트의 카운터 증가와 참가 등록을 함께 커밋한다."""

    event_id = db.scalar(
        update(Event)
        .where(
            Event.uuid == event_uuid,
            Event.deleted_at.is_(None),
            Event.status == EventStatus.APPROVED,
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
            raise ConflictException(EventErrors.ALREADY_APPLIED) from error
        raise


def _raise_event_cancellation_error(
    db: Session,
    event_uuid: UUID,
    user_id: int,
    earliest_cancellable_start: datetime,
) -> None:
    """참가 삭제 실패 원인을 이벤트, 신청, 기간 순으로 판별한다."""

    event = db.execute(
        select(
            Event.id,
            Event.date,
            Event.start_at,
            Event.host_id,
            Event.status,
            Event.deleted_at,
        ).where(Event.uuid == event_uuid)
    ).one_or_none()
    if (
        event is None
        or event.deleted_at is not None
        or event.status is not EventStatus.APPROVED
    ):
        raise NotFoundException(EventErrors.NOT_FOUND)
    if event.host_id == user_id:
        raise BadRequestException(
            EventErrors.HOST_CANNOT_CANCEL_PARTICIPATION
        )
    if db.scalar(
        select(EventParticipant.id).where(
            EventParticipant.event_id == event.id,
            EventParticipant.user_id == user_id,
        )
    ) is None:
        raise BadRequestException(EventErrors.APPLICATION_NOT_FOUND)
    if (
        datetime.combine(event.date, event.start_at, KST)
        < earliest_cancellable_start
    ):
        raise BadRequestException(EventErrors.CANCEL_PERIOD_EXPIRED)
    raise RuntimeError("Event cancellation delete failed unexpectedly")


def cancel_event_application(
    db: Session,
    event_uuid: UUID,
    user_id: int,
) -> None:
    """참가 관계 삭제와 이벤트 카운터 감소를 함께 커밋한다."""

    earliest_cancellable_start = kst_now() + timedelta(hours=2)
    event_id = db.scalar(
        select(Event.id)
        .where(
            Event.uuid == event_uuid,
            Event.deleted_at.is_(None),
            Event.status == EventStatus.APPROVED,
            or_(Event.host_id.is_(None), Event.host_id != user_id),
            or_(
                Event.date > earliest_cancellable_start.date(),
                and_(
                    Event.date == earliest_cancellable_start.date(),
                    Event.start_at
                    >= earliest_cancellable_start.time().replace(tzinfo=None),
                ),
            ),
        )
        .with_for_update()
    )
    if event_id is None:
        _raise_event_cancellation_error(
            db,
            event_uuid,
            user_id,
            earliest_cancellable_start,
        )

    participant_id = db.scalar(
        delete(EventParticipant)
        .where(
            EventParticipant.event_id == event_id,
            EventParticipant.user_id == user_id,
        )
        .returning(EventParticipant.id)
    )
    if participant_id is None:
        _raise_event_cancellation_error(
            db,
            event_uuid,
            user_id,
            earliest_cancellable_start,
        )

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
