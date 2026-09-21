"""프로필 요청 검증과 공개 범위별 응답 계약."""

import re
import unicodedata
from datetime import datetime
from typing import Annotated, Self
from uuid import UUID

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)


def nfc(value: str) -> str:
    return unicodedata.normalize("NFC", value)


def normalize_nickname(value: object) -> object:
    return nfc(value.strip()) if isinstance(value, str) else value


def validate_nickname(value: str) -> str:
    if not re.fullmatch(r"[가-힣A-Za-z0-9_]{2,12}", value):
        raise ValueError("invalid nickname")
    return value


Nickname = Annotated[
    str,
    StringConstraints(strict=True),
    BeforeValidator(normalize_nickname),
    AfterValidator(validate_nickname),
]
RequiredField = Annotated[
    str,
    StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=50),
]
Tag = Annotated[
    str,
    StringConstraints(strict=True, min_length=1, max_length=50),
    BeforeValidator(normalize_nickname),
]


def normalized_key(value: str) -> str:
    return nfc(value).casefold()


def sorted_values(values: list[str]) -> list[str]:
    distinct: dict[str, str] = {}
    for value in values:
        display = nfc(value.strip())
        distinct.setdefault(normalized_key(display), display)
    return sorted(distinct.values(), key=lambda value: (normalized_key(value), value))


class NicknameQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    nickname: Nickname


class NicknameAvailabilityResponse(BaseModel):
    nickname: str
    available: bool


class ProfileUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    nickname: Nickname | None = None
    field: RequiredField | None = None
    affiliation: (
        Annotated[str, StringConstraints(strict=True, max_length=20)] | None
    ) = None
    bio: Annotated[str, StringConstraints(strict=True, max_length=60)] | None = None
    stacks: list[Tag] | None = Field(default=None, max_length=10)
    interests: list[Tag] | None = Field(default=None, max_length=6)

    @field_validator("affiliation", "bio", mode="before")
    @classmethod
    def normalize_optional_text(cls, value: object) -> object:
        return value.strip() or None if isinstance(value, str) else value

    @model_validator(mode="after")
    def validate_patch(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("at least one field is required")
        for name in self.model_fields_set & {
            "nickname",
            "field",
            "stacks",
            "interests",
        }:
            if getattr(self, name) is None:
                raise ValueError(f"{name} cannot be null")
        # 개수 제한은 Pydantic에서 중복 제거 전에 적용한다.
        for name in ("stacks", "interests"):
            values = getattr(self, name)
            if values is not None:
                setattr(self, name, sorted_values(values))
        return self


class ProfileDetails(BaseModel):
    nickname: str
    activityRegionName: str
    field: str
    affiliation: str | None
    bio: str | None
    stacks: list[str]
    interests: list[str]
    profileImageUrl: str | None
    joinedAt: datetime


class MyProfileResponse(ProfileDetails):
    userUuid: UUID
    marketingConsent: bool
    marketingConsentChangedAt: datetime | None


class ActivityCounts(BaseModel):
    joinedMeetingCount: int = Field(ge=0)
    appliedEventCount: int = Field(ge=0)
    authoredPostCount: int = Field(ge=0)


class PublicProfile(ProfileDetails, ActivityCounts):
    pass


class OtherProfileResponse(BaseModel):
    userUuid: UUID
    isBlocked: bool
    profile: PublicProfile | None


class MarketingConsentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    marketingAgreed: bool


class MarketingConsentResponse(BaseModel):
    marketingAgreed: bool
    changedAt: datetime | None
