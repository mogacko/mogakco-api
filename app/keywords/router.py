from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db import get_db
from app.keywords.service import suggest

router = APIRouter(tags=["keywords"])


@router.get("/keywords/suggest", response_model=list[str], summary="분야·스택·관심분야 자동완성")
def get_suggestions(
    kind: Literal["FIELD", "STACK", "INTEREST"],
    prefix: str = Query(default="", max_length=100),
    limit: int = Query(default=10, ge=1, le=20),
    db: Session = Depends(get_db),
) -> list[str]:
    return suggest(db, kind, prefix, limit)
