from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import analysis, assistant, health, optimization, realtime, routes, scenarios, simulation, simulations, traffic, v2x_data
from app.core.config import settings

app = FastAPI(title=settings.app_name, version=settings.app_version)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(v2x_data.router)
app.include_router(traffic.router)
app.include_router(routes.router)
app.include_router(scenarios.router)
app.include_router(simulation.router)
app.include_router(simulations.router)
app.include_router(analysis.router)
app.include_router(optimization.router)
app.include_router(assistant.router)
app.include_router(realtime.router)
