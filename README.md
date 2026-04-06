# tailBale

Self-hosted orchestrator for Unraid that exposes Docker containers as individually shareable HTTPS services via per-service Tailscale edge containers.

Each exposed service gets its own Tailscale identity, Let's Encrypt certificate, and Cloudflare DNS record under `<service>.yourdomain.com` — no public inbound ports, no Cloudflare Tunnel, no reverse-proxy dashboard click-ops.

## How It Works

```
[Your Container] ←── Docker network ──→ [Edge Container (Tailscale + Caddy)] ←── Tailscale ──→ [Your Devices]
                                              ↑
                                         HTTPS cert from Let's Encrypt
                                         DNS A record → Tailscale IP
```

1. **Discover** running Docker containers on your Unraid server
2. **Expose** a container through the wizard — tailBale creates:
   - A dedicated edge container with Tailscale + Caddy
   - A Let's Encrypt certificate via DNS-01 challenge (Cloudflare)
   - A DNS A record pointing `service.yourdomain.com` to the edge's Tailscale IP
3. **Access** your service from any device on your tailnet at `https://service.yourdomain.com`

## Prerequisites

- **Unraid 6.12+** with Docker enabled
- **Domain** managed in Cloudflare
- **Cloudflare API token** with DNS:Edit permission
- **Tailscale account** with a reusable auth key

## Quick Start

```bash
# Clone and configure
git clone <repo-url> /mnt/user/appdata/tailbale
cd /mnt/user/appdata/tailbale
cp backend/.env.example .env
# Edit .env — at minimum set JWT_SECRET

# Build and run
docker compose -f docker-compose.prod.yml up -d --build

# Open http://<unraid-ip>:8080 and complete the setup wizard
```

See [DEPLOY.md](DEPLOY.md) for detailed deployment instructions, environment variables, and troubleshooting.

## Stack

| Layer | Technology |
|---|---|
| Backend | Python 3.12, FastAPI, SQLAlchemy 2.0, SQLite |
| Frontend | React 18, TypeScript, Vite 8, Tailwind CSS 4 |
| Edge proxy | Caddy (per-service, with file-based TLS) |
| Networking | Tailscale (per-service identity) |
| Certificates | Let's Encrypt via `lego` CLI (DNS-01 / Cloudflare) |
| DNS | Cloudflare API v4 |

## Development

### Backend

```bash
cd backend
python -m venv .venv
source .venv/bin/activate  # or .venv\Scripts\activate on Windows
pip install -r requirements.txt -r requirements-dev.txt
uvicorn app.main:app --reload --port 8080
```

### Frontend

```bash
cd frontend
npm install
npm run dev
```

### Tests

```bash
# Backend (from repo root or backend/)
py -3.12 -m pytest

# Frontend (from frontend/)
npx vitest run
```

## Project Structure

```
├── backend/
│   └── app/
│       ├── main.py              # FastAPI app + lifespan
│       ├── config.py            # Environment-based settings
│       ├── auth.py              # Password hashing, JWT, auth dependency
│       ├── models/              # SQLAlchemy models (7 tables + users)
│       ├── routers/             # API endpoints
│       ├── edge/                # Edge container + network management
│       ├── certs/               # Certificate issuance + renewal
│       ├── adapters/            # Cloudflare DNS adapter
│       ├── reconciler/          # Idempotent service reconciliation
│       ├── health/              # Health check system (11 subchecks)
│       └── events/              # Event emission
├── edge/
│   ├── Dockerfile               # Tailscale + Caddy edge image
│   └── entrypoint.sh            # Edge startup script
├── frontend/                    # React SPA
├── Dockerfile                   # Multi-stage production build
├── docker-compose.prod.yml      # Production deployment
└── docker-compose.dev.yml       # Development with hot reload
```

## License

Private — all rights reserved.
