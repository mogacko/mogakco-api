from datetime import datetime
from typing import Annotated, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.models import (
    CommentTargetType,
    CommunityPostBoard,
    CommunityPostCategory,
)
from app.services.community import validate_community_post_category

CommunityPostTitle = Annotated[
    str, StringConstraints(min_length=1, max_length=25)
]
CommunityPostBody = Annotated[
    str, StringConstraints(min_length=1, max_length=3_000)
]
CommentBody = Annotated[str, StringConstraints(min_length=1, max_length=300)]


class _RequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CommunityPostCreateRequest(_RequestModel):
    boardName: CommunityPostBoard
    categoryName: CommunityPostCategory | None = None
    title: CommunityPostTitle
    body: CommunityPostBody

    @model_validator(mode="after")
    def validate_category(self) -> Self:
        validate_community_post_category(self.boardName, self.categoryName)
        return self


class CommunityPostUpdateRequest(_RequestModel):
    model_config = ConfigDict(json_schema_extra={"minProperties": 1})

    title: CommunityPostTitle | None = None
    body: CommunityPostBody | None = None
    categoryName: CommunityPostCategory | None = None

    @model_validator(mode="after")
    def require_change(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("at least one field is required")
        if "title" in self.model_fields_set and self.title is None:
            raise ValueError("title cannot be null")
        if "body" in self.model_fields_set and self.body is None:
            raise ValueError("body cannot be null")
        return self


class CommunityPostListItem(BaseModel):
    uuid: UUID
    categoryName: CommunityPostCategory | None
    title: str
    body: str
    authorNickname: str | None
    authorAvatarUrl: str | None
    createdAt: datetime
    updatedAt: datetime | None
    likeCount: int = Field(ge=0)
    commentCount: int = Field(ge=0)
    isLiked: bool
    isPopular: bool


class CommunityPostDetailResponse(BaseModel):
    uuid: UUID
    regionName: str
    boardName: CommunityPostBoard
    title: str
    body: str
    authorUuid: UUID | None
    authorNickname: str | None
    authorAvatarUrl: str | None
    createdAt: datetime
    updatedAt: datetime | None
    likeCount: int = Field(ge=0)
    isLiked: bool


class CommunityPostPageResponse(BaseModel):
    items: list[CommunityPostListItem]
    offset: int = Field(ge=0)
    limit: int = Field(ge=1, le=50)
    hasMore: bool


class LikeResponse(BaseModel):
    likeCount: int = Field(ge=0)
    isLiked: bool


class CommentCreateRequest(_RequestModel):
    targetType: CommentTargetType
    targetUuid: UUID
    parentUuid: UUID | None = None
    body: CommentBody


class CommentUpdateRequest(_RequestModel):
    body: CommentBody


class CommentResponse(BaseModel):
    uuid: UUID
    parentUuid: UUID | None
    authorUuid: UUID | None
    authorNickname: str | None
    authorAvatarUrl: str | None
    body: str
    createdAt: datetime
    updatedAt: datetime | None
    isDeleted: bool
    isMine: bool


class CommentThread(BaseModel):
    comment: CommentResponse
    masked: bool = Field(
        description=(
            "운영자에 의해 숨김 처리됐는지 여부입니다. 운영자 숨김 기능이 "
            "아직 구현되지 않아 현재 응답은 항상 false입니다."
        )
    )
    replies: list[CommentResponse]


class CommentThreadResponse(BaseModel):
    count: int = Field(ge=0)
    items: list[CommentThread]
