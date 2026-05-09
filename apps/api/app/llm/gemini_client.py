from __future__ import annotations

from typing import Any

import httpx

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent"


async def call_gemini(prompt: str, api_key: str) -> str:
    payload: dict[str, Any] = {"contents": [{"parts": [{"text": prompt}]}]}
    async with httpx.AsyncClient(timeout=12) as client:
        response = await client.post(GEMINI_URL, params={"key": api_key}, json=payload)
        response.raise_for_status()
        data = response.json()
    return str(data["candidates"][0]["content"]["parts"][0]["text"])
