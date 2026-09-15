"""프로필의 원자적 수정과 닉네임 점유 조회."""

from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth.errors import AuthErrors
from app.exceptions import (
    AuthenticationException,
    ConflictException,
    InternalServerException,
)
from app.models import Region, User, UserAttribute, UserAttributeType
from app.schemas.user import (
    MyProfileResponse,
    ProfileUpdateRequest,
    normalized_key,
    sorted_values,
)
from app.time import KST
from app.user.errors import UserErrors


def nickname_is_available(
    db: Session, nickname: str, *, exclude_id: int | None = None
) -> bool:
    # 현재 전체 UNIQUE와 동일하게 탈퇴 후 보존된 사용자도 점유자로 판단한다.
    query = select(User.id).where(User.nickname == nickname)
    if exclude_id is not None:
        query = query.where(User.id != exclude_id)
    return db.scalar(query) is None


def my_profile(db: Session, user: User) -> MyProfileResponse:
    region_name = db.scalar(select(Region.name).where(Region.id == user.region_id))
    if (
        not user.nickname
        or not user.field
        or not user.field.strip()
        or not region_name
        or user.created_at is None
    ):
        raise InternalServerException(UserErrors.DATA_INCONSISTENT)
    attributes = db.scalars(
        select(UserAttribute)
        .where(UserAttribute.user_id == user.id)
        .order_by(UserAttribute.id)
    ).all()
    values = {kind: [] for kind in UserAttributeType}
    for attribute in attributes:
        values[attribute.type].append(attribute.value)
    return MyProfileResponse(
        userUuid=user.uuid,
        nickname=user.nickname,
        activityRegionName=region_name,
        field=user.field,
        affiliation=values[UserAttributeType.AFFILIATION][0]
        if values[UserAttributeType.AFFILIATION]
        else None,
        bio=user.bio or None,
        stacks=sorted_values(values[UserAttributeType.STACK]),
        interests=sorted_values(values[UserAttributeType.INTEREST]),
        profileImageUrl=None,
        joinedAt=user.created_at.astimezone(KST),
        isStaff=user.is_staff,
        marketingConsent=user.marketing_consent,
        marketingConsentChangedAt=user.marketing_consent_changed_at.astimezone(KST)
        if user.marketing_consent_changed_at
        else None,
    )


def update_my_profile(
    db: Session, user_uuid: UUID, request: ProfileUpdateRequest
) -> MyProfileResponse:
    try:
        # 인증 이후 읽힌 사용자도 잠금 획득 후 최신 값으로 새로 읽는다.
        user = db.scalar(
            select(User)
            .where(User.uuid == user_uuid, User.deleted_at.is_(None))
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if user is None:
            raise AuthenticationException(AuthErrors.REQUIRED)
        if "nickname" in request.model_fields_set and not nickname_is_available(
            db, request.nickname, exclude_id=user.id
        ):
            raise ConflictException(UserErrors.NICKNAME_CONFLICT)
        for name in request.model_fields_set & {"nickname", "field", "bio"}:
            setattr(user, name, getattr(request, name))
        for name, kind in (
            ("affiliation", UserAttributeType.AFFILIATION),
            ("stacks", UserAttributeType.STACK),
            ("interests", UserAttributeType.INTEREST),
        ):
            if name not in request.model_fields_set:
                continue
            db.execute(
                delete(UserAttribute).where(
                    UserAttribute.user_id == user.id, UserAttribute.type == kind
                )
            )
            value = getattr(request, name)
            values = ([value] if value else []) if name == "affiliation" else value
            for display in values:
                db.add(
                    UserAttribute(
                        user_id=user.id,
                        type=kind,
                        value=display,
                        value_normalized=normalized_key(display),
                    )
                )
        db.flush()
        response = my_profile(db, user)
        db.commit()
        return response
    except IntegrityError as error:
        db.rollback()
        # 닉네임 이외의 제약 위반은 409로 바꾸지 않는다.
        diagnostic = getattr(error.orig, "diag", None)
        if getattr(diagnostic, "constraint_name", None) == "uq_users_nickname":
            raise ConflictException(UserErrors.NICKNAME_CONFLICT) from error
        raise
    except Exception:
        db.rollback()
        raise
