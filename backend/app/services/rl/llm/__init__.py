"""
V2X LLM — provider-agnostic API client (Bedrock / Vertex / Azure).

Modules:
  llm_client     — .env 기반 프로바이더 라우팅 (chat 함수)
  vllm_inference — V2XLLM 인터페이스 (scenario_to_config / explain_results / recommend_placement)
  lora_finetune  — 오프라인 Llama 파인튜닝 (선택적, 메인 파이프라인 미사용)
"""
from .vllm_inference import V2XLLM, get_llm
__all__ = ["V2XLLM", "get_llm"]
