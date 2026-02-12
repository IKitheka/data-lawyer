# backend/app/core/llm_client.py

from __future__ import annotations
import os
import json
import httpx

DEFAULT_MODEL = os.getenv("DL_LLM_MODEL", "gpt-4o-mini")

class LLMError(Exception):
    pass

async def call_llm_json(system: str, prompt: str) -> dict:
    """
    Calls OpenAI Chat Completions and expects JSON back.
    Requires: OPENAI_API_KEY in env.
    """
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise LLMError("OPENAI_API_KEY is not set")

    url = "https://api.openai.com/v1/chat/completions"

    payload = {
        "model": DEFAULT_MODEL,
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
    }

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(url, headers=headers, json=payload)

    if r.status_code >= 400:
        raise LLMError(f"LLM HTTP {r.status_code}: {r.text[:400]}")

    data = r.json()
    content = data["choices"][0]["message"]["content"]

    try:
        return json.loads(content)
    except Exception:
        raise LLMError(f"LLM returned non-JSON: {content[:300]}")
