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
    # 프로필 이미지는 업로드에 인증이 필요하므로 가입 후 PATCH /me로 연결한다.
    field: str | None = None
    introduction: str | None = None
    organization: str | None = None
    stack: str | None = None
    interests: str | None = None
