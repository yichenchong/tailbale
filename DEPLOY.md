# Deploying tailBale

## Prerequisites

- **A Linux host** with Docker installed
- **Domain** managed in Cloudflare (e.g. `mydomain.com`)
- **Cloudflare API token** with DNS:Edit permission for your zone
- **Tailscale account** with both:
  - a reusable auth key from the admin console
  - an API key for tailnet device management
- SSH or terminal access to the host

## Option A: deploy.sh (recommended)

### 1. Clone the repo on your server

```bash
cd /opt
git clone <your-repo-url> tailbale
cd tailbale
```

### 2. Build and start

```bash
# Safe for first-time deploys, upgrades, and non-executable checkouts:
bash ./deploy.sh

# Optional host port, host data path, or runtime override:
HOST_PORT=6790 HOST_DATA_DIR=/opt/tailbale/data COOKIE_SECURE=true bash ./deploy.sh
```

> `deploy.sh` delegates to `redeploy.sh`, which builds both images from the
> repository checkout and replaces any existing `tailbale` container. The script
> forwards the runtime variables listed below (`HOST`, `PORT`, `COOKIE_SECURE`,
> `CORS_ORIGINS`, `JWT_EXPIRY_HOURS`, the login rate-limit settings, and
> data/socket settings).
>
> If you prefer Compose, set `HOST_DATA_DIR` explicitly and use
> `HOST_DATA_DIR=/opt/tailbale/data docker compose -f docker-compose.prod.yml up -d --build`
> (or the v1 `docker-compose` binary). Do not switch between Compose and
> `deploy.sh` without first stopping/removing the container created by the other
> mode; they use different Docker ownership metadata and can otherwise conflict
> on the container name or host port.

### 3. Access the setup wizard

By default the orchestrator publishes **only on `127.0.0.1` (loopback)** — it
controls the Docker socket (host-root), so it must not be reachable in cleartext
on your LAN. Choose how to reach it over your tailnet:

- **HTTPS via `tailscale serve` (recommended):** on the host, run
  `tailscale serve --bg 443 http://127.0.0.1:6780`, then open
  `https://<machine>.<tailnet>.ts.net`. Real cert, tailnet-only, and the session
  cookie auto-upgrades to `Secure` (via `X-Forwarded-Proto`). See "Network access" below.
- **Plain ip:port over the tailnet:** set `BIND_ADDR=<your-tailnet-ip>` (e.g.
  `100.x.y.z`) and open `http://<tailnet-ip>:6780`. WireGuard encrypts the wire,
  but there is no TLS/HSTS and the browser shows "not secure".
- **Local only:** open `http://localhost:6780` on the host itself.

The first-time setup wizard will walk you through:

1. **Account** — create your admin username and password
2. **Domain** — enter your base domain (e.g. `mydomain.com`)
3. **Cloudflare** — zone ID and API token
4. **ACME Email** — for Let's Encrypt certificate registration
5. **Tailscale** — your reusable auth key and API key
6. **Docker** — socket path (default is correct for most Linux hosts)

### 4. Verify

- Check the dashboard at your chosen address (see step 3 — e.g. `https://<machine>.<tailnet>.ts.net` via `tailscale serve`, or `http://localhost:6780` on the host)
- Go to **Discover** to see your running containers
- Use **Expose** to create your first service

### Rebuilding the Orchestrator

```bash
cd /opt/tailbale
git pull
bash ./deploy.sh
```

---

## Option B: Manual Docker Run

If you prefer to run the Docker commands manually:

### 1. Build the image

```bash
cd /opt/tailbale
docker build -t tailbale:latest .
```

### 2. Build the edge image

```bash
docker build -t tailbale-edge:latest --label "tailbale.version=$(tr -d '\n' < VERSION)" ./edge
```

### 3. Run the orchestrator

```bash
docker run -d \
  --name tailbale \
  --label tailbale.main=true \
  --restart unless-stopped \
  -p 127.0.0.1:6780:8080 \
  -v /opt/tailbale/data:/data \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -e DATA_DIR=/data \
  -e HOST_DATA_DIR=/opt/tailbale/data \
  -e DOCKER_SOCKET=unix:///var/run/docker.sock \
  -e PORT=8080 \
  tailbale:latest
```

> **Important**: `HOST_DATA_DIR` must be set to the **host-side** absolute path
> of the data directory. tailBale creates edge containers via the Docker socket,
> and Docker resolves bind-mount paths on the host — not inside the orchestrator
> container. Without this variable, edge container creation will fail with
> "bind source path does not exist".

Then reach it as described in "Access the setup wizard" above (loopback by default — e.g. `http://localhost:6780` on the host, or your tailnet address) to complete setup.

### Rebuilding the Orchestrator
```bash
docker build -t tailbale:latest .
docker build -t tailbale-edge:latest --label "tailbale.version=$(tr -d '\n' < VERSION)" ./edge
docker rm -f tailbale || true
docker run -d \
  --name tailbale \
  --label tailbale.main=true \
  --restart unless-stopped \
  -p 127.0.0.1:6780:8080 \
  -v /opt/tailbale/data:/data \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -e DATA_DIR=/data \
  -e HOST_DATA_DIR=/opt/tailbale/data \
  -e DOCKER_SOCKET=unix:///var/run/docker.sock \
  -e PORT=8080 \
  tailbale:latest
```

Or use the convenience script directly (auto-detects the host data path, forwards
documented runtime overrides, and is safe on first deploy, even if the scripts
are not executable):

```bash
bash ./deploy.sh
```

If you switch between manual/Compose deployments and `deploy.sh`, stop and remove
the container from the previous mode first so Docker does not keep the old
container name or host port reserved.

---

## Data & Volumes

| Host path | Container path | Purpose |
|---|---|---|
| `/opt/tailbale/data` | `/data` | SQLite DB, secrets, certs, generated configs, Tailscale state |
| `/var/run/docker.sock` | `/var/run/docker.sock` | Docker API access (required to manage edge containers) |

The `/data` volume contains everything persistent. Back it up regularly.

## Security

The JWT signing secret is **auto-generated on first startup** and stored in
`/data/secrets/.jwt_secret`. It never needs to be configured manually. To
invalidate all sessions, delete that file and restart the container — a new
secret will be generated automatically.

### Network access & exposure

The orchestrator wields the Docker socket, so an authenticated session on it is
effectively host-root. It therefore publishes on **`127.0.0.1` only by default**
(`BIND_ADDR`), and the session cookie's `Secure` flag is auto-enabled whenever the
request arrives over HTTPS (honoring `X-Forwarded-Proto`).

To reach it over your tailnet with real HTTPS, run on the host:

```bash
tailscale serve --bg 443 http://127.0.0.1:6780
```

This config lives in the host's `tailscaled`, **not** in the container: you set it
once, it survives container restarts/redeploys (the loopback port is stable), and
it self-heals when the app comes back up (a brief 502 while the container is down).
It is not re-created per deploy, so it never accumulates. Remove it with
`tailscale serve --https=443 off` only if you uninstall tailBale. Avoid
`tailscale funnel` for the admin UI — that exposes it to the public internet.

## Environment Variables Reference

| Variable | Required | Default | Description |
|---|---|---|---|
| `HOST_DATA_DIR` | **Yes** for Compose/manual Docker; auto-detected by `deploy.sh` | repo `data` directory for `deploy.sh` | Absolute host-side path to the data directory. `deploy.sh` resolves relative values before passing them to Docker; Compose requires an explicit absolute value and uses it for both the `/data` bind mount and edge-container bind sources. |
| `DATA_DIR` | No | `/data` | Data directory inside the container |
| `JWT_EXPIRY_HOURS` | No | `24` | Session duration in hours |
| `LOGIN_MAX_FAILURES` | No | `5` | Consecutive failed logins from a client before further attempts are rejected with HTTP 429 for the lockout window. A successful login resets the count. |
| `LOGIN_LOCKOUT_SECONDS` | No | `60` | Lockout window (seconds) applied once `LOGIN_MAX_FAILURES` is hit. |
| `COOKIE_SECURE` | No | `false` (auto on HTTPS) | Force the session cookie's `Secure` flag. Auto-enabled when the request arrives over HTTPS (incl. `X-Forwarded-Proto`); set `true` to force it always. |
| `CORS_ORIGINS` | No | `(empty — CORS middleware disabled; same-origin only)` | Comma-separated allowed origins |
| `PORT` | No | `8080` | Container listen port |
| `HOST` | No | `0.0.0.0` | Listen address **inside the container** (leave as `0.0.0.0`; Docker forwards the published port to it) |
| `BIND_ADDR` | No | `127.0.0.1` | Host interface the published port binds to. Loopback by default; set to your tailnet IP for `http://<tailnet-ip>:PORT`, or `0.0.0.0` to expose on all host interfaces. |
| `DOCKER_SOCKET` | No | `unix:///var/run/docker.sock` | Docker socket path |

## Updating

```bash
cd /opt/tailbale
git pull
bash ./deploy.sh
```

Your data in `/data` is preserved across rebuilds.

## Pushing Images to a Registry

When you're ready to push images (e.g. to GitHub Container Registry):

```bash
# Tag and push orchestrator
docker tag tailbale:latest ghcr.io/<your-username>/tailbale:latest
docker push ghcr.io/<your-username>/tailbale:latest

# Tag and push edge image
docker tag tailbale-edge:latest ghcr.io/<your-username>/tailbale-edge:latest
docker push ghcr.io/<your-username>/tailbale-edge:latest
```

The orchestrator **always builds the edge image locally** from the bundled
`/app/edge-image` context (`image_builder.ensure_edge_image` checks the local
`tailbale-edge:latest` tag's `tailbale.version` label and rebuilds on mismatch —
it never pulls). Repointing the `EDGE_IMAGE` constant at a registry ref alone
does **not** switch to pulling; you would also have to change `ensure_edge_image`
to pull the image instead of building it. Pushing images to a registry is only
needed to share/back up the built artifacts, not for the default self-hosted flow.

## Troubleshooting

- **"bind source path does not exist"**: `HOST_DATA_DIR` is missing or wrong. Set it to the host path of your data directory (e.g. `/opt/tailbale/data`). `deploy.sh` resolves relative paths to absolute paths; Docker Compose now requires `HOST_DATA_DIR` explicitly so it cannot accidentally use the caller's working directory.
- **Can't connect to Docker**: Ensure `/var/run/docker.sock` is mounted and the container user has access
- **Edge container won't start**: Check the Tailscale auth key is valid and reusable; the API key is also required during setup but is not used for login
- **Certs not issuing**: Verify Cloudflare API token has DNS:Edit permission for your zone
- **DNS records not updating**: Check Cloudflare zone ID matches your domain
- **Invalidate all sessions**: Delete `/data/secrets/.jwt_secret` and restart the container
