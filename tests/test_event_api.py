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
from redis.exceptions import RedisError
from sqlalchemy.orm import Session

from app.database import create_db_engine, get_db
from app.exceptions import BadRequestError, ConflictError
from app.main import app
from app.models import (
    Comment,
    CommunityPost,
    Event,
    EventCategory,
    EventParticipant,
    User,
)
from app.routers.event import PAST_EVENT_RETENTION_DAYS
from app.redis_client import get_redis_client
from app.services.event import apply_to_event, cancel_event_application
from app.time import kst_now

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
        db.add(viewer)
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
    cancel_reason: str | None = None,
    deleted_at: datetime | None = None,
) -> Event:
    """테스트에 필요한 값만 바꿔 이벤트 한 건을 생성한다."""

    event_date = days(3) if on is None else on
    event = Event(
        region_id=region_id,
        category=category,
        title=title,
        description=description,
        place="OO 공유오피스 라운지",
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
        "/api/v1/event?regionName=seoul",
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
            "place": "OO 공유오피스 라운지",
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
        "/api/v1/event?regionName=seoul",
        headers=auth(viewer_id),
    )

    assert response.status_code == 200
    assert [(item["title"], item["status"]) for item in response.json()] == [
        ("expired", "EXPIRED"),
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
        "/api/v1/event?regionName=seoul",
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
        "/api/v1/event?regionName=seoul",
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
        "/api/v1/event?regionName=seoul",
        headers=auth(viewer_id),
    )
    filtered = client.get(
        "/api/v1/event?regionName=seoul&categoryName=NETWORKING",
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
        "/api/v1/event?regionName=seoul&limit=2",
        headers=auth(viewer_id),
    )
    second = client.get(
        "/api/v1/event?regionName=seoul&limit=2&offset=2",
        headers=auth(viewer_id),
    )

    assert titles(first) == ["day-1", "day-2"]
    assert titles(second) == ["day-3", "day-4"]


def test_list_requires_authentication(
    api: tuple[TestClient, sa.Engine, int],
) -> None:
    """인증 헤더가 없으면 표준 401 오류 응답을 반환하는지 확인한다."""

    client, _, _ = api

    response = client.get("/api/v1/event?regionName=seoul")

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

    response = client.get(f"/api/v1/event{query}", headers=auth(viewer_id))

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
        "/api/v1/event?regionName=gyeonggi",
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
        f"/api/v1/event/{event_uuid}",
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
        "place": "OO 공유오피스 라운지",
        "price": 15_000,
        "capacity": 7,
        "currentCount": 1,
        "status": "PARTICIPATING",
        "cancelReason": None,
        "postImageUrl": "https://images.example.com/detail.png",
        "description": "이벤트 설명 내용",
        "dueDate": days(5).isoformat(),
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
            f"/api/v1/event/{event_uuid}",
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

    response = client.get(f"/api/v1/event/{uuid4()}")

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
        "/api/v1/event/not-a-uuid",
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
        f"/api/v1/event/{event_uuid}",
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

    response = client.post(f"/api/v1/event/{uuid4()}")

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
        f"/api/v1/event/{event_uuid}",
        headers=auth(viewer_id),
    )
    second = client.post(
        f"/api/v1/event/{event_uuid}",
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
            except ConflictError as error:
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
        f"/api/v1/event/{uuids[0]}",
        headers=auth(viewer_id),
    )
    full_response = client.post(
        f"/api/v1/event/{uuids[1]}",
        headers=auth(viewer_id),
    )
    due_today_response = client.post(
        f"/api/v1/event/{uuids[2]}",
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
            f"/api/v1/event/{event_uuid}",
            headers=auth(viewer_id),
        )
        assert response.status_code == 404
        assert response.json() == {
            "code": "EVENT_NOT_FOUND",
            "message": "존재하지 않거나 취소된 행사입니다.",
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
        f"/api/v1/event/{event_uuid}",
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
        f"/api/v1/event/{event_uuid}",
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
            except ConflictError as error:
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
) -> None:
    """행사 당일까지 참가 신청을 취소하고 카운터를 함께 감소시킨다."""

    client, engine, viewer_id = api
    with Session(engine, expire_on_commit=False) as db:
        event = add_event(
            db,
            title="cancel-application",
            on=days(0),
            due_date=days(-1),
        )
        join(db, event.id, viewer_id)
        db.commit()
        event_id = event.id
        event_uuid = event.uuid

    response = client.patch(
        f"/api/v1/event/{event_uuid}",
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


def test_cancel_event_requires_authentication(
    api: tuple[TestClient, sa.Engine, int],
) -> None:
    """참가 취소도 공통 인증 의존성을 거치는지 확인한다."""

    client, _, _ = api

    response = client.patch(f"/api/v1/event/{uuid4()}")

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
        response = client.patch(
            f"/api/v1/event/{event_uuid}",
            headers=auth(viewer_id),
        )
        assert response.status_code == 404
        assert response.json() == {
            "code": "EVENT_NOT_FOUND",
            "message": "존재하지 않는 행사입니다.",
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

    response = client.patch(
        f"/api/v1/event/{event_uuid}",
        headers=auth(viewer_id),
    )

    assert response.status_code == 400
    assert response.json() == {
        "code": "APPLICATION_NOT_FOUND",
        "message": "신청 내역이 존재하지 않거나 이미 취소되었습니다.",
    }


def test_cancel_event_rejects_expired_event_without_mutation(
    api: tuple[TestClient, sa.Engine, int],
) -> None:
    """행사 날짜가 지난 신청은 취소하거나 카운터를 감소시키지 않는다."""

    client, engine, viewer_id = api
    with Session(engine, expire_on_commit=False) as db:
        event = add_event(db, title="expired-cancel", on=days(-1))
        join(db, event.id, viewer_id)
        db.commit()
        event_id = event.id
        event_uuid = event.uuid

    response = client.patch(
        f"/api/v1/event/{event_uuid}",
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

    response = client.patch(
        f"/api/v1/event/{event_uuid}",
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

    response = client.patch(
        f"/api/v1/event/{event_uuid}",
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
            except BadRequestError as error:
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


def test_openapi_event_contract() -> None:
    """문서에 이벤트 경로, 태그, 오류 상태 코드가 노출되는지 확인한다."""

    schema = app.openapi()
    operation = schema["paths"]["/api/v1/event"]["get"]

    assert operation["summary"] == "이벤트 목록 조회"
    assert operation["tags"] == ["이벤트"]
    assert sorted(operation["responses"]) == [
        "200",
        "401",
        "404",
        "422",
        "500",
    ]

    detail_operation = schema["paths"]["/api/v1/event/{eventUuid}"]["get"]
    assert detail_operation["summary"] == "이벤트 상세 조회"
    assert detail_operation["tags"] == ["이벤트"]
    assert sorted(detail_operation["responses"]) == [
        "200",
        "401",
        "404",
        "422",
        "500",
    ]

    apply_operation = schema["paths"]["/api/v1/event/{eventUuid}"]["post"]
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

    cancel_operation = schema["paths"]["/api/v1/event/{eventUuid}"]["patch"]
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
