"""이벤트 도메인의 API 오류."""

from app.exceptions import (
    BadRequestException,
    ConflictException,
    ErrorSpec,
    ForbiddenException,
    NotFoundException,
    TooManyRequestsException,
)


class EventErrors:
    NOT_FOUND = ErrorSpec(
        NotFoundException,
        "EVENT_NOT_FOUND",
        "이벤트를 찾을 수 없습니다.",
    )
    INVALID_DATE = ErrorSpec(
        BadRequestException,
        "INVALID_EVENT_DATE",
        "신청 마감일은 오늘부터 행사 날짜 이전이어야 합니다.",
    )
    INVALID_TIME = ErrorSpec(
        BadRequestException,
        "INVALID_EVENT_TIME",
        "행사 종료 시간은 시작 시간보다 늦어야 합니다.",
    )
    NOT_OWNER = ErrorSpec(
        ForbiddenException,
        "EVENT_NOT_OWNER",
        "본인이 등록한 행사만 수정하거나 취소할 수 있습니다.",
    )
    NOT_CANCELLABLE = ErrorSpec(
        ConflictException,
        "EVENT_NOT_CANCELLABLE",
        "취소할 수 없는 행사 상태입니다.",
    )
    EDIT_ALREADY_PENDING = ErrorSpec(
        ConflictException,
        "EVENT_EDIT_ALREADY_PENDING",
        "승인 대기 중인 수정 요청이 있습니다.",
    )
    NOT_EDITABLE = ErrorSpec(
        ConflictException,
        "EVENT_NOT_EDITABLE",
        "수정할 수 없는 행사 상태입니다.",
    )
    PARTICIPATION_BUSY = ErrorSpec(
        TooManyRequestsException,
        "EVENT_PARTICIPATION_BUSY",
        "참가 요청이 많습니다. 잠시 후 다시 시도해주세요.",
    )
    ALREADY_APPLIED = ErrorSpec(
        ConflictException,
        "ALREADY_APPLIED",
        "이미 신청한 행사입니다.",
    )
    CLOSED = ErrorSpec(
        BadRequestException,
        "EVENT_CLOSED",
        "모집 기간이 종료되었습니다.",
    )
    FULL = ErrorSpec(
        ConflictException,
        "EVENT_FULL",
        "정원이 마감되어 신청할 수 없습니다.",
    )
    HOST_CANNOT_CANCEL_PARTICIPATION = ErrorSpec(
        BadRequestException,
        "HOST_CANNOT_CANCEL_PARTICIPATION",
        "행사 등록자는 참가 신청을 취소할 수 없습니다.",
    )
    APPLICATION_NOT_FOUND = ErrorSpec(
        BadRequestException,
        "APPLICATION_NOT_FOUND",
        "신청 내역이 존재하지 않거나 이미 취소되었습니다.",
    )
    CANCEL_PERIOD_EXPIRED = ErrorSpec(
        BadRequestException,
        "CANCEL_PERIOD_EXPIRED",
        "취소 가능 기간이 지났습니다.",
    )
