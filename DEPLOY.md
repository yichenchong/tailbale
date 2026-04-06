# Deploying tailBale to Unraid

## Prerequisites

- **Unraid 6.12+** with Docker enabled
- **Domain** managed in Cloudflare (e.g. `mydomain.com`)
- **Cloudflare API token** with DNS:Edit permission for your zone
- **Tailscale account** with a reusable auth key from the admin console
- SSH or terminal access to Unraid

## Option A: Docker Compose (recommended)

### 1. Clone the repo on your Unraid server

```bash
cd /mnt/user/appdata
git clone <your-repo-url> tailbale
cd tailbale
```

### 2. Build and start

```bash
# Docker Compose v2 (plugin):
docker compose -f docker-compose.prod.yml up -d --build

# Docker Compose v1 (standalone binary, common on Unraid):
docker-compose -f docker-compose.prod.yml up -d --build
```

> **Tip**: If you get `unknown shorthand flag: 'f'`, you have the v1 binary —
> use `docker-compose` (hyphenated) instead of `docker compose` (subcommand).
> If neither works, use **Option B** below.

### 3. Access the setup wizard

Open `http://<unraid-ip>:6780` in your browser. The first-time setup wizard will walk you through:

1. **Account** — create your admin username and password
2. **Domain** — enter your base domain (e.g. `mydomain.com`)
3. **Cloudflare** — zone ID and API token
4. **ACME Email** — for Let's Encrypt certificate registration
5. **Tailscale** — your reusable auth key
6. **Docker** — socket path (default is correct for Unraid)

### 4. Verify

- Check the dashboard at `http://<unraid-ip>:6780`
- Go to **Discover** to see your running containers
- Use **Expose** to create your first service

---

## Option B: Manual Docker Run

If you prefer not to use docker compose:

### 1. Build the image

```bash
cd /mnt/user/appdata/tailbale
docker build -t tailbale:latest .
```

### 2. Build the edge image

```bash
docker build -t tailbale-edge:latest ./edge
```

### 3. Run the orchestrator

```bash
docker run -d \
  --name tailbale \
  --restart unless-stopped \
  -p 6780:8080 \
  -v /mnt/user/appdata/tailbale/data:/data \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -e DATA_DIR=/data \
  -e DOCKER_SOCKET=unix:///var/run/docker.sock \
  -e PORT=8080 \
  tailbale:latest
```

Then open `http://<unraid-ip>:6780` to complete setup.

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
| `JWT_EXPIRY_HOURS` | No | `24` | Session duration in hours |
| `COOKIE_SECURE` | No | `false` | Set `true` if behind HTTPS |
| `CORS_ORIGINS` | No | `*` | Comma-separated allowed origins |
| `PORT` | No | `8080` | Container listen port |
| `HOST` | No | `0.0.0.0` | Listen address |
| `DATA_DIR` | No | `/data` | Data directory |
| `DOCKER_SOCKET` | No | `unix:///var/run/docker.sock` | Docker socket path |

## Updating

```bash
cd /mnt/user/appdata/tailbale
git pull
docker compose -f docker-compose.prod.yml up -d --build   # v2
# or: docker-compose -f docker-compose.prod.yml up -d --build  # v1
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

- **Can't connect to Docker**: Ensure `/var/run/docker.sock` is mounted and the container user has access
- **Edge container won't start**: Check Tailscale auth key is valid and reusable
- **Certs not issuing**: Verify Cloudflare API token has DNS:Edit permission for your zone
- **DNS records not updating**: Check Cloudflare zone ID matches your domain
- **Invalidate all sessions**: Delete `/data/secrets/.jwt_secret` and restart the container
