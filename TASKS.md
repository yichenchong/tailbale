# tailBale — Implementation Task Plan

**Stack:** Python 3.12 (FastAPI) + SQLAlchemy/SQLite | React + TypeScript (Vite 8) | lego (ACME) | Tailscale official base image + Caddy

Reference: [unraid-edge-orchestrator-detailed-spec.md](./unraid-edge-orchestrator-detailed-spec.md)

---

## Milestone 0: Project Scaffolding — COMPLETE

- [x] **0.1** Initialize Python backend project structure
  - `backend/app/` with `main.py`, `config.py`, `database.py`, `secrets.py`, `settings_store.py`
  - Subpackages: `adapters/`, `certs/`, `edge/`, `events/`, `health/`, `models/`, `reconciler/`, `routers/`, `schemas/`
  - `requirements.txt` + `requirements-dev.txt` + `.env.example`
- [x] **0.2** Initialize React frontend project
  - Vite 8 + React 18 + TypeScript, Tailwind CSS 4, shadcn/ui utility deps
  - React Router with sidebar layout and 5 stub pages (Dashboard, Services, Discover, Events, Settings)
  - Path alias `@/` configured, Vite proxy to backend `/api`
- [x] **0.3** Create orchestrator Dockerfile
  - Multi-stage: Node 22 frontend build → Python 3.12 production with lego binary
  - Volume `/data`, port 8080, health check endpoint
- [x] **0.4** Create `docker-compose.dev.yml` for local development
  - Backend with uvicorn reload, frontend with Vite dev server
- [x] **0.5** Set up linting and testing
  - `ruff.toml` for Python, ESLint (from Vite template) for TS
  - pytest (backend, 325 tests) + Vitest (frontend, 97 tests) — all passing (422 total)

---

## Milestone 1: Database & Settings — COMPLETE

- [x] **1.1** Design SQLAlchemy models (7 tables)
  - `settings` (key/value), `services` (desired state), `service_status` (observed state + health),
    `events` (structured log), `jobs` (async tasks), `dns_records`, `certificates`
  - FK constraints with cascade delete, indexed fields, auto-timestamps
- [x] **1.2** Implement secret file storage (`app/secrets.py`)
  - `write_secret()`, `read_secret()`, `secret_exists()`, `delete_secret()`, `get_secret_presence()`
  - Atomic writes (temp + rename), restricted permissions, values never returned to frontend
- [x] **1.3** Implement Settings Pydantic schemas (`app/schemas/settings.py`)
  - Request models: `GeneralSettingsUpdate`, `CloudflareSettingsUpdate`, `TailscaleSettingsUpdate`, `DockerSettingsUpdate`, `PathSettingsUpdate`
  - Response models: `AllSettingsResponse` with nested sections + `ConnectionTestResult`
- [x] **1.4** Implement Settings API endpoints (`app/routers/settings.py`)
  - `GET /api/settings`, `PUT /api/settings/{general,cloudflare,tailscale,docker,paths}`
  - `PUT /api/settings/setup-complete`
- [x] **1.5** Implement connection test endpoints
  - `POST /api/settings/test/docker` — Docker SDK ping
  - `POST /api/settings/test/cloudflare` — Cloudflare API zone verification
  - `POST /api/settings/test/tailscale` — auth key format validation
- [x] **1.6** Build Settings UI screens (`pages/SettingsPage.tsx`)
  - Tabbed interface with form fields, save/test buttons, secret presence badges, test result banners

---

## Milestone 2: Docker Discovery — COMPLETE

- [x] **2.1** Implement Docker adapter module (`backend/app/routers/discovery.py`)
  - Uses `docker` Python SDK (`import docker`)
  - `list_containers(running_only, hide_managed, search)` — returns list of container info dicts
  - Extract: name, image, state, published ports, networks, labels
  - Filter out containers with label `tailbale.managed=true` (orchestrator + edges)
- [x] **2.2** Implement Discovery API endpoint
  - `GET /api/discovery/containers` with query params: `running_only`, `hide_managed`, `search`
  - Return normalized container list
- [x] **2.3** Build Discover Containers UI screen
  - Table: container name, image, status, ports, networks, labels
  - Filters: running only toggle, search box
  - "Expose" button per row that navigates to service wizard
  - Shows exposure count badge for containers already exposed, with "Expose Another Port" button

---

## Milestone 3: Service CRUD & Wizard — COMPLETE

- [x] **3.1** Define Service Pydantic models
  - `ServiceCreate` — wizard submission payload
  - `ServiceUpdate` — edit payload
  - `ServiceResponse` — full service detail with `ServiceStatusResponse` (includes `health_checks`, `cert_expires_at`)
  - Match fields to spec section 10.1 (ServiceExposure)
  - Data model supports multiple exposures per container (unique edge names enforced)
- [x] **3.2** Implement Service API endpoints
  - `POST /api/services` — create new service exposure with collision-safe slug generation
  - `GET /api/services` — list all services with status, health checks, cert expiry
  - `GET /api/services/:id` — full service detail + health subchecks + cert expiry
  - `PUT /api/services/:id` — update service config
  - `DELETE /api/services/:id` — delete service (CASCADE deletes status, certs, DNS records)
  - `POST /api/services/:id/disable` — set enabled=false
  - Stub action endpoints (501): `/reload`, `/restart-edge`, `/recreate-edge`, `/renew-cert`, `/reconcile`
  - Stub log endpoint: `GET /api/services/:id/logs/edge`
- [x] **3.3** Build Expose Service Wizard UI (most important screen)
  - **Step 1 — Select container:** pre-filled from Discover page with detected ports
  - **Step 2 — Configure endpoint:** service name, hostname prefix, full hostname preview, upstream port, upstream scheme, healthcheck path, enable immediately toggle
  - **Step 3 — Advanced:** preserve host header, custom Caddy snippet
  - **Step 4 — Review:** summary of edge container name, DNS record, upstream, network
  - **Step 5 — Submit + progress:** POST to API, then poll `GET /api/services/:id` every 2s showing phase stepper with green checks / spinner / failure states, "View Service" link
- [x] **3.4** Build Service List UI
  - Table rows: service name, hostname (link), upstream container:port, status pill, edge IP (monospaced), cert expiry (color-coded)
  - Actions dropdown (MoreVertical menu): View Details, Reload Caddy, Restart Edge, Recreate Edge, Disable/Enable, Delete (with confirm)
- [x] **3.5** Build Service Detail UI
  - Configuration section with inline edit form (name, port, scheme, healthcheck, preserve host, Caddy snippet)
  - Runtime details: edge container, network, TS hostname, Tailscale IP, cert expiry, phase, message, last reconciled
  - Health breakdown section: grid of subchecks with green CheckCircle2 / red XCircle indicators, human-readable labels
  - Actions bar: Disable/Enable, Reload Caddy, Restart Edge, Recreate Edge, Force Renew Cert, Re-run Reconcile, Delete (with confirm)
  - Logs tabs: Edge Logs / Events tabs with placeholder content

---

## Milestone 4: Edge Container Runtime — COMPLETE

- [x] **4.1** Create edge container Dockerfile (`edge/Dockerfile`)
  - Base: `tailscale/tailscale:latest`
  - Install Caddy (download static binary or use official package)
  - Add entrypoint script (`edge/entrypoint.sh`):
    1. Start `tailscaled` in userspace networking mode
    2. Authenticate with `TS_AUTHKEY` if state is fresh
    3. Wait for Tailscale to be ready (`tailscale status --json`)
    4. Start `caddy run --config /etc/caddy/Caddyfile`
    5. Trap signals for graceful shutdown
  - Labels: `tailbale.managed=true`, `tailbale.service_id=<id>`
- [x] **4.2** Implement edge config renderer (`backend/app/edge/config_renderer.py`)
  - Generate Caddyfile from service config using string templates
  - Template per spec section 22.1: `auto_https off`, TLS with file certs, reverse_proxy with headers
  - Handle: hostname, upstream container name, upstream port, scheme, preserve_host, custom snippet
  - Write deterministically (stable output) so diffs are meaningful
  - Atomic file writes (temp + rename)
- [x] **4.3** Implement Docker network management (`backend/app/edge/network_manager.py`)
  - `create_network(network_name)` — creates bridge network (idempotent)
  - `connect_container(network_name, container_id)` — connect app container to edge network (idempotent)
  - `ensure_network(network_name, app_container_id)` — idempotent: create if absent, connect if not connected
  - `remove_network(network_name)` — remove network if exists
  - Don't disconnect app from its existing networks
- [x] **4.4** Implement edge container lifecycle management (`backend/app/edge/container_manager.py`)
  - `create_edge_container(service)` — create container from edge image with:
    - Mounts: tailscale state dir, cert dir, generated Caddyfile
    - Env: `TS_AUTHKEY`, `TS_HOSTNAME=edge-<slug>`
    - Network: `edge_net_<service_id>`
    - Labels: `tailbale.managed=true`, `tailbale.service_id=<id>`
  - `start_edge(service_id)`
  - `stop_edge(service_id)`
  - `restart_edge(service_id)`
  - `remove_edge(service_id)`
  - `recreate_edge(service_id)` — remove + create + start
  - `get_edge_logs(service_id, tail)` — fetch container log tail
  - `reload_caddy(service_id)` — exec `caddy reload` inside edge
- [x] **4.5** Implement Tailscale IP detection (`detect_tailscale_ip()` in container_manager)
  - After edge starts, exec into container: `tailscale ip -4`
  - Fallback: parse `tailscale status --json` output
  - Retry with backoff (Tailscale auth can take a few seconds)
  - Validates IPs start with `100.` (Tailscale CGNAT range)
- [x] **4.6** Implement edge action API endpoints (replaced stubs in `routers/services.py`)
  - `POST /api/services/:id/reload` — exec `caddy reload` inside edge container + emit event
  - `POST /api/services/:id/restart-edge` — restart edge container + emit event
  - `POST /api/services/:id/recreate-edge` — full recreate + update status + emit event
  - `GET /api/services/:id/logs/edge` — return recent edge logs with `tail` param

---

## Milestone 5: Certificate Manager — COMPLETE

- [x] **5.1** Install/bundle `lego` binary in orchestrator container
  - Already present in `Dockerfile`: downloads lego v4.21.0 release binary
  - Callable as `lego` from `/usr/local/bin/`
- [x] **5.2** Implement cert manager module (`backend/app/certs/cert_manager.py`)
  - `issue_cert(hostname, email, cloudflare_token, cert_dir)`:
    - Shell out to `lego` with `--dns cloudflare --domains <hostname> --email <email> --accept-tos run`
    - Pass Cloudflare token via env var `CF_DNS_API_TOKEN`
    - On success: atomic copy cert files to service cert dir
  - `renew_cert(hostname, email, cloudflare_token, cert_dir, days)`:
    - Shell out to `lego renew --days <n>`
    - On success: atomic file replacement
  - `get_cert_expiry(cert_path)` — parse PEM cert via `cryptography` lib, return expiry datetime
- [x] **5.3** Implement atomic cert file writes (`_atomic_copy_certs()`)
  - Write `fullchain.pem` and `privkey.pem` to `.tmp` files first
  - Rename into final paths only after both writes succeed
  - Clean up temp files on failure
- [x] **5.4** Implement cert renewal background task (`backend/app/certs/renewal_task.py`)
  - `process_service_cert(db, svc)` — per-service cert check/issue/renew
  - `run_renewal_scan()` — scan all enabled services
  - `cert_renewal_loop()` — async background loop (every 24h)
  - Registered in `main.py` lifespan with cancellation on shutdown
  - Respects retry interval (6h) after failures
  - Stores results in `certificates` table: expiry, last_renewed, last_failure, next_retry
  - Emits events: `cert_issued`, `cert_renewed`, `cert_failed`
- [x] **5.5** Implement cert API endpoints (replaced stubs in `routers/services.py`)
  - `POST /api/services/:id/renew-cert` — force renewal via `process_service_cert()`
  - `GET /api/services/:id/logs/cert` — cert-related events filtered by kind, with limit param

---

## Milestone 6: Cloudflare DNS Manager — COMPLETE

- [x] **6.1** Implement Cloudflare adapter (`backend/app/adapters/cloudflare_adapter.py`)
  - Use `httpx` to call Cloudflare API v4 directly (no SDK needed)
  - `list_zones(token)` — verify token and get zones
  - `get_zone(token, zone_id)` — verify zone exists
  - `create_a_record(token, zone_id, hostname, ip)` — create A record, proxied=false, ttl=auto
  - `update_a_record(token, zone_id, record_id, ip)` — PATCH existing record content
  - `delete_a_record(token, zone_id, record_id)` — remove record
  - `find_record(token, zone_id, hostname, record_type)` — find existing record by type+name
  - `_headers(token)` — Bearer auth helper
  - `_check_response(resp, action)` — validates CF success field, raises RuntimeError with error messages
- [x] **6.2** Implement DNS reconciliation logic (`backend/app/adapters/dns_reconciler.py`)
  - `reconcile_dns(db, service, tailscale_ip, cf_token, zone_id)`:
    1. Find existing A record for hostname via CF API
    2. If absent: create it pointing to Tailscale IP
    3. If present but wrong IP: update it
    4. If present and correct: no-op
  - Gets or creates `DnsRecord` entry in database
  - Stores record_id and current value in `dns_records` table
  - Emits events: `dns_created`, `dns_updated`
- [x] **6.3** Implement DNS drift detection (`detect_dns_drift()` in dns_reconciler)
  - Compares stored DNS value against current Tailscale IP
  - Returns dict: `dns_record_present`, `dns_matches_ip`, `stored_ip`, `current_ip`, `drifted`
  - Handles missing record and missing Tailscale IP cases
- [x] **6.4** Implement DNS cleanup on service delete
  - `cleanup_dns_record(db, service, cf_token, zone_id)` — deletes from Cloudflare (best-effort) and removes DB record
  - Emits `dns_removed` event; logs warning on CF API failure but still removes DB record
  - `DELETE /api/services/:id?cleanup_dns=true` — query param triggers cleanup before cascade delete
  - Best-effort: service deletion proceeds regardless of CF API errors

---

## Milestone 7: Reconciler & Health System — COMPLETE

- [x] **7.1** Implement reconciler engine (`backend/app/reconciler/reconciler.py`)
  - `reconcile_service(db, service, socket_path)` — idempotent per-service reconciliation following spec 11.3:
    1. Validate settings (TS auth key required)
    2. Ensure generated/cert directories
    3. Ensure Docker network + app connected
    4. Ensure cert exists or renew if expiring
    5. Render Caddy config (write only if changed)
    6. Ensure edge container exists (create if absent)
    7. Ensure edge container running (start if stopped)
    8. Detect Tailscale IP (with retry)
    9. Ensure DNS record matches IP (best-effort)
    10. Reload Caddy if config changed
    11. Run health checks
    12. Persist observed state + emit events
  - Phase progression: pending → validating → creating_network → ensuring_cert → rendering_config → ensuring_edge → detecting_ip → ensuring_dns → reloading_caddy → checking_health → healthy/warning/error/failed
  - `ReconcileError` for non-recoverable failures; unexpected errors caught and logged
- [x] **7.2** Implement reconcile triggers (`backend/app/reconciler/reconcile_loop.py`)
  - `reconcile_all(db)` — sweep all enabled services
  - `reconcile_one(db, service_id)` — reconcile single service by ID
  - `reconcile_loop()` — async background loop (configurable interval via `reconcile_interval_seconds` setting, default 60s)
  - Startup recovery: loop starts via `asyncio.create_task` in FastAPI lifespan (5s initial delay)
  - Manual trigger: `POST /api/services/:id/reconcile` (replaced 501 stub)
- [x] **7.3** Implement health checker (`backend/app/health/health_checker.py`)
  - `run_health_checks(db, service, generated_dir, certs_dir, socket_path)` — 11 boolean subchecks:
    - `upstream_container_present`, `upstream_network_connected`
    - `edge_container_present`, `edge_container_running`
    - `tailscale_ready`, `tailscale_ip_present`
    - `cert_present`, `cert_not_expiring` (14-day window)
    - `dns_record_present`, `dns_matches_ip`
    - `caddy_config_present`
  - `aggregate_status(checks)` → `"healthy"` / `"warning"` / `"error"` based on critical vs warning checks
  - Graceful Docker unavailable handling (returns all-false checks)
  - Results stored as JSON in `service_status.health_checks`
- [x] **7.4** Implement event system (`backend/app/events/event_emitter.py`)
  - `emit_event(db, service_id, kind, message, level, details)` — insert into events table
  - Does NOT commit — caller owns transaction boundary
  - Event kinds: `service_created`, `edge_started`, `tailscale_ip_acquired`, `dns_created`, `dns_updated`, `dns_removed`, `caddy_reloaded`, `cert_issued`, `cert_renewed`, `cert_failed`, `reconcile_completed`, `reconcile_failed`, `dns_update_failed`
  - Levels: `info`, `warning`, `error`
- [x] **7.5** Implement Events API endpoints (`backend/app/routers/events.py`)
  - `GET /api/events` — list events with filters: `service_id`, `kind`, `level`, `search` (message ILIKE), `limit`/`offset`; returns `{events, total}`
  - `GET /api/events/services/:id` — service-specific events with `kind`/`level` filters
  - Registered as `events_router` in `main.py`
- [x] **7.6** Build Events/Logs UI screen (`frontend/src/pages/Events.tsx`)
  - Filterable event table: search, level dropdown, kind dropdown
  - Each row: timestamp (monospaced), level badge (color-coded with icon), kind, message
  - Expandable detail view (JSON pretty-print) via chevron toggle
  - Pagination (Previous/Next) with total count

---

## Milestone 8: Dashboard & UX Polish — COMPLETE

- [x] **8.1** Build Dashboard UI (`frontend/src/pages/Dashboard.tsx` + `backend/app/routers/dashboard.py`)
  - `GET /api/dashboard/summary` — returns service counts, expiring certs, recent errors, recent events
  - Summary cards: total services, healthy, warning, error counts with color-coded icons
  - Upcoming cert expiries list (within 30 days) with days-left indicator
  - Recent errors section (last 8 error-level events)
  - Recent events timeline (last 10 events) with level badges + link to full events page
- [x] **8.2** Implement first-time setup flow (`frontend/src/pages/Setup.tsx`)
  - App.tsx checks `setup_complete` on load; redirects to `/setup` if false
  - 6-step wizard: account setup, base domain, Cloudflare (zone ID + token), ACME email, Tailscale auth key, Docker socket
  - Each step validates via test endpoints (Cloudflare, Tailscale, Docker) before proceeding
  - Shows success/failure test result inline; marks setup complete on final step
  - Progress bar shows step indicator
- [x] **8.3** Add live progress to service creation wizard (enhanced `ExposeService.tsx`)
  - Progress phases updated to match actual reconciler phases (validating → ensuring_cert → rendering_config → ensuring_edge → detecting_ip → ensuring_dns → reloading_caddy → checking_health → healthy)
  - Phase stepper with green checks / spinner / failure icons
  - Polls `GET /api/services/:id` every 2s until terminal phase
  - Shows inline error message if phase is failed
- [x] **8.4** Implement app profile system (`backend/app/routers/profiles.py`)
  - 7 profile definitions: generic, nextcloud, jellyfin, immich, calibre-web, home-assistant, vaultwarden
  - Each profile: recommended_port, healthcheck_path, preserve_host_header, post_setup_reminder, image_patterns
  - `GET /api/profiles` — list all profiles
  - `GET /api/profiles/detect?image=<name>` — auto-detect profile from Docker image name
  - `detect_profile(image_name)` — pattern matching against image_patterns
  - Wizard auto-detects profile on load, applies defaults (port, healthcheck, host header)
  - Post-setup reminder shown after successful creation (e.g., Nextcloud trusted_domains)
- [x] **8.5** Polish error handling and user messaging
  - Service detail health checks section shows actionable suggestions for each failing subcheck
  - `CHECK_SUGGESTIONS` dict: maps each health check to "what failed + what to do" guidance
  - Failing checks shown in red callout box below health grid
  - Hover tooltip on each failing check for quick reference
- [x] **8.6** Add service lifecycle confirmations (`ServiceDetail.tsx`)
  - **Delete**: Confirmation panel with cleanup checkbox ("Remove DNS record from Cloudflare"), calls `?cleanup_dns=true`
  - **Disable**: Inline confirmation with explanation ("The edge container will stop receiving traffic")
  - **Recreate Edge**: Inline confirmation with warning ("This will cause brief downtime")

---

## Milestone 9: Production Readiness — COMPLETE

- [x] **9.1** Production orchestrator Docker image + config externalization
  - Multi-stage Dockerfile: Node frontend build → Python 3.12 production with lego binary
  - All secrets/configs externalized to env vars: `JWT_SECRET`, `COOKIE_SECURE`, `CORS_ORIGINS`, `PORT`, `DATA_DIR`, `DOCKER_SOCKET`
  - Health check uses `PORT` env var (no hardcoded port)
  - Static file serving: FastAPI serves React SPA with `/assets` mount and catch-all SPA fallback
  - `.env.example` expanded with all configurable settings and documentation
  - `docker-compose.prod.yml` for production deployment with required JWT_SECRET validation
  - `.gitignore` already covers `.env`, `data/`, `__pycache__/`, `node_modules/`
  - Note: Alembic migrations deferred (create_all works well for SQLite v1; add Alembic when schema changes need versioning)
- [x] **9.2** Edge container Docker image (reviewed, finalized)
  - Based on `tailscale/tailscale:latest` with Caddy static binary
  - Entrypoint: tailscaled (userspace) → tailscale up → caddy run, with graceful shutdown
  - No hardcoded secrets — all config injected via env vars and mounted files
  - Registry push deferred until user is ready
- [x] **9.3** Unraid deployment documentation (`DEPLOY.md`)
  - Docker Compose deployment (recommended path)
  - Manual `docker run` alternative
  - Volume mount reference table
  - Full environment variables reference
  - Update and troubleshooting instructions
  - Registry push instructions (for when user is ready)
- [x] **9.4** User-facing README (`README.md`)
  - Architecture diagram (text), prerequisites, quick start
  - Stack overview, development setup, test commands
  - Project structure reference
- [x] **9.5** End-to-end test verification
  - All 325 backend pytest tests passing
  - All 97 frontend vitest tests passing (422 total)
  - E2e flow documented in DEPLOY.md (manual: install → setup wizard → discover → expose → verify)

---

## Milestone 10: Bug Fixes & Hardening (from code review)

### Blockers / High Priority

- [ ] **10.1** Edge image auto-build from orchestrator
  - `container_manager.py` hardcodes `tailbale-edge:latest` but nothing builds it in production
  - Bundle `edge/Dockerfile` + `edge/entrypoint.sh` into the orchestrator image
  - Add startup/lazy check: if `tailbale-edge:latest` doesn't exist locally, build it from the bundled context via Docker API
  - Removes the need for users to manually build the edge image

- [ ] **10.2** Fix preserve_host_header logic (inverted)
  - `config_renderer.py` emits `header_up Host {upstream_hostport}` when `preserve_host_header=True`
  - Caddy already preserves the original request Host by default
  - `{upstream_hostport}` resolves to the upstream container:port, which *overwrites* the host — the opposite of "preserve"
  - Fix: emit the `header_up Host` line only when `preserve_host_header=False`

- [ ] **10.3** Fix manual reconcile thread safety
  - `POST /api/services/{id}/reconcile` passes request-scoped `db` session into `asyncio.to_thread()`
  - SQLAlchemy sessions are not thread-safe
  - Fix: create a fresh `SessionLocal()` inside the thread closure (same pattern as `reconcile_loop`)

- [ ] **10.4** Remove `tailscale logout` from edge entrypoint shutdown handler
  - `edge/entrypoint.sh` line 11: `tailscale logout` deregisters the node on every container stop
  - Defeats persistent Tailscale state — forces re-auth on restart, may get new IP
  - With one-use auth keys, container can never re-auth after restart
  - Fix: remove the logout call, just kill tailscaled gracefully

### Medium Priority

- [ ] **10.5** Add resource cleanup to disable/delete
  - `disable_service()` only flips `enabled=False` — edge container keeps running and serving traffic
  - `delete_service()` only cleans DNS + deletes DB rows — edge container, Docker network, cert files, generated configs, and Tailscale state are orphaned
  - Fix disable: call `stop_edge()` after setting `enabled=False`
  - Fix delete: call `remove_edge()`, optionally remove Docker network, clean up disk artifacts before deleting DB rows

- [ ] **10.6** Wire DB-backed settings into runtime code
  - Settings UI persists `docker_socket_path`, `generated_root`, `cert_root`, `tailscale_state_root` to DB
  - But reconciler, container_manager, cert_manager, health_checker all read from `app.config.settings` (env vars frozen at startup)
  - Fix: read these from DB via `get_setting()` with `app.config.settings` as fallback default

### Low Priority

- [ ] **10.7** Validate upstream container exists on service creation
  - `create_service()` doesn't verify the Docker container exists or that the port is exposed
  - Reconciler catches this later, but user gets no immediate feedback
  - Fix: attempt Docker API lookup, return warning (not hard error) if container not found

- [ ] **10.8** Validate hostname belongs to configured base_domain
  - Schema validates hostname syntax but not that it ends with the base_domain
  - Can create `foo.otherdomain.com` when base is `mydomain.com` → DNS goes to wrong zone
  - Fix: verify hostname ends with `.{base_domain}` in `create_service()`, return 422 if mismatch

- [ ] **10.9** Add extensive manual health check endpoint with live Cloudflare query
  - Current health checks only compare DB state internally
  - If someone edits/deletes the Cloudflare record externally, health still shows green
  - Fix: add `POST /api/services/{id}/health-check-full` that runs standard checks PLUS live Cloudflare API query
  - Manual-only (not in reconcile loop) to avoid API rate limits

---

## Key Architecture Decisions (Reference)

| Decision | Choice | Rationale |
|---|---|---|
| Backend language | Python 3.12 (FastAPI) | User preference, good async support |
| Frontend | React 18 + TypeScript (Vite 8) | Wizard-style UI, large ecosystem |
| CSS | Tailwind CSS 4 + shadcn/ui utilities | Utility-first, pre-built accessible components |
| Database | SQLAlchemy 2.0 + SQLite | Single-file DB, good for Unraid appdata |
| ACME | Shell out to `lego` | Simpler than native ACME lib for v1 |
| Edge base image | `tailscale/tailscale` | Less maintenance for TS updates |
| Edge reverse proxy | Caddy | File-based TLS, simple config, auto-reload |
| DNS provider | Cloudflare API v4 | Direct HTTP calls via httpx |
| Docker integration | `docker` Python SDK | Official SDK, well-documented |
| Backend testing | pytest + in-memory SQLite (StaticPool) | Fast, isolated, 299 tests |
| Frontend testing | Vitest + React Testing Library + jsdom | Co-located with Vite, 97 tests |
| Node version | 24 via fnm | Latest, managed by fnm |
| Package manager | pip + requirements.txt (backend), npm (frontend) | Simple, no extra tooling |
