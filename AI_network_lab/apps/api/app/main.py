from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import base_stations, health, simulations
from app.core.config import settings

app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(base_stations.router)
app.include_router(simulations.router)
