from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.keywords import cache
from app.keywords.cache import MIN_USERS  # noqa: F401  노출 기준은 집계하는 쪽에 둔다
from app.keywords.model import UserKeyword

# 프로필 필드명 -> 키워드 종류
KINDS = {"field": "FIELD", "stack": "STACK", "interests": "INTEREST"}


def _parse(value: str | None) -> dict[str, str]:
    """콤마로 나누고 공백을 정리한 뒤, 소문자 키에 처음 본 표기를 매핑한다."""
    parsed: dict[str, str] = {}
    for part in (value or "").split(","):
        display = " ".join(part.split())[:100]
        if display:
            parsed.setdefault(display.lower(), display)
    return parsed


def sync_keywords(db: Session, user_id: int, values: dict) -> None:
    """프로필에 담겨 온 항목만 사용자 기준으로 다시 기록한다. 호출자가 커밋한다."""
    for name, kind in KINDS.items():
        if name not in values:
            continue
        db.execute(delete(UserKeyword).where(UserKeyword.user_id == user_id, UserKeyword.kind == kind))
        db.add_all(
            [
                UserKeyword(user_id=user_id, kind=kind, keyword=keyword, display=display)
                for keyword, display in _parse(values[name]).items()
            ]
        )


def suggest(db: Session, kind: str, prefix: str, limit: int) -> list[str]:
    """캐시된 목록에서 접두사로 고른다. 캐시가 없으면 PostgreSQL에서 다시 집계한다."""
    entries = cache.load(kind)
    if entries is None:
        # 미스면 재집계하면서 캐시도 채운다. Redis가 죽었으면 집계 결과만 받아 쓴다.
        entries = cache.rebuild(db, [kind])[kind]
    # 목록은 이미 (사용자 수 내림차순, 키워드순)으로 정렬돼 있다.
    normalized = " ".join(prefix.split()).lower()
    return [display for keyword, display in entries if keyword.startswith(normalized)][:limit]
