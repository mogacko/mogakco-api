"""커뮤니티 도메인의 API 오류."""

from app.exceptions import (
    DomainValidationException,
    ErrorSpec,
    NotFoundException,
    ServiceUnavailableException,
)


class CommunityErrors:
    POST_NOT_FOUND = ErrorSpec(
        NotFoundException,
        "COMMUNITY_POST_NOT_FOUND",
        "게시글을 찾을 수 없습니다.",
    )
    COMMENT_TARGET_NOT_FOUND = ErrorSpec(
        NotFoundException,
        "COMMENT_TARGET_NOT_FOUND",
        "댓글 대상을 찾을 수 없습니다.",
    )
    PARENT_COMMENT_NOT_FOUND = ErrorSpec(
        NotFoundException,
        "PARENT_COMMENT_NOT_FOUND",
        "부모 댓글을 찾을 수 없습니다.",
    )
    INVALID_COMMENT_PARENT = ErrorSpec(
        DomainValidationException,
        "INVALID_COMMENT_PARENT",
        "부모 댓글이 올바르지 않습니다.",
    )
    COMMENT_NOT_FOUND = ErrorSpec(
        NotFoundException,
        "COMMENT_NOT_FOUND",
        "댓글을 찾을 수 없습니다.",
    )
    INVALID_MENU = ErrorSpec(
        DomainValidationException,
        "INVALID_COMMUNITY_MENU",
        "게시판 또는 카테고리가 올바르지 않습니다.",
    )
    LIKE_SERVICE_UNAVAILABLE = ErrorSpec(
        ServiceUnavailableException,
        "LIKE_SERVICE_UNAVAILABLE",
        "좋아요 서비스를 이용할 수 없습니다.",
    )
