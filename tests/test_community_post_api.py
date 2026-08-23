import os
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy.orm import Session

from app.database import get_db
from app.main import app
from app.models import (
    Comment,
    CommentTargetType,
    CommunityPost,
    CommunityPostBoard,
    CommunityPostCategory,
    User,
)
from app.redis_client import get_redis_client
from app.services.community import community_post_like_key

DATABASE_URL = os.environ["DATABASE_URL"]


@pytest.fixture
def api(
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[tuple[TestClient, sa.Engine, int]]:
    command.upgrade(Config("alembic.ini"), "head")
    engine = sa.create_engine(DATABASE_URL)
    redis = get_redis_client()
    redis.flushdb()
    with Session(engine, expire_on_commit=False) as db:
        db.execute(sa.delete(Comment))
        db.execute(sa.delete(CommunityPost))
        db.execute(sa.delete(User))
        viewer = User(nickname="viewer", region_id=1)
        db.add(viewer)
        db.commit()
        viewer_id = viewer.id

    def override_db() -> Generator[Session]:
        with Session(engine, expire_on_commit=False) as db:
            yield db

    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("ENABLE_DEBUG_AUTH", "true")
    app.dependency_overrides[get_db] = override_db
    with TestClient(app) as client:
        yield client, engine, viewer_id
    app.dependency_overrides.clear()
    redis.flushdb()
    redis.close()
    get_redis_client.cache_clear()
    engine.dispose()


def add_community_post(
    db: Session,
    author_id: int | None,
    *,
    title: str,
    board: CommunityPostBoard = CommunityPostBoard.QUESTION,
    category: CommunityPostCategory | None = None,
    created_at: datetime | None = None,
    deleted_at: datetime | None = None,
    region_id: int = 1,
    body: str = "body",
) -> CommunityPost:
    community_post = CommunityPost(
        author_id=author_id,
        region_id=region_id,
        board=board,
        category=category,
        title=title,
        body=body,
        created_at=created_at or datetime.now(UTC),
        deleted_at=deleted_at,
    )
    db.add(community_post)
    db.flush()
    return community_post


def auth(user_id: int) -> dict[str, str]:
    return {"X-Debug-User-Id": str(user_id)}


def list_url(
    *,
    board_name: str = "question",
    category_name: str | None = None,
    offset: int = 0,
    limit: int = 20,
) -> str:
    url = (
        "/api/v1/community-posts"
        f"?regionName=seoul&boardName={board_name}"
        f"&offset={offset}&limit={limit}"
    )
    if category_name is not None:
        url += f"&categoryName={category_name}"
    return url


def test_list_filters_paginates_and_returns_final_dto(
    api: tuple[TestClient, sa.Engine, int],
) -> None:
    client, engine, viewer_id = api
    now = datetime.now(UTC)
    with Session(engine, expire_on_commit=False) as db:
        older = add_community_post(
            db,
            viewer_id,
            title="older",
            board=CommunityPostBoard.TALK,
            category=CommunityPostCategory.FREE,
            created_at=now - timedelta(minutes=1),
            body="x" * 80,
        )
        newer = add_community_post(
            db,
            viewer_id,
            title="newer",
            board=CommunityPostBoard.TALK,
            category=CommunityPostCategory.FREE,
            created_at=now,
        )
        add_community_post(
            db,
            viewer_id,
            title="other category",
            board=CommunityPostBoard.TALK,
            category=CommunityPostCategory.RECRUIT,
        )
        add_community_post(
            db,
            viewer_id,
            title="deleted",
            board=CommunityPostBoard.TALK,
            category=CommunityPostCategory.FREE,
            deleted_at=now,
        )
        db.commit()

    response = client.get(
        list_url(board_name="talk", category_name="free", limit=1),
        headers=auth(viewer_id),
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["offset"] == 0
    assert payload["limit"] == 1
    assert payload["hasMore"] is True
    assert [item["title"] for item in payload["items"]] == ["newer"]
    assert set(payload) == {"items", "offset", "limit", "hasMore"}
    item = payload["items"][0]
    assert UUID(item["uuid"]) == newer.uuid
    assert set(item) == {
        "uuid",
        "categoryName",
        "title",
        "body",
        "authorNickname",
        "authorAvatarUrl",
        "createdAt",
        "updatedAt",
        "likeCount",
        "commentCount",
        "isLiked",
        "isPopular",
    }

    second = client.get(
        list_url(
            board_name="talk",
            category_name="free",
            offset=1,
            limit=1,
        ),
        headers=auth(viewer_id),
    ).json()
    assert second["hasMore"] is False
    assert second["items"][0]["uuid"] == str(older.uuid)
    assert second["items"][0]["body"] == "x" * 60


@pytest.mark.parametrize(
    ("url", "status_code"),
    [
        ("/api/v1/community-posts?boardName=question", 422),
        ("/api/v1/community-posts?regionName=seoul", 422),
        (
            "/api/v1/community-posts"
            "?regionName=seoul&boardName=question&categoryName=free",
            422,
        ),
        (
            "/api/v1/community-posts"
            "?regionName=gyeonggi&boardName=question",
            404,
        ),
        (
            "/api/v1/community-posts"
            "?regionName=seoul&boardName=question&limit=51",
            422,
        ),
    ],
)
def test_list_validates_required_query_and_enabled_region(
    api: tuple[TestClient, sa.Engine, int],
    url: str,
    status_code: int,
) -> None:
    client, _, viewer_id = api
    response = client.get(url, headers=auth(viewer_id))
    assert response.status_code == status_code
    if status_code == 404:
        assert response.json() == {}
    else:
        assert response.json() == {"message": "올바르지 않은 메뉴입니다."}


def test_detail_uses_query_uuid_and_returns_full_final_dto(
    api: tuple[TestClient, sa.Engine, int],
) -> None:
    client, engine, viewer_id = api
    with Session(engine, expire_on_commit=False) as db:
        author = User(nickname="detail-author", region_id=1)
        db.add(author)
        db.flush()
        community_post = add_community_post(
            db,
            author.id,
            title="detail",
            board=CommunityPostBoard.TALK,
            category=CommunityPostCategory.FREE,
            body="full body " * 20,
        )
        db.commit()

    get_redis_client().sadd(
        community_post_like_key(community_post.id),
        viewer_id,
    )
    response = client.get(
        "/api/v1/community-posts/detail",
        params={"communityPostUuid": str(community_post.uuid)},
        headers=auth(viewer_id),
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload == {
        "uuid": str(community_post.uuid),
        "regionName": "seoul",
        "boardName": "talk",
        "title": "detail",
        "body": "full body " * 20,
        "authorUuid": str(author.uuid),
        "authorNickname": "detail-author",
        "authorAvatarUrl": None,
        "createdAt": community_post.created_at.isoformat().replace(
            "+00:00", "Z"
        ),
        "updatedAt": None,
        "likeCount": 1,
        "isLiked": True,
    }

    with Session(engine) as db:
        db.execute(
            sa.update(User)
            .where(User.id == author.id)
            .values(deleted_at=datetime.now(UTC))
        )
        db.commit()
    masked = client.get(
        "/api/v1/community-posts/detail",
        params={"communityPostUuid": str(community_post.uuid)},
        headers=auth(viewer_id),
    ).json()
    assert masked["authorUuid"] is None
    assert masked["authorNickname"] is None
    assert masked["authorAvatarUrl"] is None


def test_detail_validates_target_auth_and_redis(
    api: tuple[TestClient, sa.Engine, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, engine, viewer_id = api
    with Session(engine, expire_on_commit=False) as db:
        active = add_community_post(db, viewer_id, title="active detail")
        deleted = add_community_post(
            db,
            viewer_id,
            title="deleted detail",
            deleted_at=datetime.now(UTC),
        )
        db.commit()

    endpoint = "/api/v1/community-posts/detail"
    assert client.get(endpoint).status_code == 401
    assert client.get(endpoint, headers=auth(viewer_id)).status_code == 422
    assert client.get(
        endpoint,
        params={"communityPostUuid": str(uuid4())},
        headers=auth(viewer_id),
    ).json() == {}
    assert client.get(
        endpoint,
        params={"communityPostUuid": str(deleted.uuid)},
        headers=auth(viewer_id),
    ).json() == {}

    redis = get_redis_client()
    monkeypatch.setattr(
        redis,
        "pipeline",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RedisConnectionError("unavailable")
        ),
    )
    failed = client.get(
        endpoint,
        params={"communityPostUuid": str(active.uuid)},
        headers=auth(viewer_id),
    )
    assert failed.status_code == 503
    assert failed.json() == {}


def test_search_matches_title_body_and_only_active_author(
    api: tuple[TestClient, sa.Engine, int],
) -> None:
    client, engine, viewer_id = api
    with Session(engine, expire_on_commit=False) as db:
        active_author = User(nickname="NeedleAuthor", region_id=1)
        deleted_author = User(
            nickname="NeedleDeleted",
            region_id=1,
            deleted_at=datetime.now(UTC),
        )
        db.add_all([active_author, deleted_author])
        db.flush()
        title_match = add_community_post(
            db,
            viewer_id,
            title="Needle title",
        )
        body_match = add_community_post(
            db,
            viewer_id,
            title="body match",
            body="contains needle",
        )
        author_match = add_community_post(
            db,
            active_author.id,
            title="author match",
        )
        add_community_post(
            db,
            deleted_author.id,
            title="must not match author",
        )
        db.commit()

    response = client.get(
        "/api/v1/community-posts/search"
        "?regionName=seoul&q=NEEDLE&offset=0&limit=20",
        headers=auth(viewer_id),
    )
    assert response.status_code == 200
    uuids = {item["uuid"] for item in response.json()["items"]}
    assert uuids == {
        str(title_match.uuid),
        str(body_match.uuid),
        str(author_match.uuid),
    }
    assert set(response.json()) == {"items", "offset", "limit", "hasMore"}


def test_is_popular_uses_region_redis_like_top_three(
    api: tuple[TestClient, sa.Engine, int],
) -> None:
    client, engine, viewer_id = api
    now = datetime.now(UTC)
    with Session(engine, expire_on_commit=False) as db:
        community_posts = [
            add_community_post(
                db,
                viewer_id,
                title=f"rank-{index}",
                created_at=now + timedelta(seconds=index),
            )
            for index in range(4)
        ]
        db.commit()

    redis = get_redis_client()
    for community_post, like_count in zip(
        community_posts,
        [1, 2, 2, 4],
    ):
        if like_count:
            redis.sadd(
                community_post_like_key(community_post.id),
                *range(100, 100 + like_count),
            )

    payload = client.get(
        list_url(),
        headers=auth(viewer_id),
    ).json()
    popularity = {
        item["title"]: item["isPopular"] for item in payload["items"]
    }
    assert popularity == {
        "rank-3": True,
        "rank-2": True,
        "rank-1": True,
        "rank-0": False,
    }


def test_list_batches_redis_stats_once(
    api: tuple[TestClient, sa.Engine, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, engine, viewer_id = api
    with Session(engine, expire_on_commit=False) as db:
        for index in range(4):
            add_community_post(db, viewer_id, title=f"batch-{index}")
        db.commit()

    redis = get_redis_client()
    original_pipeline = redis.pipeline
    calls = 0

    def counted_pipeline(*args: object, **kwargs: object):
        nonlocal calls
        calls += 1
        return original_pipeline(*args, **kwargs)

    monkeypatch.setattr(redis, "pipeline", counted_pipeline)
    response = client.get(list_url(), headers=auth(viewer_id))
    assert response.status_code == 200
    assert calls == 1


def test_create_uses_current_user_preserves_raw_text_and_returns_empty_201(
    api: tuple[TestClient, sa.Engine, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, engine, viewer_id = api
    redis = get_redis_client()

    def failed_pipeline(*_args: object, **_kwargs: object):
        raise RedisConnectionError("unavailable")

    monkeypatch.setattr(redis, "pipeline", failed_pipeline)
    response = client.post(
        "/api/v1/regions/seoul/community-posts",
        headers=auth(viewer_id),
        json={
            "boardName": "talk",
            "categoryName": "free",
            "title": "  제목  ",
            "body": "  본문  ",
        },
    )
    assert response.status_code == 201
    assert response.content == b""
    with Session(engine, expire_on_commit=False) as db:
        community_post = db.scalar(sa.select(CommunityPost))
        assert community_post is not None
        assert community_post.author_id == viewer_id
        assert community_post.title == "  제목  "
        assert community_post.body == "  본문  "
        assert isinstance(community_post.uuid, UUID)


@pytest.mark.parametrize(
    ("payload", "status_code"),
    [
        (
            {
                "boardName": "notice",
                "title": "title",
                "body": "body",
            },
            403,
        ),
        (
            {
                "boardName": "talk",
                "title": "title",
                "body": "body",
            },
            422,
        ),
        (
            {
                "boardName": "question",
                "categoryName": "free",
                "title": "title",
                "body": "body",
            },
            422,
        ),
        (
            {
                "boardName": "question",
                "title": "x" * 26,
                "body": "body",
            },
            422,
        ),
        (
            {
                "boardName": "question",
                "title": "title",
                "body": "x" * 3001,
            },
            422,
        ),
        (
            {
                "boardName": "question",
                "title": "title",
                "body": "body",
                "authorUuid": "69a7dc1e-53d9-493d-b812-28d117b70c77",
            },
            422,
        ),
    ],
)
def test_create_validates_permission_category_length_and_spoofing(
    api: tuple[TestClient, sa.Engine, int],
    payload: dict[str, object],
    status_code: int,
) -> None:
    client, engine, viewer_id = api
    response = client.post(
        "/api/v1/regions/seoul/community-posts",
        headers=auth(viewer_id),
        json=payload,
    )
    assert response.status_code == status_code
    with Session(engine, expire_on_commit=False) as db:
        assert db.scalar(
            sa.select(sa.func.count()).select_from(CommunityPost)
        ) == 0


def test_update_changes_allowed_fields_and_preserves_identity(
    api: tuple[TestClient, sa.Engine, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, engine, viewer_id = api
    with Session(engine, expire_on_commit=False) as db:
        community_post = add_community_post(
            db,
            viewer_id,
            title="before",
            board=CommunityPostBoard.TALK,
            category=CommunityPostCategory.FREE,
        )
        db.commit()
        community_post_uuid = community_post.uuid
        immutable = (
            community_post.id,
            community_post.region_id,
            community_post.author_id,
            community_post.board,
            community_post.created_at,
        )

    redis = get_redis_client()
    monkeypatch.setattr(
        redis,
        "pipeline",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RedisConnectionError("unavailable")
        ),
    )
    response = client.patch(
        f"/api/v1/community-posts/{community_post_uuid}",
        headers=auth(viewer_id),
        json={
            "title": "  after  ",
            "body": "  changed  ",
            "categoryName": "retrospective",
        },
    )
    assert response.status_code == 201
    assert response.content == b""
    with Session(engine, expire_on_commit=False) as db:
        stored = db.scalar(
            sa.select(CommunityPost).where(
                CommunityPost.uuid == community_post_uuid
            )
        )
        assert stored is not None
        assert stored.title == "  after  "
        assert stored.body == "  changed  "
        assert stored.category is CommunityPostCategory.RETROSPECTIVE
        assert stored.edited_at is not None
        assert (
            stored.id,
            stored.region_id,
            stored.author_id,
            stored.board,
            stored.created_at,
        ) == immutable


def test_update_rejects_auth_missing_deleted_other_and_extra_field(
    api: tuple[TestClient, sa.Engine, int],
) -> None:
    client, engine, viewer_id = api
    with Session(engine, expire_on_commit=False) as db:
        other = User(nickname="other", region_id=1)
        db.add(other)
        db.flush()
        active = add_community_post(db, other.id, title="other")
        deleted = add_community_post(
            db,
            viewer_id,
            title="deleted",
            deleted_at=datetime.now(UTC),
        )
        db.commit()

    assert client.patch(
        f"/api/v1/community-posts/{active.uuid}",
        json={"title": "fail"},
    ).status_code == 401
    assert client.patch(
        f"/api/v1/community-posts/{active.uuid}",
        headers=auth(viewer_id),
        json={"title": "fail"},
    ).status_code == 403
    assert client.patch(
        f"/api/v1/community-posts/{deleted.uuid}",
        headers=auth(viewer_id),
        json={"title": "fail"},
    ).status_code == 404
    assert client.patch(
        f"/api/v1/community-posts/{uuid4()}",
        headers=auth(viewer_id),
        json={"title": "fail"},
    ).status_code == 404
    assert client.patch(
        f"/api/v1/community-posts/{active.uuid}",
        headers=auth(other.id),
        json={"boardName": "talk"},
    ).status_code == 422
    with Session(engine, expire_on_commit=False) as db:
        assert db.get(CommunityPost, active.id).title == "other"


def test_delete_soft_deletes_and_cleans_redis_key(
    api: tuple[TestClient, sa.Engine, int],
) -> None:
    client, engine, viewer_id = api
    with Session(engine, expire_on_commit=False) as db:
        community_post = add_community_post(
            db,
            viewer_id,
            title="delete me",
            body="preserved",
        )
        db.commit()
        community_post_id = community_post.id
        community_post_uuid = community_post.uuid

    redis = get_redis_client()
    redis.sadd(community_post_like_key(community_post_id), viewer_id)
    response = client.delete(
        f"/api/v1/community-posts/{community_post_uuid}",
        headers=auth(viewer_id),
    )
    assert response.status_code == 204
    assert response.content == b""
    with Session(engine, expire_on_commit=False) as db:
        stored = db.get(CommunityPost, community_post_id)
        assert stored is not None
        assert stored.deleted_at is not None
        assert stored.body == "preserved"
    assert redis.exists(community_post_like_key(community_post_id)) == 0
    assert client.delete(
        f"/api/v1/community-posts/{community_post_uuid}",
        headers=auth(viewer_id),
    ).status_code == 404


def test_like_is_idempotent_and_visible_in_list(
    api: tuple[TestClient, sa.Engine, int],
) -> None:
    client, engine, viewer_id = api
    with Session(engine, expire_on_commit=False) as db:
        other = User(nickname="like-other", region_id=1)
        db.add(other)
        db.flush()
        community_post = add_community_post(
            db,
            viewer_id,
            title="liked",
        )
        db.commit()

    redis = get_redis_client()
    redis.sadd(community_post_like_key(community_post.id), other.id)
    endpoint = f"/api/v1/community-posts/{community_post.uuid}/likes"
    first = client.post(endpoint, headers=auth(viewer_id))
    second = client.post(endpoint, headers=auth(viewer_id))
    assert first.json() == {"likeCount": 2, "isLiked": True}
    assert second.json() == {"likeCount": 2, "isLiked": True}
    assert redis.smembers(community_post_like_key(community_post.id)) == {
        str(viewer_id),
        str(other.id),
    }
    item = client.get(list_url(), headers=auth(viewer_id)).json()["items"][0]
    assert item["likeCount"] == 2
    assert item["isLiked"] is True


def test_unlike_is_idempotent_and_preserves_other_user(
    api: tuple[TestClient, sa.Engine, int],
) -> None:
    client, engine, viewer_id = api
    with Session(engine, expire_on_commit=False) as db:
        other = User(nickname="unlike-other", region_id=1)
        db.add(other)
        db.flush()
        community_post = add_community_post(
            db,
            viewer_id,
            title="unliked",
        )
        db.commit()

    redis = get_redis_client()
    key = community_post_like_key(community_post.id)
    redis.sadd(key, viewer_id, other.id)
    endpoint = f"/api/v1/community-posts/{community_post.uuid}/likes"
    first = client.delete(endpoint, headers=auth(viewer_id))
    second = client.delete(endpoint, headers=auth(viewer_id))
    assert first.json() == {"likeCount": 1, "isLiked": False}
    assert second.json() == {"likeCount": 1, "isLiked": False}
    assert redis.smembers(key) == {str(other.id)}


def test_like_rejects_missing_deleted_auth_and_redis_failure(
    api: tuple[TestClient, sa.Engine, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, engine, viewer_id = api
    with Session(engine, expire_on_commit=False) as db:
        active = add_community_post(db, viewer_id, title="active")
        deleted = add_community_post(
            db,
            viewer_id,
            title="deleted",
            deleted_at=datetime.now(UTC),
        )
        db.commit()

    endpoint = f"/api/v1/community-posts/{active.uuid}/likes"
    assert client.post(endpoint).status_code == 401
    assert client.post(
        f"/api/v1/community-posts/{uuid4()}/likes",
        headers=auth(viewer_id),
    ).json() == {}
    assert client.post(
        f"/api/v1/community-posts/{deleted.uuid}/likes",
        headers=auth(viewer_id),
    ).json() == {}

    redis = get_redis_client()
    monkeypatch.setattr(
        redis,
        "pipeline",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RedisConnectionError("unavailable")
        ),
    )
    failed = client.post(endpoint, headers=auth(viewer_id))
    assert failed.status_code == 503
    assert failed.json() == {}


def test_list_redis_failure_is_503_without_rdb_fallback(
    api: tuple[TestClient, sa.Engine, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, engine, viewer_id = api
    with Session(engine, expire_on_commit=False) as db:
        add_community_post(db, viewer_id, title="redis required")
        db.commit()

    redis = get_redis_client()
    monkeypatch.setattr(
        redis,
        "pipeline",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RedisConnectionError("unavailable")
        ),
    )
    response = client.get(list_url(), headers=auth(viewer_id))
    assert response.status_code == 503
    assert response.json() == {}
    assert not sa.inspect(engine).has_table("post_likes")


def test_comment_list_groups_replies_masks_authors_and_tombstones(
    api: tuple[TestClient, sa.Engine, int],
) -> None:
    client, engine, viewer_id = api
    base = datetime(2026, 8, 15, tzinfo=UTC)
    with Session(engine, expire_on_commit=False) as db:
        removed = User(nickname="removed", region_id=1)
        db.add(removed)
        db.flush()
        community_post = add_community_post(
            db,
            viewer_id,
            title="comments",
        )
        db.flush()
        deleted_root = Comment(
            target_type=CommentTargetType.COMMUNITY_POST,
            target_id=community_post.id,
            user_id=viewer_id,
            content="secret",
            created_at=base,
            deleted_at=base + timedelta(hours=1),
        )
        active_root = Comment(
            target_type=CommentTargetType.COMMUNITY_POST,
            target_id=community_post.id,
            user_id=viewer_id,
            content="active root",
            created_at=base + timedelta(minutes=1),
        )
        db.add_all([deleted_root, active_root])
        db.flush()
        reply = Comment(
            target_type=CommentTargetType.COMMUNITY_POST,
            target_id=community_post.id,
            parent_comment_id=deleted_root.id,
            user_id=removed.id,
            content="reply",
            created_at=base + timedelta(minutes=2),
        )
        deleted_reply = Comment(
            target_type=CommentTargetType.COMMUNITY_POST,
            target_id=community_post.id,
            parent_comment_id=active_root.id,
            user_id=viewer_id,
            content="deleted reply",
            deleted_at=base + timedelta(hours=1),
        )
        db.add_all([reply, deleted_reply])
        db.flush()
        target_uuid = community_post.uuid
        deleted_root_uuid = deleted_root.uuid
        reply_uuid = reply.uuid
        db.delete(removed)
        db.commit()

    statement_count = 0

    def count_statement(*_args: object) -> None:
        nonlocal statement_count
        statement_count += 1

    sa.event.listen(engine, "before_cursor_execute", count_statement)
    try:
        response = client.get(
            "/api/v1/comments",
            params={
                "targetType": "COMMUNITY_POST",
                "targetUuid": str(target_uuid),
            },
            headers=auth(viewer_id),
        )
    finally:
        sa.event.remove(engine, "before_cursor_execute", count_statement)

    assert response.status_code == 200
    assert statement_count == 3
    payload = response.json()
    assert payload["count"] == 2
    assert len(payload["items"]) == 2
    tombstone = payload["items"][0]
    assert tombstone["masked"] is False
    assert tombstone["comment"]["uuid"] == str(deleted_root_uuid)
    assert tombstone["comment"]["body"] == ""
    assert tombstone["comment"]["isDeleted"] is True
    assert tombstone["replies"][0]["uuid"] == str(reply_uuid)
    assert tombstone["replies"][0]["parentUuid"] == str(deleted_root_uuid)
    assert tombstone["replies"][0]["authorUuid"] is None
    assert tombstone["replies"][0]["authorNickname"] is None
    assert "targetType" not in tombstone["comment"]
    assert "targetUuid" not in tombstone["comment"]
    assert "secret" not in response.text
    assert "deleted reply" not in response.text


@pytest.mark.parametrize(
    ("params", "status_code"),
    [
        ({"targetType": "COMMUNITY_POST"}, 422),
        ({"targetUuid": "d6f8f9e4-b239-4cd6-89f7-337454dd6906"}, 422),
        (
            {
                "targetType": "POST",
                "targetUuid": "d6f8f9e4-b239-4cd6-89f7-337454dd6906",
            },
            422,
        ),
        (
            {
                "targetType": "COMMUNITY_POST",
                "targetUuid": "not-a-uuid",
            },
            422,
        ),
        (
            {
                "targetType": "EVENT",
                "targetUuid": "d6f8f9e4-b239-4cd6-89f7-337454dd6906",
            },
            404,
        ),
    ],
)
def test_comment_list_validates_target(
    api: tuple[TestClient, sa.Engine, int],
    params: dict[str, str],
    status_code: int,
) -> None:
    client, _, viewer_id = api
    response = client.get(
        "/api/v1/comments",
        params=params,
        headers=auth(viewer_id),
    )
    assert response.status_code == status_code
    assert response.json() == (
        {} if status_code == 404 else {"message": "올바르지 않은 대상입니다."}
    )


def test_comment_create_root_and_reply_use_uuids_and_empty_201(
    api: tuple[TestClient, sa.Engine, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, engine, viewer_id = api
    with Session(engine, expire_on_commit=False) as db:
        community_post = add_community_post(
            db,
            viewer_id,
            title="create comments",
        )
        db.commit()
        target_uuid = community_post.uuid

    redis = get_redis_client()
    monkeypatch.setattr(
        redis,
        "pipeline",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RedisConnectionError("unavailable")
        ),
    )
    root = client.post(
        "/api/v1/comments",
        headers=auth(viewer_id),
        json={
            "targetType": "COMMUNITY_POST",
            "targetUuid": str(target_uuid),
            "parentUuid": None,
            "body": "  root  ",
        },
    )
    assert root.status_code == 201
    assert root.content == b""
    with Session(engine, expire_on_commit=False) as db:
        stored_root = db.scalar(sa.select(Comment))
        assert stored_root is not None
        assert stored_root.content == "  root  "
        assert stored_root.user_id == viewer_id
        root_uuid = stored_root.uuid
        target_id = stored_root.target_id

    reply = client.post(
        "/api/v1/comments",
        headers=auth(viewer_id),
        json={
            "targetType": "COMMUNITY_POST",
            "targetUuid": str(target_uuid),
            "parentUuid": str(root_uuid),
            "body": "reply",
        },
    )
    assert reply.status_code == 201
    assert reply.content == b""
    with Session(engine, expire_on_commit=False) as db:
        comments = db.scalars(
            sa.select(Comment).order_by(Comment.id)
        ).all()
        assert comments[1].parent_comment_id == comments[0].id
        assert comments[1].target_id == target_id

    listed = client.get(
        "/api/v1/comments",
        params={
            "targetType": "COMMUNITY_POST",
            "targetUuid": str(target_uuid),
        },
        headers=auth(viewer_id),
    ).json()
    assert listed["count"] == 2
    assert listed["items"][0]["replies"][0]["parentUuid"] == str(root_uuid)


def test_comment_reply_locks_parent_before_insert(
    api: tuple[TestClient, sa.Engine, int],
) -> None:
    client, engine, viewer_id = api
    with Session(engine, expire_on_commit=False) as db:
        community_post = add_community_post(db, viewer_id, title="lock parent")
        db.flush()
        root = Comment(
            target_type=CommentTargetType.COMMUNITY_POST,
            target_id=community_post.id,
            user_id=viewer_id,
            content="root",
        )
        db.add(root)
        db.commit()

    statements: list[str] = []

    def capture_statement(
        _connection: object,
        _cursor: object,
        statement: str,
        *_args: object,
    ) -> None:
        statements.append(statement)

    sa.event.listen(engine, "before_cursor_execute", capture_statement)
    try:
        response = client.post(
            "/api/v1/comments",
            headers=auth(viewer_id),
            json={
                "targetType": "COMMUNITY_POST",
                "targetUuid": str(community_post.uuid),
                "parentUuid": str(root.uuid),
                "body": "reply",
            },
        )
    finally:
        sa.event.remove(engine, "before_cursor_execute", capture_statement)

    assert response.status_code == 201
    assert any("FOR UPDATE" in statement.upper() for statement in statements)


def test_comment_create_rejects_bad_parent_target_auth_and_spoofing(
    api: tuple[TestClient, sa.Engine, int],
) -> None:
    client, engine, viewer_id = api
    with Session(engine, expire_on_commit=False) as db:
        first = add_community_post(db, viewer_id, title="first")
        second = add_community_post(db, viewer_id, title="second")
        db.flush()
        root = Comment(
            target_type=CommentTargetType.COMMUNITY_POST,
            target_id=first.id,
            user_id=viewer_id,
            content="root",
        )
        deleted_root = Comment(
            target_type=CommentTargetType.COMMUNITY_POST,
            target_id=first.id,
            user_id=viewer_id,
            content="deleted",
            deleted_at=datetime.now(UTC),
        )
        db.add_all([root, deleted_root])
        db.flush()
        reply = Comment(
            target_type=CommentTargetType.COMMUNITY_POST,
            target_id=first.id,
            parent_comment_id=root.id,
            user_id=viewer_id,
            content="reply",
        )
        db.add(reply)
        db.commit()

    valid = {
        "targetType": "COMMUNITY_POST",
        "targetUuid": str(first.uuid),
        "body": "body",
    }
    assert client.post("/api/v1/comments", json=valid).status_code == 401
    cases = (
        ({**valid, "targetUuid": str(uuid4())}, 404),
        ({**valid, "parentUuid": str(uuid4())}, 404),
        ({**valid, "parentUuid": str(deleted_root.uuid)}, 404),
        (
            {
                **valid,
                "targetUuid": str(second.uuid),
                "parentUuid": str(root.uuid),
            },
            422,
        ),
        ({**valid, "parentUuid": str(reply.uuid)}, 422),
        ({**valid, "userUuid": str(uuid4())}, 422),
        ({**valid, "authorUuid": str(uuid4())}, 422),
        ({**valid, "body": "x" * 301}, 422),
    )
    for payload, status_code in cases:
        response = client.post(
            "/api/v1/comments",
            json=payload,
            headers=auth(viewer_id),
        )
        assert response.status_code == status_code

    with Session(engine, expire_on_commit=False) as db:
        assert db.scalar(
            sa.select(sa.func.count()).select_from(Comment)
        ) == 3


def test_comment_update_uses_uuid_preserves_relations_and_raw_body(
    api: tuple[TestClient, sa.Engine, int],
) -> None:
    client, engine, viewer_id = api
    with Session(engine, expire_on_commit=False) as db:
        community_post = add_community_post(db, viewer_id, title="update")
        db.flush()
        root = Comment(
            target_type=CommentTargetType.COMMUNITY_POST,
            target_id=community_post.id,
            user_id=viewer_id,
            content="before",
        )
        db.add(root)
        db.flush()
        immutable = (
            root.id,
            root.uuid,
            root.target_type,
            root.target_id,
            root.parent_comment_id,
            root.user_id,
            root.created_at,
        )
        db.commit()

    response = client.patch(
        f"/api/v1/comments/{root.uuid}",
        headers=auth(viewer_id),
        json={"body": "  after  "},
    )
    assert response.status_code == 201
    assert response.content == b""
    with Session(engine, expire_on_commit=False) as db:
        stored = db.get(Comment, root.id)
        assert stored.content == "  after  "
        assert stored.updated_at is not None
        assert (
            stored.id,
            stored.uuid,
            stored.target_type,
            stored.target_id,
            stored.parent_comment_id,
            stored.user_id,
            stored.created_at,
        ) == immutable


def test_comment_update_rejects_missing_deleted_other_and_extra(
    api: tuple[TestClient, sa.Engine, int],
) -> None:
    client, engine, viewer_id = api
    with Session(engine, expire_on_commit=False) as db:
        other = User(nickname="comment-other", region_id=1)
        db.add(other)
        db.flush()
        community_post = add_community_post(db, viewer_id, title="protected")
        db.flush()
        other_comment = Comment(
            target_type=CommentTargetType.COMMUNITY_POST,
            target_id=community_post.id,
            user_id=other.id,
            content="other",
        )
        deleted = Comment(
            target_type=CommentTargetType.COMMUNITY_POST,
            target_id=community_post.id,
            user_id=viewer_id,
            content="deleted",
            deleted_at=datetime.now(UTC),
        )
        db.add_all([other_comment, deleted])
        db.commit()

    cases = (
        (str(uuid4()), {"body": "fail"}, 404),
        (str(deleted.uuid), {"body": "fail"}, 404),
        (str(other_comment.uuid), {"body": "fail"}, 403),
        (str(other_comment.uuid), {"body": "x", "targetUuid": str(uuid4())}, 422),
        (str(other_comment.uuid), {"body": "x" * 301}, 422),
    )
    for comment_uuid, payload, status_code in cases:
        response = client.patch(
            f"/api/v1/comments/{comment_uuid}",
            headers=auth(viewer_id),
            json=payload,
        )
        assert response.status_code == status_code
    with Session(engine, expire_on_commit=False) as db:
        assert db.get(Comment, other_comment.id).content == "other"


def test_comment_delete_soft_deletes_and_thread_rules(
    api: tuple[TestClient, sa.Engine, int],
) -> None:
    client, engine, viewer_id = api
    with Session(engine, expire_on_commit=False) as db:
        community_post = add_community_post(db, viewer_id, title="delete comments")
        db.flush()
        root = Comment(
            target_type=CommentTargetType.COMMUNITY_POST,
            target_id=community_post.id,
            user_id=viewer_id,
            content="root secret",
        )
        db.add(root)
        db.flush()
        reply = Comment(
            target_type=CommentTargetType.COMMUNITY_POST,
            target_id=community_post.id,
            parent_comment_id=root.id,
            user_id=viewer_id,
            content="reply",
        )
        db.add(reply)
        db.commit()

    root_delete = client.delete(
        f"/api/v1/comments/{root.uuid}",
        headers=auth(viewer_id),
    )
    assert root_delete.status_code == 204
    assert root_delete.content == b""
    listed = client.get(
        "/api/v1/comments",
        params={
            "targetType": "COMMUNITY_POST",
            "targetUuid": str(community_post.uuid),
        },
        headers=auth(viewer_id),
    ).json()
    assert listed["count"] == 1
    assert listed["items"][0]["comment"]["body"] == ""
    assert listed["items"][0]["comment"]["isDeleted"] is True
    assert listed["items"][0]["masked"] is False

    assert client.delete(
        f"/api/v1/comments/{reply.uuid}",
        headers=auth(viewer_id),
    ).status_code == 204
    listed = client.get(
        "/api/v1/comments",
        params={
            "targetType": "COMMUNITY_POST",
            "targetUuid": str(community_post.uuid),
        },
        headers=auth(viewer_id),
    ).json()
    assert listed == {"count": 0, "items": []}
    with Session(engine, expire_on_commit=False) as db:
        stored_root = db.get(Comment, root.id)
        stored_reply = db.get(Comment, reply.id)
        assert stored_root.content == "root secret"
        assert stored_root.deleted_at is not None
        assert stored_reply.deleted_at is not None
        assert stored_root.updated_at is None


def test_comment_delete_rejects_auth_other_missing_and_already_deleted(
    api: tuple[TestClient, sa.Engine, int],
) -> None:
    client, engine, viewer_id = api
    with Session(engine, expire_on_commit=False) as db:
        other = User(nickname="delete-other", region_id=1)
        db.add(other)
        db.flush()
        community_post = add_community_post(db, viewer_id, title="delete validation")
        db.flush()
        other_comment = Comment(
            target_type=CommentTargetType.COMMUNITY_POST,
            target_id=community_post.id,
            user_id=other.id,
            content="other",
        )
        deleted = Comment(
            target_type=CommentTargetType.COMMUNITY_POST,
            target_id=community_post.id,
            user_id=viewer_id,
            content="deleted",
            deleted_at=datetime.now(UTC),
        )
        db.add_all([other_comment, deleted])
        db.commit()

    endpoint = f"/api/v1/comments/{other_comment.uuid}"
    assert client.delete(endpoint).status_code == 401
    assert client.delete(endpoint, headers=auth(viewer_id)).status_code == 403
    assert client.delete(
        f"/api/v1/comments/{deleted.uuid}",
        headers=auth(viewer_id),
    ).status_code == 404
    assert client.delete(
        f"/api/v1/comments/{uuid4()}",
        headers=auth(viewer_id),
    ).status_code == 404
    with Session(engine, expire_on_commit=False) as db:
        assert db.get(Comment, other_comment.id).deleted_at is None


def test_comment_writes_are_independent_of_redis(
    api: tuple[TestClient, sa.Engine, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, engine, viewer_id = api
    with Session(engine, expire_on_commit=False) as db:
        community_post = add_community_post(db, viewer_id, title="redis free")
        db.commit()

    redis = get_redis_client()
    monkeypatch.setattr(
        redis,
        "pipeline",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RedisConnectionError("unavailable")
        ),
    )
    created = client.post(
        "/api/v1/comments",
        headers=auth(viewer_id),
        json={
            "targetType": "COMMUNITY_POST",
            "targetUuid": str(community_post.uuid),
            "body": "comment",
        },
    )
    assert created.status_code == 201
    with Session(engine, expire_on_commit=False) as db:
        comment = db.scalar(sa.select(Comment))
        comment_uuid = comment.uuid
    assert client.patch(
        f"/api/v1/comments/{comment_uuid}",
        headers=auth(viewer_id),
        json={"body": "updated"},
    ).status_code == 201
    assert client.delete(
        f"/api/v1/comments/{comment_uuid}",
        headers=auth(viewer_id),
    ).status_code == 204


def test_error_envelope_keeps_status_and_hides_404_body(
    api: tuple[TestClient, sa.Engine, int],
) -> None:
    client, _, viewer_id = api
    unauthenticated = client.get(list_url())
    assert unauthenticated.status_code == 401
    assert unauthenticated.json() == {"message": "로그인이 필요합니다."}

    invalid = client.get(
        "/api/v1/community-posts"
        "?regionName=seoul&boardName=invalid",
        headers=auth(viewer_id),
    )
    assert invalid.status_code == 422
    assert invalid.json() == {"message": "올바르지 않은 메뉴입니다."}

    missing = client.delete(
        f"/api/v1/community-posts/{uuid4()}",
        headers=auth(viewer_id),
    )
    assert missing.status_code == 404
    assert missing.json() == {}
