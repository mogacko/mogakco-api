import json

import pytest
import redis
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.auth.config import TokenSettings
from app.auth.token import create_access_token
from app.db import get_db
from app.keywords import cache
from app.keywords.service import MIN_USERS, suggest, sync_keywords
from app.main import app
from app.users.model import User

STACK_KEY = "keywords:v1:STACK"


class FakeRedis:
    """쓰는 명령이 get·setex 둘뿐이라 fakeredis를 붙이지 않는다."""

    def __init__(self, down: bool = False) -> None:
        self.store: dict[str, str] = {}
        self.down = down

    def _check(self) -> None:
        if self.down:
            raise redis.RedisError("연결할 수 없습니다.")

    def get(self, key: str) -> str | None:
        self._check()
        return self.store.get(key)

    def setex(self, key: str, ttl: int, value: str) -> None:
        self._check()
        self.store[key] = value


@pytest.fixture
def db(engine):
    with Session(engine) as session:
        yield session


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeRedis:
    connection = FakeRedis()
    monkeypatch.setattr(cache, "_CLIENT", connection)
    return connection


def add_user(db: Session, nickname: str, **profile) -> int:
    user = User(nickname=nickname, activity_region="SEOUL", **profile)
    db.add(user)
    db.flush()
    sync_keywords(db, user.id, profile)
    db.commit()
    return user.id


def seed(db: Session, count: int, **profile) -> None:
    for index in range(count):
        add_user(db, f"user{index}-{next(iter(profile.values()))}", **profile)


def test_suggest_reads_cache_without_touching_db(db, fake):
    seed(db, MIN_USERS, stack="Spring Boot")
    cache.rebuild(db)

    # db에 None을 넘겨도 답이 나오면 PostgreSQL을 조회하지 않았다는 뜻이다.
    assert suggest(None, "STACK", "spring", 10) == ["Spring Boot"]


def test_cache_miss_rebuilds_and_fills_the_key(db, fake):
    seed(db, MIN_USERS, stack="Django")
    assert fake.store == {}

    assert suggest(db, "STACK", "dj", 10) == ["Django"]
    assert json.loads(fake.store[STACK_KEY]) == [["django", "Django"]]


def test_falls_back_to_postgres_while_redis_is_down(db, monkeypatch: pytest.MonkeyPatch):
    seed(db, MIN_USERS, stack="Rust")
    monkeypatch.setattr(cache, "_CLIENT", FakeRedis(down=True))

    assert suggest(db, "STACK", "ru", 10) == ["Rust"]


def test_rebuild_restores_the_same_answer_after_flush(db, fake):
    seed(db, MIN_USERS, stack="Go")
    seed(db, MIN_USERS - 1, field="백엔드")
    cache.rebuild(db)
    before = fake.store[STACK_KEY]

    fake.store.clear()
    cache.rebuild(db)

    assert fake.store[STACK_KEY] == before
    # 5명을 못 채운 키워드는 캐시에도 담기지 않는다.
    assert json.loads(fake.store["keywords:v1:FIELD"]) == []


def test_cache_keeps_min_users_threshold(db, fake):
    seed(db, MIN_USERS - 1, stack="Elixir")
    cache.rebuild(db)
    assert suggest(None, "STACK", "el", 10) == []

    add_user(db, "one-more", stack="Elixir")
    cache.rebuild(db)
    assert suggest(None, "STACK", "el", 10) == ["Elixir"]


@pytest.fixture
def client(engine, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AUTH_JWT_SECRET", "12345678901234567890123456789012")
    monkeypatch.setenv("AUTH_ACCESS_TOKEN_TTL_SECONDS", "900")
    monkeypatch.setenv("AUTH_LOGIN_CODE_TTL_SECONDS", "60")

    def override_db():
        with Session(engine) as session:
            yield session

    # 백그라운드 작업은 요청 세션이 닫힌 뒤에 돌아 자기 세션을 연다. 테스트 엔진으로 돌린다.
    monkeypatch.setattr("app.db.SessionLocal", lambda: Session(engine))
    app.dependency_overrides[get_db] = override_db
    yield TestClient(app)
    app.dependency_overrides.clear()


def auth_headers(engine, nickname: str) -> dict[str, str]:
    with Session(engine) as db:
        user = User(nickname=nickname, activity_region="SEOUL")
        db.add(user)
        db.commit()
        user_id = user.id
    return {"Authorization": f"Bearer {create_access_token(user_id, TokenSettings.from_env())}"}


def test_profile_save_refreshes_the_cache(client, engine, fake):
    with Session(engine) as db:
        seed(db, MIN_USERS - 1, stack="Kotlin")
        cache.rebuild(db)
    assert json.loads(fake.store[STACK_KEY]) == []

    headers = auth_headers(engine, "last-one")
    assert client.patch("/me", json={"stack": "Kotlin"}, headers=headers).status_code == 200

    # 저장 뒤 백그라운드 갱신이 돌아 5명 기준을 넘긴 키워드가 캐시에 들어온다.
    assert json.loads(fake.store[STACK_KEY]) == [["kotlin", "Kotlin"]]


def test_profile_save_succeeds_while_redis_is_down(client, engine, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(cache, "_CLIENT", FakeRedis(down=True))
    headers = auth_headers(engine, "solo")

    response = client.patch("/me", json={"stack": "Zig"}, headers=headers)

    assert response.status_code == 200, response.text
    assert response.json()["stack"] == "Zig"
