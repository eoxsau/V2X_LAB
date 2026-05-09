from __future__ import annotations

import re
from typing import Any


def parse_scenario_template(text: str) -> dict[str, object]:
    area = "Seoul downtown"
    if "강남" in text or "Gangnam" in text:
        area = "Gangnam Station"
    elif "서울역" in text:
        area = "Seoul Station"

    surrounding_count = _surrounding_vehicle_count(text, default=20)
    mode = "six_g_like_v2x" if "6G" in text or "six" in text.lower() else "urban_mobility"
    obstacles = []
    if "사고" in text or "accident" in text.lower():
        obstacles.append({"type": "accident_zone"})
    if "공사" in text or "construction" in text.lower():
        obstacles.append({"type": "construction_zone"})

    return {
        "area": area,
        "ego_vehicle_count": 1,
        "surrounding_vehicle_count": surrounding_count,
        "obstacles": obstacles,
        "mode": mode,
    }


def explain_results_template(context: dict[str, Any]) -> str:
    route = context.get("recommended_route", {})
    metrics = context.get("metrics", {})
    latency = metrics.get("avg_vehicle_latency", "현재")
    congestion = metrics.get("avg_road_congestion", "현재")
    return (
        f"추천 경로는 {route.get('name', '현재 추천 경로')}입니다. 기존 경로보다 거리는 길 수 있지만, "
        f"도로 혼잡도({congestion})와 네트워크 지연({latency} ms)을 함께 고려했을 때 전체 V2X 안정성이 더 높습니다. "
        "이 설명은 템플릿 기반 fallback이며, 경로 계산에는 LLM을 사용하지 않습니다."
    )


def _surrounding_vehicle_count(text: str, default: int) -> int:
    patterns = [
        r"주변\s*차량\s*(\d+)\s*대",
        r"surrounding\s*vehicles?\s*(\d+)",
        r"nearby\s*vehicles?\s*(\d+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return int(match.group(1))
    numbers = [int(item) for item in re.findall(r"\d+", text)]
    return numbers[0] if numbers else default
