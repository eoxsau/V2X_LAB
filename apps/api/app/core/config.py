import os

from dotenv import load_dotenv
from pydantic import BaseModel

load_dotenv()


class Settings(BaseModel):
    app_name: str = "Autonomous V2X AI Routing Lab API"
    app_version: str = "0.1.0"
    cors_origins: list[str] = ["http://localhost:3000"]
    its_api_key: str = os.getenv("ITS_API_KEY", "")
    its_api_base_url: str = os.getenv("ITS_API_BASE_URL", "https://openapi.its.go.kr:9443/trafficInfo")
    sumo_enabled: bool = os.getenv("SUMO_ENABLED", "false").lower() in {"1", "true", "yes", "on"}
    sumo_binary: str = os.getenv("SUMO_BINARY", "sumo")
    sumo_config_path: str = os.getenv("SUMO_CONFIG_PATH", "")
    sumo_net_path: str = os.getenv("SUMO_NET_PATH", "")
    sumo_gui_enabled: bool = os.getenv("SUMO_GUI_ENABLED", "false").lower() in {"1", "true", "yes", "on"}
    sumo_host: str = os.getenv("SUMO_HOST", "localhost")
    sumo_port: int = int(os.getenv("SUMO_PORT", "8813"))
    llm_enabled: bool = os.getenv("LLM_ENABLED", "false").lower() in {"1", "true", "yes", "on"}
    llm_provider: str = os.getenv("LLM_PROVIDER", "template").lower()
    gemini_api_key: str = os.getenv("GEMINI_API_KEY", "")
    huggingface_api_key: str = os.getenv("HUGGINGFACE_API_KEY", "")
    vworld_api_key: str = os.getenv("VWORLD_API_KEY", os.getenv("NEXT_PUBLIC_VWORLD_API_KEY", ""))


settings = Settings()
