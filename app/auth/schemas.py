from pydantic import BaseModel


class TokenExchangeRequest(BaseModel):
    code: str


class RefreshRequest(BaseModel):
    refresh_token: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class SignupRequest(BaseModel):
    code: str
    nickname: str
    activity_region: str
    agreed_term_version_ids: list[int]
    profile_image_url: str | None = None
    field: str | None = None
    introduction: str | None = None
    organization: str | None = None
    stack: str | None = None
    interests: str | None = None
