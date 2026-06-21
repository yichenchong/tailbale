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
2. **Complete the setup wizard** — tailBale stores and validates:
   - base domain
   - Cloudflare zone ID + API token
   - ACME email
   - Tailscale reusable auth key
   - Tailscale API key
   - Docker socket path
3. **Expose** a container through the UI — tailBale creates:
   - a dedicated edge container with Tailscale + Caddy
   - a Let's Encrypt certificate via DNS-01 challenge (Cloudflare)
   - a DNS A record pointing `service.yourdomain.com` to the edge's Tailscale IP
4. **Access** your service from any device on your tailnet at `https://service.yourdomain.com`

## Prerequisites

- **Unraid 6.12+** with Docker enabled
- **Domain** managed in Cloudflare
- **Cloudflare API token** with DNS:Edit permission
- **Tailscale account** with both:
  - a reusable auth key for edge login
  - an API key for device cleanup and management

## Quick Start

```bash
# Clone
git clone https://github.com/yichenchong/tailbale.git /mnt/user/appdata/tailbale
cd /mnt/user/appdata/tailbale

# Build and run (safe for first-time deploys, upgrades, and non-executable checkouts)
bash ./deploy.sh

# Optional host port, host data path, or runtime override:
# HOST_PORT=6790 HOST_DATA_DIR=/mnt/user/appdata/tailbale/data COOKIE_SECURE=true bash ./deploy.sh

# Open http://<unraid-ip>:6780 (or your HOST_PORT override) and complete the setup wizard
```

See [DEPLOY.md](DEPLOY.md) for detailed deployment instructions, environment variables, and troubleshooting.

## Notable Behavior

- **Edge container per service** — each exposure gets its own Docker network, edge container, Tailscale identity, and certificate material.
- **Developer Mode** — in **Settings → General**, enable Developer Mode to reveal the **Developer** tab with:
  - `Reset setup_complete`
  - `Reset all`
- **Tailscale keys are different**:
  - **Auth key**: used by the edge container to run `tailscale up`
  - **API key**: used by tailBale to clean up tailnet devices on recreate/delete
- **HTTPS probe failures are logged explicitly** — probe failures now log whether the cause was missing Tailscale IP, non-running edge container, curl failure, no HTTP response, or upstream 5xx.

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
python3 -m pytest

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
