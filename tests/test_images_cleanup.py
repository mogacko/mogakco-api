from datetime import UTC, datetime, timedelta

import boto3
import pytest
from botocore.stub import Stubber
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.images.cleanup import delete_orphan_assets, find_orphan_assets
from app.images.config import StorageSettings
from app.images.model import PROFILE, MediaAsset, MediaUsage
from app.users.model import User

BUCKET = "mogakco-media-test"


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch) -> StorageSettings:
    monkeypatch.setenv("S3_BUCKET", BUCKET)
    monkeypatch.setenv("S3_REGION", "ap-northeast-2")
    monkeypatch.setenv("S3_PUBLIC_BASE_URL", "https://cdn.example.com")
    return StorageSettings.from_env()


@pytest.fixture
def s3(monkeypatch: pytest.MonkeyPatch):
    stubbed = boto3.client(
        "s3",
        region_name="ap-northeast-2",
        endpoint_url="https://s3.ap-northeast-2.amazonaws.com",
        aws_access_key_id="fake",
        aws_secret_access_key="fake",
    )
    stubber = Stubber(stubbed)
    stubber.activate()
    monkeypatch.setattr("app.images.cleanup.s3_client", lambda settings: stubbed)
    yield stubber
    stubber.assert_no_pending_responses()
    stubber.deactivate()


@pytest.fixture
def db(engine):
    with Session(engine) as session:
        yield session


def add_asset(db: Session, owner: User, key: str, status: str, age_hours: float) -> MediaAsset:
    asset = MediaAsset(
        owner_id=owner.id,
        key=key,
        url=f"https://cdn.example.com/{key}",
        content_type="image/png",
        status=status,
        created_at=datetime.now(UTC) - timedelta(hours=age_hours),
    )
    db.add(asset)
    db.flush()
    return asset


def add_user(db: Session, nickname: str) -> User:
    user = User(nickname=nickname, activity_region="SEOUL")
    db.add(user)
    db.flush()
    return user


def use_as_profile(db: Session, user: User, asset: MediaAsset) -> None:
    db.add(MediaUsage(asset_id=asset.id, usage_type=PROFILE, usage_id=user.id))


def expect_delete(stubber: Stubber, *keys: str) -> None:
    stubber.add_response(
        "delete_objects",
        {},
        {"Bucket": BUCKET, "Delete": {"Objects": [{"Key": key} for key in keys], "Quiet": True}},
    )


def remaining_keys(db: Session) -> list[str]:
    return sorted(db.scalars(select(MediaAsset.key)).all())


def test_removes_abandoned_and_unreferenced_assets(db, settings, s3):
    user = add_user(db, "hannah")
    add_asset(db, user, "images/never-uploaded.png", "PENDING", age_hours=48)
    replaced = add_asset(db, user, "images/old-profile.png", "READY", age_hours=48)
    current = add_asset(db, user, "images/current-profile.png", "READY", age_hours=48)
    use_as_profile(db, user, current)
    db.commit()

    # 참조가 끊긴 것만, 오래된 순이 아니라 조회 순서대로 지운다.
    expect_delete(s3, "images/never-uploaded.png", "images/old-profile.png")
    assert delete_orphan_assets(db, settings) == 2

    assert remaining_keys(db) == ["images/current-profile.png"]
    assert replaced.key not in remaining_keys(db)


def test_dry_run_lists_targets_without_touching_s3(db, settings, s3):
    user = add_user(db, "hannah")
    add_asset(db, user, "images/orphan.png", "READY", age_hours=48)
    kept = add_asset(db, user, "images/in-use.png", "READY", age_hours=48)
    use_as_profile(db, user, kept)
    db.commit()

    # S3 스텁에 응답을 넣지 않았다. 삭제를 시도하면 여기서 실패한다.
    assert [asset.key for asset in find_orphan_assets(db)] == ["images/orphan.png"]
    assert remaining_keys(db) == ["images/in-use.png", "images/orphan.png"]


def test_keeps_recent_uploads_not_yet_attached(db, settings, s3):
    user = add_user(db, "hannah")
    add_asset(db, user, "images/just-uploaded.png", "READY", age_hours=1)
    add_asset(db, user, "images/still-uploading.png", "PENDING", age_hours=0.5)
    db.commit()

    # 지울 게 없으면 S3를 부르지 않는다. 부르면 스텁이 응답 없이 실패한다.
    assert delete_orphan_assets(db, settings) == 0
    assert len(remaining_keys(db)) == 2


def test_age_threshold_is_configurable(db, settings, s3):
    user = add_user(db, "hannah")
    add_asset(db, user, "images/two-hours-old.png", "PENDING", age_hours=2)
    db.commit()

    assert delete_orphan_assets(db, settings, age_hours=24) == 0

    expect_delete(s3, "images/two-hours-old.png")
    assert delete_orphan_assets(db, settings, age_hours=1) == 1
    assert remaining_keys(db) == []


def test_keeps_assets_referenced_by_other_users(db, settings, s3):
    owner = add_user(db, "hannah")
    other = add_user(db, "someone-else")
    shared = add_asset(db, owner, "images/shared.png", "READY", age_hours=48)
    use_as_profile(db, other, shared)
    db.commit()

    assert delete_orphan_assets(db, settings) == 0
    assert remaining_keys(db) == ["images/shared.png"]
