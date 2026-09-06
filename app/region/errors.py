"""지역 도메인의 API 오류."""

from app.exceptions import ErrorSpec, NotFoundException


class RegionErrors:
    NOT_FOUND = ErrorSpec(
        NotFoundException,
        "REGION_NOT_FOUND",
        "지역을 찾을 수 없습니다.",
    )
