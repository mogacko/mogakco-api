from datetime import datetime
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.models import CommentTargetType, PostBoard, PostCategory
from app.services.community import validate_post_category

PostTitle = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=60)
]
PostBody = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=10_000)
]
CommentBody = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)
]


class _RequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PostCreateRequest(_RequestModel):
    boardCode: PostBoard
    categoryCode: PostCategory | None = None
    title: PostTitle
    body: PostBody

    @model_validator(mode="after")
    def validate_category(self) -> Self:
        validate_post_category(self.boardCode, self.categoryCode)
        return self


class PostUpdateRequest(_RequestModel):
    title: PostTitle | None = None
    body: PostBody | None = None
    categoryCode: PostCategory | None = None

    @model_validator(mode="after")
    def require_change(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("at least one field is required")
        if "title" in self.model_fields_set and self.title is None:
            raise ValueError("title cannot be null")
        if "body" in self.model_fields_set and self.body is None:
            raise ValueError("body cannot be null")
        return self


class PostResponse(BaseModel):
    id: int
    chapterCode: str
    boardCode: PostBoard
    categoryCode: PostCategory | None
    title: str
    body: str
    authorId: int | None
    authorNickname: str
    authorAvatarUrl: str | None = None
    createdAt: datetime
    editedAt: datetime | None
    likeCount: int = Field(ge=0)
    commentCount: int = Field(ge=0)
    isLiked: bool


class PostPageResponse(BaseModel):
    items: list[PostResponse]
    offset: int = Field(ge=0)
    limit: int = Field(ge=1, le=50)
    total: int = Field(ge=0)
    hasMore: bool
    boardTotal: int | None = Field(default=None, ge=0)
    categoryCounts: dict[PostCategory, int] | None = None


class PopularPostsResponse(BaseModel):
    items: list[PostResponse]


class LikeResponse(BaseModel):
    likeCount: int = Field(ge=0)
    isLiked: bool


class CommentCreateRequest(_RequestModel):
    targetType: CommentTargetType
    targetId: int = Field(gt=0)
    parentId: int | None = Field(default=None, gt=0)
    body: CommentBody


class CommentUpdateRequest(_RequestModel):
    body: CommentBody


class CommentResponse(BaseModel):
    id: int
    targetType: CommentTargetType
    targetId: int
    parentId: int | None
    authorId: int | None
    authorNickname: str
    authorAvatarUrl: str | None = None
    body: str
    createdAt: datetime
    editedAt: datetime | None
    isDeleted: bool
    isMine: bool


class CommentThread(BaseModel):
    comment: CommentResponse
    masked: bool
    replies: list[CommentResponse]


class CommentThreadResponse(BaseModel):
    count: int = Field(ge=0)
    items: list[CommentThread]
