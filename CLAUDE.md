# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**tailBale** is a self-hosted orchestrator that exposes Docker containers as HTTPS services via Tailscale and Caddy, with automatic Let's Encrypt certificates (DNS-01 via Cloudflare) and DNS management.

## Commands

### Frontend (`frontend/`)
```bash
npm run dev        # Vite dev server (proxies /api to localhost:8080)
npm run build      # TypeScript check + Vite production build
npm run lint       # ESLint
npm run test       # Vitest single run
npm run test:watch # Vitest watch mode
```

### Backend (`backend/`)
```bash
# Setup
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt

# Run
uvicorn app.main:app --reload --port 8080

# Test (from repo root)
backend/.venv/bin/python -m pytest
backend/.venv/bin/python -m pytest backend/tests/test_foo.py  # single file
```

### Docker
```bash
HOST_DATA_DIR=$PWD/data docker compose -f docker-compose.dev.yml up           # Dev (hot reload)
HOST_DATA_DIR=$PWD/data docker compose -f docker-compose.prod.yml up -d --build  # Production
```

## Architecture

### Stack
- **Backend**: Python 3.14 + FastAPI + SQLAlchemy 2.0 (SQLite WAL) + Docker SDK
- **Frontend**: React 18 + Vite + TypeScript + Tailwind CSS v4 + React Router v7
- **Edge containers**: Per-service Tailscale + Caddy image (built from `edge/Dockerfile`)
- **Certs**: Lego CLI (ACME DNS-01 via Cloudflare), stored in `data/certs/`

### Backend Structure (`backend/app/`)

| Module | Purpose |
|--------|---------|
| `main.py` | FastAPI app, lifespan (starts reconciliation/cert-renewal loops), 11 routers |
| `config.py` | Pydantic settings — reads `.env`, derives all data paths |
| `database.py` | SQLite setup, WAL mode, FK constraints, lightweight post-launch migrations |
| `auth.py` | JWT (HS256 cookie) + two-layer password hashing (SHA-256 + bcrypt) |
| `models/` | ORM models for services, status, certificates, DNS records, events, jobs, settings, and users |
| `routers/` | REST routers: auth, settings, developer, connection_tests, discovery, services, service_actions, events, dashboard, profiles, jobs |
| `services/` | Transport-agnostic service lifecycle layer — `create`/`update`/`delete` (with shared `lifecycle` helpers and response `mapping`), `edge_ops`, `cert_ops`, and domain `errors`; routers delegate here and the central handler in `main.py` maps the raised domain exceptions to HTTP |
| `reconciler/` | **Core engine** — 14-step idempotent per-service reconciliation (per-phase step helpers in the `reconciler/steps/` package, wired together by `reconciler.py`); full reconcile hourly + a lightweight 60s health sweep that escalates to a full reconcile on drift |
| `edge/` | Edge container management (Caddy + Tailscale lifecycle) |
| `certs/` | Certificate issuance and renewal (lego ACME) |
| `adapters/` | Cloudflare DNS adapter, DNS record reconciliation |
| `health/` | 12 health subchecks aggregated into service phase |
| `events/` | Event emission and storage |
| `settings_store.py` | Key-value persistent config (wraps Setting model) |
| `secrets.py` | Secret file management |

### Frontend Structure (`frontend/src/`)

| Path | Purpose |
|------|---------|
| `App.tsx` | Router root — checks `/api/auth/status` to gate setup/login/app |
| `pages/` | Route-level pages (Dashboard, Services, ServiceDetail, Discover, Expose, Events, OrphanDns, Settings, Setup, Login) plus Settings tab modules under `pages/settings/` |
| `components/` | Shared layout/state/pagination UI plus focused `service/` and `settings/` component folders |
| `lib/api.ts` + `lib/api/` | API barrel and endpoint modules built on `lib/api/core.ts` (`api.get/post/put/delete`, 401 redirect handling) |
| `lib/use*.ts(x)` | Shared hooks for polling/resources, dirty forms, pagination, timezone formatting, transient messages, secret fields, and favicon state |

### Core Data Flow

1. **Reconcile loop** (hourly by default, `reconcile_interval_seconds`): For each enabled service, runs the 14 idempotent steps — ensures Docker network, issues/renews cert, writes Caddyfile, creates/starts the edge container, detects Tailscale IP, reconciles DNS record, reloads Caddy, runs health checks, updates `ServiceStatus`. A separate lightweight **health sweep** (every 60s, `health_check_interval_seconds`) re-runs the health checks and escalates a drifting service to a full reconcile.

2. **Health checks** (12 subchecks): Upstream container present & running, edge container state, Tailscale ready/IP present, cert present & not expiring, DNS record matches IP, Caddy config exists, HTTPS probe success. `aggregate_status` returns one of `healthy` / `warning` / `error`. (The reconciler separately sets `pending` / `disabled` / `failed` on the service status outside the health aggregation.)

3. **Edge container per service**: Isolated Docker network shared with upstream container. Caddy reverse-proxies to upstream via Docker DNS. TLS via mounted Let's Encrypt certs.

### Auth Flow
- Initial setup creates the single admin `User` via `/api/auth/setup-user`
- JWT token stored as `access_token` cookie; every route requires it except the public ones: `/api/health`, `/api/version`, the static SPA, and the pre-auth auth routes (`/api/auth/status`, `/api/auth/login`, `/api/auth/setup-user`, plus `/api/auth/setup-progress` until setup completes)
- `get_current_user` FastAPI dependency used on protected endpoints

### Frontend Patterns
- No global state (Redux/context) — local `useState` per page, polling for real-time updates
- Path alias `@/` maps to `src/`
- Vite dev proxy: `/api/*` → `http://localhost:8080` (no CORS issues in dev)
- Dynamic favicon (`public/`) turns green/red based on overall health

### Database Migrations
Schema changes are applied as lightweight post-launch migrations in `database.py::run_migrations` — inspection-guarded (via `inspect()`'s `has_table`/`get_columns`) `ADD COLUMN` for new nullable columns plus `CREATE INDEX IF NOT EXISTS`. Additive-only (no drops/renames/type changes/backfills); no migration framework — check the existing pattern when adding columns.
