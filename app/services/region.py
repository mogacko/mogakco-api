"""지역을 조회하고 API에서 사용할 수 있는 상태인지 검증한다."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.exceptions import NotFoundException
from app.models import Region
from app.region.errors import RegionErrors


def enabled_region(db: Session, region_name: str) -> Region:
    """이름과 일치하는 활성 지역을 반환한다.

    존재하지 않는 지역과 비활성 지역을 동일하게 취급해, 호출자가 비활성 지역의
    내부 정보까지 구분해서 알 수 없도록 한다.
    """

    region = db.scalar(select(Region).where(Region.name == region_name))
    if region is None or not region.is_enabled:
        raise NotFoundException(RegionErrors.NOT_FOUND)
    return region
