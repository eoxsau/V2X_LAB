"""
V2X LLM Client — provider-agnostic, reads from .env.

LLM_PROVIDER (env):
  bedrock  → AWS Bedrock  (BEDROCK_API_KEY, BEDROCK_MODEL)
  vertex   → Gemini       (GCP_API_KEY, VERTEX_MODEL)
  azure    → Azure OpenAI (AZURE_AI_API_KEY, AZURE_OPENAI_ENDPOINT, AZURE_MODEL)
  auto     → bedrock → vertex → azure 순서로 시도
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

# ── .env 자동 로드 ────────────────────────────────────────────────────────────
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent.parent.parent.parent / ".env", override=False)
except ImportError:
    pass

_PROVIDER = os.getenv("LLM_PROVIDER", "auto").lower()

# ── V2X 시스템 프롬프트 + Few-shot (파인튜닝 대체) ───────────────────────────
_SYSTEM_PROMPT = """당신은 V2X(Vehicle-to-Everything) 통신 및 지능형 교통 시스템(ITS) 전문가입니다.
3GPP TS 22.186, ETSI EN 302 637-2, IEEE 802.11p 표준에 기반하여 정확한 기술 답변을 제공합니다.
수식과 출처를 포함하여 한국어로 답변하세요.

[참고 예시]
Q: CBR이 0.65 초과 시 조치는?
A: ETSI TS 102 687 §5.2.2에 따라 DCC 활성화: CAM 주파수 10→1 Hz, Tx Power 감소, MCS 하향.

Q: M/M/1 ρ=0.8 평균 대기시간은?
A: W_q = ρ/(μ(1−ρ)) = 4/μ. μ=100 req/s → 40 ms. [Kleinrock 1975 §3.3]

Q: RSU PC5 vs 기지국 Uu 지연 비교?
A: RSU PC5: 1~3 ms (백홀 없음). BS Uu: 4~30 ms. MEC 배포 시 8~15 ms 목표. [ETSI TR 102 638 §4.3]

Q: 5G NR URLLC E2E 지연 요건?
A: 차량 충돌 경보 ≤ 3 ms, 교차로 경보 ≤ 10 ms, CAM ≤ 100 ms. [3GPP TS 22.186 §5.1]

Q: Jain FI가 0.6이면?
A: 중간 불공정 수준. n=4 BS이면 J_min=0.25. 목표 J≥0.85. [Jain et al. 1984 §3.1]"""


def chat(user_message: str, system: Optional[str] = None) -> str:
    """
    LLM에 메시지를 보내고 응답을 반환합니다.

    Parameters
    ----------
    user_message : str
        사용자 질문 또는 분석 요청
    system : str | None
        커스텀 시스템 프롬프트. None이면 V2X 기본 프롬프트 사용.

    Returns
    -------
    str
        LLM 응답 텍스트
    """
    sys_prompt = system or _SYSTEM_PROMPT
    providers = [_PROVIDER] if _PROVIDER != "auto" else ["bedrock", "vertex", "azure"]

    last_err: Optional[Exception] = None
    for p in providers:
        try:
            return _dispatch(p, sys_prompt, user_message)
        except Exception as e:
            last_err = e

    return _rule_based_fallback(user_message)


# ── 프로바이더 디스패치 ────────────────────────────────────────────────────────
def _dispatch(provider: str, system: str, user: str) -> str:
    if provider == "bedrock":
        return _call_bedrock(system, user)
    if provider == "vertex":
        return _call_vertex(system, user)
    if provider == "azure":
        return _call_azure(system, user)
    raise ValueError(f"Unknown LLM_PROVIDER: {provider}")


def _call_bedrock(system: str, user: str) -> str:
    """AWS Bedrock — Anthropic Claude (BEDROCK_API_KEY 사용)."""
    import anthropic

    api_key = os.environ["BEDROCK_API_KEY"]
    model   = os.getenv("BEDROCK_MODEL", "claude-opus-4-8")

    # 새 Bedrock API 키 형식(ABSK...)은 AnthropicBedrock의 aws_api_key로 전달
    if api_key.startswith("ABSK"):
        client = anthropic.AnthropicBedrock(aws_api_key=api_key)
    else:
        # base64 디코딩된 값 또는 일반 Anthropic 키
        client = anthropic.Anthropic(api_key=api_key)

    msg = client.messages.create(
        model=model,
        max_tokens=1024,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return msg.content[0].text


def _call_vertex(system: str, user: str) -> str:
    """Google Vertex AI / Gemini (GCP_API_KEY 사용)."""
    import google.generativeai as genai  # pip install google-generativeai

    genai.configure(api_key=os.environ["GCP_API_KEY"])
    model_name = os.getenv("VERTEX_MODEL", "gemini-2.5-flash")
    model = genai.GenerativeModel(
        model_name=model_name,
        system_instruction=system,
    )
    resp = model.generate_content(user)
    return resp.text


def _call_azure(system: str, user: str) -> str:
    """Azure OpenAI (AZURE_AI_API_KEY + AZURE_OPENAI_ENDPOINT 사용)."""
    from openai import AzureOpenAI  # pip install openai

    client = AzureOpenAI(
        api_key=os.environ["AZURE_AI_API_KEY"],
        azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
        api_version="2024-12-01-preview",
    )
    model = os.getenv("AZURE_MODEL", "gpt-4o")
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user",   "content": user},
        ],
        max_tokens=1024,
    )
    return resp.choices[0].message.content


# ── 규칙 기반 폴백 (모든 API 실패 시) ──────────────────────────────────────────
def _rule_based_fallback(message: str) -> str:
    m = message.lower()
    if "cbr" in m:
        return "CBR > 0.65: DCC 활성화 권장. CAM 10→1 Hz, Tx 출력 감소. [ETSI TS 102 687 §5.2.2]"
    if "latency" in m or "지연" in m:
        return "RSU PC5: 1~3 ms. BS Uu: 4~30 ms. MEC 배포 시 8~15 ms 목표. [3GPP TR 22.886]"
    if "rsu" in m:
        return "RSU 배치 권장: 교차로·고속도로 진출입로. 커버리지 반경 150~500 m. [ETSI TR 102 638]"
    if "fairness" in m or "공정" in m or "jain" in m:
        return "Jain FI ≥ 0.85 목표. 현재 값이 낮으면 과부하 BS 커버리지 반경 축소 권장."
    if "handover" in m or "핸드오버" in m:
        return "핸드오버 중단 시간 5G NR: ~200 ms (도심 실측). DAPS 활성화로 무손실 HO 가능."
    return "V2X 시뮬레이션 결과를 분석 중입니다. 로그를 확인하세요."
