# AI Network Digital Twin Lab

Base platform for an AI Network Digital Twin Lab.

This repository intentionally does not implement AI models, latency prediction, blockage prediction, routing reinforcement learning, or real 5G/6G PHY simulation yet. It only sets up the application foundation.

The map and simulator are designed as a public wireless-station-data-based simulation layer. They should not be treated as exact commercial telecom topology reproduction.

## Project Setup

Requirements:

- Node.js 20+
- pnpm 9+
- Python 3.11+
- Docker and Docker Compose

Create your local environment file:

```bash
cp .env.example .env
```

Repository layout:

```text
apps/web      Next.js App Router frontend
apps/api      FastAPI backend
packages/db   Prisma schema and database client package
```

## Frontend Install

From the repository root:

```bash
pnpm install
```

This installs the frontend app and shared Prisma package through the pnpm workspace.

## Backend Virtualenv Setup

From the repository root:

```bash
cd apps/api
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Docker Postgres Setup

Start PostgreSQL from the repository root:

```bash
docker compose up -d postgres
```

Check that the container is running:

```bash
docker compose ps
```

Generate the Prisma client:

```bash
pnpm db:generate
```

Apply the initial Prisma migration when you are ready to create database tables:

```bash
pnpm db:migrate
```

## Run Frontend

From the repository root:

```bash
pnpm dev:web
```

The frontend runs at `http://localhost:3000`.

## Run Backend

From the repository root:

```bash
cd apps/api
source .venv/bin/activate
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

The backend runs at `http://localhost:8000`.

Health check:

```bash
curl http://localhost:8000/health
```
