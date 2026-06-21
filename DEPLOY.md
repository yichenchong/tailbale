# Deploying tailBale to Unraid

## Prerequisites

- **Unraid 6.12+** with Docker enabled
- **Domain** managed in Cloudflare (e.g. `mydomain.com`)
- **Cloudflare API token** with DNS:Edit permission for your zone
- **Tailscale account** with both:
  - a reusable auth key from the admin console
  - an API key for tailnet device management
- SSH or terminal access to Unraid

## Option A: deploy.sh (recommended)

### 1. Clone the repo on your Unraid server

```bash
cd /mnt/user/appdata
git clone <your-repo-url> tailbale
cd tailbale
```

### 2. Build and start

```bash
# Safe for first-time deploys, upgrades, and non-executable checkouts:
bash ./deploy.sh

# Optional host port, host data path, or runtime override:
HOST_PORT=6790 HOST_DATA_DIR=/mnt/user/appdata/tailbale/data COOKIE_SECURE=true bash ./deploy.sh
```

> `deploy.sh` delegates to `redeploy.sh`, which builds both images from the
> repository checkout and replaces any existing `tailbale` container. The script
> forwards the runtime variables listed below (`HOST`, `PORT`, `COOKIE_SECURE`,
> `CORS_ORIGINS`, `JWT_EXPIRY_HOURS`, and data/socket settings).
>
> If you prefer Compose, set `HOST_DATA_DIR` explicitly and use
> `HOST_DATA_DIR=/mnt/user/appdata/tailbale/data docker compose -f docker-compose.prod.yml up -d --build`
> (or the v1 `docker-compose` binary). Do not switch between Compose and
> `deploy.sh` without first stopping/removing the container created by the other
> mode; they use different Docker ownership metadata and can otherwise conflict
> on the container name or host port.

### 3. Access the setup wizard

Open `http://<unraid-ip>:6780` in your browser. The first-time setup wizard will walk you through:

1. **Account** — create your admin username and password
2. **Domain** — enter your base domain (e.g. `mydomain.com`)
3. **Cloudflare** — zone ID and API token
4. **ACME Email** — for Let's Encrypt certificate registration
5. **Tailscale** — your reusable auth key and API key
6. **Docker** — socket path (default is correct for Unraid)

### 4. Verify

- Check the dashboard at `http://<unraid-ip>:6780`
- Go to **Discover** to see your running containers
- Use **Expose** to create your first service

### Rebuilding the Orchestrator

```bash
cd /mnt/user/appdata/tailbale
git pull
bash ./deploy.sh
```

---

## Option B: Manual Docker Run

If you prefer to run the Docker commands manually:

### 1. Build the image

```bash
cd /mnt/user/appdata/tailbale
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
  -p 6780:8080 \
  -v /mnt/user/appdata/tailbale/data:/data \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -e DATA_DIR=/data \
  -e HOST_DATA_DIR=/mnt/user/appdata/tailbale/data \
  -e DOCKER_SOCKET=unix:///var/run/docker.sock \
  -e PORT=8080 \
  tailbale:latest
```

> **Important**: `HOST_DATA_DIR` must be set to the **host-side** absolute path
> of the data directory. tailBale creates edge containers via the Docker socket,
> and Docker resolves bind-mount paths on the host — not inside the orchestrator
> container. Without this variable, edge container creation will fail with
> "bind source path does not exist".

Then open `http://<unraid-ip>:6780` to complete setup.

### Rebuilding the Orchestrator
```bash
docker build -t tailbale:latest .
docker build -t tailbale-edge:latest --label "tailbale.version=$(tr -d '\n' < VERSION)" ./edge
docker rm -f tailbale || true
docker run -d \
  --name tailbale \
  --label tailbale.main=true \
  --restart unless-stopped \
  -p 6780:8080 \
  -v /mnt/user/appdata/tailbale/data:/data \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -e DATA_DIR=/data \
  -e HOST_DATA_DIR=/mnt/user/appdata/tailbale/data \
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
| `/mnt/user/appdata/tailbale/data` | `/data` | SQLite DB, secrets, certs, generated configs, Tailscale state |
| `/var/run/docker.sock` | `/var/run/docker.sock` | Docker API access (required to manage edge containers) |

The `/data` volume contains everything persistent. Back it up regularly.

## Security

The JWT signing secret is **auto-generated on first startup** and stored in
`/data/secrets/.jwt_secret`. It never needs to be configured manually. To
invalidate all sessions, delete that file and restart the container — a new
secret will be generated automatically.

## Environment Variables Reference

| Variable | Required | Default | Description |
|---|---|---|---|
| `HOST_DATA_DIR` | **Yes** for Compose/manual Docker; auto-detected by `deploy.sh` | repo `data` directory for `deploy.sh` | Absolute host-side path to the data directory. `deploy.sh` resolves relative values before passing them to Docker; Compose requires an explicit absolute value and uses it for both the `/data` bind mount and edge-container bind sources. |
| `DATA_DIR` | No | `/data` | Data directory inside the container |
| `JWT_EXPIRY_HOURS` | No | `24` | Session duration in hours |
| `COOKIE_SECURE` | No | `false` | Set `true` if behind HTTPS |
| `CORS_ORIGINS` | No | `*` | Comma-separated allowed origins |
| `PORT` | No | `8080` | Container listen port |
| `HOST` | No | `0.0.0.0` | Listen address |
| `DOCKER_SOCKET` | No | `unix:///var/run/docker.sock` | Docker socket path |

## Updating

```bash
cd /mnt/user/appdata/tailbale
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

Then update the edge image reference in the orchestrator's container_manager to point to your registry image instead of a local build.

## Troubleshooting

- **"bind source path does not exist"**: `HOST_DATA_DIR` is missing or wrong. Set it to the host path of your data directory (e.g. `/mnt/user/appdata/tailbale/data`). `deploy.sh` resolves relative paths to absolute paths; Docker Compose now requires `HOST_DATA_DIR` explicitly so it cannot accidentally use the caller's working directory.
- **Can't connect to Docker**: Ensure `/var/run/docker.sock` is mounted and the container user has access
- **Edge container won't start**: Check the Tailscale auth key is valid and reusable; the API key is also required during setup but is not used for login
- **Certs not issuing**: Verify Cloudflare API token has DNS:Edit permission for your zone
- **DNS records not updating**: Check Cloudflare zone ID matches your domain
- **Invalidate all sessions**: Delete `/data/secrets/.jwt_secret` and restart the container
