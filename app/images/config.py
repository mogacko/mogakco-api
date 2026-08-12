import os
from dataclasses import dataclass


def _required(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise ValueError(f"{name} 환경 변수가 필요합니다.")
    return value


@dataclass(frozen=True)
class StorageSettings:
    """S3 자격 증명은 환경 변수나 인스턴스 역할에서 boto3 기본 체인으로 읽는다."""

    bucket: str
    region: str
    public_base_url: str
    upload_ttl_seconds: int
    max_bytes: int

    @classmethod
    def from_env(cls) -> "StorageSettings":
        return cls(
            bucket=_required("S3_BUCKET"),
            region=_required("S3_REGION"),
            public_base_url=_required("S3_PUBLIC_BASE_URL").rstrip("/"),
            upload_ttl_seconds=int(os.getenv("S3_UPLOAD_TTL_SECONDS", "300")),
            max_bytes=int(os.getenv("S3_MAX_UPLOAD_BYTES", str(5 * 1024 * 1024))),
        )
