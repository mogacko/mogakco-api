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
            author_id=viewer_id,
            target_type=CommentTargetType.POST,
            target_id=post.id,
            body="root",
        )
        db.add(root)
        db.flush()
        db.add_all(
            [
                Comment(
                    author_id=viewer_id,
                    target_type=CommentTargetType.POST,
                    target_id=post.id,
                    parent_id=root.id,
                    body="reply",
                ),
                Comment(
                    author_id=viewer_id,
                    target_type=CommentTargetType.POST,
                    target_id=post.id,
                    body="deleted",
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
                        author_id=viewer_id,
                        target_type=CommentTargetType.POST,
                        target_id=post.id,
                        body=f"comment-{index}",
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
