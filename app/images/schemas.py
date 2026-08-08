from pydantic import BaseModel, ConfigDict


class UploadUrlRequest(BaseModel):
    content_type: str


class UploadUrlResponse(BaseModel):
    asset_id: int
    upload_url: str
    fields: dict[str, str]


class AssetResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    url: str
    content_type: str
    size_bytes: int | None
