from __future__ import annotations

import copy
import re
from typing import Any

from jsonschema import Draft202012Validator

from app.algorithms import (
    baseStationSelectionRegistry,
    latencyAlgorithmRegistry,
    resourceAllocationRegistry,
    routeAlgorithmRegistry,
)

DEFAULT_SIMULATION_CONFIG: dict[str, Any] = {
    "cost_weights": {
        "w_distance": 0.25,
        "w_latency": 0.25,
        "w_load": 0.2,
        "w_resource": 0.15,
        "w_future": 0.15,
    },
    "algorithm_selection": {
        "route_algorithm": "dijkstra",
        "latency_algorithm": "full_composite_latency",
        "base_station_selection_algorithm": "lowest_latency_bs",
        "resource_allocation_algorithm": "equal_allocation",
    },
    "policy_options": {
        "avoid_high_load_bs": False,
        "prefer_low_latency": False,
        "prefer_short_distance": True,
        "enable_future_scan": False,
    },
    "custom_policy": {
        "policy_name": "default_safe_policy",
        "policy_type": "cost_function",
        "rules": [],
        "parameters": {},
    },
    "data_column_mapping": {
        "source_type": "json",
        "entity_type": "generic",
        "field_mapping": {},
        "geometry_mapping": {},
    },
    "warnings": [],
}

ALLOWED_WEIGHT_KEYS = tuple(DEFAULT_SIMULATION_CONFIG["cost_weights"].keys())
ALLOWED_POLICY_TYPES = {
    "cost_function",
    "route_evaluator",
    "bs_selector",
    "allocator",
}
ALLOWED_ENTITY_TYPES = {
    "generic",
    "vehicle",
    "road_segment",
    "base_station",
    "edge_node",
    "obstacle",
    "route",
}
ALLOWED_SOURCE_TYPES = {"csv", "json", "excel", "geojson", "osm"}
ALLOWED_MAPPING_TARGETS = {
    "id",
    "name",
    "latitude",
    "longitude",
    "geometry",
    "speed",
    "heading",
    "capacity",
    "load",
    "frequency",
    "tx_power",
    "antenna_height",
    "congestion_score",
    "travel_time_estimate",
    "obstacle_risk",
    "source",
}
ALLOWED_AI_OUTPUT_FIELDS = {
    "cost_weights",
    "algorithm_selection",
    "policy_options",
    "custom_policy",
    "data_column_mapping",
    "warnings",
}

DANGEROUS_STRING_PATTERNS = (
    r"<\s*/?\s*script\b",
    r"\beval\s*\(",
    r"\bfunction\b",
    r"\bnew\s+Function\b",
    r"\bimport\s+",
    r"\brequire\s*\(",
    r"__proto__",
    r"\bconstructor\b",
    r"\bprototype\b",
    r"\bos\.system\b",
    r"\bsubprocess\b",
    r"\bexec\s*\(",
)

AI_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "cost_weights": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                key: {"type": ["number", "string"], "minimum": 0, "maximum": 1}
                for key in ALLOWED_WEIGHT_KEYS
            },
        },
        "algorithm_selection": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "route_algorithm": {"type": "string"},
                "latency_algorithm": {"type": "string"},
                "base_station_selection_algorithm": {"type": "string"},
                "resource_allocation_algorithm": {"type": "string"},
            },
        },
        "policy_options": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "avoid_high_load_bs": {"type": "boolean"},
                "prefer_low_latency": {"type": "boolean"},
                "prefer_short_distance": {"type": "boolean"},
                "enable_future_scan": {"type": "boolean"},
            },
        },
        "custom_policy": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "policy_name": {"type": "string"},
                "policy_type": {"type": "string"},
                "rules": {"type": "array"},
                "parameters": {"type": "object"},
            },
        },
        "data_column_mapping": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "source_type": {"type": "string"},
                "entity_type": {"type": "string"},
                "field_mapping": {
                    "type": "object",
                    "additionalProperties": {"type": "string"},
                },
                "geometry_mapping": {
                    "type": "object",
                    "additionalProperties": {"type": "string"},
                    "properties": {
                        "lat_field": {"type": "string"},
                        "lng_field": {"type": "string"},
                        "geometry_field": {"type": "string"},
                        "crs": {"type": "string"},
                    },
                },
            },
        },
        "warnings": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
}

_AI_OUTPUT_VALIDATOR = Draft202012Validator(AI_OUTPUT_SCHEMA)


def _default_config() -> dict[str, Any]:
    return copy.deepcopy(DEFAULT_SIMULATION_CONFIG)


def _safe_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


def _sanitize_string(value: str, warnings: list[str], path: str) -> str:
    sanitized = value
    for pattern in DANGEROUS_STRING_PATTERNS:
        updated = re.sub(pattern, "", sanitized, flags=re.IGNORECASE)
        if updated != sanitized:
            warnings.append(f"Removed unsafe token from {path}")
            sanitized = updated
    return sanitized.strip()


def _sanitize_value(value: Any, warnings: list[str], path: str = "root") -> Any:
    if isinstance(value, str):
        return _sanitize_string(value, warnings, path)
    if isinstance(value, list):
        return [_sanitize_value(item, warnings, f"{path}[]") for item in value]
    if isinstance(value, dict):
        sanitized_dict: dict[str, Any] = {}
        for raw_key, raw_value in value.items():
            key = _sanitize_string(str(raw_key), warnings, f"{path}.key")
            sanitized_dict[key] = _sanitize_value(raw_value, warnings, f"{path}.{key}")
        return sanitized_dict
    return value


def _normalize_cost_weights(
    candidate: dict[str, Any],
    warnings: list[str],
) -> dict[str, float]:
    defaults = _default_config()["cost_weights"]
    normalized: dict[str, float] = {}
    for key, default in defaults.items():
        raw = candidate.get(key, default)
        value = _safe_float(raw, default)
        clamped = min(max(value, 0.0), 1.0)
        if clamped != value:
            warnings.append(f"Clamped weight '{key}' into safe range [0, 1]")
        normalized[key] = round(clamped, 4)
    return normalized


def _normalize_algorithm_selection(
    candidate: dict[str, Any],
    warnings: list[str],
) -> dict[str, str]:
    defaults = _default_config()["algorithm_selection"]
    normalized = dict(defaults)

    route_algorithm = str(candidate.get("route_algorithm", defaults["route_algorithm"]))
    if routeAlgorithmRegistry.has(route_algorithm):
        normalized["route_algorithm"] = route_algorithm
    else:
        warnings.append(f"Unknown route algorithm '{route_algorithm}', fallback applied")

    latency_algorithm = str(candidate.get("latency_algorithm", defaults["latency_algorithm"]))
    if latencyAlgorithmRegistry.has(latency_algorithm):
        normalized["latency_algorithm"] = latency_algorithm
    else:
        warnings.append(f"Unknown latency algorithm '{latency_algorithm}', fallback applied")

    bs_algorithm = str(
        candidate.get(
            "base_station_selection_algorithm",
            defaults["base_station_selection_algorithm"],
        )
    )
    if baseStationSelectionRegistry.has(bs_algorithm):
        normalized["base_station_selection_algorithm"] = bs_algorithm
    else:
        warnings.append(f"Unknown base station selection algorithm '{bs_algorithm}', fallback applied")

    allocation_algorithm = str(
        candidate.get(
            "resource_allocation_algorithm",
            defaults["resource_allocation_algorithm"],
        )
    )
    if resourceAllocationRegistry.has(allocation_algorithm):
        normalized["resource_allocation_algorithm"] = allocation_algorithm
    else:
        warnings.append(f"Unknown resource allocation algorithm '{allocation_algorithm}', fallback applied")

    return normalized


def _normalize_policy_options(candidate: dict[str, Any]) -> dict[str, bool]:
    defaults = _default_config()["policy_options"]
    return {
        key: bool(candidate.get(key, default))
        for key, default in defaults.items()
    }


def _normalize_custom_policy(
    candidate: dict[str, Any],
    warnings: list[str],
) -> dict[str, Any]:
    defaults = _default_config()["custom_policy"]
    normalized = dict(defaults)
    policy_name = str(candidate.get("policy_name", defaults["policy_name"])).strip() or defaults["policy_name"]
    policy_type = str(candidate.get("policy_type", defaults["policy_type"])).strip() or defaults["policy_type"]

    if policy_type not in ALLOWED_POLICY_TYPES:
        warnings.append(f"Unknown custom policy type '{policy_type}', fallback applied")
        policy_type = defaults["policy_type"]

    normalized["policy_name"] = policy_name
    normalized["policy_type"] = policy_type

    rules = candidate.get("rules", defaults["rules"])
    normalized["rules"] = rules if isinstance(rules, list) else []
    parameters = candidate.get("parameters", defaults["parameters"])
    normalized["parameters"] = parameters if isinstance(parameters, dict) else {}
    return normalized


def _normalize_data_column_mapping(
    candidate: dict[str, Any],
    warnings: list[str],
) -> dict[str, Any]:
    defaults = _default_config()["data_column_mapping"]
    normalized = {
        "source_type": defaults["source_type"],
        "entity_type": defaults["entity_type"],
        "field_mapping": {},
        "geometry_mapping": {},
    }

    source_type = str(candidate.get("source_type", defaults["source_type"])).lower()
    if source_type in ALLOWED_SOURCE_TYPES:
        normalized["source_type"] = source_type
    else:
        warnings.append(f"Unknown source type '{source_type}', fallback applied")

    entity_type = str(candidate.get("entity_type", defaults["entity_type"])).lower()
    if entity_type in ALLOWED_ENTITY_TYPES:
        normalized["entity_type"] = entity_type
    else:
        warnings.append(f"Unknown entity type '{entity_type}', fallback applied")

    field_mapping = candidate.get("field_mapping", {})
    if isinstance(field_mapping, dict):
        normalized["field_mapping"] = {
            str(target): str(source)
            for target, source in field_mapping.items()
            if str(target) in ALLOWED_MAPPING_TARGETS and str(source).strip()
        }
    geometry_mapping = candidate.get("geometry_mapping", {})
    if isinstance(geometry_mapping, dict):
        normalized["geometry_mapping"] = {
            str(key): str(value)
            for key, value in geometry_mapping.items()
            if str(value).strip()
        }
    return normalized


def validateAIConfigOutput(aiOutput: Any) -> dict[str, Any]:
    """Validate AI JSON output against a strict schema and safe allowlists."""
    warnings: list[str] = []
    errors: list[str] = []

    if not isinstance(aiOutput, dict):
        return {
            "valid": False,
            "warnings": [],
            "errors": ["AI output must be a JSON object"],
            "fallback_used": True,
            "debug_info": {"validation_warnings": [], "schema_errors": ["AI output must be a JSON object"]},
        }

    schema_errors = sorted(_AI_OUTPUT_VALIDATOR.iter_errors(aiOutput), key=lambda error: list(error.path))
    for error in schema_errors:
        path = ".".join(str(part) for part in error.path) or "root"
        errors.append(f"{path}: {error.message}")

    unknown_fields = sorted(set(aiOutput.keys()) - ALLOWED_AI_OUTPUT_FIELDS)
    if unknown_fields:
        errors.extend([f"Unsupported top-level field '{field}'" for field in unknown_fields])

    normalized = normalizeAIConfigOutput(aiOutput)
    warnings.extend(normalized["debug_info"]["validation_warnings"])

    is_valid = len(errors) == 0
    return {
        "valid": is_valid,
        "warnings": _dedupe(warnings),
        "errors": errors,
        "fallback_used": not is_valid,
        "normalized_output": normalized["normalized_output"],
        "debug_info": {
            "validation_warnings": _dedupe(warnings),
            "schema_errors": errors,
        },
    }


def normalizeAIConfigOutput(aiOutput: Any) -> dict[str, Any]:
    """Sanitize AI output, fill defaults, and normalize values into safe JSON."""
    warnings: list[str] = []
    base = _default_config()

    if not isinstance(aiOutput, dict):
        warnings.append("AI output was not an object; default config used")
        return {
            "normalized_output": base,
            "debug_info": {"validation_warnings": warnings},
        }

    sanitized = _sanitize_value(aiOutput, warnings)
    normalized = {
        "cost_weights": _normalize_cost_weights(dict(sanitized.get("cost_weights") or {}), warnings),
        "algorithm_selection": _normalize_algorithm_selection(
            dict(sanitized.get("algorithm_selection") or {}),
            warnings,
        ),
        "policy_options": _normalize_policy_options(dict(sanitized.get("policy_options") or {})),
        "custom_policy": _normalize_custom_policy(dict(sanitized.get("custom_policy") or {}), warnings),
        "data_column_mapping": _normalize_data_column_mapping(
            dict(sanitized.get("data_column_mapping") or {}),
            warnings,
        ),
        "warnings": [
            str(item).strip()
            for item in list(sanitized.get("warnings") or [])
            if str(item).strip()
        ],
    }

    return {
        "normalized_output": normalized,
        "debug_info": {"validation_warnings": _dedupe(warnings)},
    }


def convertAIOutputToSimulationConfig(aiOutput: Any) -> dict[str, Any]:
    """Convert validated AI output into the internal simulation config envelope."""
    validation = validateAIConfigOutput(aiOutput)
    fallback_used = not validation["valid"]
    normalized_output = validation.get("normalized_output") or _default_config()

    simulation_config = {
        "cost_weights": normalized_output["cost_weights"],
        "algorithm_selection": normalized_output["algorithm_selection"],
        "policy_options": normalized_output["policy_options"],
        "custom_policy": normalized_output["custom_policy"],
        "data_column_mapping": normalized_output["data_column_mapping"],
        "warnings": normalized_output["warnings"],
        "debug_info": {
            "validation_warnings": validation["debug_info"]["validation_warnings"],
            "schema_errors": validation["debug_info"]["schema_errors"],
            "fallback_used": fallback_used,
            "custom_policy_type_allowed": normalized_output["custom_policy"]["policy_type"] in ALLOWED_POLICY_TYPES,
        },
    }

    if fallback_used:
        fallback = _default_config()
        simulation_config.update(
            {
                "cost_weights": fallback["cost_weights"],
                "algorithm_selection": fallback["algorithm_selection"],
                "policy_options": fallback["policy_options"],
                "custom_policy": fallback["custom_policy"],
                "data_column_mapping": fallback["data_column_mapping"],
            }
        )
    return simulation_config


def convertNaturalLanguageIntentToPolicySeed(intent: str) -> dict[str, Any]:
    """Build a safe rule-based policy seed without invoking any external LLM."""
    warnings: list[str] = []
    text = _sanitize_string(intent or "", warnings, "intent").lower()
    seed = _default_config()

    if any(keyword in text for keyword in ("지연", "latency", "delay")):
        seed["cost_weights"]["w_latency"] = 0.4
        seed["policy_options"]["prefer_low_latency"] = True
        seed["algorithm_selection"]["latency_algorithm"] = "full_composite_latency"
        seed["algorithm_selection"]["route_algorithm"] = "network_aware_routing"

    if any(keyword in text for keyword in ("거리", "shortest", "distance")):
        seed["cost_weights"]["w_distance"] = 0.35
        seed["policy_options"]["prefer_short_distance"] = True

    if any(keyword in text for keyword in ("부하", "혼잡", "load", "congestion", "avoid high load")):
        seed["cost_weights"]["w_load"] = 0.25
        seed["policy_options"]["avoid_high_load_bs"] = True
        seed["algorithm_selection"]["base_station_selection_algorithm"] = "load_balanced_bs"
        seed["algorithm_selection"]["resource_allocation_algorithm"] = "traffic_aware_allocation"

    if any(keyword in text for keyword in ("미래", "look-ahead", "look ahead", "handover")):
        seed["cost_weights"]["w_future"] = 0.2
        seed["policy_options"]["enable_future_scan"] = True
        seed["algorithm_selection"]["route_algorithm"] = "look_ahead_routing"
        seed["algorithm_selection"]["base_station_selection_algorithm"] = "look_ahead_bs_selection"

    total_weight = sum(seed["cost_weights"].values())
    if total_weight > 0:
        seed["cost_weights"] = {
            key: round(value / total_weight, 4)
            for key, value in seed["cost_weights"].items()
        }

    seed["warnings"] = _dedupe(warnings)
    return seed


def validateDataColumnMapping(mapping: Any) -> dict[str, Any]:
    """Validate field and geometry mappings before data ingestion is attempted."""
    warnings: list[str] = []
    errors: list[str] = []

    if not isinstance(mapping, dict):
        return {
            "valid": False,
            "warnings": [],
            "errors": ["Mapping must be a JSON object"],
            "normalized_mapping": _default_config()["data_column_mapping"],
        }

    normalized = _normalize_data_column_mapping(mapping, warnings)
    field_mapping = normalized["field_mapping"]
    invalid_targets = sorted(set((mapping.get("field_mapping") or {}).keys()) - ALLOWED_MAPPING_TARGETS)
    if invalid_targets:
        errors.extend([f"Unsupported mapping target '{target}'" for target in invalid_targets])

    if not field_mapping and not normalized["geometry_mapping"]:
        warnings.append("Mapping is empty; no columns will be transformed")

    return {
        "valid": len(errors) == 0,
        "warnings": _dedupe(warnings),
        "errors": errors,
        "normalized_mapping": normalized,
    }


def applyDataColumnMapping(rawData: Any, mapping: Any) -> dict[str, Any]:
    """Apply a validated column mapping to raw tabular records without executing code."""
    validation = validateDataColumnMapping(mapping)
    normalized_mapping = validation["normalized_mapping"]

    if not isinstance(rawData, list):
        return {
            "records": [],
            "warnings": validation["warnings"],
            "errors": ["Raw data must be a list of JSON objects"],
            "applied_mapping": normalized_mapping,
            "debug_info": {
                "validation_warnings": validation["warnings"],
                "mapped_record_count": 0,
            },
        }

    records: list[dict[str, Any]] = []
    for raw_row in rawData:
        if not isinstance(raw_row, dict):
            continue

        mapped_row: dict[str, Any] = {}
        for target, source in normalized_mapping["field_mapping"].items():
            mapped_row[target] = raw_row.get(source)

        geometry_mapping = normalized_mapping["geometry_mapping"]
        if "lat_field" in geometry_mapping:
            mapped_row["latitude"] = raw_row.get(geometry_mapping["lat_field"])
        if "lng_field" in geometry_mapping:
            mapped_row["longitude"] = raw_row.get(geometry_mapping["lng_field"])
        if "geometry_field" in geometry_mapping:
            mapped_row["geometry"] = raw_row.get(geometry_mapping["geometry_field"])
        if "crs" in geometry_mapping:
            mapped_row["crs"] = geometry_mapping["crs"]

        records.append(mapped_row)

    return {
        "records": records,
        "warnings": validation["warnings"],
        "errors": validation["errors"],
        "applied_mapping": normalized_mapping,
        "debug_info": {
            "validation_warnings": validation["warnings"],
            "mapped_record_count": len(records),
        },
    }
