from __future__ import annotations

import json
import re

from app.llm.safety_validator import validate_scenario
from app.llm.template_explainer import parse_scenario_template


SCENARIO_PROMPT = """Extract only valid JSON for an autonomous V2X simulation scenario.
Schema:
{"area": string, "ego_vehicle_count": number, "surrounding_vehicle_count": number, "obstacles": [{"type": string}], "mode": "urban_mobility" | "six_g_like_v2x"}
Do not include markdown.
User request:
"""


def build_scenario_prompt(text: str) -> str:
    return f"{SCENARIO_PROMPT}{text}"


def parse_llm_scenario_text(text: str) -> dict[str, object]:
    try:
        parsed = json.loads(_extract_json(text))
    except Exception:
        parsed = parse_scenario_template(text)
    return validate_scenario(parsed)


def parse_template_scenario(text: str) -> dict[str, object]:
    return validate_scenario(parse_scenario_template(text))


def _extract_json(text: str) -> str:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    return match.group(0) if match else text
