# backend/app/core/llm_judge.py

from __future__ import annotations
from backend.app.core.prompts import SYSTEM_POLICY_ANALYZER, POLICY_ANALYSIS_PROMPT
from backend.app.core.llm_client import call_llm_json

from urllib.parse import urlparse

def _domain_from_url(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower()
    except Exception:
        return ""

async def llm_analyze_policy(url: str, text: str) -> dict:
    domain = _domain_from_url(url)
    prompt = POLICY_ANALYSIS_PROMPT.format(
        url=url,
        domain=domain,
        text=text[:20000],
    )
    return await call_llm_json(SYSTEM_POLICY_ANALYZER, prompt)
