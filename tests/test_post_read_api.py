import os
from collections.abc import Generator
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.database import get_db
from app.main import app
from app.models import Comment, CommentTargetType, Post, PostBoard, PostCategory, PostLike, User

DATABASE_URL = os.environ["DATABASE_URL"]


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch) -> Generator[tuple[TestClient, sa.Engine, int]]:
    command.upgrade(Config("alembic.ini"), "head")
    engine = sa.create_engine(DATABASE_URL)
    with Session(engine) as db:
        db.execute(sa.delete(Comment))
        db.execute(sa.delete(PostLike))
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
        db.add(PostLike(post_id=post.id, user_id=viewer_id))
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

    response = client.get(
        "/api/v1/chapters/seoul/posts/popular", headers=auth(viewer_id)
    )

    assert response.status_code == 200
    assert [item["title"] for item in response.json()["items"]] == [
        "score24",
        "score22",
        "tie-new",
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


def test_delete_post_soft_deletes_only_for_owner_and_keeps_relations(
    api: tuple[TestClient, sa.Engine, int],
) -> None:
    client, engine, viewer_id = api
    with Session(engine) as db:
        other = User(nickname="other", region_id=1)
        db.add(other)
        db.flush()
        owned = add_post(db, viewer_id, title="owned")
        foreign = add_post(db, other.id, title="foreign")
        db.add(PostLike(post_id=owned.id, user_id=viewer_id))
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
            .select_from(PostLike)
            .where(PostLike.post_id == owned_id)
        ) == 1
        assert db.scalar(
            sa.select(sa.func.count())
            .select_from(Comment)
            .where(
                Comment.target_type == CommentTargetType.POST,
                Comment.target_id == owned_id,
            )
        ) == 1
