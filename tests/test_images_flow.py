import io

import boto3
import pytest
from botocore.response import StreamingBody
from botocore.stub import Stubber
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.config import TokenSettings
from app.auth.token import create_access_token
from app.db import get_db
from app.images.model import PROFILE, AssetUsage, MediaAsset
from app.keywords.model import UserKeyword
from app.main import app
from app.users.model import User

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
EXE = b"MZ\x90\x00\x03\x00\x00\x00" + b"\x00" * 100
BUCKET = "mogakco-media-test"


@pytest.fixture
def client(engine, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AUTH_JWT_SECRET", "12345678901234567890123456789012")
    monkeypatch.setenv("AUTH_ACCESS_TOKEN_TTL_SECONDS", "900")
    monkeypatch.setenv("AUTH_LOGIN_CODE_TTL_SECONDS", "60")
    monkeypatch.setenv("S3_BUCKET", BUCKET)
    monkeypatch.setenv("S3_REGION", "ap-northeast-2")
    monkeypatch.setenv("S3_PUBLIC_BASE_URL", "https://cdn.example.com")

    def override_db():
        with Session(engine) as session:
            yield session

    # 저장 후 캐시 갱신은 자기 세션을 열므로 테스트 엔진으로 돌린다.
    monkeypatch.setattr("app.db.SessionLocal", lambda: Session(engine))
    app.dependency_overrides[get_db] = override_db
    yield TestClient(app), engine
    app.dependency_overrides.clear()


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
    monkeypatch.setattr("app.images.service.s3_client", lambda settings: stubbed)
    yield stubber
    stubber.assert_no_pending_responses()
    stubber.deactivate()


def make_user(engine, nickname: str) -> tuple[int, dict[str, str]]:
    with Session(engine) as db:
        user = User(nickname=nickname, activity_region="SEOUL")
        db.add(user)
        db.commit()
        user_id = user.id
    token = create_access_token(user_id, TokenSettings.from_env())
    return user_id, {"Authorization": f"Bearer {token}"}


def stub_head(stubber: Stubber, key: str, data: bytes, total: int) -> None:
    stubber.add_response(
        "get_object",
        {"Body": StreamingBody(io.BytesIO(data), len(data)), "ContentRange": f"bytes 0-511/{total}"},
        {"Bucket": BUCKET, "Key": key, "Range": "bytes=0-511"},
    )


def request_upload(test_client: TestClient, headers: dict, content_type: str = "image/png"):
    response = test_client.post("/images/upload-url", json={"content_type": content_type}, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def key_of(engine, asset_id: int) -> str:
    with Session(engine) as db:
        return db.get(MediaAsset, asset_id).key


def test_upload_url_then_complete_marks_ready(client, s3):
    test_client, engine = client
    _, headers = make_user(engine, "hannah")

    issued = request_upload(test_client, headers)
    assert issued["upload_url"] == f"https://s3.ap-northeast-2.amazonaws.com/{BUCKET}"
    assert issued["fields"]["Content-Type"] == "image/png"

    stub_head(s3, key_of(engine, issued["asset_id"]), PNG, 2048)
    completed = test_client.post(f"/images/{issued['asset_id']}/complete", headers=headers)

    assert completed.status_code == 200, completed.text
    assert completed.json()["size_bytes"] == 2048
    assert completed.json()["url"].startswith("https://cdn.example.com/images/")
    with Session(engine) as db:
        assert db.get(MediaAsset, issued["asset_id"]).status == "READY"


def test_complete_rejects_disguised_file_and_deletes_it(client, s3):
    test_client, engine = client
    _, headers = make_user(engine, "hannah")

    issued = request_upload(test_client, headers, "image/jpeg")
    key = key_of(engine, issued["asset_id"])
    stub_head(s3, key, EXE, 4096)
    # 검증에 실패하면 S3 객체까지 지워야 한다. 호출되지 않으면 이 응답이 남아 픽스처가 실패한다.
    s3.add_response("delete_object", {}, {"Bucket": BUCKET, "Key": key})

    rejected = test_client.post(f"/images/{issued['asset_id']}/complete", headers=headers)

    assert rejected.status_code == 422
    assert rejected.json()["detail"] == "이미지 파일이 아닙니다."
    with Session(engine) as db:
        assert db.get(MediaAsset, issued["asset_id"]) is None


def test_complete_is_idempotent(client, s3):
    test_client, engine = client
    _, headers = make_user(engine, "hannah")

    issued = request_upload(test_client, headers)
    # S3 응답을 한 번만 준비한다. 재시도가 S3를 다시 부르면 스텁이 실패한다.
    stub_head(s3, key_of(engine, issued["asset_id"]), PNG, 2048)
    first = test_client.post(f"/images/{issued['asset_id']}/complete", headers=headers)
    retried = test_client.post(f"/images/{issued['asset_id']}/complete", headers=headers)

    assert first.status_code == 200, first.text
    assert retried.status_code == 200, retried.text
    assert retried.json() == first.json()


def test_complete_without_upload_returns_conflict(client, s3):
    test_client, engine = client
    _, headers = make_user(engine, "hannah")

    issued = request_upload(test_client, headers)
    s3.add_client_error("get_object", service_error_code="NoSuchKey", http_status_code=404)

    response = test_client.post(f"/images/{issued['asset_id']}/complete", headers=headers)

    assert response.status_code == 409
    with Session(engine) as db:
        assert db.get(MediaAsset, issued["asset_id"]).status == "PENDING"


def test_upload_url_rejects_non_image_content_type(client, s3):
    test_client, engine = client
    _, headers = make_user(engine, "hannah")

    response = test_client.post("/images/upload-url", json={"content_type": "application/pdf"}, headers=headers)

    assert response.status_code == 422
    with Session(engine) as db:
        assert db.scalars(select(MediaAsset)).all() == []


def test_upload_url_requires_authentication(client, s3):
    test_client, _ = client
    assert test_client.post("/images/upload-url", json={"content_type": "image/png"}).status_code == 401


def test_profile_accepts_own_ready_asset_only(client, s3):
    test_client, engine = client
    _, owner_headers = make_user(engine, "hannah")
    _, other_headers = make_user(engine, "someone-else")

    pending = request_upload(test_client, owner_headers)
    blocked = test_client.patch("/me", json={"profile_image_asset_id": pending["asset_id"]}, headers=owner_headers)
    assert blocked.status_code == 422, "검증 전 에셋은 붙일 수 없어야 한다"

    stub_head(s3, key_of(engine, pending["asset_id"]), PNG, 2048)
    test_client.post(f"/images/{pending['asset_id']}/complete", headers=owner_headers)

    stolen = test_client.patch("/me", json={"profile_image_asset_id": pending["asset_id"]}, headers=other_headers)
    assert stolen.status_code == 422, "남의 에셋은 붙일 수 없어야 한다"

    attached = test_client.patch("/me", json={"profile_image_asset_id": pending["asset_id"]}, headers=owner_headers)
    assert attached.status_code == 200, attached.text
    assert attached.json()["profile_image_url"].startswith("https://cdn.example.com/images/")


def ready_asset(test_client: TestClient, engine, s3: Stubber, headers: dict) -> int:
    uploaded = request_upload(test_client, headers)
    stub_head(s3, key_of(engine, uploaded["asset_id"]), PNG, 2048)
    test_client.post(f"/images/{uploaded['asset_id']}/complete", headers=headers)
    return uploaded["asset_id"]


def usages(engine, user_id: int) -> list[int]:
    with Session(engine) as db:
        return db.scalars(
            select(AssetUsage.asset_id).where(AssetUsage.usage_type == PROFILE, AssetUsage.usage_id == user_id)
        ).all()


def test_replacing_profile_image_leaves_one_usage(client, s3):
    test_client, engine = client
    user_id, headers = make_user(engine, "hannah")
    first = ready_asset(test_client, engine, s3, headers)
    second = ready_asset(test_client, engine, s3, headers)

    test_client.patch("/me", json={"profile_image_asset_id": first}, headers=headers)
    assert usages(engine, user_id) == [first]

    # 자리당 한 장이다. 갈아끼워도 행이 쌓이지 않는다.
    replaced = test_client.patch("/me", json={"profile_image_asset_id": second}, headers=headers)
    assert replaced.status_code == 200, replaced.text
    assert usages(engine, user_id) == [second]


def test_clearing_profile_image_removes_the_usage(client, s3):
    test_client, engine = client
    user_id, headers = make_user(engine, "hannah")
    asset_id = ready_asset(test_client, engine, s3, headers)
    test_client.patch("/me", json={"profile_image_asset_id": asset_id}, headers=headers)

    cleared = test_client.patch("/me", json={"profile_image_asset_id": None}, headers=headers)

    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["profile_image_url"] is None
    assert usages(engine, user_id) == []


def test_deleting_the_asset_detaches_the_profile(client, s3):
    test_client, engine = client
    user_id, headers = make_user(engine, "hannah")
    asset_id = ready_asset(test_client, engine, s3, headers)
    test_client.patch("/me", json={"profile_image_asset_id": asset_id}, headers=headers)

    with Session(engine) as db:
        db.delete(db.get(MediaAsset, asset_id))
        db.commit()

    # 사용처 행이 CASCADE로 함께 사라져 프로필에서도 떨어진다.
    assert usages(engine, user_id) == []
    assert test_client.get("/me", headers=headers).json()["profile_image_url"] is None


def test_profile_update_syncs_keywords(client, s3):
    test_client, engine = client
    user_id, headers = make_user(engine, "hannah")

    assert test_client.patch("/me", json={"stack": "React, Django"}, headers=headers).status_code == 200
    with Session(engine) as db:
        rows = db.scalars(select(UserKeyword.keyword).where(UserKeyword.user_id == user_id)).all()
    assert sorted(rows) == ["django", "react"]

    test_client.patch("/me", json={"stack": "Rust"}, headers=headers)
    with Session(engine) as db:
        rows = db.scalars(select(UserKeyword.keyword).where(UserKeyword.user_id == user_id)).all()
    assert rows == ["rust"]
