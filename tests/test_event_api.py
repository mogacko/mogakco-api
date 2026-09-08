"""이벤트 API의 조회 조건, 상태 계산, 오류 계약을 검증한다."""

import os
from collections.abc import Generator
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, time, timedelta
from threading import Barrier, Lock
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from geoalchemy2 import Geometry
from geoalchemy2.elements import WKTElement
from redis.exceptions import RedisError
from sqlalchemy.orm import Session

from app.database import create_db_engine, get_db
from app.exceptions import BadRequestException, ConflictException
from app.jobs.event_lifecycle import seconds_until_next_run
from app.main import app
from app.models import (
    Comment,
    CommunityPost,
    Event,
    EventCategory,
    EventEditRequest,
    EventEditRequestStatus,
    EventParticipant,
    EventStatus,
    User,
)
from app.routers.event import PAST_EVENT_RETENTION_DAYS
from app.redis_client import get_redis_client
from app.services.event import (
    advance_event_lifecycle,
    apply_to_event,
    cancel_event_application,
)
from app.time import KST, kst_now

DATABASE_URL = os.environ["DATABASE_URL"]
_DEBUG_USER_UUIDS: dict[int, UUID] = {}


class FakeEventRedis:
    """이벤트 락 테스트에 필요한 Redis 명령만 메모리에서 제공한다."""

    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.mutex = Lock()

    def clear(self) -> None:
        with self.mutex:
            self.values.clear()

    def set(
        self,
        key: str,
        value: str,
        *,
        nx: bool,
        px: int,
    ) -> bool:
        del px
        with self.mutex:
            if nx and key in self.values:
                return False
            self.values[key] = value
            return True

    def eval(
        self,
        _script: str,
        _key_count: int,
        key: str,
        token: str,
    ) -> int:
        with self.mutex:
            if self.values.get(key) != token:
                return 0
            del self.values[key]
            return 1


class BrokenEventRedis(FakeEventRedis):
    """Redis 장애 시 DB 보호 로직으로 진행되는지 확인하는 대역."""

    def set(
        self,
        key: str,
        value: str,
        *,
        nx: bool,
        px: int,
    ) -> bool:
        raise RedisError("redis unavailable")


_EVENT_REDIS = FakeEventRedis()

DISABLED_REGION_ID = 3  # gyeonggi, 0001_initial 시드에서 is_enabled=False


def days(offset: int) -> date:
    """KST 오늘을 기준으로 테스트 데이터에 사용할 상대 날짜를 만든다."""
    return kst_now().date() + timedelta(days=offset)


@pytest.fixture
def api(
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[tuple[TestClient, sa.Engine, int]]:
    """마이그레이션과 인증 사용자를 준비한 API 클라이언트를 제공한다.

    각 테스트 전에 이벤트 관련 데이터를 비우고, 운영 인증 대신 테스트에서
    허용되는 디버그 UUID 헤더로 현재 사용자를 식별한다.
    """

    command.upgrade(Config("alembic.ini"), "head")
    engine = create_db_engine(DATABASE_URL)
    with Session(engine, expire_on_commit=False) as db:
        db.execute(sa.delete(EventParticipant))
        db.execute(sa.delete(Event))
        db.execute(sa.delete(Comment))
        db.execute(sa.delete(CommunityPost))
        db.execute(sa.delete(User))
        viewer = User(nickname="event-viewer", region_id=1)
        host = User(nickname="event-host", region_id=1)
        db.add_all([viewer, host])
        db.commit()
        viewer_id = viewer.id
        _DEBUG_USER_UUIDS[viewer_id] = viewer.uuid

    def override_db() -> Generator[Session]:
        with Session(engine, expire_on_commit=False) as db:
            yield db

    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("ENABLE_DEBUG_AUTH", "true")
    monkeypatch.delenv("DEBUG_DEFAULT_USER_UUID", raising=False)
    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_redis_client] = lambda: _EVENT_REDIS
    _EVENT_REDIS.clear()
    with TestClient(app) as client:
        yield client, engine, viewer_id
    app.dependency_overrides.clear()
    _DEBUG_USER_UUIDS.clear()
    engine.dispose()


def auth(user: int | UUID) -> dict[str, str]:
    """사용자 ID 또는 UUID를 디버그 인증 헤더로 변환한다."""

    user_uuid = user if isinstance(user, UUID) else _DEBUG_USER_UUIDS[user]
    return {"X-Debug-User-Uuid": str(user_uuid)}


def add_event(
    db: Session,
    *,
    title: str,
    description: str = "이벤트 설명 내용",
    category: EventCategory = EventCategory.SEMINAR,
    region_id: int = 1,
    on: date | None = None,
    due_date: date | None = None,
    start_at: time = time(19, 0),
    end_at: time = time(21, 0),
    price: int = 0,
    capacity: int = 10,
    post_image_url: str | None = None,
    place_name: str = "카페 그리다 역삼",
    address: str = "서울특별시 강남구 테헤란로 132",
    detail_address: str | None = "2층",
    latitude: float = 37.5001234,
    longitude: float = 127.035321,
    kakao_place_id: str | None = "123456789",
    cancel_reason: str | None = None,
    host_id: int | None = None,
    status: EventStatus | None = None,
    deleted_at: datetime | None = None,
) -> Event:
    """테스트에 필요한 값만 바꿔 이벤트 한 건을 생성한다."""

    event_date = days(3) if on is None else on
    stored_status = status or (
        EventStatus.CANCEL
        if cancel_reason is not None
        else EventStatus.APPROVED
    )
    stored_host_id = host_id if host_id is not None else db.scalar(
        sa.select(User.id).where(User.nickname == "event-host")
    )
    event = Event(
        region_id=region_id,
        host_id=stored_host_id,
        status=stored_status,
        category=category,
        title=title,
        description=description,
        place_name=place_name,
        address=address,
        detail_address=detail_address,
        location=WKTElement(f"POINT({longitude} {latitude})", srid=4326),
        kakao_place_id=kakao_place_id,
        date=event_date,
        due_date=event_date if due_date is None else due_date,
        start_at=start_at,
        end_at=end_at,
        price=price,
        capacity=capacity,
        post_image_url=post_image_url,
        cancel_reason=cancel_reason,
        deleted_at=deleted_at,
    )
    db.add(event)
    db.flush()
    return event


def join(db: Session, event_id: int, user_id: int) -> None:
    """지정한 사용자를 이벤트 참가자로 등록한다."""

    db.execute(
        sa.update(Event)
        .where(Event.id == event_id)
        .values(current_count=Event.current_count + 1)
    )
    db.add(EventParticipant(event_id=event_id, user_id=user_id))


def titles(response) -> list[str]:
    """목록 응답에서 정렬과 필터 확인에 필요한 제목만 추출한다."""

    return [item["title"] for item in response.json()]


def event_create_payload(**changes: object) -> dict[str, object]:
    """행사 등록 테스트의 기본 요청 본문을 만든다."""

    payload: dict[str, object] = {
        "categoryName": "SEMINAR",
        "title": "이벤트 제목",
        "description": "이벤트 설명 내용",
        "placeName": "카페 그리다 역삼",
        "address": "서울특별시 강남구 테헤란로 132",
        "detailAddress": "2층",
        "latitude": 37.5001234,
        "longitude": 127.035321,
        "kakaoPlaceId": "123456789",
        "date": days(3).isoformat(),
        "startAt": "19:00:00",
        "endAt": "21:00:00",
        "capacity": 7,
        "dueDate": days(2).isoformat(),
        "postImageUrl": "https://images.example.com/event.png",
        "price": 0,
    }
    payload.update(changes)
    return payload


def test_create_event_registers_pending_host_as_first_participant(
    api: tuple[TestClient, sa.Engine, int],
) -> None:
    """등록 요청자와 참가 카운터가 승인 대기 행사에 함께 저장되는지 확인한다."""

    client, engine, viewer_id = api

    response = client.post(
        "/api/v1/events",
        headers=auth(viewer_id),
        json=event_create_payload(),
    )

    assert response.status_code == 201
    assert response.json()["message"] == "행사가 성공적으로 등록되었습니다."
    event_uuid = UUID(response.json()["eventUuid"])
    with Session(engine) as db:
        event = db.scalar(sa.select(Event).where(Event.uuid == event_uuid))
        assert event is not None
        assert event.host_id == viewer_id
        assert event.region_id == 1
        assert event.status is EventStatus.PENDING
        assert event.current_count == 1
        assert event.category is EventCategory.SEMINAR
        assert event.title == "이벤트 제목"
        assert event.description == "이벤트 설명 내용"
        assert event.place_name == "카페 그리다 역삼"
        assert event.address == "서울특별시 강남구 테헤란로 132"
        assert event.detail_address == "2층"
        stored_latitude, stored_longitude = db.execute(
            sa.select(
                sa.func.ST_Y(
                    Event.location.cast(
                        Geometry("POINT", srid=4326, spatial_index=False)
                    )
                ),
                sa.func.ST_X(
                    Event.location.cast(
                        Geometry("POINT", srid=4326, spatial_index=False)
                    )
                ),
            ).where(Event.id == event.id)
        ).one()
        assert stored_latitude == pytest.approx(37.5001234)
        assert stored_longitude == pytest.approx(127.035321)
        assert event.kakao_place_id == "123456789"
        assert event.date == days(3)
        assert event.due_date == days(2)
        assert event.start_at == time(19, 0)
        assert event.end_at == time(21, 0)
        assert event.capacity == 7
        assert event.price == 0
        assert event.post_image_url == "https://images.example.com/event.png"
        assert db.scalar(
            sa.select(sa.func.count(EventParticipant.id)).where(
                EventParticipant.event_id == event.id,
                EventParticipant.user_id == viewer_id,
            )
        ) == 1


def test_create_event_requires_authentication_and_valid_input(
    api: tuple[TestClient, sa.Engine, int],
) -> None:
    """미인증 요청과 잘못된 날짜·시간·정원을 저장 전에 거절한다."""

    client, _, viewer_id = api

    assert client.post(
        "/api/v1/events",
        json=event_create_payload(),
    ).status_code == 401

    invalid_cases = (
        (event_create_payload(dueDate=days(-1).isoformat()), 400),
        (event_create_payload(dueDate=days(3).isoformat()), 400),
        (event_create_payload(startAt="21:00:00"), 400),
        (event_create_payload(startAt="18:00:00+09:00"), 422),
        (event_create_payload(endAt="21:00:00+09:00"), 422),
        (event_create_payload(capacity=0), 422),
        (event_create_payload(latitude=-90.0001), 422),
        (event_create_payload(latitude=90.0001), 422),
        (event_create_payload(longitude=-180.0001), 422),
        (event_create_payload(longitude=180.0001), 422),
        (event_create_payload(unexpected="value"), 422),
    )
    for payload, expected_status in invalid_cases:
        response = client.post(
            "/api/v1/events",
            headers=auth(viewer_id),
            json=payload,
        )
        assert response.status_code == expected_status


def test_create_event_allows_location_without_kakao_place_id(
    api: tuple[TestClient, sa.Engine, int],
) -> None:
    """직접 지정한 장소는 카카오 ID 없이 저장하고 빈 상세 주소를 NULL로 바꾼다."""

    client, engine, viewer_id = api
    response = client.post(
        "/api/v1/events",
        headers=auth(viewer_id),
        json=event_create_payload(
            title="직접 지정 장소",
            detailAddress="   ",
            kakaoPlaceId=None,
        ),
    )

    assert response.status_code == 201
    with Session(engine) as db:
        event = db.scalar(
            sa.select(Event).where(
                Event.uuid == UUID(response.json()["eventUuid"])
            )
        )
        assert event is not None
        assert event.detail_address is None
        assert event.kakao_place_id is None


def test_public_queries_hide_pending_and_rejected_events(
    api: tuple[TestClient, sa.Engine, int],
) -> None:
    """심사 중이거나 반려된 행사는 목록과 공개 상세에서 모두 숨긴다."""

    client, engine, viewer_id = api
    with Session(engine, expire_on_commit=False) as db:
        pending = add_event(
            db,
            title="pending",
            status=EventStatus.PENDING,
        )
        rejected = add_event(
            db,
            title="rejected",
            status=EventStatus.REJECTED,
        )
        add_event(db, title="approved")
        add_event(db, title="completed", status=EventStatus.COMPLETED)
        add_event(db, title="cancelled", cancel_reason="운영 사정")
        db.commit()
        hidden_uuids = (pending.uuid, rejected.uuid)

    response = client.get(
        "/api/v1/events?regionName=seoul",
        headers=auth(viewer_id),
    )

    assert response.status_code == 200
    assert titles(response) == ["approved", "completed", "cancelled"]
    for event_uuid in hidden_uuids:
        detail = client.get(
            f"/api/v1/events/{event_uuid}",
            headers=auth(viewer_id),
        )
        assert detail.status_code == 404


def test_list_returns_full_dto(
    api: tuple[TestClient, sa.Engine, int],
) -> None:
    """목록 항목이 합의된 모든 필드와 JSON 형식을 반환하는지 확인한다."""

    client, engine, viewer_id = api
    with Session(engine, expire_on_commit=False) as db:
        event = add_event(
            db,
            title="이벤트 제목",
            category=EventCategory.HACKATHON,
            on=days(8),
            price=15_000,
            capacity=30,
            post_image_url="https://images.example.com/a.png",
        )
        other = User(nickname="joiner", region_id=1)
        db.add(other)
        db.flush()
        join(db, event.id, other.id)
        db.commit()
        event_uuid = str(event.uuid)

    response = client.get(
        "/api/v1/events?regionName=seoul",
        headers=auth(viewer_id),
    )

    assert response.status_code == 200
    assert response.json() == [
        {
            "eventUuid": event_uuid,
            "categoryName": "HACKATHON",
            "date": days(8).isoformat(),
            "startAt": "19:00:00",
            "endAt": "21:00:00",
            "title": "이벤트 제목",
            "placeName": "카페 그리다 역삼",
            "price": 15_000,
            "capacity": 30,
            "currentCount": 1,
            "status": "OPEN",
            "cancelReason": None,
            "postImageUrl": "https://images.example.com/a.png",
        }
    ]


def test_status_reflects_capacity_participation_and_cancellation(
    api: tuple[TestClient, sa.Engine, int],
) -> None:
    """정원, 본인 참가, 취소 조건에 따른 상태와 우선순위를 확인한다."""

    client, engine, viewer_id = api
    with Session(engine, expire_on_commit=False) as db:
        add_event(db, title="open", on=days(1), capacity=2)

        add_event(
            db,
            title="expired",
            on=days(-1),
            due_date=days(-2),
        )

        add_event(
            db,
            title="completed",
            on=days(0),
            status=EventStatus.COMPLETED,
        )

        full = add_event(db, title="full", on=days(2), capacity=1)
        stranger = User(nickname="stranger", region_id=1)
        db.add(stranger)
        db.flush()
        join(db, full.id, stranger.id)

        mine = add_event(db, title="mine", on=days(3), capacity=5)
        join(db, mine.id, viewer_id)

        # 정원이 찼어도 내가 참가했으면 PARTICIPATING 이 우선한다.
        mine_full = add_event(db, title="mine-full", on=days(4), capacity=1)
        join(db, mine_full.id, viewer_id)

        # 취소는 참가 여부보다 우선한다.
        cancelled = add_event(
            db,
            title="cancelled",
            on=days(5),
            cancel_reason="강사 사정으로 취소되었습니다.",
        )
        join(db, cancelled.id, viewer_id)

        # 신청 마감일이 지났어도 행사 날짜가 남아 있으면 EXPIRED가 아니다.
        add_event(
            db,
            title="application-closed",
            on=days(6),
            due_date=days(-1),
        )
        db.commit()

    response = client.get(
        "/api/v1/events?regionName=seoul",
        headers=auth(viewer_id),
    )

    assert response.status_code == 200
    assert [(item["title"], item["status"]) for item in response.json()] == [
        ("expired", "EXPIRED"),
        ("completed", "EXPIRED"),
        ("open", "OPEN"),
        ("full", "FULL"),
        ("mine", "PARTICIPATING"),
        ("mine-full", "PARTICIPATING"),
        ("cancelled", "CANCEL"),
        ("application-closed", "OPEN"),
    ]
    cancelled_item = next(
        item for item in response.json() if item["title"] == "cancelled"
    )
    assert cancelled_item["cancelReason"] == "강사 사정으로 취소되었습니다."


def test_advance_event_lifecycle_uses_event_times(
    api: tuple[TestClient, sa.Engine, int],
) -> None:
    """종료된 승인 행사는 완료하고 시작된 대기 행사는 거절한다."""

    _, engine, _ = api
    now = datetime(2026, 9, 7, 3, tzinfo=KST)
    with Session(engine, expire_on_commit=False) as db:
        completed = add_event(
            db,
            title="completed-by-job",
            on=now.date(),
            start_at=time(1),
            end_at=time(2),
        )
        rejected = add_event(
            db,
            title="rejected-by-job",
            on=now.date(),
            start_at=time(3),
            status=EventStatus.PENDING,
        )
        active = add_event(
            db,
            title="still-active",
            on=now.date(),
            start_at=time(3, 1),
            end_at=time(4),
        )
        db.commit()

        assert advance_event_lifecycle(db, now) == 2
        assert completed.status is EventStatus.COMPLETED
        assert rejected.status is EventStatus.REJECTED
        assert active.status is EventStatus.APPROVED
        assert advance_event_lifecycle(db, now) == 0


def test_active_event_requires_host(
    api: tuple[TestClient, sa.Engine, int],
) -> None:
    """활성 이벤트만 호스트 NULL을 DB 제약으로 거절한다."""

    _, engine, _ = api
    with Session(engine) as db:
        active = add_event(db, title="host-required")
        active.host_id = None
        with pytest.raises(sa.exc.IntegrityError):
            db.commit()
        db.rollback()

        completed = add_event(
            db,
            title="host-optional",
            status=EventStatus.COMPLETED,
        )
        completed.host_id = None
        db.commit()


def test_participation_prevents_physical_user_deletion(
    api: tuple[TestClient, sa.Engine, int],
) -> None:
    """참가 이력이 남은 사용자는 물리 삭제해 카운터를 깨뜨릴 수 없다."""

    _, engine, _ = api
    with Session(engine, expire_on_commit=False) as db:
        participant = User(nickname="protected-participant", region_id=1)
        db.add(participant)
        db.flush()
        event = add_event(db, title="protected-participation")
        join(db, event.id, participant.id)
        db.commit()
        event_id = event.id
        participant_id = participant.id

        db.delete(participant)
        with pytest.raises(sa.exc.IntegrityError):
            db.commit()
        db.rollback()

        assert db.get(User, participant_id) is not None
        assert db.scalar(
            sa.select(Event.current_count).where(Event.id == event_id)
        ) == 1
        assert db.scalar(
            sa.select(EventParticipant.id).where(
                EventParticipant.event_id == event_id,
                EventParticipant.user_id == participant_id,
            )
        ) is not None


def test_event_lifecycle_job_runs_daily_at_0300_kst() -> None:
    assert seconds_until_next_run(
        datetime(2026, 9, 7, 2, tzinfo=KST)
    ) == 3_600
    assert seconds_until_next_run(
        datetime(2026, 9, 7, 3, tzinfo=KST)
    ) == 86_400


def test_list_orders_by_date_then_start_time(
    api: tuple[TestClient, sa.Engine, int],
) -> None:
    """이벤트가 날짜와 시작 시간 오름차순으로 정렬되는지 확인한다."""

    client, engine, viewer_id = api
    with Session(engine, expire_on_commit=False) as db:
        add_event(db, title="late", on=days(2), start_at=time(20, 0))
        add_event(db, title="early", on=days(2), start_at=time(9, 0))
        add_event(db, title="recent-past", on=days(-3))
        add_event(db, title="far-future", on=days(365))
        db.commit()

    response = client.get(
        "/api/v1/events?regionName=seoul",
        headers=auth(viewer_id),
    )

    assert response.status_code == 200
    assert titles(response) == ["recent-past", "early", "late", "far-future"]


def test_list_keeps_recent_past_events_and_drops_older(
    api: tuple[TestClient, sa.Engine, int],
) -> None:
    """정확히 7일 전 이벤트는 남기고 그보다 오래된 이벤트는 제외한다."""

    client, engine, viewer_id = api
    retention = PAST_EVENT_RETENTION_DAYS
    with Session(engine, expire_on_commit=False) as db:
        add_event(db, title="too-old", on=days(-retention - 1))
        add_event(db, title="oldest-kept", on=days(-retention))
        add_event(db, title="today", on=days(0))
        db.commit()

    response = client.get(
        "/api/v1/events?regionName=seoul",
        headers=auth(viewer_id),
    )

    assert response.status_code == 200
    assert titles(response) == ["oldest-kept", "today"]


def test_list_filters_by_region_category_and_excludes_deleted(
    api: tuple[TestClient, sa.Engine, int],
) -> None:
    """지역·카테고리 필터와 소프트 삭제 제외 조건을 함께 확인한다."""

    client, engine, viewer_id = api
    with Session(engine, expire_on_commit=False) as db:
        add_event(db, title="seminar", category=EventCategory.SEMINAR)
        add_event(db, title="networking", category=EventCategory.NETWORKING)
        add_event(db, title="busan", region_id=2)
        add_event(db, title="deleted", deleted_at=kst_now())
        db.commit()

    everything = client.get(
        "/api/v1/events?regionName=seoul",
        headers=auth(viewer_id),
    )
    filtered = client.get(
        "/api/v1/events?regionName=seoul&categoryName=NETWORKING",
        headers=auth(viewer_id),
    )

    assert sorted(titles(everything)) == ["networking", "seminar"]
    assert titles(filtered) == ["networking"]


def test_list_paginates_with_offset_and_limit(
    api: tuple[TestClient, sa.Engine, int],
) -> None:
    """정렬된 결과에 offset과 limit이 올바르게 적용되는지 확인한다."""

    client, engine, viewer_id = api
    with Session(engine, expire_on_commit=False) as db:
        for day in range(1, 6):
            add_event(db, title=f"day-{day}", on=days(day))
        db.commit()

    first = client.get(
        "/api/v1/events?regionName=seoul&limit=2",
        headers=auth(viewer_id),
    )
    second = client.get(
        "/api/v1/events?regionName=seoul&limit=2&offset=2",
        headers=auth(viewer_id),
    )

    assert titles(first) == ["day-1", "day-2"]
    assert titles(second) == ["day-3", "day-4"]


def test_list_requires_authentication(
    api: tuple[TestClient, sa.Engine, int],
) -> None:
    """인증 헤더가 없으면 표준 401 오류 응답을 반환하는지 확인한다."""

    client, _, _ = api

    response = client.get("/api/v1/events?regionName=seoul")

    assert response.status_code == 401
    assert response.json() == {
        "code": "AUTH_REQUIRED",
        "message": "로그인이 필요합니다.",
    }


@pytest.mark.parametrize(
    ("query", "status_code"),
    [
        ("", 422),
        ("?regionName=", 404),
        ("?regionName=없는지역", 404),
        ("?regionName=gyeonggi", 404),
        ("?regionName=seoul&categoryName=UNKNOWN", 422),
        ("?regionName=seoul&limit=0", 422),
        ("?regionName=seoul&limit=51", 422),
        ("?regionName=seoul&offset=-1", 422),
    ],
)
def test_list_validates_query_and_enabled_region(
    api: tuple[TestClient, sa.Engine, int],
    query: str,
    status_code: int,
) -> None:
    """필수값, enum, 범위, 활성 지역에 대한 오류 계약을 확인한다."""

    client, _, viewer_id = api

    response = client.get(f"/api/v1/events{query}", headers=auth(viewer_id))

    assert response.status_code == status_code
    if status_code == 404:
        assert response.json() == {
            "code": "REGION_NOT_FOUND",
            "message": "지역을 찾을 수 없습니다.",
        }
    else:
        assert response.json() == {
            "code": "INVALID_REQUEST",
            "message": "요청값이 올바르지 않습니다.",
        }


def test_disabled_region_is_not_listed(
    api: tuple[TestClient, sa.Engine, int],
) -> None:
    """DB에 존재하지만 비활성화된 지역은 조회할 수 없는지 확인한다."""

    client, engine, viewer_id = api
    with Session(engine, expire_on_commit=False) as db:
        add_event(db, title="gyeonggi", region_id=DISABLED_REGION_ID)
        db.commit()

    response = client.get(
        "/api/v1/events?regionName=gyeonggi",
        headers=auth(viewer_id),
    )

    assert response.status_code == 404


def test_detail_returns_full_dto(
    api: tuple[TestClient, sa.Engine, int],
) -> None:
    """상세 조회가 설명, 신청 마감일, 참가 상태를 포함해 반환하는지 확인한다."""

    client, engine, viewer_id = api
    with Session(engine, expire_on_commit=False) as db:
        event = add_event(
            db,
            title="이벤트 제목",
            description="이벤트 설명 내용",
            category=EventCategory.HACKATHON,
            on=days(8),
            due_date=days(5),
            price=15_000,
            capacity=7,
            post_image_url="https://images.example.com/detail.png",
        )
        join(db, event.id, viewer_id)
        db.commit()
        event_uuid = str(event.uuid)

    response = client.get(
        f"/api/v1/events/{event_uuid}",
        headers=auth(viewer_id),
    )

    assert response.status_code == 200
    assert response.json() == {
        "eventUuid": event_uuid,
        "categoryName": "HACKATHON",
        "date": days(8).isoformat(),
        "startAt": "19:00:00",
        "endAt": "21:00:00",
        "title": "이벤트 제목",
        "placeName": "카페 그리다 역삼",
        "price": 15_000,
        "capacity": 7,
        "currentCount": 1,
        "status": "PARTICIPATING",
        "cancelReason": None,
        "postImageUrl": "https://images.example.com/detail.png",
        "description": "이벤트 설명 내용",
        "dueDate": days(5).isoformat(),
        "address": "서울특별시 강남구 테헤란로 132",
        "detailAddress": "2층",
        "latitude": 37.5001234,
        "longitude": 127.035321,
        "kakaoPlaceId": "123456789",
    }


def test_detail_returns_404_for_missing_or_deleted_event(
    api: tuple[TestClient, sa.Engine, int],
) -> None:
    """존재하지 않거나 삭제된 이벤트는 동일한 404 응답을 반환하는지 확인한다."""

    client, engine, viewer_id = api
    with Session(engine, expire_on_commit=False) as db:
        deleted = add_event(db, title="deleted", deleted_at=kst_now())
        db.commit()
        deleted_uuid = deleted.uuid

    for event_uuid in (uuid4(), deleted_uuid):
        response = client.get(
            f"/api/v1/events/{event_uuid}",
            headers=auth(viewer_id),
        )
        assert response.status_code == 404
        assert response.json() == {
            "code": "EVENT_NOT_FOUND",
            "message": "이벤트를 찾을 수 없습니다.",
        }


def test_detail_requires_authentication(
    api: tuple[TestClient, sa.Engine, int],
) -> None:
    """상세 조회도 목록과 동일하게 인증을 요구하는지 확인한다."""

    client, _, _ = api

    response = client.get(f"/api/v1/events/{uuid4()}")

    assert response.status_code == 401
    assert response.json() == {
        "code": "AUTH_REQUIRED",
        "message": "로그인이 필요합니다.",
    }


def test_detail_rejects_invalid_uuid(
    api: tuple[TestClient, sa.Engine, int],
) -> None:
    """UUID 형식이 아닌 경로 값은 표준 422 응답으로 거절하는지 확인한다."""

    client, _, viewer_id = api

    response = client.get(
        "/api/v1/events/not-a-uuid",
        headers=auth(viewer_id),
    )

    assert response.status_code == 422
    assert response.json() == {
        "code": "INVALID_REQUEST",
        "message": "요청값이 올바르지 않습니다.",
    }


def test_apply_event_increments_counter_and_adds_participant(
    api: tuple[TestClient, sa.Engine, int],
) -> None:
    """신청 성공 시 카운터와 참가 관계가 같은 요청에서 저장되는지 확인한다."""

    client, engine, viewer_id = api
    with Session(engine, expire_on_commit=False) as db:
        event = add_event(db, title="apply", capacity=2)
        db.commit()
        event_id = event.id
        event_uuid = event.uuid

    response = client.post(
        f"/api/v1/events/{event_uuid}/participants",
        headers=auth(viewer_id),
    )

    assert response.status_code == 200
    assert response.content == b""
    with Session(engine) as db:
        assert db.scalar(
            sa.select(Event.current_count).where(Event.id == event_id)
        ) == 1
        assert db.scalar(
            sa.select(sa.func.count(EventParticipant.id)).where(
                EventParticipant.event_id == event_id,
                EventParticipant.user_id == viewer_id,
            )
        ) == 1


def test_apply_event_requires_authentication(
    api: tuple[TestClient, sa.Engine, int],
) -> None:
    """참가 신청도 공통 인증 의존성을 거치는지 확인한다."""

    client, _, _ = api

    response = client.post(f"/api/v1/events/{uuid4()}/participants")

    assert response.status_code == 401
    assert response.json() == {
        "code": "AUTH_REQUIRED",
        "message": "로그인이 필요합니다.",
    }


def test_apply_event_rejects_duplicate_without_incrementing_counter(
    api: tuple[TestClient, sa.Engine, int],
) -> None:
    """중복 신청은 UNIQUE 제약과 트랜잭션 롤백으로 카운터를 보존한다."""

    client, engine, viewer_id = api
    with Session(engine, expire_on_commit=False) as db:
        event = add_event(db, title="duplicate", capacity=2)
        db.commit()
        event_id = event.id
        event_uuid = event.uuid

    first = client.post(
        f"/api/v1/events/{event_uuid}/participants",
        headers=auth(viewer_id),
    )
    second = client.post(
        f"/api/v1/events/{event_uuid}/participants",
        headers=auth(viewer_id),
    )

    assert first.status_code == 200
    assert second.status_code == 409
    assert second.json() == {
        "code": "ALREADY_APPLIED",
        "message": "이미 신청한 행사입니다.",
    }
    with Session(engine) as db:
        assert db.scalar(
            sa.select(Event.current_count).where(Event.id == event_id)
        ) == 1


def test_unique_constraint_rolls_back_concurrent_duplicate_counter(
    api: tuple[TestClient, sa.Engine, int],
) -> None:
    """동일 사용자의 동시 신청 중 하나는 롤백되어 카운터가 어긋나지 않는다."""

    _, engine, viewer_id = api
    with Session(engine, expire_on_commit=False) as db:
        event = add_event(db, title="duplicate-race", capacity=2)
        db.commit()
        event_id = event.id
        event_uuid = event.uuid

    barrier = Barrier(2)

    def submit() -> str:
        with Session(engine) as db:
            barrier.wait()
            try:
                apply_to_event(db, event_uuid, viewer_id)
            except ConflictException as error:
                return error.code
            return "OK"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _index: submit(), range(2)))

    assert sorted(results) == ["ALREADY_APPLIED", "OK"]
    with Session(engine) as db:
        assert db.scalar(
            sa.select(Event.current_count).where(Event.id == event_id)
        ) == 1
        assert db.scalar(
            sa.select(sa.func.count(EventParticipant.id)).where(
                EventParticipant.event_id == event_id,
                EventParticipant.user_id == viewer_id,
            )
        ) == 1


def test_apply_event_checks_closed_and_full_events(
    api: tuple[TestClient, sa.Engine, int],
) -> None:
    """마감일과 정원 조건을 구분하고 마감 당일 신청은 허용한다."""

    client, engine, viewer_id = api
    with Session(engine, expire_on_commit=False) as db:
        closed = add_event(
            db,
            title="closed",
            due_date=days(-1),
        )
        full = add_event(db, title="full", capacity=0)
        due_today = add_event(
            db,
            title="due-today",
            due_date=days(0),
        )
        db.commit()
        uuids = (closed.uuid, full.uuid, due_today.uuid)

    closed_response = client.post(
        f"/api/v1/events/{uuids[0]}/participants",
        headers=auth(viewer_id),
    )
    full_response = client.post(
        f"/api/v1/events/{uuids[1]}/participants",
        headers=auth(viewer_id),
    )
    due_today_response = client.post(
        f"/api/v1/events/{uuids[2]}/participants",
        headers=auth(viewer_id),
    )

    assert closed_response.status_code == 400
    assert closed_response.json() == {
        "code": "EVENT_CLOSED",
        "message": "모집 기간이 종료되었습니다.",
    }
    assert full_response.status_code == 409
    assert full_response.json() == {
        "code": "EVENT_FULL",
        "message": "정원이 마감되어 신청할 수 없습니다.",
    }
    assert due_today_response.status_code == 200


def test_apply_event_hides_missing_deleted_and_cancelled_events(
    api: tuple[TestClient, sa.Engine, int],
) -> None:
    """없거나 삭제·취소된 이벤트는 같은 404 계약으로 숨긴다."""

    client, engine, viewer_id = api
    with Session(engine, expire_on_commit=False) as db:
        deleted = add_event(db, title="deleted", deleted_at=kst_now())
        cancelled = add_event(
            db,
            title="cancelled",
            cancel_reason="운영 사정",
        )
        db.commit()
        uuids = (uuid4(), deleted.uuid, cancelled.uuid)

    for event_uuid in uuids:
        response = client.post(
            f"/api/v1/events/{event_uuid}/participants",
            headers=auth(viewer_id),
        )
        assert response.status_code == 404
        assert response.json() == {
            "code": "EVENT_NOT_FOUND",
            "message": "이벤트를 찾을 수 없습니다.",
        }


def test_apply_event_returns_busy_when_event_lock_is_held(
    api: tuple[TestClient, sa.Engine, int],
) -> None:
    """다른 요청이 이벤트 락을 점유하면 짧게 재시도한 뒤 429를 반환한다."""

    client, engine, viewer_id = api
    with Session(engine, expire_on_commit=False) as db:
        event = add_event(db, title="busy")
        db.commit()
        event_uuid = event.uuid
    _EVENT_REDIS.values[
        f"event:participation:{event_uuid}"
    ] = "another-request"

    response = client.post(
        f"/api/v1/events/{event_uuid}/participants",
        headers=auth(viewer_id),
    )

    assert response.status_code == 429
    assert response.json() == {
        "code": "EVENT_PARTICIPATION_BUSY",
        "message": "참가 요청이 많습니다. 잠시 후 다시 시도해주세요.",
    }


def test_apply_event_falls_back_to_database_when_redis_fails(
    api: tuple[TestClient, sa.Engine, int],
) -> None:
    """Redis 장애가 나도 DB 원자적 갱신으로 신청을 처리한다."""

    client, engine, viewer_id = api
    app.dependency_overrides[get_redis_client] = BrokenEventRedis
    with Session(engine, expire_on_commit=False) as db:
        event = add_event(db, title="redis-down")
        db.commit()
        event_uuid = event.uuid

    response = client.post(
        f"/api/v1/events/{event_uuid}/participants",
        headers=auth(viewer_id),
    )

    assert response.status_code == 200


def test_atomic_counter_allows_only_one_user_into_last_seat(
    api: tuple[TestClient, sa.Engine, int],
) -> None:
    """동시에 마지막 자리를 신청해도 DB가 한 요청만 성공시킨다."""

    _, engine, viewer_id = api
    with Session(engine, expire_on_commit=False) as db:
        other = User(nickname="event-concurrent", region_id=1)
        db.add(other)
        event = add_event(db, title="last-seat", capacity=1)
        db.commit()
        other_id = other.id
        event_id = event.id
        event_uuid = event.uuid

    barrier = Barrier(2)

    def submit(user_id: int) -> str:
        with Session(engine) as db:
            barrier.wait()
            try:
                apply_to_event(db, event_uuid, user_id)
            except ConflictException as error:
                return error.code
            return "OK"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(submit, (viewer_id, other_id)))

    assert sorted(results) == ["EVENT_FULL", "OK"]
    with Session(engine) as db:
        assert db.scalar(
            sa.select(Event.current_count).where(Event.id == event_id)
        ) == 1
        assert db.scalar(
            sa.select(sa.func.count(EventParticipant.id)).where(
                EventParticipant.event_id == event_id
            )
        ) == 1


def test_cancel_event_removes_participant_and_decrements_counter(
    api: tuple[TestClient, sa.Engine, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """행사 시작 정확히 2시간 전에는 취소와 카운터 감소를 허용한다."""

    client, engine, viewer_id = api
    now = datetime(2026, 9, 6, 10, tzinfo=KST)
    monkeypatch.setattr("app.services.event.kst_now", lambda: now)
    with Session(engine, expire_on_commit=False) as db:
        event = add_event(
            db,
            title="cancel-application",
            on=now.date(),
            due_date=now.date() - timedelta(days=1),
            start_at=time(12),
        )
        join(db, event.id, viewer_id)
        db.commit()
        event_id = event.id
        event_uuid = event.uuid

    response = client.delete(
        f"/api/v1/events/{event_uuid}/participants",
        headers=auth(viewer_id),
    )

    assert response.status_code == 204
    assert response.content == b""
    with Session(engine) as db:
        assert db.scalar(
            sa.select(Event.current_count).where(Event.id == event_id)
        ) == 0
        assert db.scalar(
            sa.select(EventParticipant.id).where(
                EventParticipant.event_id == event_id,
                EventParticipant.user_id == viewer_id,
            )
        ) is None


def test_cancel_event_locks_event_before_deleting_participant(
    api: tuple[TestClient, sa.Engine, int],
) -> None:
    _, engine, viewer_id = api
    with Session(engine, expire_on_commit=False) as db:
        event = add_event(db, title="cancel-lock-order")
        join(db, event.id, viewer_id)
        db.commit()
        event_uuid = event.uuid

    statements: list[str] = []

    def capture_statement(
        _connection: object,
        _cursor: object,
        statement: str,
        *_args: object,
    ) -> None:
        statements.append(statement.upper())

    sa.event.listen(engine, "before_cursor_execute", capture_statement)
    try:
        with Session(engine) as db:
            cancel_event_application(db, event_uuid, viewer_id)
    finally:
        sa.event.remove(engine, "before_cursor_execute", capture_statement)

    event_lock = next(
        index
        for index, statement in enumerate(statements)
        if "FROM EVENTS" in statement and "FOR UPDATE" in statement
    )
    participant_delete = next(
        index
        for index, statement in enumerate(statements)
        if statement.lstrip().startswith("DELETE FROM EVENT_PARTICIPANTS")
    )
    assert event_lock < participant_delete


def test_host_cannot_cancel_own_participation(
    api: tuple[TestClient, sa.Engine, int],
) -> None:
    """행사 등록자는 참가 관계만 제거하지 못하고 행사 취소 절차를 사용한다."""

    client, engine, viewer_id = api
    with Session(engine, expire_on_commit=False) as db:
        event = add_event(
            db,
            title="hosted-event",
            host_id=viewer_id,
        )
        join(db, event.id, viewer_id)
        db.commit()
        event_id = event.id
        event_uuid = event.uuid

    response = client.delete(
        f"/api/v1/events/{event_uuid}/participants",
        headers=auth(viewer_id),
    )

    assert response.status_code == 400
    assert response.json() == {
        "code": "HOST_CANNOT_CANCEL_PARTICIPATION",
        "message": "행사 등록자는 참가 신청을 취소할 수 없습니다.",
    }
    with Session(engine) as db:
        assert db.scalar(
            sa.select(Event.current_count).where(Event.id == event_id)
        ) == 1
        assert db.scalar(
            sa.select(sa.func.count(EventParticipant.id)).where(
                EventParticipant.event_id == event_id,
                EventParticipant.user_id == viewer_id,
            )
        ) == 1


def test_cancel_event_requires_authentication(
    api: tuple[TestClient, sa.Engine, int],
) -> None:
    """참가 취소도 공통 인증 의존성을 거치는지 확인한다."""

    client, _, _ = api

    response = client.delete(f"/api/v1/events/{uuid4()}/participants")

    assert response.status_code == 401
    assert response.json() == {
        "code": "AUTH_REQUIRED",
        "message": "로그인이 필요합니다.",
    }


def test_cancel_event_hides_missing_deleted_and_cancelled_events(
    api: tuple[TestClient, sa.Engine, int],
) -> None:
    """없거나 삭제·취소된 이벤트의 참가 신청은 동일한 404로 숨긴다."""

    client, engine, viewer_id = api
    with Session(engine, expire_on_commit=False) as db:
        deleted = add_event(db, title="deleted", deleted_at=kst_now())
        cancelled = add_event(
            db,
            title="cancelled",
            cancel_reason="운영 사정",
        )
        join(db, deleted.id, viewer_id)
        join(db, cancelled.id, viewer_id)
        db.commit()
        uuids = (uuid4(), deleted.uuid, cancelled.uuid)

    for event_uuid in uuids:
        response = client.delete(
            f"/api/v1/events/{event_uuid}/participants",
            headers=auth(viewer_id),
        )
        assert response.status_code == 404
        assert response.json() == {
            "code": "EVENT_NOT_FOUND",
            "message": "이벤트를 찾을 수 없습니다.",
        }


def test_cancel_event_rejects_missing_application(
    api: tuple[TestClient, sa.Engine, int],
) -> None:
    """참가 신청 내역이 없다면 카운터를 변경하지 않는다."""

    client, engine, viewer_id = api
    with Session(engine, expire_on_commit=False) as db:
        event = add_event(db, title="not-applied")
        db.commit()
        event_uuid = event.uuid

    response = client.delete(
        f"/api/v1/events/{event_uuid}/participants",
        headers=auth(viewer_id),
    )

    assert response.status_code == 400
    assert response.json() == {
        "code": "APPLICATION_NOT_FOUND",
        "message": "신청 내역이 존재하지 않거나 이미 취소되었습니다.",
    }


def test_cancel_event_rejects_expired_event_without_mutation(
    api: tuple[TestClient, sa.Engine, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """취소 기한을 1초 넘기면 신청과 카운터를 변경하지 않는다."""

    client, engine, viewer_id = api
    now = datetime(2026, 9, 6, 10, tzinfo=KST)
    monkeypatch.setattr("app.services.event.kst_now", lambda: now)
    with Session(engine, expire_on_commit=False) as db:
        event = add_event(
            db,
            title="expired-cancel",
            on=now.date(),
            start_at=time(11, 59, 59),
        )
        join(db, event.id, viewer_id)
        db.commit()
        event_id = event.id
        event_uuid = event.uuid

    response = client.delete(
        f"/api/v1/events/{event_uuid}/participants",
        headers=auth(viewer_id),
    )

    assert response.status_code == 400
    assert response.json() == {
        "code": "CANCEL_PERIOD_EXPIRED",
        "message": "취소 가능 기간이 지났습니다.",
    }
    with Session(engine) as db:
        assert db.scalar(
            sa.select(Event.current_count).where(Event.id == event_id)
        ) == 1
        assert db.scalar(
            sa.select(EventParticipant.id).where(
                EventParticipant.event_id == event_id,
                EventParticipant.user_id == viewer_id,
            )
        ) is not None


def test_cancel_event_returns_busy_when_participation_lock_is_held(
    api: tuple[TestClient, sa.Engine, int],
) -> None:
    """참가 락이 점유 중이면 취소도 짧게 재시도한 뒤 429를 반환한다."""

    client, engine, viewer_id = api
    with Session(engine, expire_on_commit=False) as db:
        event = add_event(db, title="cancel-busy")
        join(db, event.id, viewer_id)
        db.commit()
        event_uuid = event.uuid
    _EVENT_REDIS.values[
        f"event:participation:{event_uuid}"
    ] = "another-request"

    response = client.delete(
        f"/api/v1/events/{event_uuid}/participants",
        headers=auth(viewer_id),
    )

    assert response.status_code == 429
    assert response.json() == {
        "code": "EVENT_PARTICIPATION_BUSY",
        "message": "참가 요청이 많습니다. 잠시 후 다시 시도해주세요.",
    }


def test_cancel_event_falls_back_to_database_when_redis_fails(
    api: tuple[TestClient, sa.Engine, int],
) -> None:
    """Redis 장애 중에도 DB 트랜잭션으로 참가 취소를 안전하게 처리한다."""

    client, engine, viewer_id = api
    app.dependency_overrides[get_redis_client] = BrokenEventRedis
    with Session(engine, expire_on_commit=False) as db:
        event = add_event(db, title="cancel-redis-down")
        join(db, event.id, viewer_id)
        db.commit()
        event_uuid = event.uuid

    response = client.delete(
        f"/api/v1/events/{event_uuid}/participants",
        headers=auth(viewer_id),
    )

    assert response.status_code == 204


def test_concurrent_cancellation_decrements_counter_only_once(
    api: tuple[TestClient, sa.Engine, int],
) -> None:
    """동일 신청을 동시에 취소해도 참가 행과 카운터를 한 번만 줄인다."""

    _, engine, viewer_id = api
    with Session(engine, expire_on_commit=False) as db:
        event = add_event(db, title="cancel-race")
        join(db, event.id, viewer_id)
        db.commit()
        event_id = event.id
        event_uuid = event.uuid

    barrier = Barrier(2)

    def cancel() -> str:
        with Session(engine) as db:
            barrier.wait()
            try:
                cancel_event_application(db, event_uuid, viewer_id)
            except BadRequestException as error:
                return error.code
            return "OK"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _index: cancel(), range(2)))

    assert sorted(results) == ["APPLICATION_NOT_FOUND", "OK"]
    with Session(engine) as db:
        assert db.scalar(
            sa.select(Event.current_count).where(Event.id == event_id)
        ) == 0
        assert db.scalar(
            sa.select(sa.func.count(EventParticipant.id)).where(
                EventParticipant.event_id == event_id
            )
        ) == 0


def test_owned_events_returns_all_stored_statuses_in_latest_order(
    api: tuple[TestClient, sa.Engine, int],
) -> None:
    """내 행사는 공개 상태 계산 없이 저장 상태와 생성 시각을 반환한다."""

    client, engine, viewer_id = api
    with Session(engine, expire_on_commit=False) as db:
        older = add_event(
            db,
            title="older",
            host_id=viewer_id,
            status=EventStatus.PENDING,
        )
        older.created_at = datetime(2026, 9, 6, 10, tzinfo=KST)
        newer = add_event(
            db,
            title="newer",
            host_id=viewer_id,
            status=EventStatus.REJECTED,
        )
        newer.created_at = datetime(2026, 9, 7, 10, tzinfo=KST)
        add_event(db, title="someone-else")
        add_event(
            db,
            title="deleted",
            host_id=viewer_id,
            deleted_at=kst_now(),
        )
        db.commit()
        uuids = (str(newer.uuid), str(older.uuid))

    response = client.get("/api/v1/me/events", headers=auth(viewer_id))

    assert response.status_code == 200
    assert [item["eventUuid"] for item in response.json()] == list(uuids)
    assert [item["status"] for item in response.json()] == [
        "REJECTED",
        "PENDING",
    ]
    assert set(response.json()[0]) == {
        "eventUuid",
        "categoryName",
        "title",
        "date",
        "startAt",
        "endAt",
        "placeName",
        "price",
        "capacity",
        "currentCount",
        "status",
        "cancelReason",
        "postImageUrl",
        "createdAt",
        "updatedAt",
    }


def test_owned_events_requires_authentication_and_can_be_empty(
    api: tuple[TestClient, sa.Engine, int],
) -> None:
    client, _, viewer_id = api

    assert client.get("/api/v1/me/events").status_code == 401
    response = client.get("/api/v1/me/events", headers=auth(viewer_id))
    assert response.status_code == 200
    assert response.json() == []


def test_owner_cancellation_is_idempotent_and_preserves_event_history(
    api: tuple[TestClient, sa.Engine, int],
) -> None:
    """행사 취소는 상태만 바꾸고 행사·참가 기록과 인원을 보존한다."""

    client, engine, viewer_id = api
    with Session(engine, expire_on_commit=False) as db:
        event = add_event(db, title="owned", host_id=viewer_id)
        join(db, event.id, viewer_id)
        db.commit()
        event_id = event.id
        event_uuid = event.uuid

    first = client.delete(
        f"/api/v1/me/events/{event_uuid}", headers=auth(viewer_id)
    )
    second = client.delete(
        f"/api/v1/me/events/{event_uuid}", headers=auth(viewer_id)
    )

    assert first.status_code == second.status_code == 204
    assert first.content == second.content == b""
    with Session(engine) as db:
        event = db.get(Event, event_id)
        assert event is not None
        assert event.status is EventStatus.CANCEL
        assert event.deleted_at is None
        assert event.current_count == 1
        assert event.updated_at is not None
        assert db.scalar(
            sa.select(EventParticipant.id).where(
                EventParticipant.event_id == event_id,
                EventParticipant.user_id == viewer_id,
            )
        ) is not None


def test_owner_cancellation_rejects_invalid_targets(
    api: tuple[TestClient, sa.Engine, int],
) -> None:
    client, engine, viewer_id = api
    with Session(engine, expire_on_commit=False) as db:
        not_owned = add_event(db, title="not-owned")
        completed = add_event(
            db,
            title="completed-owned",
            host_id=viewer_id,
            status=EventStatus.COMPLETED,
        )
        deleted = add_event(
            db,
            title="deleted-owned",
            host_id=viewer_id,
            deleted_at=kst_now(),
        )
        db.commit()
        cases = (
            (not_owned.uuid, 403, "EVENT_NOT_OWNER"),
            (completed.uuid, 409, "EVENT_NOT_CANCELLABLE"),
            (deleted.uuid, 404, "EVENT_NOT_FOUND"),
            (uuid4(), 404, "EVENT_NOT_FOUND"),
        )

    for event_uuid, expected_status, code in cases:
        response = client.delete(
            f"/api/v1/me/events/{event_uuid}", headers=auth(viewer_id)
        )
        assert response.status_code == expected_status
        assert response.json()["code"] == code
    assert client.delete(f"/api/v1/me/events/{uuid4()}").status_code == 401
    assert client.delete(
        "/api/v1/me/events/not-a-uuid", headers=auth(viewer_id)
    ).status_code == 422


def test_event_edit_request_saves_only_changes_without_mutating_event(
    api: tuple[TestClient, sa.Engine, int],
) -> None:
    """부분 수정값은 별도 저장하고 승인 전 원본 행사는 유지한다."""

    client, engine, viewer_id = api
    with Session(engine, expire_on_commit=False) as db:
        event = add_event(
            db,
            title="original",
            host_id=viewer_id,
            due_date=days(2),
        )
        db.commit()
        event_id = event.id
        event_uuid = event.uuid

    response = client.patch(
        f"/api/v1/me/events/{event_uuid}",
        headers=auth(viewer_id),
        json={"title": "changed", "date": days(4).isoformat()},
    )

    assert response.status_code == 202
    assert response.json() == {
        "eventUuid": str(event_uuid),
        "message": "행사 수정 요청이 접수되었습니다.",
    }
    with Session(engine) as db:
        event = db.get(Event, event_id)
        edit = db.scalar(
            sa.select(EventEditRequest).where(
                EventEditRequest.event_id == event_id
            )
        )
        assert event is not None
        assert event.title == "original"
        assert event.date == days(3)
        assert edit is not None
        assert edit.changes == {
            "title": "changed",
            "date": days(4).isoformat(),
        }
        assert edit.status is EventEditRequestStatus.PENDING


def test_event_edit_request_validates_body_and_pending_uniqueness(
    api: tuple[TestClient, sa.Engine, int],
) -> None:
    client, engine, viewer_id = api
    with Session(engine, expire_on_commit=False) as db:
        event = add_event(
            db,
            title="editable",
            host_id=viewer_id,
            due_date=days(2),
        )
        db.commit()
        event_uuid = event.uuid

    invalid_bodies = (
        {},
        {"unexpected": "value"},
        {"title": None},
        {"dueDate": days(3).isoformat()},
        {"startAt": "22:00:00"},
        {"startAt": "18:00:00+09:00"},
        {"endAt": "21:00:00+09:00"},
        {"capacity": 0},
    )
    for body in invalid_bodies:
        response = client.patch(
            f"/api/v1/me/events/{event_uuid}",
            headers=auth(viewer_id),
            json=body,
        )
        assert response.status_code == 422
        assert response.json()["code"] == "INVALID_REQUEST"

    first = client.patch(
        f"/api/v1/me/events/{event_uuid}",
        headers=auth(viewer_id),
        json={"detailAddress": None},
    )
    duplicate = client.patch(
        f"/api/v1/me/events/{event_uuid}",
        headers=auth(viewer_id),
        json={"title": "second"},
    )
    assert first.status_code == 202
    assert duplicate.status_code == 409
    assert duplicate.json()["code"] == "EVENT_EDIT_ALREADY_PENDING"


def test_event_edit_request_rejects_missing_nonowner_and_terminal_events(
    api: tuple[TestClient, sa.Engine, int],
) -> None:
    client, engine, viewer_id = api
    with Session(engine, expire_on_commit=False) as db:
        not_owned = add_event(db, title="not-owned-edit")
        rejected = add_event(
            db,
            title="rejected-edit",
            host_id=viewer_id,
            status=EventStatus.REJECTED,
        )
        db.commit()
        cases = (
            (not_owned.uuid, 403, "EVENT_NOT_OWNER"),
            (rejected.uuid, 409, "EVENT_NOT_EDITABLE"),
            (uuid4(), 404, "EVENT_NOT_FOUND"),
        )

    for event_uuid, expected_status, code in cases:
        response = client.patch(
            f"/api/v1/me/events/{event_uuid}",
            headers=auth(viewer_id),
            json={"title": "change"},
        )
        assert response.status_code == expected_status
        assert response.json()["code"] == code
    assert client.patch(
        f"/api/v1/me/events/{uuid4()}", json={"title": "change"}
    ).status_code == 401


def test_openapi_event_contract() -> None:
    """문서에 이벤트 경로, 태그, 오류 상태 코드가 노출되는지 확인한다."""

    schema = app.openapi()
    events_path = schema["paths"]["/api/v1/events"]
    operation = events_path["get"]

    assert operation["summary"] == "이벤트 목록 조회"
    assert operation["tags"] == ["이벤트"]
    assert sorted(operation["responses"]) == [
        "200",
        "401",
        "404",
        "422",
        "500",
    ]
    assert set(
        operation["responses"]["500"]["content"]["application/json"][
            "examples"
        ]
    ) == {"INTERNAL_SERVER_ERROR", "CONFIGURATION_ERROR"}

    create_operation = events_path["post"]
    assert create_operation["summary"] == "이벤트 등록 신청"
    assert create_operation["tags"] == ["이벤트"]
    assert sorted(create_operation["responses"]) == [
        "201",
        "400",
        "401",
        "422",
        "500",
    ]

    detail_operation = schema["paths"]["/api/v1/events/{eventUuid}"]["get"]
    assert detail_operation["summary"] == "이벤트 상세 조회"
    assert detail_operation["tags"] == ["이벤트"]
    assert sorted(detail_operation["responses"]) == [
        "200",
        "401",
        "404",
        "422",
        "500",
    ]

    owned_operation = schema["paths"]["/api/v1/me/events"]["get"]
    assert owned_operation["summary"] == "내가 올린 행사 조회"
    assert sorted(owned_operation["responses"]) == [
        "200",
        "401",
        "422",
        "500",
    ]

    edit_operation = schema["paths"][
        "/api/v1/me/events/{eventUuid}"
    ]["patch"]
    assert edit_operation["summary"] == "내가 올린 행사 수정 요청"
    assert sorted(edit_operation["responses"]) == [
        "202",
        "401",
        "403",
        "404",
        "409",
        "422",
        "500",
    ]

    owner_cancel_operation = schema["paths"][
        "/api/v1/me/events/{eventUuid}"
    ]["delete"]
    assert owner_cancel_operation["summary"] == "내가 올린 행사 등록 취소"
    assert sorted(owner_cancel_operation["responses"]) == [
        "204",
        "401",
        "403",
        "404",
        "409",
        "422",
        "500",
    ]

    participation_path = schema["paths"][
        "/api/v1/events/{eventUuid}/participants"
    ]
    apply_operation = participation_path["post"]
    assert apply_operation["summary"] == "이벤트 참가 신청"
    assert apply_operation["tags"] == ["이벤트"]
    assert sorted(apply_operation["responses"]) == [
        "200",
        "400",
        "401",
        "404",
        "409",
        "422",
        "429",
        "500",
    ]

    cancel_operation = participation_path["delete"]
    assert cancel_operation["summary"] == "이벤트 참가 신청 취소"
    assert cancel_operation["tags"] == ["이벤트"]
    assert sorted(cancel_operation["responses"]) == [
        "204",
        "400",
        "401",
        "404",
        "422",
        "429",
        "500",
    ]
