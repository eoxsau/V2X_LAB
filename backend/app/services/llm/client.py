"""Multi-provider LLM client for V2X analysis.

Supported providers (set LLM_PROVIDER in .env):
  auto    — tries vertex → azure → bedrock in order of available keys
  vertex  — Google Vertex AI / Gemini API
  azure   — Azure OpenAI (GPT-4o or configured deployment)
  bedrock — AWS Bedrock (existing Claude endpoint)

All env vars are read at call time so dotenv can be loaded before first call.
"""
from __future__ import annotations

import json
import os
from pathlib import Path


# ── Helpers ───────────────────────────────────────────────────────────────────

def _cfg() -> dict:
    """Read provider configuration from environment at call time."""
    key_file = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "gcp_key.json")
    # Resolve relative to backend dir if not absolute
    if not os.path.isabs(key_file):
        key_file = str(Path(__file__).parent.parent.parent.parent / key_file)
    return {
        "provider":          os.environ.get("LLM_PROVIDER", "auto").lower(),
        "gcp_api_key":       os.environ.get("GCP_API_KEY", ""),
        "gcp_project":       os.environ.get("GCP_PROJECT_ID", "tta-v2x-lab-rookie"),
        "gcp_key_file":      key_file,
        "vertex_model":      os.environ.get("VERTEX_MODEL", "gemini-2.0-flash"),
        "azure_api_key":     os.environ.get("AZURE_AI_API_KEY", ""),
        "azure_oai_url":     os.environ.get("AZURE_OPENAI_ENDPOINT", ""),
        "azure_ai_url":      os.environ.get("AZURE_AI_ENDPOINT", ""),
        "azure_model":       os.environ.get("AZURE_MODEL", "gpt-4o"),
        "bedrock_key":       os.environ.get("BEDROCK_API_KEY", ""),
    }


def _resolve_provider(cfg: dict) -> str:
    if cfg["provider"] != "auto":
        return cfg["provider"]
    if cfg["gcp_api_key"] or os.path.isfile(cfg["gcp_key_file"]):
        return "vertex"
    if cfg["azure_api_key"] and (cfg["azure_ai_url"] or cfg["azure_oai_url"]):
        return "azure"
    if cfg["bedrock_key"]:
        return "bedrock"
    raise RuntimeError(
        "LLM 프로바이더가 설정되지 않았습니다. "
        ".env 파일에 GCP_API_KEY, AZURE_AI_API_KEY, 또는 BEDROCK_API_KEY를 설정하세요."
    )


# ── Provider implementations ──────────────────────────────────────────────────

def _call_vertex(prompt: str, cfg: dict) -> str:
    try:
        from google import genai
        from google.genai import types as genai_types
    except ImportError as e:
        raise RuntimeError("google-genai 패키지가 필요합니다: pip install google-genai") from e

    if cfg["gcp_api_key"]:
        client = genai.Client(api_key=cfg["gcp_api_key"])
    else:
        try:
            from google.oauth2 import service_account
            creds = service_account.Credentials.from_service_account_file(
                cfg["gcp_key_file"],
                scopes=["https://www.googleapis.com/auth/cloud-platform"],
            )
        except Exception as e:
            raise RuntimeError(f"GCP 서비스 계정 키 로드 실패 ({cfg['gcp_key_file']}): {e}") from e
        client = genai.Client(
            credentials=creds,
            project=cfg["gcp_project"],
            location="us-central1",
            vertexai=True,
        )

    response = client.models.generate_content(
        model=cfg["vertex_model"],
        contents=prompt,
        config=genai_types.GenerateContentConfig(
            max_output_tokens=4096,
            temperature=0.3,
        ),
    )
    return response.text


def _call_azure(prompt: str, cfg: dict) -> str:
    # Azure AI Foundry endpoint uses Bearer auth via standard OpenAI SDK (not AzureOpenAI).
    # GPT-5.x models require max_completion_tokens instead of max_tokens.
    try:
        from openai import OpenAI
    except ImportError as e:
        raise RuntimeError("openai 패키지가 필요합니다: pip install openai") from e

    # Extract base URL: strip trailing /api/projects/... path if present
    ai_url = cfg["azure_ai_url"]  # e.g. https://<resource>.services.ai.azure.com/api/projects/<proj>
    if "/api/projects" in ai_url:
        resource_base = ai_url.split("/api/projects")[0]
    else:
        resource_base = ai_url.rstrip("/")
    base_url = f"{resource_base}/openai/v1/"

    client = OpenAI(
        base_url=base_url,
        api_key=cfg["azure_api_key"],
    )
    response = client.chat.completions.create(
        model=cfg["azure_model"],
        messages=[{"role": "user", "content": prompt}],
        max_completion_tokens=4096,
        temperature=0.3,
    )
    return response.choices[0].message.content


def _call_bedrock(prompt: str, cfg: dict) -> str:
    import requests as _requests
    url = (
        "https://bedrock-runtime.us-east-1.amazonaws.com"
        "/model/us.anthropic.claude-sonnet-4-6/converse"
    )
    payload = {
        "messages": [{"role": "user", "content": [{"text": prompt}]}],
        "inferenceConfig": {"maxTokens": 4096, "temperature": 0.3},
    }
    resp = _requests.post(
        url,
        json=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {cfg['bedrock_key']}",
        },
        timeout=90,
    )
    resp.raise_for_status()
    raw = resp.json()
    return raw["output"]["message"]["content"][0]["text"]


# ── Public API ────────────────────────────────────────────────────────────────

def generate(prompt: str, provider: str | None = None) -> tuple[str, str]:
    """Call the configured LLM and return (text, provider_used).

    Args:
        prompt:   The prompt string to send.
        provider: Override the configured provider for this call.
                  Pass None to use LLM_PROVIDER from .env.
    """
    cfg = _cfg()
    if provider:
        cfg["provider"] = provider
    used = _resolve_provider(cfg)

    if used == "vertex":
        return _call_vertex(prompt, cfg), "vertex"
    elif used == "azure":
        return _call_azure(prompt, cfg), "azure"
    elif used == "bedrock":
        return _call_bedrock(prompt, cfg), "bedrock"
    else:
        raise ValueError(f"Unknown LLM provider: {used!r}")


def list_providers() -> list[dict]:
    """Return metadata about all configured providers."""
    cfg = _cfg()
    result = []

    gcp_ok = bool(cfg["gcp_api_key"]) or os.path.isfile(cfg["gcp_key_file"])
    if gcp_ok:
        auth = "api_key" if cfg["gcp_api_key"] else "service_account"
        result.append({
            "id": "vertex",
            "name": "Google Vertex AI / Gemini",
            "model": cfg["vertex_model"],
            "auth": auth,
            "ready": True,
        })

    azure_ok = bool(cfg["azure_api_key"]) and (bool(cfg["azure_ai_url"]) or bool(cfg["azure_oai_url"]))
    if azure_ok:
        result.append({
            "id": "azure",
            "name": "Azure AI / GPT",
            "model": cfg["azure_model"],
            "auth": "api_key",
            "ready": True,
        })

    if cfg["bedrock_key"]:
        result.append({
            "id": "bedrock",
            "name": "AWS Bedrock / Claude",
            "model": "claude-sonnet-4-6",
            "auth": "api_key",
            "ready": True,
        })

    active = _resolve_provider(cfg) if result else None
    for p in result:
        p["active"] = (p["id"] == active)

    return result
