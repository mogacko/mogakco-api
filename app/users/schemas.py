from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    nickname: str
    activity_region: str
    profile_image_url: str | None
    field: str | None
    introduction: str | None
    organization: str | None
    stack: str | None
    interests: str | None
    created_at: datetime
    updated_at: datetime


class UserUpdateRequest(BaseModel):
    nickname: str | None = Field(default=None, min_length=1, max_length=30)
    activity_region: str | None = None
    profile_image_url: str | None = Field(default=None, max_length=2048)
    field: str | None = Field(default=None, max_length=100)
    introduction: str | None = None
    organization: str | None = Field(default=None, max_length=100)
    stack: str | None = None
    interests: str | None = None
