import re

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.keywords import cache
from app.keywords.cache import MIN_USERS  # noqa: F401  노출 기준은 집계하는 쪽에 둔다
from app.keywords.model import UserKeyword

# 프로필 필드명 -> 키워드 종류
KINDS = {"field": "FIELD", "stack": "STACK", "interests": "INTEREST"}

# 표기가 갈려도 한 키워드로 모으려고 구분자를 지운다.
# Spring Boot · SPRINGBOOT · spring-boot · Spring_Boot · spring.boot 가 모두 springboot가 된다.
# 특수문자를 전부 지우지는 않는다. 그러면 C·C#·C++가 셋 다 c가 되어 버린다.
_SEPARATORS = re.compile(r"[\s\-_.]")


def normalize(value: str) -> str:
    return _SEPARATORS.sub("", value.lower())


def _parse(value: str | None) -> dict[str, str]:
    """콤마로 나누고 공백을 정리한 뒤, 정규화한 키에 처음 본 표기를 매핑한다."""
    parsed: dict[str, str] = {}
    for part in (value or "").split(","):
        display = " ".join(part.split())[:100]
        keyword = normalize(display)
        if keyword:
            # 구분자만 있던 값(`---` 같은 것)은 키가 비어 아무 접두사에나 걸리므로 버린다.
            parsed.setdefault(keyword, display)
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
    # 검색어도 같은 규칙으로 정규화해야 "spring b"가 springboot를 찾는다.
    normalized = normalize(prefix)
    return [display for keyword, display in entries if keyword.startswith(normalized)][:limit]
