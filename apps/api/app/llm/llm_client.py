from __future__ import annotations

from typing import Any

from app.core.config import settings
from app.llm.gemini_client import call_gemini
from app.llm.huggingface_client import call_huggingface
from app.llm.result_explainer import build_result_prompt, explain_with_template, normalize_explanation
from app.llm.scenario_parser import build_scenario_prompt, parse_llm_scenario_text, parse_template_scenario


def get_llm_status() -> dict[str, object]:
    provider = settings.llm_provider if settings.llm_enabled else "template"
    has_key = {
        "gemini": bool(settings.gemini_api_key),
        "huggingface": bool(settings.huggingface_api_key),
        "template": True,
    }.get(provider, False)
    active_provider = provider if settings.llm_enabled and has_key else "template"
    return {
        "enabled": settings.llm_enabled,
        "configured_provider": settings.llm_provider,
        "active_provider": active_provider,
        "fallback_active": active_provider == "template",
        "keys": {
            "gemini": "configured" if settings.gemini_api_key else "missing",
            "huggingface": "configured" if settings.huggingface_api_key else "missing",
        },
        "policy": "LLM may parse setup/explain results only; it does not calculate routes or directly control simulation.",
    }


async def parse_scenario(text: str) -> dict[str, object]:
    status = get_llm_status()
    provider = str(status["active_provider"])
    if provider == "gemini":
        raw = await call_gemini(build_scenario_prompt(text), settings.gemini_api_key)
        parsed = parse_llm_scenario_text(raw)
    elif provider == "huggingface":
        raw = await call_huggingface(build_scenario_prompt(text), settings.huggingface_api_key)
        parsed = parse_llm_scenario_text(raw)
    else:
        parsed = parse_template_scenario(text)
    return {"provider_status": status, "result": parsed}


async def explain_results(context: dict[str, Any]) -> dict[str, object]:
    status = get_llm_status()
    provider = str(status["active_provider"])
    if provider == "gemini":
        raw = await call_gemini(build_result_prompt(context), settings.gemini_api_key)
        explanation = normalize_explanation(raw, context)
    elif provider == "huggingface":
        raw = await call_huggingface(build_result_prompt(context), settings.huggingface_api_key)
        explanation = normalize_explanation(raw, context)
    else:
        explanation = explain_with_template(context)
    return {"provider_status": status, "explanation": explanation}
