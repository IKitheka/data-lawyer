from pydantic import BaseModel
from typing import Optional, Any

class PolicyAnalyzeInput(BaseModel):
    url: str
    text: str
    service_name: Optional[str] = None

class PolicyAnalysisOut(BaseModel):
    id: int
    url: str
    domain: str
    service_name: str | None = None
    risk_level: str
    risk_score: float
    summary: str
    jurisdiction: str
    red_flags: list[str]
    reasoning: dict[str, Any]
    extracted: dict[str, Any]
    policy_changed: bool
    llm_model: str | None = None
    llm_used: str | None = None
    model: str | None = None



    class Config:
        from_attributes = True
