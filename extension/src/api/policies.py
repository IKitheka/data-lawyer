from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(prefix="/policies", tags=["policies"])

class AnalyzeRequest(BaseModel):
    url: str
    text: str
    service_name: str | None = None

@router.post("/analyze")
def analyze_policy(payload: AnalyzeRequest):
    # TODO: replace with your real analysis logic
    text_lower = payload.text.lower()

    red_flags = []
    if "third party" in text_lower or "third-party" in text_lower:
        red_flags.append("Mentions sharing with third parties.")
    if "advertising" in text_lower:
        red_flags.append("Mentions advertising/marketing data use.")
    if "location" in text_lower:
        red_flags.append("Mentions collecting location data.")

    score = min(100, 20 + 20 * len(red_flags))
    level = "LOW" if score < 40 else "MEDIUM" if score < 70 else "HIGH"

    return {
        "url": payload.url,
        "service_name": payload.service_name,
        "risk_score": score,
        "risk_level": level,
        "summary": "Automated policy scan based on keyword signals.",
        "red_flags": red_flags,
        "recommendations": ["Review third-party sharing clauses carefully."] if red_flags else [],
        "jurisdiction": "UNKNOWN",
    }
