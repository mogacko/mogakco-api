from datetime import timedelta

from app.time import kst_now


def test_kst_now_is_timezone_aware() -> None:
    now = kst_now()

    assert now.tzinfo is not None
    assert now.utcoffset() == timedelta(hours=9)
