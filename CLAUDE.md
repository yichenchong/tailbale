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

# Test (run from repo root or backend/)
py -3.12 -m pytest
py -3.12 -m pytest tests/test_foo.py  # single file
```

### Docker
```bash
docker compose -f docker-compose.dev.yml up           # Dev (hot reload)
docker compose -f docker-compose.prod.yml up -d --build  # Production
```

## Architecture

### Stack
- **Backend**: Python 3.12 + FastAPI + SQLAlchemy 2.0 (SQLite WAL) + Docker SDK
- **Frontend**: React 18 + Vite + TypeScript + Tailwind CSS v4 + React Router v7
- **Edge containers**: Per-service Tailscale + Caddy image (built from `edge/Dockerfile`)
- **Certs**: Lego CLI (ACME DNS-01 via Cloudflare), stored in `data/certs/`

### Backend Structure (`backend/app/`)

| Module | Purpose |
|--------|---------|
| `main.py` | FastAPI app, lifespan (starts reconciliation/cert-renewal loops), 8 routers |
| `config.py` | Pydantic settings — reads `.env`, derives all data paths |
| `database.py` | SQLite setup, WAL mode, FK constraints, lightweight post-launch migrations |
| `auth.py` | JWT (HS256 cookie) + two-layer password hashing (SHA-256 + bcrypt) |
| `models/` | 8 ORM models: Service, ServiceStatus, Certificate, DnsRecord, Event, Job, Setting, User |
| `routers/` | 8 REST routers: auth, services, settings, discovery, events, dashboard, profiles, jobs |
| `reconciler/` | **Core engine** — 14-step idempotent per-service reconciliation loop (60s cadence) |
| `edge/` | Edge container management (Caddy + Tailscale lifecycle) |
| `certs/` | Certificate issuance and renewal (lego ACME) |
| `adapters/` | Cloudflare DNS adapter, DNS record reconciliation |
| `health/` | 11 health subchecks aggregated into service phase |
| `events/` | Event emission and storage |
| `settings_store.py` | Key-value persistent config (wraps Setting model) |
| `secrets.py` | Secret file management |

### Frontend Structure (`frontend/src/`)

| Path | Purpose |
|------|---------|
| `App.tsx` | Router root — checks `/api/auth/status` to gate setup/login/app |
| `pages/` | 12 pages: Dashboard, Services, ServiceDetail, Discover, Expose, Events, OrphanDns, Settings, Setup, Login, etc. |
| `components/` | Layout (sidebar + outlet), shared UI |
| `lib/api.ts` | Fetch-based API client (`api.get/post/put/delete`), auto-redirects on 401 |

### Core Data Flow

1. **Reconciler loop** (every 60s): For each enabled service, runs 14 idempotent steps — ensures Docker network, creates/starts the edge container, writes Caddyfile, issues/renews cert, reconciles DNS record, detects Tailscale IP, reloads Caddy, runs health checks, updates `ServiceStatus`.

2. **Health checks** (11 subchecks): Upstream container present & running, edge container state, Tailscale ready/IP present, cert present & not expiring, DNS record matches IP, Caddy config exists, HTTPS probe success. Result aggregates to phase: `healthy` / `warning` / `error` / `failed`.

3. **Edge container per service**: Isolated Docker network shared with upstream container. Caddy reverse-proxies to upstream via Docker DNS. TLS via mounted Let's Encrypt certs.

### Auth Flow
- Initial setup creates the single admin `User` via `/api/auth/setup`
- JWT token stored as `access_token` cookie; all routes except `/api/auth/*` require it
- `get_current_user` FastAPI dependency used on protected endpoints

### Frontend Patterns
- No global state (Redux/context) — local `useState` per page, polling for real-time updates
- Path alias `@/` maps to `src/`
- Vite dev proxy: `/api/*` → `http://localhost:8080` (no CORS issues in dev)
- Dynamic favicon (`public/`) turns green/red based on overall health

### Database Migrations
Schema changes are applied as lightweight post-launch migrations in `database.py` (ADD COLUMN statements guarded by try/except). No migration framework — check existing pattern when adding columns.
