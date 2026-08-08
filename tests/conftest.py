import os

os.environ.setdefault("DATABASE_URL", "sqlite://")

import pytest  # noqa: E402
from sqlalchemy import create_engine, event  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

import app.models  # noqa: E402,F401  모든 표를 메타데이터에 올린다
from app.db import Base  # noqa: E402

# 기본은 인메모리 SQLite. 운영과 같은 PostgreSQL로 돌리려면 TEST_DATABASE_URL을 준다.
TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL", "sqlite://")


def _case_sensitive_like(connection, _record) -> None:
    # SQLite의 LIKE는 기본이 대소문자 무시라 PostgreSQL과 다르게 동작한다.
    # 정규화가 빠져도 통과해 버리는 것을 막으려고 기준을 맞춘다.
    connection.execute("PRAGMA case_sensitive_like = ON")


@pytest.fixture
def engine():
    if TEST_DATABASE_URL.startswith("sqlite"):
        built = create_engine(TEST_DATABASE_URL, connect_args={"check_same_thread": False}, poolclass=StaticPool)
        event.listen(built, "connect", _case_sensitive_like)
    else:
        built = create_engine(TEST_DATABASE_URL)
    Base.metadata.drop_all(built)
    Base.metadata.create_all(built)
    yield built
    Base.metadata.drop_all(built)
    built.dispose()
