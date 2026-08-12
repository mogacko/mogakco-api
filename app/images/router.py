from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.auth.token import get_current_user
from app.db import get_db
from app.images.config import StorageSettings
from app.images.model import MediaAsset
from app.images.schemas import AssetResponse, UploadUrlRequest, UploadUrlResponse
from app.images.service import complete_upload, create_upload
from app.users.model import User

router = APIRouter(tags=["images"])


@router.post("/images/upload-url", response_model=UploadUrlResponse, summary="이미지 업로드 URL 발급")
def post_upload_url(
    request: UploadUrlRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> UploadUrlResponse:
    asset, presigned = create_upload(db, current_user.id, request.content_type, StorageSettings.from_env())
    return UploadUrlResponse(asset_id=asset.id, upload_url=presigned["url"], fields=presigned["fields"])


@router.post("/images/{asset_id}/complete", response_model=AssetResponse, summary="이미지 업로드 완료 검증")
def post_complete(
    asset_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> MediaAsset:
    return complete_upload(db, current_user.id, asset_id, StorageSettings.from_env())
