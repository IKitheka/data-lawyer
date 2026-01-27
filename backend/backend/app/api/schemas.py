from __future__ import annotations
from pydantic import BaseModel, field_validator

class AnalyzeRequest(BaseModel):
    url: str | None = None
    text: str | None = None

    @field_validator("url", "text")
    @classmethod
    def strip_strings(cls, v):
        if v is None:
            return None
        return str(v).strip()

    @field_validator("text")
    @classmethod
    def cap_text(cls, v):
        if v is None:
            return None
        return v[:20000]

    @field_validator("url")
    @classmethod
    def cap_url(cls, v):
        if v is None:
            return None
        return v[:2048]
