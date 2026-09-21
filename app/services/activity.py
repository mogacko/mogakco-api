"""전체 지역의 본인 활동 집계. 다른 회원 실연동 시에도 같은 함수를 사용한다."""

from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.exceptions import InternalServerException
from app.models import CommunityPost, User
from app.schemas.user import ActivityCounts
from app.services import profile_sources
from app.services.event import select_events_with_stats
from app.time import KST, kst_now
from app.user.errors import UserErrors

# 행사 목록의 현재 보존 기간. 담당자 공통 조회 연결 시 함께 교체한다.
EVENT_HISTORY_DAYS = 7


def activity_counts(
    db: Session, user: User, *, now: datetime | None = None
) -> ActivityCounts:
    reference = now if now is not None else kst_now()
    oldest_event = reference.astimezone(KST).date() - timedelta(days=EVENT_HISTORY_DAYS)
    try:
        # 기존 행사 조회의 공개 상태·삭제 조건·본인 참가 판정을 그대로 재사용한다.
        events = select_events_with_stats(user.id).subquery()
        event_count = (
            select(func.count())
            .select_from(events)
            .where(events.c.is_participating.is_(True), events.c.date >= oldest_event)
            .scalar_subquery()
        )
        # 지역 운영 여부와 게시판을 제한하지 않는다. 댓글 통계나 Redis 조회도 없다.
        post_count = (
            select(func.count(CommunityPost.id))
            .where(
                CommunityPost.author_id == user.id, CommunityPost.deleted_at.is_(None)
            )
            .scalar_subquery()
        )
        applied_events, authored_posts = db.execute(
            select(event_count, post_count)
        ).one()
        meetings = profile_sources.joined_mogacko_count(user.uuid, reference)
        return ActivityCounts(
            joinedMeetingCount=meetings,
            appliedEventCount=applied_events,
            authoredPostCount=authored_posts,
        )
    except SQLAlchemyError as error:
        raise InternalServerException(UserErrors.ACTIVITY_LOOKUP_FAILED) from error
