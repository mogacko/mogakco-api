from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.orm import Session

from app.auth.token import get_current_user
from app.db import get_db
from app.terms.schemas import CurrentTermResponse, MarketingConsentRequest
from app.terms.service import current_term_versions, set_marketing_consent
from app.users.model import User

router = APIRouter(tags=["terms"])


@router.get("/terms/current", response_model=list[CurrentTermResponse], summary="현재 약관 조회")
def get_current_terms(db: Session = Depends(get_db)) -> list[CurrentTermResponse]:
    return [
        CurrentTermResponse(
            code=term.code,
            required=term.required,
            version=version.version,
            content=version.content,
            effective_at=version.effective_at,
        )
        for term, version in current_term_versions(db)
    ]


@router.put("/me/marketing-consent", status_code=status.HTTP_204_NO_CONTENT, summary="마케팅 수신 동의 변경")
def put_marketing_consent(
    request: MarketingConsentRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Response:
    set_marketing_consent(db, current_user.id, request.agreed)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
