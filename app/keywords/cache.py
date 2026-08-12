"""자동완성 집계를 Redis에 둔다. 원본은 언제나 `user_keywords`다.

캐시는 사라져도 되는 사본이다. Redis가 비었거나 죽었으면 PostgreSQL에서 다시 집계해
응답하므로 자동완성이 멈추지 않는다. 유실 뒤 되살릴 때는 아래를 부른다.

    uv run python -m app.keywords.cache
"""
import json
import logging
import os

import redis
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.keywords.model import UserKeyword

logger = logging.getLogger(__name__)

KINDS = ("FIELD", "STACK", "INTEREST")

# 노출 기준. 서로 다른 사용자 수가 이보다 적은 키워드는 캐시에 담지 않는다.
MIN_USERS = 5

# ponytail: kind마다 노출 대상 전체를 키 하나에 담고 접두사는 Python에서 고른다. 노출 키워드가
# 수백 개인 지금은 GET 한 번이 제일 싸다. 수천 개를 넘어 응답 크기가 걸리면 접두사 앞 두 글자로
# 키를 쪼개거나 ZSET + ZRANGEBYLEX로 옮긴다.
# 담긴 값의 구조가 바뀌면 v를 올린다. 옛 키는 TTL로 알아서 사라진다.
_KEY = "keywords:v1:{kind}"
_TTL_SECONDS = int(os.getenv("KEYWORD_CACHE_TTL_SECONDS", str(24 * 60 * 60)))

_CLIENT: redis.Redis | None = None


def client() -> redis.Redis | None:
    """REDIS_URL이 없으면 None. 캐시 없이 PostgreSQL만으로 도는 것도 정상 동작이다."""
    global _CLIENT
    if _CLIENT is None:
        url = os.getenv("REDIS_URL")
        if not url:
            return None
        _CLIENT = redis.Redis.from_url(url, decode_responses=True)
    return _CLIENT


def load(kind: str) -> list[list[str]] | None:
    """캐시된 목록. 미설정·미스·장애를 구분하지 않고 None을 준다. 호출자는 어느 쪽이든 재집계한다."""
    connection = client()
    if connection is None:
        return None
    try:
        cached = connection.get(_KEY.format(kind=kind))
    except redis.RedisError:
        # 조용히 넘어가면 Redis가 죽은 것을 아무도 모른 채 PostgreSQL만 계속 두드린다.
        logger.warning("자동완성 캐시를 읽지 못해 PostgreSQL로 넘어간다. kind=%s", kind, exc_info=True)
        return None
    return json.loads(cached) if cached else None


def _aggregate(db: Session, kind: str) -> list[list[str]]:
    users = func.count()
    # 표기는 그 키워드를 쓴 사용자들의 표기 중 하나다. 대소문자를 구분하지 않기로 했으므로 굳이 고르지 않는다.
    rows = db.execute(
        select(UserKeyword.keyword, func.max(UserKeyword.display))
        .where(UserKeyword.kind == kind)
        .group_by(UserKeyword.keyword)
        .having(users >= MIN_USERS)
        .order_by(users.desc(), UserKeyword.keyword)
    ).all()
    return [[keyword, display] for keyword, display in rows]


def rebuild(db: Session, kinds: tuple[str, ...] | list[str] = KINDS) -> dict[str, list[list[str]]]:
    """PostgreSQL에서 집계해 Redis에 쓰고, 쓴 내용을 그대로 돌려준다.

    저장 후 갱신과 유실 후 재구축은 같은 동작이라 한 함수로 둔다. Redis 쓰기가 실패해도
    집계 결과는 반환한다. 그래야 장애 중에도 호출자가 응답할 수 있다.
    """
    built = {kind: _aggregate(db, kind) for kind in kinds}
    connection = client()
    if connection is None:
        return built
    try:
        # TTL을 두면 갱신이 한 번 유실돼도 하루 안에 스스로 맞춰진다.
        for kind, entries in built.items():
            connection.setex(_KEY.format(kind=kind), _TTL_SECONDS, json.dumps(entries, ensure_ascii=False))
    except redis.RedisError:
        logger.warning("자동완성 캐시를 쓰지 못했다. kinds=%s", list(built), exc_info=True)
    return built


def refresh_after_save() -> None:
    """프로필 저장이 끝난 뒤 BackgroundTasks로 부른다. 무슨 일이 있어도 예외를 밖으로 내지 않는다.

    응답이 나간 뒤에 도는 작업이라 저장을 되돌릴 수는 없지만, 여기서 터지면 ASGI 오류로 남는다.

    ponytail: 어느 kind가 바뀌었는지 따지지 않고 셋 다 다시 집계한다. 바뀐 kind를 라우터까지
    들고 가는 배선이 집계 두 번보다 비싸고, 프로필 저장은 드물다. 저장이 잦아지면 그때 좁힌다.
    """
    # yield 의존성은 백그라운드 작업보다 먼저 정리되므로 요청 세션은 이미 닫혀 있다. 새로 연다.
    from app.db import SessionLocal

    try:
        with SessionLocal() as session:
            rebuild(session)
    except Exception:
        logger.warning("저장 후 자동완성 캐시 갱신에 실패했다. 다음 저장이나 TTL 만료 때 맞춰진다.", exc_info=True)


if __name__ == "__main__":
    from app.db import SessionLocal

    with SessionLocal() as session:
        rebuilt = rebuild(session)
    for kind, entries in rebuilt.items():
        print(f"{kind} {len(entries)}개")
