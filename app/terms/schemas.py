from pydantic import BaseModel


class MarketingConsentRequest(BaseModel):
    agreed: bool
