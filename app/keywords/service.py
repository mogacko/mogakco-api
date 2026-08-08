from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.keywords.model import UserKeyword

# 이 인원 이상이 쓴 키워드만 자동완성에 노출한다. 소수만 쓴 값이 드러나지 않게 하는 기준이다.
MIN_USERS = 5

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
    # ponytail: 조회할 때마다 집계한다. 사용자 수천 명 규모에서는 인덱스 스캔으로 충분하고,
    # 느려지면 그때 집계 결과를 별도 표나 캐시에 둔다.
    users = func.count()
    # 표기는 그 키워드를 쓴 사용자들의 표기 중 하나다. 대소문자는 구분하지 않기로 했으므로 굳이 고르지 않는다.
    rows = db.execute(
        select(func.max(UserKeyword.display))
        .where(
            UserKeyword.kind == kind,
            UserKeyword.keyword.startswith(" ".join(prefix.split()).lower(), autoescape=True),
        )
        .group_by(UserKeyword.keyword)
        .having(users >= MIN_USERS)
        .order_by(users.desc(), UserKeyword.keyword)
        .limit(limit)
    ).all()
    return [display for (display,) in rows]
