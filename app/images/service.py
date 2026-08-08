from uuid import uuid4

import boto3
from botocore.exceptions import ClientError
from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.images.config import StorageSettings
from app.images.model import MediaAsset

EXTENSIONS = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}

# 확장자와 Content-Type은 위조할 수 있으므로 파일 앞부분의 시그니처로 실제 형식을 확인한다.
_SIGNATURES = (b"\xff\xd8\xff", b"\x89PNG\r\n\x1a\n")
HEAD_BYTES = 512


def is_image(head: bytes) -> bool:
    # WEBP는 RIFF 컨테이너라 앞 4바이트와 8~12바이트를 함께 봐야 한다.
    return head.startswith(_SIGNATURES) or (head[:4] == b"RIFF" and head[8:12] == b"WEBP")


def s3_client(settings: StorageSettings):
    # 리전 엔드포인트를 못 박아야 업로드가 307로 되돌아가지 않는다.
    return boto3.client(
        "s3", region_name=settings.region, endpoint_url=f"https://s3.{settings.region}.amazonaws.com"
    )


def create_upload(db: Session, owner_id: int, content_type: str, settings: StorageSettings) -> tuple[MediaAsset, dict]:
    extension = EXTENSIONS.get(content_type)
    if extension is None:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="이미지 파일이 아닙니다.")
    key = f"images/{uuid4().hex}.{extension}"
    asset = MediaAsset(
        owner_id=owner_id,
        key=key,
        url=f"{settings.public_base_url}/{key}",
        content_type=content_type,
        status="PENDING",
    )
    db.add(asset)
    db.commit()
    db.refresh(asset)
    # 용량 제한은 S3가 업로드 시점에 거절하도록 정책에 담는다.
    presigned = s3_client(settings).generate_presigned_post(
        Bucket=settings.bucket,
        Key=key,
        Fields={"Content-Type": content_type},
        Conditions=[{"Content-Type": content_type}, ["content-length-range", 1, settings.max_bytes]],
        ExpiresIn=settings.upload_ttl_seconds,
    )
    return asset, presigned


def complete_upload(db: Session, owner_id: int, asset_id: int, settings: StorageSettings) -> MediaAsset:
    asset = db.get(MediaAsset, asset_id)
    if asset is None or asset.owner_id != owner_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="업로드를 찾을 수 없습니다.")
    if asset.status == "READY":
        return asset

    client = s3_client(settings)
    try:
        # 앞 512바이트만 받아 오므로 파일 크기와 무관하게 서버 부담이 없다.
        response = client.get_object(Bucket=settings.bucket, Key=asset.key, Range=f"bytes=0-{HEAD_BYTES - 1}")
    except ClientError:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="업로드된 파일이 없습니다.") from None

    if not is_image(response["Body"].read(HEAD_BYTES)):
        client.delete_object(Bucket=settings.bucket, Key=asset.key)
        db.delete(asset)
        db.commit()
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="이미지 파일이 아닙니다.")

    content_range = response.get("ContentRange")
    asset.size_bytes = int(content_range.split("/")[-1]) if content_range else response["ContentLength"]
    asset.status = "READY"
    db.commit()
    db.refresh(asset)
    return asset
