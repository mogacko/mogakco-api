"""이벤트 종료 상태를 매일 03:00 KST에 갱신한다."""

import logging
from datetime import datetime, time, timedelta
from time import sleep

from sqlalchemy.orm import Session

from app.database import get_engine
from app.services.event import advance_event_lifecycle
from app.time import KST, kst_now

logger = logging.getLogger(__name__)
RUN_AT = time(3)


def seconds_until_next_run(now: datetime) -> float:
    current = now.astimezone(KST)
    next_run = datetime.combine(current.date(), RUN_AT, KST)
    if next_run <= current:
        next_run += timedelta(days=1)
    return (next_run - current).total_seconds()


def main() -> None:
    while True:
        wait_seconds = seconds_until_next_run(kst_now())
        with Session(get_engine()) as db:
            updated = advance_event_lifecycle(db)
        logger.info("Advanced %d event lifecycle statuses", updated)
        sleep(wait_seconds)


if __name__ == "__main__":
    main()
