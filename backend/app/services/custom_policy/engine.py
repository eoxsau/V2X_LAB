"""
Custom Policy Engine — Stage 2 of the V2X algorithm customization.

Users define weighted-sum scoring policies as JSON objects.
No eval(), exec(), or Function() is used — all computation is pure
arithmetic over a fixed, validated feature dictionary.
"""
from __future__ import annotations

import json as _json
from typing import Any

# ── Allowed feature sets per policy type ──────────────────────────────────────

COST_FEATURES: frozenset[str] = frozenset({
    "distance", "time", "latency", "load", "handover", "blockage", "future_risk",
})

BS_FEATURES: frozenset[str] = frozenset({
    "distance", "latency", "load", "resource_deficit", "future_risk",
})

RESOURCE_FEATURES: frozenset[str] = frozenset({
    "demand", "load", "priority", "distance",
})

POLICY_KEYS: frozenset[str] = frozenset({
    "custom_cost_policy",
    "custom_bs_selection_policy",
    "custom_resource_policy",
})

_FEATURE_SETS: dict[str, frozenset[str]] = {
    "custom_cost_policy":         COST_FEATURES,
    "custom_bs_selection_policy": BS_FEATURES,
    "custom_resource_policy":     RESOURCE_FEATURES,
}

ALLOWED_TYPES: frozenset[str] = frozenset({"weighted_sum"})


# ── Core functions ─────────────────────────────────────────────────────────────

def parse_custom_policy(raw: "str | dict") -> dict:
    """
    Parse raw policy input (JSON string or dict).
    Raises ValueError for invalid JSON, TypeError for non-object result.
    """
    if isinstance(raw, str):
        try:
            result = _json.loads(raw)
        except _json.JSONDecodeError as exc:
            raise ValueError(f"JSON 파싱 오류: {exc}") from exc
    elif isinstance(raw, dict):
        result = dict(raw)
    else:
        raise TypeError("policy는 JSON 문자열 또는 dict이어야 합니다")
    if not isinstance(result, dict):
        raise TypeError("policy는 JSON 오브젝트이어야 합니다")
    return result


def validate_custom_policy(policy_key: str, policy: dict) -> tuple[bool, list[str]]:
    """
    Validate a custom policy against its schema.
    Returns (is_valid, errors_list).
    """
    if policy_key not in POLICY_KEYS:
        return False, [f"알 수 없는 policy key: '{policy_key}'"]
    if not isinstance(policy, dict):
        return False, ["policy는 JSON 오브젝트이어야 합니다"]

    errors: list[str] = []

    ptype = policy.get("type")
    if ptype not in ALLOWED_TYPES:
        errors.append(
            f"type은 {sorted(ALLOWED_TYPES)} 중 하나이어야 합니다 (현재: {ptype!r})"
        )

    allowed = _FEATURE_SETS[policy_key]
    weights = policy.get("weights")
    if not isinstance(weights, dict):
        errors.append("weights 필드가 필요합니다 (object)")
    else:
        if not weights:
            errors.append("weights에 최소 하나 이상의 항목이 필요합니다")
        weight_total = 0.0
        for k, v in weights.items():
            if k not in allowed:
                errors.append(
                    f"weights.{k}: 허용되지 않는 feature "
                    f"(허용: {sorted(allowed)})"
                )
            elif (
                not isinstance(v, (int, float))
                or v < 0
                or v != v           # NaN check
                or v == float("inf")
            ):
                errors.append(f"weights.{k}: 0 이상의 유한한 숫자이어야 합니다")
            else:
                weight_total += float(v)
        if not errors and weight_total == 0.0:
            errors.append("weights 합이 0입니다 — policy가 아무 효과를 내지 않습니다")

    constraints = policy.get("constraints", {})
    if not isinstance(constraints, dict):
        errors.append("constraints는 오브젝트이어야 합니다")
    else:
        for k, v in constraints.items():
            if k == "max_handover":
                if not isinstance(v, int) or v < 0:
                    errors.append(
                        "constraints.max_handover: 0 이상의 정수이어야 합니다"
                    )
            elif k == "max_disconnection_ratio":
                if not isinstance(v, (int, float)) or not (0.0 <= float(v) <= 1.0):
                    errors.append(
                        "constraints.max_disconnection_ratio: 0.0–1.0 범위이어야 합니다"
                    )
            else:
                errors.append(f"constraints.{k}: 알 수 없는 제약 조건 키")

    return len(errors) == 0, errors


def run_custom_weighted_policy(policy: dict, features: "dict[str, float]") -> float:
    """
    Compute a weighted-sum score from policy weights and feature values.

    Features must be pre-normalized to roughly [0, 1].
    Returns a non-negative float — higher means worse / more costly.
    Unknown or non-numeric feature values are silently skipped.
    """
    if policy.get("type") != "weighted_sum":
        return 0.0
    weights: dict[str, Any] = policy.get("weights", {})
    score = 0.0
    for feat, w in weights.items():
        val = features.get(feat, 0.0)
        try:
            score += float(w) * float(val)
        except (TypeError, ValueError):
            pass
    return max(0.0, score)
