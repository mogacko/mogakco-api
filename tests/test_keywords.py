from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth.config import TokenSettings
from app.auth.service import create_login_code, signup
from app.keywords.model import UserKeyword
from app.keywords.service import MIN_USERS, suggest, sync_keywords
from app.terms.model import Term, TermVersion
from app.users.model import User


@pytest.fixture
def db(engine):
    with Session(engine) as session:
        yield session


def add_user(db: Session, nickname: str, **profile) -> int:
    user = User(nickname=nickname, activity_region="SEOUL", **profile)
    db.add(user)
    db.flush()
    sync_keywords(db, user.id, profile)
    db.commit()
    return user.id


def test_suggestion_needs_min_users(db):
    for index in range(MIN_USERS - 1):
        add_user(db, f"user{index}", stack="Django")
    assert suggest(db, "STACK", "dj", 10) == []

    add_user(db, "one-more", stack="Django")
    assert suggest(db, "STACK", "dj", 10) == ["Django"]


def test_comma_split_and_case_insensitive_grouping(db):
    add_user(db, "a", stack="React, Spring Boot")
    add_user(db, "b", stack="react")
    add_user(db, "c", stack="REACT , django")
    add_user(db, "d", stack="React")
    add_user(db, "e", stack="  react  ")

    # 표기가 갈려도 한 키워드로 합쳐지므로 5명 기준을 넘는다. 어떤 표기로 나올지는 정하지 않는다.
    assert [value.lower() for value in suggest(db, "STACK", "rea", 10)] == ["react"]
    assert [value.lower() for value in suggest(db, "STACK", "", 10)] == ["react"]
    assert suggest(db, "FIELD", "", 10) == []


def test_repeated_save_by_one_user_counts_once(db):
    user_id = add_user(db, "solo", stack="Rust")
    for _ in range(MIN_USERS + 3):
        sync_keywords(db, user_id, {"stack": "Rust"})
        db.commit()
    assert suggest(db, "STACK", "ru", 10) == []


def test_cleared_field_removes_keywords(db):
    for index in range(MIN_USERS):
        add_user(db, f"user{index}", interests="사이드프로젝트")
    assert suggest(db, "INTEREST", "사이드", 10) == ["사이드프로젝트"]

    sync_keywords(db, 1, {"interests": None})
    db.commit()
    assert suggest(db, "INTEREST", "사이드", 10) == []


def test_separator_variants_merge_into_one_keyword(db):
    for index, typed in enumerate(["Spring Boot", "SPRINGBOOT", "spring-boot", "Spring_Boot", "spring.boot"]):
        add_user(db, f"user{index}", stack=typed)

    # 다섯 명이 제각각 쳤지만 한 키워드다. 기준을 넘어 노출된다.
    assert len(suggest(db, "STACK", "spring", 10)) == 1
    assert db.scalar(select(func.count()).select_from(UserKeyword).where(UserKeyword.keyword == "springboot")) == 5


def test_prefix_with_separator_still_matches(db):
    for index in range(MIN_USERS):
        add_user(db, f"user{index}", stack="Spring Boot")

    # 검색어도 같은 규칙으로 정규화한다.
    for typed in ("spring b", "spring-b", "springb", "Spring Boot"):
        assert suggest(db, "STACK", typed, 10) == ["Spring Boot"], typed


def test_meaningful_symbols_are_kept_apart(db):
    for index in range(MIN_USERS):
        add_user(db, f"c{index}", stack="C")
        add_user(db, f"sharp{index}", stack="C#")
        add_user(db, f"plus{index}", stack="C++")

    # 특수문자를 전부 지웠다면 셋 다 c로 뭉쳐 하나만 남는다.
    assert sorted(suggest(db, "STACK", "c", 10)) == ["C", "C#", "C++"]


def test_separator_only_input_is_dropped(db):
    for index in range(MIN_USERS):
        add_user(db, f"user{index}", stack="---, Go")

    # 키가 비면 아무 접두사에나 걸리므로 아예 저장하지 않는다.
    assert db.scalars(select(UserKeyword.keyword).where(UserKeyword.keyword == "")).all() == []
    assert suggest(db, "STACK", "", 10) == ["Go"]


def test_prefix_wildcards_are_escaped(db):
    for index in range(MIN_USERS):
        add_user(db, f"user{index}", stack="Go")
    assert suggest(db, "STACK", "%", 10) == []


def test_prefix_matches_regardless_of_typed_case(db):
    for index in range(MIN_USERS):
        add_user(db, f"user{index}", stack="Spring Boot")
    for typed in ("spring", "Spring", "SPRING", " sPrInG "):
        assert suggest(db, "STACK", typed, 10) == ["Spring Boot"], typed


def test_signup_syncs_keywords(db, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AUTH_JWT_SECRET", "12345678901234567890123456789012")
    monkeypatch.setenv("AUTH_ACCESS_TOKEN_TTL_SECONDS", "900")
    monkeypatch.setenv("AUTH_LOGIN_CODE_TTL_SECONDS", "60")
    settings = TokenSettings.from_env()
    term = Term(code="SERVICE", required=True)
    db.add(term)
    db.flush()
    version = TermVersion(term_id=term.id, version="v1", content="약관", effective_at=datetime.now(UTC))
    db.add(version)
    db.commit()
    code = create_login_code(db, None, settings, "GOOGLE", "google-user")

    signup(
        db,
        code,
        {"nickname": "newbie", "activity_region": "SEOUL", "field": "백엔드", "stack": "Go, Rust"},
        [version.id],
        settings,
    )

    assert sorted(db.scalars(select(UserKeyword.keyword)).all()) == ["go", "rust", "백엔드"]
