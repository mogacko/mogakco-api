from datetime import datetime

from pydantic import BaseModel


class CurrentTermResponse(BaseModel):
    code: str
    required: bool
    version: str
    content: str
    effective_at: datetime


class MarketingConsentRequest(BaseModel):
    agreed: bool
