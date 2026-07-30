"""
V2X LLM Inference — provider-agnostic wrapper (Bedrock / Vertex / Azure).

Public API (unchanged):
  get_llm()                  → V2XLLM singleton
  V2XLLM.scenario_to_config()
  V2XLLM.explain_results()
  V2XLLM.recommend_placement()

Provider 선택은 .env의 LLM_PROVIDER로 제어.
Llama / vLLM 의존성 없음 — llm_client.py가 라우팅 담당.
"""
from __future__ import annotations

import json
from typing import Optional

from .llm_client import chat as _llm_chat


class V2XLLM:
    """V2X 도메인 LLM 인터페이스."""

    def scenario_to_config(self, description: str) -> dict:
        """
        자연어 시나리오 설명 → 시뮬레이션 설정 dict.

        Example
        -------
        "서울 강남구 교차로에서 차량 밀도 50대/km², 5G" →
        {"region": "강남구", "density": 50, "network_mode": "5G", ...}
        """
        prompt = (
            "다음 V2X 시뮬레이션 시나리오를 JSON 설정으로 변환하세요. "
            "JSON만 출력하고, 키는 영어로 작성하세요.\n\n"
            f"시나리오: {description}"
        )
        raw = _llm_chat(prompt)
        try:
            start = raw.find("{")
            end   = raw.rfind("}") + 1
            if start >= 0 and end > start:
                return json.loads(raw[start:end])
        except (json.JSONDecodeError, ValueError):
            pass
        return {"description": description, "raw_response": raw}

    def explain_results(self, kpis: dict, algorithm: str = "unknown") -> str:
        """
        시뮬레이션 KPI → 한국어 분석 설명 생성.

        Parameters
        ----------
        kpis : dict
            avg_latency_ms, prr, coverage_ratio, handover_count, jain_fi 등
        algorithm : str
            사용된 라우팅 알고리즘 이름
        """
        kpi_text = "\n".join(f"  {k}: {v}" for k, v in kpis.items())
        prompt = (
            f"다음은 V2X 시뮬레이션 ({algorithm} 알고리즘) KPI 결과입니다:\n"
            f"{kpi_text}\n\n"
            "이 결과를 논문 수준으로 분석하고, 병목 원인과 개선 방향을 3GPP/ETSI 표준 근거와 함께 설명하세요."
        )
        return _llm_chat(prompt)

    def recommend_placement(
        self,
        region_name: str,
        traffic_density: float,
        network_mode: str,
        current_kpis: Optional[dict] = None,
    ) -> str:
        """
        지역·밀도·네트워크 모드 기반 BS/RSU 배치 권고안 생성.
        """
        kpi_section = ""
        if current_kpis:
            kpi_section = "\n현재 KPI:\n" + "\n".join(
                f"  {k}: {v}" for k, v in current_kpis.items()
            )

        prompt = (
            f"V2X 네트워크 배치 최적화 권고:\n"
            f"  지역: {region_name}\n"
            f"  차량 밀도: {traffic_density:.1f} veh/km²\n"
            f"  네트워크 모드: {network_mode}"
            f"{kpi_section}\n\n"
            "기지국(BS)과 RSU의 최적 배치 전략을 3GPP/ETSI 표준 근거와 함께 제시하세요. "
            "CBR, AoI, 핸드오버 횟수, Jain 공정성 지수 관점에서 분석하세요."
        )
        return _llm_chat(prompt)


# ── 싱글턴 ────────────────────────────────────────────────────────────────────
_instance: Optional[V2XLLM] = None


def get_llm() -> V2XLLM:
    """V2XLLM 싱글턴 반환."""
    global _instance
    if _instance is None:
        _instance = V2XLLM()
    return _instance
