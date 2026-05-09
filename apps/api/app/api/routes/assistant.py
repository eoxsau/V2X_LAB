from fastapi import APIRouter
from pydantic import BaseModel

from app.assistant.explainer import explain_current_recommendation
from app.llm import explain_results, get_llm_status, parse_scenario
from app.services.realtime_payload import build_realtime_payload
from app.services.mock_repository import get_assistant_messages
from app.simulation.state import get_state

router = APIRouter(prefix="/assistant", tags=["assistant"])


class ExplainRequest(BaseModel):
    vehicle_id: str | None = None


class ScenarioParseRequest(BaseModel):
    text: str


class ResultExplainRequest(BaseModel):
    context: dict[str, object] | None = None


@router.get("/messages")
def messages() -> list[dict[str, str]]:
    return get_assistant_messages()


@router.get("/llm-status")
def llm_status() -> dict[str, object]:
    return get_llm_status()


@router.post("/explain")
def explain(payload: ExplainRequest | None = None) -> dict[str, object]:
    return explain_current_recommendation(payload.vehicle_id if payload else None)


@router.post("/scenario/parse")
async def scenario_parse(payload: ScenarioParseRequest) -> dict[str, object]:
    return await parse_scenario(payload.text)


@router.post("/results/explain")
async def results_explain(payload: ResultExplainRequest | None = None) -> dict[str, object]:
    context = payload.context if payload and payload.context else build_realtime_payload(get_state())
    return await explain_results(context)
