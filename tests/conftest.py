import os

os.environ.setdefault("DATABASE_URL", "sqlite://")

import pytest  # noqa: E402
import redis  # noqa: E402
from sqlalchemy import create_engine, event  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

import app.models  # noqa: E402,F401  모든 표를 메타데이터에 올린다
from app.db import Base  # noqa: E402

# 기본은 인메모리 SQLite. 운영과 같은 PostgreSQL로 돌리려면 TEST_DATABASE_URL을 준다.
TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL", "sqlite://")


def _match_postgres(connection, _record) -> None:
    # SQLite의 LIKE는 기본이 대소문자 무시라 PostgreSQL과 다르게 동작한다.
    # 정규화가 빠져도 통과해 버리는 것을 막으려고 기준을 맞춘다.
    connection.execute("PRAGMA case_sensitive_like = ON")
    # 외래 키도 기본이 꺼져 있다. 켜지 않으면 ON DELETE CASCADE가 SQLite에서 그냥 무시된다.
    connection.execute("PRAGMA foreign_keys = ON")


class FakeRedis:
    """쓰는 명령이 get·setex·delete뿐이라 fakeredis를 붙이지 않는다."""

    def __init__(self, down: bool = False) -> None:
        self.store: dict[str, str] = {}
        self.ttls: dict[str, int] = {}
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
        self.ttls[key] = ttl

    def delete(self, key: str) -> None:
        self._check()
        self.store.pop(key, None)
        self.ttls.pop(key, None)


@pytest.fixture(autouse=True)
def signup_codes(monkeypatch: pytest.MonkeyPatch) -> FakeRedis:
    """가입 코드는 Redis가 원본이라 대역이 없으면 가입 경로가 전부 죽는다.

    autouse로 두어 어떤 테스트도 실제 Redis에 닿지 않게 한다.
    """
    from app.auth import service

    connection = FakeRedis()
    monkeypatch.setattr(service, "_REDIS", connection)
    return connection


@pytest.fixture
def engine():
    if TEST_DATABASE_URL.startswith("sqlite"):
        built = create_engine(TEST_DATABASE_URL, connect_args={"check_same_thread": False}, poolclass=StaticPool)
        event.listen(built, "connect", _match_postgres)
    else:
        built = create_engine(TEST_DATABASE_URL)
    Base.metadata.drop_all(built)
    Base.metadata.create_all(built)
    yield built
    Base.metadata.drop_all(built)
    built.dispose()
