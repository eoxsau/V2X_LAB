import os

from dotenv import load_dotenv
from pydantic import BaseModel

load_dotenv()


class Settings(BaseModel):
    app_name: str = "AI Network Digital Twin Lab API"
    app_version: str = "0.1.0"
    cors_origins: list[str] = ["http://localhost:3000"]
    public_data_api_key: str | None = os.getenv("PUBLIC_DATA_API_KEY")
    public_data_api_base_url: str | None = os.getenv("PUBLIC_DATA_API_BASE_URL")


settings = Settings()
