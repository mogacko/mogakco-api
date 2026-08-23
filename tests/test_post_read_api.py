import os
from collections.abc import Generator
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy.orm import Session

from app.database import get_db
from app.main import app
from app.models import Comment, CommentTargetType, Post, PostBoard, PostCategory, User
from app.redis_client import get_redis_client
from app.services.community import post_like_key

DATABASE_URL = os.environ["DATABASE_URL"]


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch) -> Generator[tuple[TestClient, sa.Engine, int]]:
    command.upgrade(Config("alembic.ini"), "head")
    engine = sa.create_engine(DATABASE_URL)
    redis = get_redis_client()
    redis.flushdb()
    with Session(engine) as db:
        db.execute(sa.delete(Comment))
        db.execute(sa.delete(Post))
        db.execute(sa.delete(User))
        viewer = User(nickname="viewer", region_id=1)
        db.add(viewer)
        db.commit()
        viewer_id = viewer.id

    def override_db() -> Generator[Session]:
        with Session(engine) as db:
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


def add_post(
    db: Session,
    author_id: int,
    *,
    title: str,
    board: PostBoard = PostBoard.QUESTION,
    category: PostCategory | None = None,
    created_at: datetime | None = None,
    deleted_at: datetime | None = None,
    region_id: int = 1,
    body: str = "body",
) -> Post:
    post = Post(
        author_id=author_id,
        region_id=region_id,
        board=board,
        category=category,
        title=title,
        body=body,
        created_at=created_at or datetime.now(UTC),
        deleted_at=deleted_at,
    )
    db.add(post)
    db.flush()
    return post


def auth(viewer_id: int) -> dict[str, str]:
    return {"X-Debug-User-Id": str(viewer_id)}


def test_list_posts_filters_paginates_and_returns_metadata(
    api: tuple[TestClient, sa.Engine, int],
) -> None:
    client, engine, viewer_id = api
    now = datetime.now(UTC)
    with Session(engine) as db:
        add_post(
            db,
            viewer_id,
            title="old free",
            board=PostBoard.TALK,
            category=PostCategory.FREE,
            created_at=now - timedelta(minutes=2),
        )
        newest = add_post(
            db,
            viewer_id,
            title="new free",
            board=PostBoard.TALK,
            category=PostCategory.FREE,
            created_at=now,
        )
        add_post(
            db,
            viewer_id,
            title="recruit",
            board=PostBoard.TALK,
            category=PostCategory.RECRUIT,
        )
        add_post(db, viewer_id, title="question")
        add_post(
            db,
            viewer_id,
            title="deleted",
            board=PostBoard.TALK,
            category=PostCategory.FREE,
            deleted_at=now,
        )
        db.commit()
        newest_id = newest.id

    response = client.get(
        "/api/v1/chapters/seoul/posts",
        params={"boardCode": "talk", "categoryCode": "free", "limit": 1},
        headers=auth(viewer_id),
    )

    assert response.status_code == 200
    body = response.json()
    assert [item["id"] for item in body["items"]] == [newest_id]
    assert body | {"items": []} == {
        "items": [],
        "offset": 0,
        "limit": 1,
        "total": 2,
        "hasMore": True,
        "boardTotal": 3,
        "categoryCounts": {"free": 2, "retrospective": 0, "recruit": 1},
    }
    assert client.get(
        "/api/v1/chapters/gyeonggi/posts", headers=auth(viewer_id)
    ).status_code == 404
    assert client.get(
        "/api/v1/chapters/unknown/posts", headers=auth(viewer_id)
    ).status_code == 404
    assert client.get(
        "/api/v1/chapters/seoul/posts",
        params={"categoryCode": "free"},
        headers=auth(viewer_id),
    ).status_code == 422
    assert client.get(
        "/api/v1/chapters/seoul/posts", params={"limit": 51}, headers=auth(viewer_id)
    ).status_code == 422


def test_post_detail_returns_counts_like_state_and_deleted_author_mask(
    api: tuple[TestClient, sa.Engine, int],
) -> None:
    client, engine, viewer_id = api
    with Session(engine) as db:
        deleted_author = User(
            nickname="gone", region_id=1, deleted_at=datetime.now(UTC)
        )
        db.add(deleted_author)
        db.flush()
        post = add_post(db, deleted_author.id, title="detail")
        hidden = add_post(
            db, viewer_id, title="hidden", deleted_at=datetime.now(UTC)
        )
        root = Comment(
            user_id=viewer_id,
            target_type=CommentTargetType.POST,
            target_id=post.id,
            content="root",
        )
        db.add(root)
        db.flush()
        db.add_all(
            [
                Comment(
                    user_id=viewer_id,
                    target_type=CommentTargetType.POST,
                    target_id=post.id,
                    parent_comment_id=root.id,
                    content="reply",
                ),
                Comment(
                    user_id=viewer_id,
                    target_type=CommentTargetType.POST,
                    target_id=post.id,
                    content="deleted",
                    deleted_at=datetime.now(UTC),
                ),
            ]
        )
        db.commit()
        post_id, hidden_id = post.id, hidden.id

    get_redis_client().sadd(post_like_key(post_id), viewer_id)

    response = client.get(f"/api/v1/posts/{post_id}", headers=auth(viewer_id))

    assert response.status_code == 200
    assert response.json() | {"createdAt": None} == {
        "id": post_id,
        "chapterCode": "seoul",
        "boardCode": "question",
        "categoryCode": None,
        "title": "detail",
        "body": "body",
        "authorId": None,
        "authorNickname": "탈퇴한 사용자",
        "authorAvatarUrl": None,
        "createdAt": None,
        "editedAt": None,
        "likeCount": 1,
        "commentCount": 2,
        "isLiked": True,
    }
    assert client.get(
        f"/api/v1/posts/{hidden_id}", headers=auth(viewer_id)
    ).status_code == 404


def test_search_matches_content_and_only_active_author_nickname(
    api: tuple[TestClient, sa.Engine, int],
) -> None:
    client, engine, viewer_id = api
    with Session(engine) as db:
        active = User(nickname="NeedleWriter", region_id=1)
        deleted = User(
            nickname="NeedleGone", region_id=1, deleted_at=datetime.now(UTC)
        )
        db.add_all([active, deleted])
        db.flush()
        add_post(db, viewer_id, title="NEEDLE title")
        add_post(db, viewer_id, title="body match", body="contains needle")
        add_post(db, active.id, title="author match")
        add_post(db, deleted.id, title="must not match")
        add_post(db, viewer_id, title="other region", region_id=2, body="needle")
        add_post(
            db,
            viewer_id,
            title="deleted needle",
            deleted_at=datetime.now(UTC),
        )
        db.commit()

    response = client.get(
        "/api/v1/chapters/seoul/posts/search",
        params={"q": "  needle  "},
        headers=auth(viewer_id),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 3
    assert {item["title"] for item in body["items"]} == {
        "NEEDLE title",
        "body match",
        "author match",
    }
    assert body["boardTotal"] is None
    assert body["categoryCounts"] is None
    assert client.get(
        "/api/v1/chapters/seoul/posts/search",
        params={"q": "   "},
        headers=auth(viewer_id),
    ).status_code == 422
    assert client.get(
        "/api/v1/chapters/seoul/posts/search",
        params={"q": "x" * 101},
        headers=auth(viewer_id),
    ).status_code == 422


def test_popular_posts_apply_score_window_exclusions_and_top_three(
    api: tuple[TestClient, sa.Engine, int],
) -> None:
    client, engine, viewer_id = api
    now = datetime.now(UTC)
    with Session(engine) as db:
        scored = [
            (add_post(db, viewer_id, title="redis-score", created_at=now), 0),
            (add_post(db, viewer_id, title="score24", created_at=now), 12),
            (add_post(db, viewer_id, title="score22", created_at=now), 11),
            (add_post(db, viewer_id, title="tie-new", created_at=now), 10),
            (
                add_post(
                    db,
                    viewer_id,
                    title="tie-old",
                    created_at=now - timedelta(minutes=1),
                ),
                10,
            ),
            (
                add_post(
                    db,
                    viewer_id,
                    title="notice",
                    board=PostBoard.NOTICE,
                    created_at=now,
                ),
                20,
            ),
            (
                add_post(
                    db,
                    viewer_id,
                    title="too-old",
                    created_at=now - timedelta(days=8),
                ),
                20,
            ),
            (
                add_post(
                    db,
                    viewer_id,
                    title="deleted",
                    created_at=now,
                    deleted_at=now,
                ),
                20,
            ),
        ]
        for post, count in scored:
            db.add_all(
                [
                    Comment(
                        user_id=viewer_id,
                        target_type=CommentTargetType.POST,
                        target_id=post.id,
                        content=f"comment-{index}",
                    )
                    for index in range(count)
                ]
            )
        db.commit()
        redis_score_id = next(
            post.id for post, _ in scored if post.title == "redis-score"
        )

    get_redis_client().sadd(post_like_key(redis_score_id), *range(100, 125))

    response = client.get(
        "/api/v1/chapters/seoul/posts/popular", headers=auth(viewer_id)
    )

    assert response.status_code == 200
    assert [item["title"] for item in response.json()["items"]] == [
        "redis-score",
        "score24",
        "score22",
    ]


def test_create_post_uses_current_user_path_region_and_validates_permissions(
    api: tuple[TestClient, sa.Engine, int],
) -> None:
    client, engine, viewer_id = api
    payload = {
        "boardCode": "talk",
        "categoryCode": "free",
        "title": f" {'t' * 60} ",
        "body": f" {'b' * 10_000} ",
    }

    response = client.post(
        "/api/v1/chapters/busan/posts",
        json=payload,
        headers=auth(viewer_id),
    )

    assert response.status_code == 201
    body = response.json()
    assert body["chapterCode"] == "busan"
    assert body["authorId"] == viewer_id
    assert body["boardCode"] == "talk"
    assert body["categoryCode"] == "free"
    assert body["title"] == payload["title"].strip()
    assert body["body"] == payload["body"].strip()
    with Session(engine) as db:
        created = db.get(Post, body["id"])
        assert created is not None
        assert created.author_id == viewer_id
        assert created.region_id == 2

    invalid_cases = [
        (
            "/api/v1/chapters/seoul/posts",
            {"boardCode": "notice", "title": "notice", "body": "body"},
            403,
        ),
        (
            "/api/v1/chapters/gyeonggi/posts",
            {"boardCode": "question", "title": "title", "body": "body"},
            404,
        ),
        (
            "/api/v1/chapters/unknown/posts",
            {"boardCode": "question", "title": "title", "body": "body"},
            404,
        ),
        (
            "/api/v1/chapters/seoul/posts",
            {
                "boardCode": "question",
                "categoryCode": "free",
                "title": "title",
                "body": "body",
            },
            422,
        ),
        (
            "/api/v1/chapters/seoul/posts",
            {"boardCode": "talk", "title": "title", "body": "body"},
            422,
        ),
        (
            "/api/v1/chapters/seoul/posts",
            {"boardCode": "question", "title": "x" * 61, "body": "body"},
            422,
        ),
        (
            "/api/v1/chapters/seoul/posts",
            {"boardCode": "question", "title": "title", "body": "x" * 10_001},
            422,
        ),
    ]
    for path, invalid_payload, expected_status in invalid_cases:
        assert (
            client.post(path, json=invalid_payload, headers=auth(viewer_id)).status_code
            == expected_status
        )
    assert client.post(
        "/api/v1/chapters/seoul/posts",
        json={"boardCode": "question", "title": "title", "body": "body"},
    ).status_code == 401
    with Session(engine) as db:
        assert db.scalar(sa.select(sa.func.count()).select_from(Post)) == 1


def test_update_post_is_partial_owner_only_and_preserves_board_region(
    api: tuple[TestClient, sa.Engine, int],
) -> None:
    client, engine, viewer_id = api
    with Session(engine) as db:
        other = User(nickname="other", region_id=1)
        db.add(other)
        db.flush()
        owned = add_post(
            db,
            viewer_id,
            title="before",
            board=PostBoard.TALK,
            category=PostCategory.FREE,
            region_id=2,
        )
        foreign = add_post(db, other.id, title="foreign")
        deleted = add_post(
            db, viewer_id, title="deleted", deleted_at=datetime.now(UTC)
        )
        db.commit()
        owned_id, foreign_id, deleted_id = owned.id, foreign.id, deleted.id

    response = client.patch(
        f"/api/v1/posts/{owned_id}",
        json={"title": "  after  ", "categoryCode": "recruit"},
        headers=auth(viewer_id),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["title"] == "after"
    assert body["body"] == "body"
    assert body["categoryCode"] == "recruit"
    assert body["boardCode"] == "talk"
    assert body["chapterCode"] == "busan"
    assert body["editedAt"] is not None
    assert client.patch(
        f"/api/v1/posts/{foreign_id}",
        json={"title": "blocked"},
        headers=auth(viewer_id),
    ).status_code == 403
    assert client.patch(
        f"/api/v1/posts/{owned_id}",
        json={"categoryCode": None},
        headers=auth(viewer_id),
    ).status_code == 422
    assert client.patch(
        f"/api/v1/posts/{deleted_id}",
        json={"title": "blocked"},
        headers=auth(viewer_id),
    ).status_code == 404
    assert client.patch(
        f"/api/v1/posts/{owned_id}", json={}, headers=auth(viewer_id)
    ).status_code == 422
    assert client.patch(
        f"/api/v1/posts/{owned_id}",
        json={"boardCode": "question"},
        headers=auth(viewer_id),
    ).status_code == 422


def test_delete_post_soft_deletes_cleans_likes_and_keeps_comments(
    api: tuple[TestClient, sa.Engine, int],
) -> None:
    client, engine, viewer_id = api
    with Session(engine) as db:
        other = User(nickname="other", region_id=1)
        db.add(other)
        db.flush()
        owned = add_post(db, viewer_id, title="owned")
        foreign = add_post(db, other.id, title="foreign")
        db.add(
            Comment(
                user_id=viewer_id,
                target_type=CommentTargetType.POST,
                target_id=owned.id,
                content="kept",
            )
        )
        db.commit()
        owned_id, foreign_id = owned.id, foreign.id

    redis = get_redis_client()
    redis.sadd(post_like_key(owned_id), viewer_id)

    assert client.delete(
        f"/api/v1/posts/{foreign_id}", headers=auth(viewer_id)
    ).status_code == 403
    response = client.delete(
        f"/api/v1/posts/{owned_id}", headers=auth(viewer_id)
    )

    assert response.status_code == 204
    assert response.content == b""
    assert client.get(
        f"/api/v1/posts/{owned_id}", headers=auth(viewer_id)
    ).status_code == 404
    assert client.delete(
        f"/api/v1/posts/{owned_id}", headers=auth(viewer_id)
    ).status_code == 404
    with Session(engine) as db:
        assert db.get(Post, owned_id).deleted_at is not None
        assert db.get(Post, foreign_id).deleted_at is None
        assert db.scalar(
            sa.select(sa.func.count())
            .select_from(Comment)
            .where(
                Comment.target_type == CommentTargetType.POST,
                Comment.target_id == owned_id,
            )
        ) == 1
    assert redis.exists(post_like_key(owned_id)) == 0


def test_like_post_is_idempotent_and_updates_list_and_detail(
    api: tuple[TestClient, sa.Engine, int],
) -> None:
    client, engine, viewer_id = api
    with Session(engine) as db:
        other = User(nickname="other", region_id=1)
        db.add(other)
        db.flush()
        post = add_post(db, viewer_id, title="liked")
        db.commit()
        post_id, other_id = post.id, other.id

    redis = get_redis_client()
    redis.sadd(post_like_key(post_id), other_id)
    assert (
        "requestBody"
        not in app.openapi()["paths"]["/api/v1/posts/{postId}/likes"]["post"]
    )

    first = client.post(
        f"/api/v1/posts/{post_id}/likes",
        json={"userId": other_id},
        headers=auth(viewer_id),
    )
    second = client.post(
        f"/api/v1/posts/{post_id}/likes", headers=auth(viewer_id)
    )

    assert first.status_code == 200
    assert first.json() == {"likeCount": 2, "isLiked": True}
    assert second.status_code == 200
    assert second.json() == {"likeCount": 2, "isLiked": True}
    assert redis.smembers(post_like_key(post_id)) == {str(viewer_id), str(other_id)}

    listed = client.get(
        "/api/v1/chapters/seoul/posts", headers=auth(viewer_id)
    ).json()["items"][0]
    detailed = client.get(
        f"/api/v1/posts/{post_id}", headers=auth(viewer_id)
    ).json()
    assert (listed["likeCount"], listed["isLiked"]) == (2, True)
    assert (detailed["likeCount"], detailed["isLiked"]) == (2, True)


def test_unlike_post_is_idempotent_and_preserves_other_users_like(
    api: tuple[TestClient, sa.Engine, int],
) -> None:
    client, engine, viewer_id = api
    with Session(engine) as db:
        other = User(nickname="other", region_id=1)
        db.add(other)
        db.flush()
        post = add_post(db, viewer_id, title="unliked")
        db.commit()
        post_id, other_id = post.id, other.id

    redis = get_redis_client()
    redis.sadd(post_like_key(post_id), viewer_id, other_id)

    first = client.delete(
        f"/api/v1/posts/{post_id}/likes", headers=auth(viewer_id)
    )
    second = client.delete(
        f"/api/v1/posts/{post_id}/likes", headers=auth(viewer_id)
    )

    assert first.status_code == 200
    assert first.json() == {"likeCount": 1, "isLiked": False}
    assert second.status_code == 200
    assert second.json() == {"likeCount": 1, "isLiked": False}
    assert redis.smembers(post_like_key(post_id)) == {str(other_id)}

    listed = client.get(
        "/api/v1/chapters/seoul/posts", headers=auth(viewer_id)
    ).json()["items"][0]
    detailed = client.get(
        f"/api/v1/posts/{post_id}", headers=auth(viewer_id)
    ).json()
    assert (listed["likeCount"], listed["isLiked"]) == (1, False)
    assert (detailed["likeCount"], detailed["isLiked"]) == (1, False)


def test_like_mutations_reject_missing_deleted_posts_and_invalid_auth(
    api: tuple[TestClient, sa.Engine, int],
) -> None:
    client, engine, viewer_id = api
    with Session(engine) as db:
        active = add_post(db, viewer_id, title="active")
        deleted = add_post(
            db, viewer_id, title="deleted", deleted_at=datetime.now(UTC)
        )
        db.commit()
        active_id, deleted_id = active.id, deleted.id

    redis = get_redis_client()
    redis.sadd(post_like_key(deleted_id), viewer_id)

    for method in (client.post, client.delete):
        assert method(
            "/api/v1/posts/999999/likes", headers=auth(viewer_id)
        ).status_code == 404
        assert method(
            f"/api/v1/posts/{deleted_id}/likes", headers=auth(viewer_id)
        ).status_code == 404

    assert client.post(f"/api/v1/posts/{active_id}/likes").status_code == 401
    assert redis.exists(post_like_key(active_id)) == 0
    assert redis.smembers(post_like_key(deleted_id)) == {str(viewer_id)}


def test_list_batches_redis_like_stats_in_one_pipeline(
    api: tuple[TestClient, sa.Engine, int], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, engine, viewer_id = api
    with Session(engine) as db:
        posts = [
            add_post(db, viewer_id, title=f"post-{index}") for index in range(3)
        ]
        db.commit()
        post_ids = [post.id for post in posts]

    redis = get_redis_client()
    for post_id in post_ids:
        redis.sadd(post_like_key(post_id), viewer_id)
    original_pipeline = redis.pipeline
    pipeline_calls: list[bool] = []

    def tracked_pipeline(*, transaction: bool = True):
        pipeline_calls.append(transaction)
        return original_pipeline(transaction=transaction)

    monkeypatch.setattr(redis, "pipeline", tracked_pipeline)
    response = client.get(
        "/api/v1/chapters/seoul/posts", headers=auth(viewer_id)
    )

    assert response.status_code == 200
    assert pipeline_calls == [False]
    assert {
        (item["id"], item["likeCount"], item["isLiked"])
        for item in response.json()["items"]
    } == {(post_id, 1, True) for post_id in post_ids}


def test_redis_failure_returns_service_unavailable(
    api: tuple[TestClient, sa.Engine, int], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, engine, viewer_id = api
    with Session(engine) as db:
        post = add_post(db, viewer_id, title="redis unavailable")
        db.commit()
        post_id = post.id

    def failed_pipeline(*, transaction: bool = True):
        raise RedisConnectionError("unavailable")

    monkeypatch.setattr(get_redis_client(), "pipeline", failed_pipeline)

    response = client.get(f"/api/v1/posts/{post_id}", headers=auth(viewer_id))

    assert response.status_code == 503
    assert response.json() == {"detail": "Like service unavailable"}


def test_redis_failure_rolls_back_post_writes_but_not_delete_cleanup(
    api: tuple[TestClient, sa.Engine, int], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, engine, viewer_id = api
    with Session(engine) as db:
        post = add_post(db, viewer_id, title="original")
        db.commit()
        post_id = post.id

    def failed_pipeline(*, transaction: bool = True):
        raise RedisConnectionError("unavailable")

    redis = get_redis_client()
    monkeypatch.setattr(redis, "pipeline", failed_pipeline)

    create_response = client.post(
        "/api/v1/chapters/seoul/posts",
        json={"boardCode": "question", "title": "rolled back", "body": "body"},
        headers=auth(viewer_id),
    )
    update_response = client.patch(
        f"/api/v1/posts/{post_id}",
        json={"title": "also rolled back"},
        headers=auth(viewer_id),
    )

    assert create_response.status_code == 503
    assert update_response.status_code == 503
    with Session(engine) as db:
        assert db.scalar(
            sa.select(sa.func.count()).where(Post.title == "rolled back")
        ) == 0
        unchanged = db.get(Post, post_id)
        assert unchanged.title == "original"
        assert unchanged.edited_at is None

    like_key = post_like_key(post_id)
    redis.sadd(like_key, viewer_id)

    def failed_delete(*_keys: str) -> None:
        raise RedisConnectionError("unavailable")

    monkeypatch.setattr(redis, "delete", failed_delete)
    delete_response = client.delete(
        f"/api/v1/posts/{post_id}", headers=auth(viewer_id)
    )

    assert delete_response.status_code == 204
    assert redis.exists(like_key) == 1
    with Session(engine) as db:
        assert db.get(Post, post_id).deleted_at is not None


def test_list_comment_threads_maps_dto_masks_deletions_and_avoids_n_plus_one(
    api: tuple[TestClient, sa.Engine, int], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, engine, viewer_id = api
    base = datetime(2026, 8, 15, tzinfo=UTC)
    with Session(engine) as db:
        other = User(nickname="other", region_id=1)
        withdrawn = User(
            nickname="withdrawn", region_id=1, deleted_at=base
        )
        removed = User(nickname="removed", region_id=1)
        db.add_all([other, withdrawn, removed])
        db.flush()
        post = add_post(db, viewer_id, title="comments")
        other_post = add_post(db, viewer_id, title="other post")
        db.flush()

        root = Comment(
            target_type=CommentTargetType.POST,
            target_id=post.id,
            user_id=other.id,
            content="root",
            created_at=base,
            updated_at=base + timedelta(minutes=1),
        )
        db.add(root)
        db.flush()
        other_reply = Comment(
            target_type=CommentTargetType.POST,
            target_id=post.id,
            parent_comment_id=root.id,
            user_id=other.id,
            content="other reply",
            created_at=base + timedelta(minutes=2),
        )
        db.add(other_reply)
        db.flush()
        mine_reply = Comment(
            target_type=CommentTargetType.POST,
            target_id=post.id,
            parent_comment_id=root.id,
            user_id=viewer_id,
            content="mine reply",
            created_at=base + timedelta(minutes=2),
        )
        deleted_reply = Comment(
            target_type=CommentTargetType.POST,
            target_id=post.id,
            parent_comment_id=root.id,
            user_id=viewer_id,
            content="deleted reply secret",
            created_at=base + timedelta(minutes=3),
            deleted_at=base + timedelta(minutes=4),
        )
        db.add_all([mine_reply, deleted_reply])
        db.flush()
        depth_two = Comment(
            target_type=CommentTargetType.POST,
            target_id=post.id,
            parent_comment_id=other_reply.id,
            user_id=viewer_id,
            content="invalid depth two",
            created_at=base + timedelta(minutes=4),
        )
        deleted_root = Comment(
            target_type=CommentTargetType.POST,
            target_id=post.id,
            user_id=other.id,
            content="deleted root secret",
            created_at=base + timedelta(minutes=5),
            deleted_at=base + timedelta(minutes=6),
        )
        db.add_all([depth_two, deleted_root])
        db.flush()
        deleted_root_reply = Comment(
            target_type=CommentTargetType.POST,
            target_id=post.id,
            parent_comment_id=deleted_root.id,
            user_id=viewer_id,
            content="visible reply",
            created_at=base + timedelta(minutes=7),
        )
        empty_deleted_root = Comment(
            target_type=CommentTargetType.POST,
            target_id=post.id,
            user_id=other.id,
            content="hidden thread",
            created_at=base + timedelta(minutes=8),
            deleted_at=base + timedelta(minutes=9),
        )
        withdrawn_root = Comment(
            target_type=CommentTargetType.POST,
            target_id=post.id,
            user_id=withdrawn.id,
            content="withdrawn author",
            created_at=base + timedelta(minutes=10),
        )
        removed_root = Comment(
            target_type=CommentTargetType.POST,
            target_id=post.id,
            user_id=removed.id,
            content="removed author",
            created_at=base + timedelta(minutes=11),
        )
        unrelated = Comment(
            target_type=CommentTargetType.POST,
            target_id=other_post.id,
            user_id=viewer_id,
            content="unrelated",
            created_at=base,
        )
        db.add_all(
            [
                deleted_root_reply,
                empty_deleted_root,
                withdrawn_root,
                removed_root,
                unrelated,
            ]
        )
        db.flush()
        ids = {
            "post": post.id,
            "other": other.id,
            "root": root.id,
            "other_reply": other_reply.id,
            "mine_reply": mine_reply.id,
            "deleted_reply": deleted_reply.id,
            "depth_two": depth_two.id,
            "deleted_root": deleted_root.id,
            "empty_deleted_root": empty_deleted_root.id,
            "withdrawn_root": withdrawn_root.id,
            "removed_root": removed_root.id,
        }
        db.delete(removed)
        db.commit()

    redis = get_redis_client()
    original_pipeline = redis.pipeline

    def failed_pipeline(*, transaction: bool = True):
        raise RedisConnectionError("unavailable")

    monkeypatch.setattr(redis, "pipeline", failed_pipeline)
    select_count = 0

    def count_selects(
        conn, cursor, statement, parameters, context, executemany
    ) -> None:
        nonlocal select_count
        if statement.lstrip().upper().startswith("SELECT"):
            select_count += 1

    sa.event.listen(engine, "before_cursor_execute", count_selects)
    try:
        response = client.get(
            f"/api/v1/comments?targetType=post&targetId={ids['post']}",
            headers=auth(viewer_id),
        )
    finally:
        sa.event.remove(engine, "before_cursor_execute", count_selects)

    assert response.status_code == 200
    assert select_count == 3  # Debug Auth, target validation, comments + authors.
    payload = response.json()
    assert payload["count"] == 6
    assert [item["comment"]["id"] for item in payload["items"]] == [
        ids["root"],
        ids["deleted_root"],
        ids["withdrawn_root"],
        ids["removed_root"],
    ]

    first = payload["items"][0]
    assert first["masked"] is False
    assert first["comment"] == {
        "id": ids["root"],
        "targetType": "post",
        "targetId": ids["post"],
        "parentId": None,
        "authorId": ids["other"],
        "authorNickname": "other",
        "authorAvatarUrl": None,
        "body": "root",
        "createdAt": base.isoformat().replace("+00:00", "Z"),
        "editedAt": (base + timedelta(minutes=1)).isoformat().replace(
            "+00:00", "Z"
        ),
        "isDeleted": False,
        "isMine": False,
    }
    assert [reply["id"] for reply in first["replies"]] == [
        ids["other_reply"],
        ids["mine_reply"],
    ]
    assert [reply["isMine"] for reply in first["replies"]] == [False, True]

    tombstone = payload["items"][1]
    assert tombstone["masked"] is True
    assert tombstone["comment"]["isDeleted"] is True
    assert tombstone["comment"]["body"] == ""
    assert [reply["body"] for reply in tombstone["replies"]] == [
        "visible reply"
    ]

    for item in payload["items"][2:]:
        assert item["comment"]["authorId"] is None
        assert item["comment"]["authorNickname"] == "탈퇴한 사용자"
        assert item["comment"]["authorAvatarUrl"] is None
        assert item["comment"]["isMine"] is False

    response_text = response.text
    assert "deleted root secret" not in response_text
    assert "deleted reply secret" not in response_text
    assert "invalid depth two" not in response_text
    assert str(ids["empty_deleted_root"]) not in {
        str(item["comment"]["id"]) for item in payload["items"]
    }

    monkeypatch.setattr(redis, "pipeline", original_pipeline)
    detail = client.get(
        f"/api/v1/posts/{ids['post']}", headers=auth(viewer_id)
    )
    assert detail.status_code == 200
    assert detail.json()["commentCount"] == 6


def test_list_comments_validates_target_query_auth_and_post_comment_count(
    api: tuple[TestClient, sa.Engine, int]
) -> None:
    client, engine, viewer_id = api
    now = datetime.now(UTC)
    with Session(engine) as db:
        active = add_post(db, viewer_id, title="active")
        deleted = add_post(
            db, viewer_id, title="deleted", deleted_at=now
        )
        db.flush()
        root = Comment(
            target_type=CommentTargetType.POST,
            target_id=active.id,
            user_id=viewer_id,
            content="root",
        )
        db.add(root)
        db.flush()
        reply = Comment(
            target_type=CommentTargetType.POST,
            target_id=active.id,
            parent_comment_id=root.id,
            user_id=viewer_id,
            content="reply",
        )
        deleted_reply = Comment(
            target_type=CommentTargetType.POST,
            target_id=active.id,
            parent_comment_id=root.id,
            user_id=viewer_id,
            content="deleted",
            deleted_at=now,
        )
        db.add_all([reply, deleted_reply])
        db.commit()
        active_id, deleted_id = active.id, deleted.id

    endpoint = f"/api/v1/comments?targetType=post&targetId={active_id}"
    response = client.get(endpoint, headers=auth(viewer_id))
    assert response.status_code == 200
    assert response.json()["count"] == 2
    assert client.get(endpoint).status_code == 401
    assert client.get(
        "/api/v1/comments?targetType=post&targetId=999999",
        headers=auth(viewer_id),
    ).status_code == 404
    assert client.get(
        f"/api/v1/comments?targetType=post&targetId={deleted_id}",
        headers=auth(viewer_id),
    ).status_code == 404
    for target_type in ("event", "meetup"):
        assert client.get(
            f"/api/v1/comments?targetType={target_type}&targetId=1",
            headers=auth(viewer_id),
        ).status_code == 404
    for invalid_query in (
        f"targetId={active_id}",
        "targetType=post",
        f"targetType=invalid&targetId={active_id}",
        "targetType=post&targetId=0",
    ):
        assert client.get(
            f"/api/v1/comments?{invalid_query}", headers=auth(viewer_id)
        ).status_code == 422

    detail = client.get(
        f"/api/v1/posts/{active_id}", headers=auth(viewer_id)
    )
    assert detail.status_code == 200
    assert detail.json()["commentCount"] == 2


def test_create_root_and_reply_returns_dto_and_updates_reads(
    api: tuple[TestClient, sa.Engine, int], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, engine, viewer_id = api
    with Session(engine) as db:
        post = add_post(db, viewer_id, title="comment target")
        db.commit()
        post_id = post.id

    redis = get_redis_client()
    original_pipeline = redis.pipeline
    before = client.get(f"/api/v1/posts/{post_id}", headers=auth(viewer_id))
    assert before.status_code == 200
    assert before.json()["commentCount"] == 0

    def failed_pipeline(*, transaction: bool = True):
        raise RedisConnectionError("unavailable")

    monkeypatch.setattr(redis, "pipeline", failed_pipeline)
    root_response = client.post(
        "/api/v1/comments",
        json={"targetType": "post", "targetId": post_id, "body": "  x  "},
        headers=auth(viewer_id),
    )
    assert root_response.status_code == 201
    root = root_response.json()
    assert root == {
        "id": root["id"],
        "targetType": "post",
        "targetId": post_id,
        "parentId": None,
        "authorId": viewer_id,
        "authorNickname": "viewer",
        "authorAvatarUrl": None,
        "body": "x",
        "createdAt": root["createdAt"],
        "editedAt": None,
        "isDeleted": False,
        "isMine": True,
    }

    monkeypatch.setattr(redis, "pipeline", original_pipeline)
    after_root = client.get(
        f"/api/v1/posts/{post_id}", headers=auth(viewer_id)
    )
    assert after_root.status_code == 200
    assert after_root.json()["commentCount"] == 1
    monkeypatch.setattr(redis, "pipeline", failed_pipeline)

    reply_body = "r" * 300
    reply_response = client.post(
        "/api/v1/comments",
        json={
            "targetType": "post",
            "targetId": post_id,
            "parentId": root["id"],
            "body": reply_body,
        },
        headers=auth(viewer_id),
    )
    assert reply_response.status_code == 201
    reply = reply_response.json()
    assert reply["parentId"] == root["id"]
    assert reply["body"] == reply_body
    assert reply["authorId"] == viewer_id
    assert reply["editedAt"] is None
    assert reply["isDeleted"] is False
    assert reply["isMine"] is True

    with Session(engine) as db:
        rows = db.scalars(
            sa.select(Comment)
            .where(Comment.target_id == post_id)
            .order_by(Comment.id)
        ).all()
        assert len(rows) == 2
        assert rows[0].user_id == viewer_id
        assert rows[0].content == "x"
        assert rows[0].parent_comment_id is None
        assert rows[0].uuid is not None
        assert rows[0].updated_at is None
        assert rows[0].deleted_at is None
        assert rows[1].parent_comment_id == rows[0].id

    listed = client.get(
        f"/api/v1/comments?targetType=post&targetId={post_id}",
        headers=auth(viewer_id),
    )
    assert listed.status_code == 200
    assert listed.json()["count"] == 2
    assert listed.json()["items"][0]["comment"]["id"] == root["id"]
    assert listed.json()["items"][0]["replies"][0]["id"] == reply["id"]

    monkeypatch.setattr(redis, "pipeline", original_pipeline)
    detail = client.get(f"/api/v1/posts/{post_id}", headers=auth(viewer_id))
    assert detail.status_code == 200
    assert detail.json()["commentCount"] == 2


def test_create_reply_rejects_missing_deleted_mismatched_and_reply_parent(
    api: tuple[TestClient, sa.Engine, int]
) -> None:
    client, engine, viewer_id = api
    now = datetime.now(UTC)
    with Session(engine) as db:
        first_post = add_post(db, viewer_id, title="first")
        second_post = add_post(db, viewer_id, title="second")
        db.flush()
        root = Comment(
            target_type=CommentTargetType.POST,
            target_id=first_post.id,
            user_id=viewer_id,
            content="root",
        )
        deleted_root = Comment(
            target_type=CommentTargetType.POST,
            target_id=first_post.id,
            user_id=viewer_id,
            content="deleted",
            deleted_at=now,
        )
        other_post_root = Comment(
            target_type=CommentTargetType.POST,
            target_id=second_post.id,
            user_id=viewer_id,
            content="other post",
        )
        other_type_root = Comment(
            target_type=CommentTargetType.EVENT,
            target_id=first_post.id,
            user_id=viewer_id,
            content="other type",
        )
        db.add_all([root, deleted_root, other_post_root, other_type_root])
        db.flush()
        reply = Comment(
            target_type=CommentTargetType.POST,
            target_id=first_post.id,
            parent_comment_id=root.id,
            user_id=viewer_id,
            content="reply",
        )
        db.add(reply)
        db.commit()
        ids = {
            "post": first_post.id,
            "deleted": deleted_root.id,
            "other_post": other_post_root.id,
            "other_type": other_type_root.id,
            "reply": reply.id,
        }

    before = 5
    cases = (
        (999999, 404),
        (ids["deleted"], 404),
        (ids["other_post"], 422),
        (ids["other_type"], 422),
        (ids["reply"], 422),
    )
    for parent_id, expected_status in cases:
        response = client.post(
            "/api/v1/comments",
            json={
                "targetType": "post",
                "targetId": ids["post"],
                "parentId": parent_id,
                "body": "must fail",
            },
            headers=auth(viewer_id),
        )
        assert response.status_code == expected_status
        with Session(engine) as db:
            assert db.scalar(
                sa.select(sa.func.count()).select_from(Comment)
            ) == before


def test_create_comment_validates_target_body_auth_and_spoofed_author(
    api: tuple[TestClient, sa.Engine, int]
) -> None:
    client, engine, viewer_id = api
    with Session(engine) as db:
        active = add_post(db, viewer_id, title="active")
        deleted = add_post(
            db, viewer_id, title="deleted", deleted_at=datetime.now(UTC)
        )
        db.commit()
        active_id, deleted_id = active.id, deleted.id

    valid = {"targetType": "post", "targetId": active_id, "body": "body"}
    assert client.post("/api/v1/comments", json=valid).status_code == 401
    assert client.post(
        "/api/v1/comments",
        json=valid,
        headers={"X-Debug-User-Id": "invalid"},
    ).status_code == 401

    for payload, expected_status in (
        ({**valid, "targetId": 999999}, 404),
        ({**valid, "targetId": deleted_id}, 404),
        ({**valid, "targetType": "event"}, 404),
        ({**valid, "targetType": "meetup"}, 404),
        ({**valid, "body": "   "}, 422),
        ({**valid, "body": "x" * 301}, 422),
        ({**valid, "userId": viewer_id}, 422),
        ({**valid, "authorId": viewer_id}, 422),
    ):
        assert client.post(
            "/api/v1/comments",
            json=payload,
            headers=auth(viewer_id),
        ).status_code == expected_status

    schema = app.openapi()["components"]["schemas"]["CommentCreateRequest"]
    assert set(schema["properties"]) == {
        "targetType",
        "targetId",
        "parentId",
        "body",
    }
    with Session(engine) as db:
        assert db.scalar(sa.select(sa.func.count()).select_from(Comment)) == 0


def test_update_root_and_reply_reuses_dto_and_preserves_thread_and_count(
    api: tuple[TestClient, sa.Engine, int], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, engine, viewer_id = api
    base = datetime(2026, 8, 15, tzinfo=UTC)
    with Session(engine) as db:
        post = add_post(db, viewer_id, title="update comments")
        db.flush()
        root = Comment(
            target_type=CommentTargetType.POST,
            target_id=post.id,
            user_id=viewer_id,
            content="root before",
            created_at=base,
            updated_at=base,
        )
        db.add(root)
        db.flush()
        reply = Comment(
            target_type=CommentTargetType.POST,
            target_id=post.id,
            parent_comment_id=root.id,
            user_id=viewer_id,
            content="reply before",
            created_at=base + timedelta(minutes=1),
            updated_at=base,
        )
        db.add(reply)
        db.flush()
        ids = {"post": post.id, "root": root.id, "reply": reply.id}
        immutable = {
            "root_uuid": root.uuid,
            "reply_uuid": reply.uuid,
            "root_created_at": root.created_at,
            "reply_created_at": reply.created_at,
        }
        db.commit()

    redis = get_redis_client()
    original_pipeline = redis.pipeline
    before = client.get(
        f"/api/v1/posts/{ids['post']}", headers=auth(viewer_id)
    )
    assert before.status_code == 200
    assert before.json()["commentCount"] == 2

    def failed_pipeline(*, transaction: bool = True):
        raise RedisConnectionError("unavailable")

    monkeypatch.setattr(redis, "pipeline", failed_pipeline)
    root_response = client.patch(
        f"/api/v1/comments/{ids['root']}",
        json={"body": "  x  "},
        headers=auth(viewer_id),
    )
    reply_body = "r" * 300
    reply_response = client.patch(
        f"/api/v1/comments/{ids['reply']}",
        json={"body": reply_body},
        headers=auth(viewer_id),
    )

    assert root_response.status_code == 200
    assert reply_response.status_code == 200
    root_payload = root_response.json()
    reply_payload = reply_response.json()
    assert root_payload["body"] == "x"
    assert root_payload["parentId"] is None
    assert reply_payload["body"] == reply_body
    assert reply_payload["parentId"] == ids["root"]
    for payload in (root_payload, reply_payload):
        assert payload["targetType"] == "post"
        assert payload["targetId"] == ids["post"]
        assert payload["authorId"] == viewer_id
        assert payload["authorNickname"] == "viewer"
        assert payload["editedAt"] is not None
        assert payload["isDeleted"] is False
        assert payload["isMine"] is True

    same_body = client.patch(
        f"/api/v1/comments/{ids['root']}",
        json={"body": "x"},
        headers=auth(viewer_id),
    )
    assert same_body.status_code == 200
    assert datetime.fromisoformat(same_body.json()["editedAt"]) > datetime.fromisoformat(
        root_payload["editedAt"]
    )

    with Session(engine) as db:
        stored_root = db.get(Comment, ids["root"])
        stored_reply = db.get(Comment, ids["reply"])
        assert stored_root.content == "x"
        assert stored_reply.content == reply_body
        assert stored_root.uuid == immutable["root_uuid"]
        assert stored_reply.uuid == immutable["reply_uuid"]
        assert stored_root.created_at == immutable["root_created_at"]
        assert stored_reply.created_at == immutable["reply_created_at"]
        assert stored_root.parent_comment_id is None
        assert stored_reply.parent_comment_id == ids["root"]
        assert stored_root.target_id == stored_reply.target_id == ids["post"]
        assert stored_root.user_id == stored_reply.user_id == viewer_id
        assert stored_root.deleted_at is None
        assert stored_reply.deleted_at is None

    listed = client.get(
        f"/api/v1/comments?targetType=post&targetId={ids['post']}",
        headers=auth(viewer_id),
    )
    assert listed.status_code == 200
    assert listed.json()["count"] == 2
    thread = listed.json()["items"][0]
    assert thread["comment"]["body"] == "x"
    assert thread["comment"]["editedAt"] == same_body.json()["editedAt"]
    assert thread["replies"][0]["body"] == reply_body
    assert thread["replies"][0]["editedAt"] == reply_payload["editedAt"]

    monkeypatch.setattr(redis, "pipeline", original_pipeline)
    after = client.get(
        f"/api/v1/posts/{ids['post']}", headers=auth(viewer_id)
    )
    assert after.status_code == 200
    assert after.json()["commentCount"] == 2


def test_update_comment_rejects_missing_deleted_other_and_deleted_author(
    api: tuple[TestClient, sa.Engine, int]
) -> None:
    client, engine, viewer_id = api
    now = datetime.now(UTC)
    with Session(engine) as db:
        other = User(nickname="other", region_id=1)
        removed = User(nickname="removed", region_id=1)
        db.add_all([other, removed])
        db.flush()
        post = add_post(db, viewer_id, title="protected comments")
        db.flush()
        other_comment = Comment(
            target_type=CommentTargetType.POST,
            target_id=post.id,
            user_id=other.id,
            content="other original",
        )
        deleted_comment = Comment(
            target_type=CommentTargetType.POST,
            target_id=post.id,
            user_id=viewer_id,
            content="deleted original",
            deleted_at=now,
        )
        removed_author_comment = Comment(
            target_type=CommentTargetType.POST,
            target_id=post.id,
            user_id=removed.id,
            content="removed original",
        )
        db.add_all([other_comment, deleted_comment, removed_author_comment])
        db.flush()
        ids = {
            "other": other_comment.id,
            "deleted": deleted_comment.id,
            "removed": removed_author_comment.id,
        }
        db.delete(removed)
        db.commit()

    for comment_id, expected_status in (
        (999999, 404),
        (ids["deleted"], 404),
        (ids["other"], 403),
        (ids["removed"], 403),
    ):
        response = client.patch(
            f"/api/v1/comments/{comment_id}",
            json={"body": "must fail"},
            headers=auth(viewer_id),
        )
        assert response.status_code == expected_status

    with Session(engine) as db:
        assert db.get(Comment, ids["other"]).content == "other original"
        assert db.get(Comment, ids["deleted"]).content == "deleted original"
        removed_comment = db.get(Comment, ids["removed"])
        assert removed_comment.content == "removed original"
        assert removed_comment.user_id is None


def test_update_comment_validates_body_auth_and_extra_fields(
    api: tuple[TestClient, sa.Engine, int]
) -> None:
    client, engine, viewer_id = api
    with Session(engine) as db:
        post = add_post(db, viewer_id, title="validation")
        db.flush()
        comment = Comment(
            target_type=CommentTargetType.POST,
            target_id=post.id,
            user_id=viewer_id,
            content="original",
        )
        db.add(comment)
        db.commit()
        comment_id = comment.id

    endpoint = f"/api/v1/comments/{comment_id}"
    assert client.patch(endpoint, json={"body": "x"}).status_code == 401
    assert client.patch(
        endpoint,
        json={"body": "x"},
        headers={"X-Debug-User-Id": "invalid"},
    ).status_code == 401
    assert client.patch(
        "/api/v1/comments/not-an-integer",
        json={"body": "x"},
        headers=auth(viewer_id),
    ).status_code == 422

    invalid_payloads = (
        {},
        {"body": "   "},
        {"body": "x" * 301},
        {"body": "x", "targetType": "post"},
        {"body": "x", "targetId": 1},
        {"body": "x", "parentId": 1},
        {"body": "x", "authorId": viewer_id},
        {"body": "x", "userId": viewer_id},
    )
    for payload in invalid_payloads:
        assert client.patch(
            endpoint, json=payload, headers=auth(viewer_id)
        ).status_code == 422

    schema = app.openapi()["components"]["schemas"]["CommentUpdateRequest"]
    assert set(schema["properties"]) == {"body"}
    with Session(engine) as db:
        unchanged = db.get(Comment, comment_id)
        assert unchanged.content == "original"
        assert unchanged.updated_at is None


def test_delete_root_soft_deletes_preserves_data_and_updates_counts(
    api: tuple[TestClient, sa.Engine, int], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, engine, viewer_id = api
    created_at = datetime.now(UTC) - timedelta(minutes=1)
    updated_at = created_at + timedelta(seconds=1)
    with Session(engine) as db:
        post = add_post(db, viewer_id, title="delete root")
        db.flush()
        root = Comment(
            target_type=CommentTargetType.POST,
            target_id=post.id,
            user_id=viewer_id,
            content="root secret",
            created_at=created_at,
            updated_at=updated_at,
        )
        db.add(root)
        db.flush()
        post_id, root_id = post.id, root.id
        preserved = {
            "uuid": root.uuid,
            "target_type": root.target_type,
            "target_id": root.target_id,
            "parent_comment_id": root.parent_comment_id,
            "user_id": root.user_id,
            "content": root.content,
            "created_at": root.created_at,
            "updated_at": root.updated_at,
        }
        db.commit()

    redis = get_redis_client()
    like_key = post_like_key(post_id)
    redis.sadd(like_key, *range(10_000, 10_018))
    assert client.get(
        "/api/v1/chapters/seoul/posts/popular", headers=auth(viewer_id)
    ).json()["items"][0]["id"] == post_id

    original_pipeline = redis.pipeline

    def failed_pipeline(*, transaction: bool = True):
        raise RedisConnectionError("unavailable")

    monkeypatch.setattr(redis, "pipeline", failed_pipeline)
    response = client.delete(
        f"/api/v1/comments/{root_id}", headers=auth(viewer_id)
    )
    assert response.status_code == 204
    assert response.content == b""

    with Session(engine) as db:
        stored = db.get(Comment, root_id)
        assert stored is not None
        assert stored.deleted_at is not None
        for field, value in preserved.items():
            assert getattr(stored, field) == value

    listed = client.get(
        f"/api/v1/comments?targetType=post&targetId={post_id}",
        headers=auth(viewer_id),
    )
    assert listed.status_code == 200
    assert listed.json() == {"count": 0, "items": []}
    assert "root secret" not in listed.text
    assert client.delete(
        f"/api/v1/comments/{root_id}", headers=auth(viewer_id)
    ).status_code == 404
    assert client.patch(
        f"/api/v1/comments/{root_id}",
        json={"body": "restore"},
        headers=auth(viewer_id),
    ).status_code == 404
    assert client.post(
        "/api/v1/comments",
        json={
            "targetType": "post",
            "targetId": post_id,
            "parentId": root_id,
            "body": "new reply",
        },
        headers=auth(viewer_id),
    ).status_code == 404

    monkeypatch.setattr(redis, "pipeline", original_pipeline)
    detail = client.get(f"/api/v1/posts/{post_id}", headers=auth(viewer_id))
    assert detail.status_code == 200
    assert detail.json()["commentCount"] == 0
    popular = client.get(
        "/api/v1/chapters/seoul/posts/popular", headers=auth(viewer_id)
    )
    assert popular.status_code == 200
    assert popular.json()["items"] == []
    assert redis.scard(like_key) == 18


def test_delete_root_and_replies_keeps_only_threads_with_active_comments(
    api: tuple[TestClient, sa.Engine, int]
) -> None:
    client, engine, viewer_id = api
    with Session(engine) as db:
        post = add_post(db, viewer_id, title="delete thread")
        db.flush()
        root = Comment(
            target_type=CommentTargetType.POST,
            target_id=post.id,
            user_id=viewer_id,
            content="deleted root secret",
        )
        db.add(root)
        db.flush()
        replies = [
            Comment(
                target_type=CommentTargetType.POST,
                target_id=post.id,
                parent_comment_id=root.id,
                user_id=viewer_id,
                content=body,
            )
            for body in ("reply one", "reply two")
        ]
        db.add_all(replies)
        db.flush()
        ids = {
            "post": post.id,
            "root": root.id,
            "replies": [reply.id for reply in replies],
        }
        db.commit()

    detail = client.get(
        f"/api/v1/posts/{ids['post']}", headers=auth(viewer_id)
    )
    assert detail.json()["commentCount"] == 3

    assert client.delete(
        f"/api/v1/comments/{ids['root']}", headers=auth(viewer_id)
    ).status_code == 204
    with Session(engine) as db:
        assert db.get(Comment, ids["root"]).deleted_at is not None
        assert all(
            db.get(Comment, reply_id).deleted_at is None
            for reply_id in ids["replies"]
        )

    listed = client.get(
        f"/api/v1/comments?targetType=post&targetId={ids['post']}",
        headers=auth(viewer_id),
    )
    thread = listed.json()["items"][0]
    assert listed.json()["count"] == 2
    assert thread["masked"] is True
    assert thread["comment"]["isDeleted"] is True
    assert thread["comment"]["body"] == ""
    assert [reply["body"] for reply in thread["replies"]] == [
        "reply one",
        "reply two",
    ]
    assert "deleted root secret" not in listed.text
    assert client.get(
        f"/api/v1/posts/{ids['post']}", headers=auth(viewer_id)
    ).json()["commentCount"] == 2

    assert client.delete(
        f"/api/v1/comments/{ids['replies'][0]}",
        headers=auth(viewer_id),
    ).status_code == 204
    listed = client.get(
        f"/api/v1/comments?targetType=post&targetId={ids['post']}",
        headers=auth(viewer_id),
    )
    assert listed.json()["count"] == 1
    assert [reply["id"] for reply in listed.json()["items"][0]["replies"]] == [
        ids["replies"][1]
    ]
    assert client.get(
        f"/api/v1/posts/{ids['post']}", headers=auth(viewer_id)
    ).json()["commentCount"] == 1

    assert client.delete(
        f"/api/v1/comments/{ids['replies'][1]}",
        headers=auth(viewer_id),
    ).status_code == 204
    listed = client.get(
        f"/api/v1/comments?targetType=post&targetId={ids['post']}",
        headers=auth(viewer_id),
    )
    assert listed.json() == {"count": 0, "items": []}
    assert client.get(
        f"/api/v1/posts/{ids['post']}", headers=auth(viewer_id)
    ).json()["commentCount"] == 0
    with Session(engine) as db:
        assert all(
            db.get(Comment, reply_id).deleted_at is not None
            for reply_id in ids["replies"]
        )


def test_delete_comment_rejects_invalid_state_auth_and_ownership(
    api: tuple[TestClient, sa.Engine, int]
) -> None:
    client, engine, viewer_id = api
    deleted_at = datetime.now(UTC)
    with Session(engine) as db:
        other = User(nickname="delete-other", region_id=1)
        removed = User(nickname="delete-removed", region_id=1)
        db.add_all([other, removed])
        db.flush()
        post = add_post(db, viewer_id, title="delete validation")
        db.flush()
        other_comment = Comment(
            target_type=CommentTargetType.POST,
            target_id=post.id,
            user_id=other.id,
            content="other original",
        )
        deleted_comment = Comment(
            target_type=CommentTargetType.POST,
            target_id=post.id,
            user_id=viewer_id,
            content="already deleted",
            deleted_at=deleted_at,
        )
        removed_author_comment = Comment(
            target_type=CommentTargetType.POST,
            target_id=post.id,
            user_id=removed.id,
            content="removed original",
        )
        db.add_all([other_comment, deleted_comment, removed_author_comment])
        db.flush()
        ids = {
            "other": other_comment.id,
            "deleted": deleted_comment.id,
            "removed": removed_author_comment.id,
        }
        db.delete(removed)
        db.commit()

    endpoint = f"/api/v1/comments/{ids['other']}"
    assert client.delete(endpoint).status_code == 401
    assert client.delete(
        endpoint, headers={"X-Debug-User-Id": "invalid"}
    ).status_code == 401
    assert client.delete(
        "/api/v1/comments/not-an-integer", headers=auth(viewer_id)
    ).status_code == 422

    for comment_id, expected_status in (
        (999999, 404),
        (ids["deleted"], 404),
        (ids["other"], 403),
        (ids["removed"], 403),
    ):
        assert client.delete(
            f"/api/v1/comments/{comment_id}", headers=auth(viewer_id)
        ).status_code == expected_status

    assert "requestBody" not in app.openapi()["paths"][
        "/api/v1/comments/{commentId}"
    ]["delete"]
    with Session(engine) as db:
        assert db.get(Comment, ids["other"]).deleted_at is None
        assert db.get(Comment, ids["other"]).content == "other original"
        assert db.get(Comment, ids["deleted"]).deleted_at == deleted_at
        removed_comment = db.get(Comment, ids["removed"])
        assert removed_comment.deleted_at is None
        assert removed_comment.user_id is None
        assert removed_comment.content == "removed original"
