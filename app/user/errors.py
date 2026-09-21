from app.exceptions import (
    ConflictException,
    DomainValidationException,
    ErrorSpec,
    InternalServerException,
    NotFoundException,
)


class UserErrors:
    CONSENT_CONFIGURATION = ErrorSpec(
        InternalServerException, "CONFIGURATION_ERROR", "설정을 처리하지 못했습니다."
    )
    CONSENT_SAVE_FAILED = ErrorSpec(
        InternalServerException,
        "INTERNAL_SERVER_ERROR",
        "수신 동의를 변경하지 못했습니다.",
    )
    ACTIVITY_LOOKUP_FAILED = ErrorSpec(
        InternalServerException,
        "INTERNAL_SERVER_ERROR",
        "활동 정보를 불러오지 못했습니다.",
    )
    NICKNAME_LOOKUP_FAILED = ErrorSpec(
        InternalServerException,
        "INTERNAL_SERVER_ERROR",
        "닉네임을 확인하지 못했습니다.",
    )
    UPDATE_DATA_INCONSISTENT = ErrorSpec(
        InternalServerException,
        "PROFILE_DATA_INCONSISTENT",
        "프로필 정보를 처리하지 못했습니다.",
    )
    NICKNAME_CONFLICT = ErrorSpec(
        ConflictException, "USER_NICKNAME_CONFLICT", "이미 사용 중인 닉네임입니다."
    )
    INVALID_NICKNAME = ErrorSpec(
        DomainValidationException, "INVALID_REQUEST", "닉네임 형식을 확인해주세요."
    )
    NOT_FOUND = ErrorSpec(
        NotFoundException, "USER_NOT_FOUND", "회원을 찾을 수 없습니다."
    )
    DATA_INCONSISTENT = ErrorSpec(
        InternalServerException,
        "PROFILE_DATA_INCONSISTENT",
        "프로필 정보를 불러오지 못했습니다.",
    )
