from datetime import UTC, datetime

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.terms.model import Term, TermVersion, UserTermAgreement


def current_term_versions(db: Session) -> list[tuple[Term, TermVersion]]:
    rows = db.execute(
        select(Term, TermVersion)
        .join(TermVersion, TermVersion.term_id == Term.id)
        .where(TermVersion.effective_at <= datetime.now(UTC))
        .order_by(Term.code, TermVersion.effective_at.desc())
    ).all()
    latest: dict[str, tuple[Term, TermVersion]] = {}
    for term, version in rows:
        latest.setdefault(term.code, (term, version))
    return list(latest.values())


def set_marketing_consent(db: Session, user_id: int, agreed: bool) -> None:
    current = next(((term, version) for term, version in current_term_versions(db) if term.code == "MARKETING"), None)
    if current is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="현재 마케팅 약관이 없습니다.")
    _, version = current
    active = db.scalar(
        select(UserTermAgreement)
        .where(UserTermAgreement.user_id == user_id, UserTermAgreement.term_version_id == version.id)
        .where(UserTermAgreement.withdrawn_at.is_(None))
        .order_by(UserTermAgreement.agreed_at.desc())
    )
    if agreed and active is None:
        db.add(UserTermAgreement(user_id=user_id, term_version_id=version.id))
    elif not agreed and active is not None:
        active.withdrawn_at = datetime.now(UTC)
    db.commit()
