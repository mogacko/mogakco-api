"""동일 회원의 동의 변경을 직렬화하고 현재 상태와 이력을 함께 저장한다."""

import logging
import re
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.auth.errors import AuthErrors
from app.exceptions import AuthenticationException, InternalServerException
from app.models import MarketingConsentHistory, TermAgreement, User
from app.schemas.user import MarketingConsentResponse
from app.time import KST, kst_now
from app.user.errors import UserErrors

# TERM_AGREEMENT 명세의 초기 문자열. 버전 선택 정책 확정 시 선택 로직을 교체한다.
INITIAL_MARKETING_VERSION = "1.0.0"
MARKETING = "MARKETING"
logger = logging.getLogger(__name__)


def current_marketing_agreement(db: Session, user_id: int) -> TermAgreement | None:
    rows = db.scalars(
        select(TermAgreement)
        .where(TermAgreement.user_id == user_id, TermAgreement.type == MARKETING)
        .execution_options(populate_existing=True)
    ).all()

    # 임시 정책: 변경 시각 대신 숫자 버전으로 선택한다(1.10.0 > 1.9.0).
    # 기존 행만 선택하며 과거 동의를 새 버전으로 복사하지 않는다.
    def version_key(agreement: TermAgreement) -> tuple[int, int, int]:
        if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", agreement.version):
            raise InternalServerException(UserErrors.CONSENT_CONFIGURATION)
        major, minor, patch = agreement.version.split(".")
        return int(major), int(minor), int(patch)

    return max(rows, key=lambda row: (version_key(row), row.id), default=None)


def consent_time(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    # SQLite 테스트는 timezone 정보를 보존하지 않는다. 저장 시각은 KST다.
    return value.replace(tzinfo=KST) if value.tzinfo is None else value.astimezone(KST)


def marketing_state(db: Session, user: User) -> tuple[bool | None, datetime | None]:
    # 프로필 쓰기 오류는 약관 조회 실패로 숨기지 않는다.
    db.flush()
    try:
        # PostgreSQL의 조회 오류가 바깥 프로필 수정 트랜잭션을 중단하지 않게 격리한다.
        with db.begin_nested():
            agreement = current_marketing_agreement(db, user.id)
            if agreement is not None:
                return agreement.is_agreed, consent_time(agreement.signed_at)
            # 기존 값은 첫 실제 변경까지 보존하며 과거 이력을 만들지 않는다.
            return user.marketing_consent, consent_time(
                user.marketing_consent_changed_at
            )
    except (SQLAlchemyError, InternalServerException):
        logger.exception("Marketing consent unavailable while reading profile")
        return None, None


def change_marketing_consent(
    db: Session, user_uuid: UUID, agreed: bool
) -> MarketingConsentResponse:
    try:
        user = db.scalar(
            select(User)
            .where(User.uuid == user_uuid, User.deleted_at.is_(None))
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if user is None:
            raise AuthenticationException(AuthErrors.REQUIRED)
        agreement = current_marketing_agreement(db, user.id)
        previous = agreement.is_agreed if agreement else user.marketing_consent
        changed_at = (
            agreement.signed_at if agreement else user.marketing_consent_changed_at
        )
        if previous == agreed:
            result = MarketingConsentResponse(
                marketingAgreed=previous, changedAt=consent_time(changed_at)
            )
            db.commit()
            return result
        version = agreement.version if agreement else INITIAL_MARKETING_VERSION
        if not version or not version.strip():
            raise InternalServerException(UserErrors.CONSENT_CONFIGURATION)
        now = kst_now()
        previous_recorded = agreement is not None or previous or changed_at is not None
        if agreement is None:
            agreement = TermAgreement(
                user_id=user.id, type=MARKETING, version=version, is_required=False
            )
            db.add(agreement)
        agreement.is_agreed = agreed
        agreement.signed_at = now
        db.add(
            MarketingConsentHistory(
                user_id=user.id,
                type=MARKETING,
                version=version,
                previous_is_agreed=previous if previous_recorded else None,
                is_agreed=agreed,
                changed_at=now,
            )
        )
        db.commit()
        return MarketingConsentResponse(marketingAgreed=agreed, changedAt=now)
    except SQLAlchemyError as error:
        db.rollback()
        raise InternalServerException(UserErrors.CONSENT_SAVE_FAILED) from error
    except Exception:
        db.rollback()
        raise
