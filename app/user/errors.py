from app.exceptions import (
    ConflictException,
    DomainValidationException,
    ErrorSpec,
    InternalServerException,
    NotFoundException,
)


class UserErrors:
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
