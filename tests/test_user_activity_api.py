from datetime import datetime, timedelta
from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session
from test_user_profile_api import AUTH, ME, MOCK_VIEWER_UUID
from test_user_profile_api import api as api  # noqa: PLC0414 -- pytest fixture 재사용

from app.models import (
    CommunityPost,
    CommunityPostBoard,
    Event,
    EventParticipant,
    Region,
    User,
)
from app.services import activity, profile_sources
from app.time import KST

URL = ME + "/activity-summary"
NOW = datetime(2026, 9, 22, 0, 0, tzinfo=KST)


@pytest.fixture
def activities(api, monkeypatch):
    client, engine = api
    CommunityPost.__table__.create(engine)
    # 집계는 공간 컬럼을 사용하지 않는다. SQLite에는 읽는 컬럼만 마련한다.
    columns = (
        "id",
        "uuid",
        "region_id",
        "host_id",
        "category",
        "date",
        "start_at",
        "end_at",
        "title",
        "place_name",
        "price",
        "capacity",
        "current_count",
        "status",
        "cancel_reason",
        "post_image_url",
        "deleted_at",
    )
    meta = sa.MetaData()
    event_table = sa.Table(
        "events",
        meta,
        *[
            sa.Column(
                name,
                Event.__table__.c[name].type,
                primary_key=name == "id",
                nullable=name != "id",
            )
            for name in columns
        ],
    )
    event_table.create(engine)
    EventParticipant.__table__.create(engine)
    monkeypatch.setattr(activity, "kst_now", lambda: NOW)
    with Session(engine) as db:
        db.add(Region(id=2, name="busan", is_enabled=False))
        other = User(uuid=uuid4(), nickname="other", field="백엔드", region_id=1)
        db.add(other)
        db.commit()
        viewer_id = db.scalar(sa.select(User.id).where(User.uuid == MOCK_VIEWER_UUID))
        other_id = other.id
    return client, engine, event_table, viewer_id, other_id


def add_event(
    db,
    table,
    viewer_id,
    other_id,
    *,
    status="APPROVED",
    days=0,
    joined=True,
    region=1,
    deleted=False,
    host_only=False,
):
    event_id = db.execute(
        table.insert()
        .values(
            uuid=uuid4(),
            region_id=region,
            host_id=viewer_id if host_only else other_id,
            status=status,
            date=NOW.date() + timedelta(days=days),
        )
        .returning(table.c.id)
    ).scalar_one()
    if joined:
        db.add(EventParticipant(event_id=event_id, user_id=viewer_id))
    if deleted:
        db.execute(table.update().where(table.c.id == event_id).values(deleted_at=NOW))
    return event_id


def test_summary_counts_all_regions_and_exact_visible_memberships(activities):
    client, engine, table, viewer_id, other_id = activities
    with Session(engine) as db:
        add_event(db, table, viewer_id, other_id, days=1)
        add_event(db, table, viewer_id, other_id, days=-7, status="CANCEL", region=2)
        add_event(db, table, viewer_id, other_id, days=-7, status="COMPLETED")
        add_event(db, table, viewer_id, other_id, days=-8)
        add_event(db, table, viewer_id, other_id, status="PENDING")
        add_event(db, table, viewer_id, other_id, status="REJECTED")
        add_event(db, table, viewer_id, other_id, deleted=True)
        add_event(db, table, viewer_id, other_id, joined=False, host_only=True)
        # 실제 참가자인 모임장도 참가 관계로 한 번 센다.
        add_event(db, table, viewer_id, other_id, host_only=True)
        for board, region in [
            (CommunityPostBoard.NOTICE, 1),
            (CommunityPostBoard.QUESTION, 2),
            (CommunityPostBoard.TALK, 2),
        ]:
            db.add(
                CommunityPost(
                    uuid=uuid4(),
                    author_id=viewer_id,
                    region_id=region,
                    board=board,
                    title="글",
                    body="본문",
                )
            )
        db.add(
            CommunityPost(
                uuid=uuid4(),
                author_id=viewer_id,
                region_id=1,
                board=CommunityPostBoard.QUESTION,
                title="삭제",
                body="본문",
                deleted_at=NOW,
            )
        )
        db.add(
            CommunityPost(
                uuid=uuid4(),
                author_id=other_id,
                region_id=1,
                board=CommunityPostBoard.QUESTION,
                title="다른 글",
                body="본문",
            )
        )
        db.commit()
    response = client.get(URL, headers=AUTH)
    assert response.status_code == 200
    assert response.json() == {
        "joinedMeetingCount": 1,
        "appliedEventCount": 4,
        "authoredPostCount": 3,
    }


def test_summary_cannot_change_subject_or_region(activities):
    client, _, _, _, _ = activities
    assert client.get(URL).status_code == 401
    for query in (
        "regionName=seoul",
        "userUuid=" + str(uuid4()),
    ):
        assert client.get(URL + "?" + query, headers=AUTH).status_code == 422
    response = client.get(URL, headers=AUTH)
    assert response.status_code == 200
    assert response.json() == {
        "joinedMeetingCount": 1,
        "appliedEventCount": 0,
        "authoredPostCount": 0,
    }


def test_summary_failure_returns_no_partial_counts(activities, monkeypatch):
    client, _, _, _, _ = activities

    def fail_meetings(user_uuid, now):
        raise sa.exc.OperationalError("meeting read", {}, RuntimeError("unavailable"))

    monkeypatch.setattr(profile_sources, "joined_mogacko_count", fail_meetings)
    response = client.get(URL, headers=AUTH)
    assert response.status_code == 500
    assert response.json() == {
        "code": "INTERNAL_SERVER_ERROR",
        "message": "활동 정보를 불러오지 못했습니다.",
    }


def test_cancelled_participation_disappears_from_summary(activities):
    client, engine, table, viewer_id, other_id = activities
    with Session(engine) as db:
        event_id = add_event(db, table, viewer_id, other_id)
        db.commit()
    assert client.get(URL, headers=AUTH).json()["appliedEventCount"] == 1
    with Session(engine) as db:
        db.execute(
            sa.delete(EventParticipant).where(EventParticipant.event_id == event_id)
        )
        db.commit()
    assert client.get(URL, headers=AUTH).json()["appliedEventCount"] == 0
