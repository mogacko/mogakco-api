"""차단 정책 확정 전 사용하는 개발용 공개 프로필 fixture.

실제 사용자/차단 조회는 이 공급자를 교체해 연결한다.
"""

from datetime import datetime
from uuid import UUID

from app.exceptions import NotFoundException
from app.schemas.user import OtherProfileResponse, PublicProfile
from app.time import KST
from app.user.errors import UserErrors

MOCK_VIEWER_UUID = UUID("923740fa-dd11-4128-8ad1-f5794dc21f39")
MOCK_MEMBER_UUID = UUID("d7fdca21-4c91-4a8c-92bf-bd38fbb0b7d0")
MOCK_BLOCKED_UUID = UUID("819d4e53-d569-4b4c-a8d6-69efb8949357")
MOCK_BLOCKS = frozenset({(MOCK_VIEWER_UUID, MOCK_BLOCKED_UUID)})


def get_other_profile(viewer_uuid: UUID, target_uuid: UUID) -> OtherProfileResponse:
    # 실제 회원·차단 조회가 준비되면 이 함수의 구현만 교체한다.
    members = {
        MOCK_VIEWER_UUID: "evan",
        MOCK_MEMBER_UUID: "지우",
        MOCK_BLOCKED_UUID: "개발하는곰",
    }
    if target_uuid not in members:
        raise NotFoundException(UserErrors.NOT_FOUND)
    if (viewer_uuid, target_uuid) in MOCK_BLOCKS:
        return OtherProfileResponse(userUuid=target_uuid, isBlocked=True, profile=None)
    return OtherProfileResponse(
        userUuid=target_uuid,
        isBlocked=False,
        profile=PublicProfile(
            nickname=members[target_uuid],
            activityRegionName="seoul",
            field="백엔드",
            affiliation="숙명여대",
            bio="Spring 공부 중입니다.",
            stacks=["Java", "Spring"],
            interests=["백엔드", "사이드 프로젝트"],
            profileImageUrl=None,
            joinedAt=datetime(2026, 3, 1, 10, tzinfo=KST),
            isStaff=False,
        ),
    )
