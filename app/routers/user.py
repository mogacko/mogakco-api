"""내 프로필 API와 개발용 공개 프로필 조회."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.auth.errors import AuthErrors
from app.common.errors import CommonErrors
from app.database import get_db
from app.dependencies.auth import get_current_user
from app.exceptions import DomainValidationException
from app.models import User
from app.schemas.error import error_responses
from app.schemas.user import (
    ActivityCounts,
    MarketingConsentRequest,
    MarketingConsentResponse,
    MyProfileResponse,
    NicknameAvailabilityResponse,
    NicknameQuery,
    OtherProfileResponse,
    ProfileUpdateRequest,
)
from app.services.activity import activity_counts
from app.services.consent import change_marketing_consent
from app.services.profile_sources import get_other_profile
from app.services.user import check_nickname_availability, my_profile, update_my_profile
from app.user.errors import UserErrors

router = APIRouter(prefix="/api/v1/users", tags=["내 프로필"])
_ERRORS = (
    AuthErrors.REQUIRED,
    CommonErrors.INVALID_REQUEST,
    CommonErrors.INTERNAL_SERVER_ERROR,
    UserErrors.DATA_INCONSISTENT,
)


def no_query_parameters(request: Request) -> None:
    if request.query_params:
        raise DomainValidationException(CommonErrors.INVALID_REQUEST)


@router.get(
    "/me",
    response_model=MyProfileResponse,
    responses=error_responses(*_ERRORS, UserErrors.CONSENT_CONFIGURATION),
    summary="내 프로필 조회",
    dependencies=[Depends(no_query_parameters)],
)
def get_me(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> MyProfileResponse:
    return my_profile(db, current_user)


@router.patch(
    "/me",
    response_model=MyProfileResponse,
    responses=error_responses(
        AuthErrors.REQUIRED,
        CommonErrors.INVALID_REQUEST,
        CommonErrors.INTERNAL_SERVER_ERROR,
        UserErrors.UPDATE_DATA_INCONSISTENT,
        UserErrors.CONSENT_CONFIGURATION,
        UserErrors.NICKNAME_CONFLICT,
    ),
    summary="내 프로필 수정",
    dependencies=[Depends(no_query_parameters)],
)
def patch_me(
    request: ProfileUpdateRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> MyProfileResponse:
    return update_my_profile(db, current_user.uuid, request)


def nickname_query(request: Request) -> NicknameQuery:
    try:
        if len(request.query_params.getlist("nickname")) != 1:
            raise DomainValidationException(UserErrors.INVALID_NICKNAME)
        return NicknameQuery.model_validate(dict(request.query_params))
    except ValidationError:
        raise DomainValidationException(UserErrors.INVALID_NICKNAME) from None


@router.get(
    "/nickname-availability",
    response_model=NicknameAvailabilityResponse,
    responses=error_responses(
        UserErrors.INVALID_NICKNAME, UserErrors.NICKNAME_LOOKUP_FAILED
    ),
    summary="닉네임 중복 검사",
    openapi_extra={
        "parameters": [
            {
                "name": "nickname",
                "in": "query",
                "required": True,
                "schema": {"type": "string", "minLength": 2, "maxLength": 12},
                "description": "trim·NFC 후 한글 완성형·영문·숫자·밑줄",
            }
        ]
    },
)
def check_nickname(
    query: Annotated[NicknameQuery, Depends(nickname_query)],
    db: Annotated[Session, Depends(get_db)],
) -> NicknameAvailabilityResponse:
    return NicknameAvailabilityResponse(
        nickname=query.nickname,
        available=check_nickname_availability(db, query.nickname),
    )


@router.get(
    "/me/activity-summary",
    response_model=ActivityCounts,
    responses=error_responses(
        AuthErrors.REQUIRED,
        CommonErrors.INVALID_REQUEST,
        UserErrors.ACTIVITY_LOOKUP_FAILED,
    ),
    summary="내 활동 요약 조회",
    dependencies=[Depends(no_query_parameters)],
)
def get_my_activity(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> ActivityCounts:
    return activity_counts(db, current_user)


@router.patch(
    "/me/marketing-consent",
    response_model=MarketingConsentResponse,
    responses=error_responses(
        AuthErrors.REQUIRED,
        CommonErrors.INVALID_REQUEST,
        UserErrors.CONSENT_CONFIGURATION,
        UserErrors.CONSENT_SAVE_FAILED,
    ),
    summary="마케팅 수신 동의 변경",
    dependencies=[Depends(no_query_parameters)],
)
def patch_marketing_consent(
    request: MarketingConsentRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> MarketingConsentResponse:
    return change_marketing_consent(db, current_user.uuid, request.marketingAgreed)


@router.get(
    "/{userUuid}/profile",
    response_model=OtherProfileResponse,
    responses=error_responses(*_ERRORS, UserErrors.NOT_FOUND),
    summary="다른 회원 프로필 조회",
    dependencies=[Depends(no_query_parameters)],
    description="정책 확정 전 목데이터로 프로필과 차단 상태를 반환합니다.",
)
def get_member_profile(
    userUuid: UUID, current_user: Annotated[User, Depends(get_current_user)]
) -> OtherProfileResponse:
    return get_other_profile(current_user.uuid, userUuid)
