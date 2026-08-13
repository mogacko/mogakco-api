from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.orm import Session

from app.auth.token import get_current_user
from app.db import get_db
from app.terms.schemas import MarketingConsentRequest
from app.terms.service import set_marketing_consent
from app.users.model import User

router = APIRouter(tags=["terms"])

@router.put("/me/marketing-consent", status_code=status.HTTP_204_NO_CONTENT, summary="마케팅 수신 동의 변경")
def put_marketing_consent(
    request: MarketingConsentRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Response:
    set_marketing_consent(db, current_user.id, request.agreed)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
