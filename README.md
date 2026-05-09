# Autonomous V2X AI Routing Lab

Browser-based autonomous-driving V2X network-aware AI routing and analysis platform.

The lab compares a rule-based routing baseline against an AI-assisted optimization path. The current implementation is a clean MVP scaffold with deterministic mock analysis and a baseline ML module, ready for future model upgrades.

The platform does not implement real autonomous driving control, real 5G/6G PHY, Kakao Maps, VWorld 3D, or traffic APIs that require approval. It uses VWorld 2D only for Korean public spatial map visualization.

## Current MVP Focus

The current MVP prioritizes realtime network simulation stability and public spatial-data integration.

It uses stable 2D public spatial maps because realtime route, vehicle, base-station, and edge-node visualization needs predictable rendering, smooth updates, and reliable local development before optional 3D support is added later.

## Project Setup

Requirements:

- Node.js 20+
- pnpm 9+
- Python 3.11+
- Docker and Docker Compose

Create local environment files:

```bash
cp .env.example .env
cp apps/web/.env.example apps/web/.env.local
cp apps/api/.env.example apps/api/.env
cp packages/db/.env.example packages/db/.env
```

## Frontend Install

```bash
pnpm install
```

## Backend Setup

```bash
cd apps/api
python3 -m venv .venv
source .venv/bin/activate
```

Install:

```bash
pip install -r requirements.txt
```

## Docker Postgres Setup

```bash
docker compose up -d postgres
```

Prisma schema and client:

```bash
pnpm db:generate
pnpm db:validate
pnpm db:migrate
```

The Prisma schema lives in `packages/db/prisma/schema.prisma` and defines the core V2X lab entities:
users, projects, scenarios, vehicles, road segments, base stations, edge nodes, obstacles, routes,
simulations, simulation ticks, simulation metrics, AI analyses, experiments, and assistant logs.

`SimulationMetric` stores realtime aggregate metrics, including average vehicle latency, predicted latency,
road congestion, base-station congestion, obstacle risk, route changes, and base-station handoffs.

`AIAnalysis` stores the deterministic AI-assisted route analysis output for a vehicle in a simulation,
including predicted latency, congestion, blockage risk, recommended route/base station, confidence,
and structured explanation factors. No external LLM is required for this MVP.

## Run Frontend

```bash
pnpm dev:web
```

Frontend: `http://localhost:3000/dashboard`

## Run Backend

```bash
cd apps/api
source .venv/bin/activate
uvicorn app.main:app --reload --port 8000
```

Backend: `http://localhost:8000`

Health check:

```bash
curl http://localhost:8000/health
```

## Optional SUMO/TraCI

SUMO is disabled by default so the first demo runs with Mock Simulation Mode:

```bash
SUMO_ENABLED=false
SUMO_BINARY=sumo
SUMO_CONFIG_PATH=
SUMO_GUI_ENABLED=false
```

Install SUMO on macOS:

```bash
brew install sumo
```

Install Python TraCI packages:

```bash
cd apps/api
source .venv/bin/activate
pip install traci eclipse-sumo
```

To run a real SUMO/TraCI simulation, set `SUMO_ENABLED=true`, provide a valid `SUMO_CONFIG_PATH`, and start the simulation with mode `sumo_traci`.

```bash
curl -X POST http://localhost:8000/simulations/start \
  -H "Content-Type: application/json" \
  -d '{"mode":"sumo_traci"}'
```

If SUMO, TraCI, or the config is unavailable, the API automatically falls back to Mock Simulation Mode and the frontend clearly shows that fallback state.

## API Scope

- `GET /health`
- `GET /routes/candidates`
- `POST /routes/analyze`
- `GET /simulation/state`
- `POST /simulation/start`
- `POST /simulation/stop`
- `POST /assistant/explain`

Rule-based logic is only the baseline. AI-assisted analysis and optimization is the core target, but this MVP keeps the AI layer deterministic and modular.
# V2X_LAB
