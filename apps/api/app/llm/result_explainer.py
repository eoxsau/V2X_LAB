from __future__ import annotations

import json
from typing import Any

from app.llm.template_explainer import explain_results_template


def build_result_prompt(context: dict[str, Any]) -> str:
    compact = json.dumps(context, ensure_ascii=False, default=str)[:5000]
    return (
        "다음 V2X 시뮬레이션 결과를 한국어로 간결하게 설명해줘. "
        "경로 계산을 변경하지 말고, 결과 해석만 제공해. 입력:\n"
        f"{compact}"
    )


def normalize_explanation(text: str, context: dict[str, Any]) -> dict[str, object]:
    content = text.strip() or explain_results_template(context)
    return {
        "language": "ko",
        "content": content[:1600],
        "control_policy": "explanation_only_no_route_calculation",
    }


def explain_with_template(context: dict[str, Any]) -> dict[str, object]:
    return normalize_explanation(explain_results_template(context), context)
