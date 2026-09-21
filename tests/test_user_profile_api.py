"""외부 DB 없이 프로필 요청·저장·목 응답 계약을 검증한다.

SQLite는 행 잠금과 PostgreSQL 동시 UNIQUE 경합을 검증하지 않는다.
"""

from collections.abc import Generator
from datetime import datetime
from uuid import uuid4

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import get_db
from app.main import app
from app.models import (
    MarketingConsentHistory,
    Region,
    TermAgreement,
    User,
    UserAttribute,
    UserAttributeType,
)
from app.services.profile_sources import (
    MOCK_BLOCKED_UUID,
    MOCK_MEMBER_UUID,
    MOCK_VIEWER_UUID,
)
from app.time import KST


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch) -> Generator[tuple[TestClient, sa.Engine]]:
    engine = sa.create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )

    @sa.event.listens_for(engine, "connect")
    def sqlite_functions(connection, _record):
        connection.create_function("char_length", 1, len)
        connection.execute("PRAGMA foreign_keys=ON")

    for table in (
        Region.__table__,
        User.__table__,
        UserAttribute.__table__,
        TermAgreement.__table__,
        MarketingConsentHistory.__table__,
    ):
        table.create(engine)
    with Session(engine) as db:
        db.add(Region(id=1, name="seoul", is_enabled=True))
        db.flush()
        db.add_all(
            [
                User(
                    uuid=MOCK_VIEWER_UUID,
                    nickname="evan",
                    region_id=1,
                    field="백엔드",
                    bio="소개",
                    created_at=datetime(2026, 3, 1, 10, tzinfo=KST),
                ),
                User(uuid=uuid4(), nickname="taken", region_id=1, field="프론트엔드"),
            ]
        )
        db.commit()

    def db_override():
        with Session(engine) as db:
            yield db

    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("ENABLE_DEBUG_AUTH", "true")
    monkeypatch.delenv("DEBUG_DEFAULT_USER_UUID", raising=False)
    app.dependency_overrides[get_db] = db_override
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            yield client, engine
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


AUTH = {"X-Debug-User-Uuid": str(MOCK_VIEWER_UUID)}
ME = "/api/v1/users/me"
CHECK = "/api/v1/users/nickname-availability"


def test_get_and_patch_profile_persist_and_preserve_omitted_fields(api):
    client, engine = api
    response = client.get(ME, headers=AUTH)
    assert response.status_code == 200
    assert response.json()["field"] == "백엔드"
    assert response.json()["marketingConsent"] is False
    assert response.json()["marketingConsentChangedAt"] is None
    assert response.json()["stacks"] == []
    assert response.json()["joinedAt"].endswith("+09:00")
    assert "isStaff" not in response.json()
    assert "joinedMeetingCount" not in response.json()

    response = client.patch(
        ME,
        headers=AUTH,
        json={
            "nickname": " evan_2 ",
            "affiliation": "학교",
            "stacks": [" Flutter ", "Dart", "flutter"],
            "interests": ["모바일", "사이드 프로젝트"],
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["nickname"] == "evan_2"
    assert data["field"] == "백엔드"
    assert data["bio"] == "소개"
    assert data["stacks"] == ["Dart", "Flutter"]
    assert "isStaff" not in data
    assert client.get(ME, headers=AUTH).json() == data
    with Session(engine) as db:
        assert (
            db.scalar(sa.select(User.nickname).where(User.uuid == MOCK_VIEWER_UUID))
            == "evan_2"
        )
        assert (
            db.scalar(
                sa.select(UserAttribute.value_normalized).where(
                    UserAttribute.type == UserAttributeType.STACK,
                    UserAttribute.value == "Flutter",
                )
            )
            == "flutter"
        )

    cleared = client.patch(
        ME, headers=AUTH, json={"bio": "  ", "affiliation": None, "stacks": []}
    )
    assert cleared.status_code == 200
    assert cleared.json()["bio"] is None
    assert cleared.json()["affiliation"] is None
    assert cleared.json()["stacks"] == []
    assert cleared.json()["interests"] == data["interests"]


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"unknown": "x"},
        {"nickname": None},
        {"nickname": "bad-name"},
        {"field": None},
        {"field": " "},
        {"field": "x" * 51},
        {"stacks": None},
        {"stacks": "Java"},
        {"stacks": ["Java"] * 11},
        {"stacks": [""]},
        {"stacks": [3]},
        {"interests": ["a"] * 7},
        {"affiliation": "x" * 21},
        {"bio": "x" * 61},
        {"isStaff": True},
        {"marketingConsent": True},
    ],
)
def test_patch_rejects_invalid_requests_without_changes(api, body):
    client, _ = api
    before = client.get(ME, headers=AUTH).json()
    assert client.patch(ME, headers=AUTH, json=body).status_code == 422
    assert client.get(ME, headers=AUTH).json() == before


def test_nickname_conflict_and_self_update(api):
    client, _ = api
    before = client.get(ME, headers=AUTH).json()
    conflict = client.patch(
        ME,
        headers=AUTH,
        json={"nickname": "taken", "bio": "바뀌면 안됨", "stacks": ["Java"]},
    )
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "USER_NICKNAME_CONFLICT"
    assert client.get(ME, headers=AUTH).json() == before
    assert client.patch(ME, headers=AUTH, json={"nickname": "evan"}).status_code == 200
    assert client.patch(ME, headers=AUTH, json={"nickname": "Evan"}).status_code == 200


def test_public_nickname_check_normalizes_and_has_no_owner_data(api):
    client, _ = api
    assert client.get(CHECK, params={"nickname": " evan "}).json() == {
        "nickname": "evan",
        "available": False,
    }
    assert client.get(CHECK, params={"nickname": "Evan"}).json()["available"] is True
    assert client.get(CHECK, params={"nickname": "가나"}).json() == {
        "nickname": "가나",
        "available": True,
    }


@pytest.mark.parametrize(
    "params",
    [
        {},
        {"nickname": "x"},
        {"nickname": "ㄱㄴ"},
        {"nickname": "🙂🙂"},
        {"nickname": "x" * 13},
        {"nickname": "valid", "excludeUserId": "1"},
        [("nickname", "first"), ("nickname", "second")],
    ],
)
def test_nickname_check_validation(api, params):
    response = api[0].get(CHECK, params=params)
    assert response.status_code == 422
    assert response.json() == {
        "code": "INVALID_REQUEST",
        "message": "닉네임 형식을 확인해주세요.",
    }


def test_authenticated_endpoints_and_deleted_user(api):
    client, engine = api
    assert client.get(ME).status_code == 401
    assert client.patch(ME, json={"bio": "소개"}).status_code == 401
    assert client.get(f"/api/v1/users/{MOCK_MEMBER_UUID}/profile").status_code == 401
    with Session(engine) as db:
        db.execute(
            sa.update(User)
            .where(User.uuid == MOCK_VIEWER_UUID)
            .values(deleted_at=datetime.now(KST))
        )
        db.commit()
    assert client.get(ME, headers=AUTH).status_code == 401


def test_missing_required_profile_rolls_back_and_can_be_completed(api):
    client, engine = api
    with Session(engine) as db:
        db.execute(
            sa.update(User).where(User.uuid == MOCK_VIEWER_UUID).values(field=None)
        )
        db.commit()
    assert client.get(ME, headers=AUTH).json()["code"] == "PROFILE_DATA_INCONSISTENT"
    failed = client.patch(ME, headers=AUTH, json={"bio": "저장되지 않음"})
    assert failed.status_code == 500
    assert failed.json() == {
        "code": "PROFILE_DATA_INCONSISTENT",
        "message": "프로필 정보를 처리하지 못했습니다.",
    }
    with Session(engine) as db:
        assert (
            db.scalar(sa.select(User.bio).where(User.uuid == MOCK_VIEWER_UUID))
            == "소개"
        )
    assert client.patch(ME, headers=AUTH, json={"field": "백엔드"}).status_code == 200


def test_other_profile_mock_contract_and_direction(api):
    client, engine = api
    normal = client.get(f"/api/v1/users/{MOCK_MEMBER_UUID}/profile", headers=AUTH)
    assert normal.status_code == 200
    assert normal.json()["isBlocked"] is False
    assert "marketingConsent" not in normal.json()["profile"]
    assert "userUuid" not in normal.json()["profile"]
    assert "isStaff" not in normal.json()["profile"]
    assert normal.json()["profile"]["joinedMeetingCount"] == 2
    assert normal.json()["profile"]["appliedEventCount"] == 1
    assert normal.json()["profile"]["authoredPostCount"] == 3
    blocked = client.get(f"/api/v1/users/{MOCK_BLOCKED_UUID}/profile", headers=AUTH)
    assert blocked.json() == {
        "userUuid": str(MOCK_BLOCKED_UUID),
        "isBlocked": True,
        "profile": None,
    }
    with Session(engine) as db:
        db.add(
            User(uuid=MOCK_BLOCKED_UUID, nickname="bear", field="백엔드", region_id=1)
        )
        db.commit()
    reverse = client.get(
        f"/api/v1/users/{MOCK_VIEWER_UUID}/profile",
        headers={"X-Debug-User-Uuid": str(MOCK_BLOCKED_UUID)},
    )
    assert reverse.json()["isBlocked"] is False
    assert reverse.json()["profile"]["joinedMeetingCount"] == 1
    assert reverse.json()["profile"]["authoredPostCount"] == 4
    assert (
        client.get(f"/api/v1/users/{uuid4()}/profile", headers=AUTH).status_code == 404
    )
    assert client.get("/api/v1/users/invalid/profile", headers=AUTH).status_code == 422


def test_attributes_constraints(api):
    _, engine = api
    with Session(engine) as db:
        user_id = db.scalar(sa.select(User.id).where(User.uuid == MOCK_VIEWER_UUID))
        db.add(
            UserAttribute(
                user_id=user_id,
                type=UserAttributeType.AFFILIATION,
                value="학교",
                value_normalized="학교",
            )
        )
        db.commit()
        db.add(
            UserAttribute(
                user_id=user_id,
                type=UserAttributeType.AFFILIATION,
                value="회사",
                value_normalized="회사",
            )
        )
        with pytest.raises(sa.exc.IntegrityError):
            db.commit()
        db.rollback()


def test_rejects_identity_query_and_documents_public_check(api):
    client, _ = api
    assert (
        client.get(ME, headers=AUTH, params={"userUuid": str(uuid4())}).status_code
        == 422
    )
    assert (
        client.patch(
            ME + "?regionName=busan", headers=AUTH, json={"bio": "소개"}
        ).status_code
        == 422
    )
    assert (
        client.get(
            f"/api/v1/users/{MOCK_MEMBER_UUID}/profile?userId=1", headers=AUTH
        ).status_code
        == 422
    )
    operation = client.get("/openapi.json").json()["paths"][CHECK]["get"]
    assert operation["parameters"][0]["name"] == "nickname"
    assert operation["parameters"][0]["required"] is True


def test_db_failure_after_attribute_deletion_rolls_back_every_change(api, monkeypatch):
    client, _ = api
    initial = client.patch(ME, headers=AUTH, json={"stacks": ["Java"]}).json()
    original_flush = Session.flush

    def fail_attribute_insert(self, *args, **kwargs):
        if any(
            isinstance(row, UserAttribute) and row.value == "Flutter"
            for row in self.new
        ):
            raise sa.exc.OperationalError(
                "insert", {}, RuntimeError("storage unavailable")
            )
        return original_flush(self, *args, **kwargs)

    monkeypatch.setattr(Session, "flush", fail_attribute_insert)
    failed = client.patch(
        ME, headers=AUTH, json={"bio": "바뀌면 안됨", "stacks": ["Flutter"]}
    )
    assert failed.status_code == 500
    assert failed.json()["code"] == "INTERNAL_SERVER_ERROR"
    assert client.get(ME, headers=AUTH).json() == initial


def test_nickname_storage_failure_uses_lookup_error(api, monkeypatch):
    client, _ = api

    def fail_lookup(*args, **kwargs):
        raise sa.exc.OperationalError("select", {}, RuntimeError("unavailable"))

    monkeypatch.setattr(Session, "scalar", fail_lookup)
    response = client.get(CHECK, params={"nickname": "valid"})
    assert response.status_code == 500
    assert response.json() == {
        "code": "INTERNAL_SERVER_ERROR",
        "message": "닉네임을 확인하지 못했습니다.",
    }
