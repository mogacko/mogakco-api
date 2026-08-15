from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import PostBoard, PostCategory, Region


def validate_post_category(
    board: PostBoard, category: PostCategory | None
) -> None:
    if board is PostBoard.TALK and category is None:
        raise ValueError("talk posts require a category")
    if board is not PostBoard.TALK and category is not None:
        raise ValueError("only talk posts accept a category")


def get_region_by_chapter_code(db: Session, chapter_code: str) -> Region | None:
    return db.scalar(select(Region).where(Region.name == chapter_code))
