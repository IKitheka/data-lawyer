from fastapi import APIRouter, Request

router = APIRouter()

@router.post("/analyze")
async def analyze_policy(request: Request, payload: dict):
    url = payload.get("url", "")
    text = payload.get("text", "")
    return {
        "risk_level": "UNKNOWN",
        "risk_score": None,
        "summary": f"Received policy text ({len(text)} chars).",
        "red_flags": [],
        "jurisdiction": "UNKNOWN",
        "service_name": url,
        "llm_model": "none",
    }
