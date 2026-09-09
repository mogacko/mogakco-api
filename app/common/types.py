"""여러 API 도메인에서 공유하는 값 타입."""

from datetime import time
from typing import Annotated

from pydantic import AfterValidator


def _reject_time_offset(value: time) -> time:
    """오프셋이 포함된 time 값을 거부한다."""

    if value.utcoffset() is not None:
        raise ValueError("time offset is not allowed")
    return value


OffsetFreeTime = Annotated[time, AfterValidator(_reject_time_offset)]
