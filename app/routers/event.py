"""이벤트 목록 및 상세 조회 HTTP 엔드포인트."""

from datetime import timedelta
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status
from redis import Redis
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user
from app.exceptions import NotFoundError
from app.models import Event, EventCategory, User
from app.redis_client import get_redis_client
from app.schemas import EventDetailResponse, EventListItem
from app.schemas.error import error_responses
from app.services.event import (
    apply_to_event,
    event_application_lock,
    event_detail_from_row,
    event_list_item_from_row,
    select_events_with_stats,
)
from app.services.region import enabled_region
from app.time import kst_now

router = APIRouter(prefix="/api/v1", tags=["이벤트"])

# 지난 행사는 종료 후 7일까지만 목록에 남기고, 예정된 행사는 전부 보여준다.
PAST_EVENT_RETENTION_DAYS = 7


@router.get(
    "/event",
    response_model=list[EventListItem],
    responses=error_responses(401, 404, 422, 500),
    summary="이벤트 목록 조회",
)
def list_events(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    regionName: Annotated[str, Query()],
    categoryName: Annotated[EventCategory | None, Query()] = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> list[EventListItem]:
    """지역별 이벤트를 조건에 맞게 조회해 시간순으로 반환한다.

    인증된 사용자의 ID는 참가 상태 계산에만 사용한다. 지역은 필수이며 카테고리는
    선택 조건이고, offset/limit은 정렬 이후 결과에 적용된다.
    """

    # 잘못됐거나 운영하지 않는 지역이면 이벤트를 조회하기 전에 404로 끝낸다.
    region = enabled_region(db, regionName)
    # 오래 끝난 이벤트만 제외하고, 예정된 이벤트에는 상한 날짜를 두지 않는다.
    oldest_listed = kst_now().date() - timedelta(days=PAST_EVENT_RETENTION_DAYS)
    rows = db.execute(
        select_events_with_stats(current_user.id)
        .where(Event.region_id == region.id, Event.date >= oldest_listed)
        .where(
            # 카테고리가 없으면 전체를 조회하도록 항상 참인 조건을 사용한다.
            Event.category == categoryName
            if categoryName is not None
            else True
        )
        # 같은 날짜에는 시작 시간이 빠른 순서로 안정적으로 정렬한다.
        .order_by(Event.date, Event.start_at, Event.id)
        .offset(offset)
        .limit(limit)
    ).mappings().all()
    # 내부 컬럼 이름과 계산 값을 공개 응답 형식으로 변환한다.
    return [event_list_item_from_row(row) for row in rows]


@router.get(
    "/event/{eventUuid}",
    response_model=EventDetailResponse,
    responses=error_responses(401, 404, 422, 500),
    summary="이벤트 상세 조회",
)
def get_event_detail(
    eventUuid: UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> EventDetailResponse:
    """UUID로 삭제되지 않은 이벤트 한 건과 참가 상태를 조회한다."""

    row = db.execute(
        select_events_with_stats(current_user.id)
        .add_columns(Event.description)
        .where(Event.uuid == eventUuid)
    ).mappings().one_or_none()
    if row is None:
        raise NotFoundError("EVENT_NOT_FOUND", "이벤트를 찾을 수 없습니다.")
    return event_detail_from_row(row)


@router.post(
    "/event/{eventUuid}",
    status_code=status.HTTP_200_OK,
    response_class=Response,
    responses=error_responses(400, 401, 404, 409, 422, 429, 500),
    summary="이벤트 참가 신청",
)
def apply_event(
    eventUuid: UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    redis: Annotated[Redis, Depends(get_redis_client)],
) -> Response:
    """이벤트별 진입을 직렬화하고 DB에서 정원을 원자적으로 확보한다."""

    with event_application_lock(redis, eventUuid):
        apply_to_event(db, eventUuid, current_user.id)
    return Response(status_code=status.HTTP_200_OK)
