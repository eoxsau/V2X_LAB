from __future__ import annotations

import httpx

HF_URL = "https://api-inference.huggingface.co/models/mistralai/Mistral-7B-Instruct-v0.2"


async def call_huggingface(prompt: str, api_key: str) -> str:
    headers = {"Authorization": f"Bearer {api_key}"}
    async with httpx.AsyncClient(timeout=18) as client:
        response = await client.post(HF_URL, headers=headers, json={"inputs": prompt, "parameters": {"max_new_tokens": 400}})
        response.raise_for_status()
        data = response.json()
    if isinstance(data, list) and data:
        return str(data[0].get("generated_text", ""))
    return str(data)
