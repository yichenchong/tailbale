# Unraid Per-Service Edge Orchestrator — Detailed Product & Technical Specification

Version: 1.0  
Status: Draft build spec  
Target platform: Unraid (Docker-based)  
Primary audience: Solo builder / future contributors / implementation AIs

---

## 1. Purpose

Build a self-hosted orchestrator for Unraid that lets the user expose selected Docker containers as individually shareable HTTPS services under:

`https://<service>.mydomain.com`

The orchestrator must:

- preserve per-service Tailscale access control
- avoid public inbound exposure on 80/443
- avoid Cloudflare Tunnel
- avoid reverse-proxy dashboard click-ops
- keep existing application containers largely unchanged
- make edge creation simple through a UI similar in spirit to Unraid’s app config flow

The orchestrator is both:

1. a **control plane**: desired state, discovery, reconciliation, status, secrets, DNS, certs
2. a **deployment engine**: creates and manages per-service edge containers

---

## 2. Core design decision

Each exposed application **port** gets its own **edge container**. A single application container may have multiple exposures (one per port/hostname), each backed by a separate edge container.

Each edge container contains:

- a unique Tailscale node identity
- `tailscaled` in userspace networking mode
- Caddy as reverse proxy and TLS terminator
- a generated Caddy config
- mounted cert files issued by the orchestrator

The orchestrator itself:

- discovers Unraid Docker containers
- stores desired exposure configuration
- issues and renews certificates centrally using ACME DNS-01
- writes certificates to per-service cert directories
- creates or updates Cloudflare DNS records
- creates / updates / reloads edge containers
- provides a simple UI for users to wrap a container as an exposed service

---

## 3. Goals

### 3.1 Functional goals

- List existing Docker containers running on Unraid
- Let user select one and “wrap” it with an edge
- Generate one edge container per exposed service
- Manage TLS certificates centrally
- Manage Cloudflare DNS automatically
- Keep service URLs canonical under `mydomain.com`
- Surface health / DNS / cert / edge status in UI
- Allow basic lifecycle operations from UI:
  - create exposure
  - edit exposure
  - disable exposure
  - reload edge
  - recreate edge
  - force certificate renewal
  - view logs

### 3.2 UX goals

- Simple enough that spinning up an edge is easier than manual Nginx Proxy Manager work
- Form-driven, with sane defaults
- Avoid requiring users to understand Docker networking internals for normal use
- Show what is happening, but not overload the user with raw infrastructure detail

### 3.3 Security goals

- No Cloudflare credentials in edge containers
- No ACME in edge containers
- Least-privilege Cloudflare token
- Clear separation between orchestrator secrets and edge runtime
- All user-facing access intended to traverse Tailscale

---

## 4. Explicit non-goals

- Kubernetes
- Cloudflare Tunnel
- Public internet reverse proxy exposure
- Shared central reverse proxy for all services
- Wildcard cert reuse as the primary design
- Managing Tailscale node sharing itself through unsupported/private APIs
- Full secrets vault product in v1
- Deep app-specific config automation for every self-hosted app in v1

---

## 5. Assumptions and constraints

### 5.1 Environment assumptions

- Unraid host is already running Docker
- User installs apps mainly through Community Applications
- User has:
  - a domain managed in Cloudflare
  - a Tailscale tailnet
  - ability to create a Tailscale auth key
- User is willing to mount Docker socket or a docker-socket-proxy
- User accepts that only clients on Tailscale can reach `service.mydomain.com` when that record points to a Tailscale IP

### 5.2 Access model assumption

Public DNS can point to a Tailscale `100.x.y.z` address, but the route only works for devices on Tailscale. That is intentional.

### 5.3 UX constraint

The orchestrator should present “simple forms”, but the backend must still use a declarative model and reconciliation loop rather than imperative one-off scripts.

---

## 6. High-level architecture

### 6.1 Components

#### A. Orchestrator backend
Responsibilities:

- discovery of Docker containers
- persistent storage of desired state and observed state
- cert issuance and renewal
- DNS record reconciliation
- edge lifecycle management
- event/job queue
- status collection
- API for frontend

#### B. Orchestrator frontend
Responsibilities:

- service discovery UI
- wrap/edit wizard
- service details page
- health/status dashboards
- secrets/settings screens
- logs and event history views

#### C. Edge container (per service)
Responsibilities:

- run `tailscaled`
- run Caddy
- terminate TLS using file-based certs
- proxy traffic to upstream app container

#### D. Docker engine / Unraid
Provides:

- container list
- network create/connect/disconnect
- container create/start/stop/restart/exec/logs

#### E. Cloudflare
Provides:

- public DNS records
- DNS-01 support for certificate issuance

#### F. Let’s Encrypt (or compatible ACME CA)
Provides:

- public TLS certificates for `service.mydomain.com`

### 6.2 Trust boundaries

#### Orchestrator trusted zone
Contains:

- Cloudflare token
- Tailscale auth material for new edge nodes
- cert private keys
- persistent state database

#### Edge trusted zone
Contains:

- Tailscale node state for that service only
- mounted service cert and key
- generated Caddy config

Edge must **not** contain:

- Cloudflare API token
- ACME account management logic
- global orchestrator secrets

---

## 7. User journeys

### 7.1 First-time setup journey

1. User opens orchestrator UI
2. User is prompted to complete setup:
   - root domain
   - Cloudflare zone
   - Cloudflare token
   - ACME email
   - Tailscale auth key
   - optional defaults for edge image / resource settings
3. Orchestrator validates settings:
   - can talk to Docker
   - can talk to Cloudflare
   - can issue test DNS lookup
   - can create Tailscale-authenticated edge
4. Setup marked complete
5. User sees discovered containers

### 7.2 Wrap a container journey

1. User clicks “Expose service”
2. User selects an existing container
3. Wizard auto-fills:
   - service name
   - hostname
   - likely internal port(s)
   - upstream scheme default
4. User confirms / edits:
   - hostname
   - port
   - path handling
   - trusted proxy headers mode
   - app-specific notes
5. Backend creates desired service config
6. Reconciler:
   - creates network
   - connects app container if needed
   - creates cert directory
   - issues cert
   - creates edge container
   - waits for Tailscale IP
   - updates DNS
   - reloads/restarts edge if needed
7. UI shows status until healthy

### 7.3 Edit service journey

User can change:

- hostname
- upstream port
- upstream scheme
- headers/preserve host
- edge resource limits
- enabled/disabled state
- advanced Caddy snippet override

Backend updates desired state and reconciliation handles transitions.

### 7.4 Disable service journey

User disables exposure without deleting config.

Expected behavior:

- edge container is stopped or removed depending on chosen mode
- DNS record may be removed or disabled
- certs may be retained
- service remains in UI as disabled

### 7.5 Delete service journey

Expected behavior:

- desired state removed
- edge container removed
- optional cleanup prompt for:
  - DNS record
  - cert directory
  - tailscale state volume
  - dedicated docker network

---

## 8. Functional requirements

### 8.1 Discovery

The system must discover Docker containers and display at least:

- container name
- image
- running/stopped state
- published ports
- attached networks
- labels
- likely app type heuristic if possible

The system should allow filtering out:

- orchestrator container
- edge containers
- helper infrastructure containers

### 8.2 Desired state management

System must persist a normalized desired-state record per exposed service.

**Important**: A single application container may have multiple service exposures. For example, an app that listens on both port 80 (HTTP UI) and port 443 (HTTPS API) can be exposed as two separate services, each with its own hostname, edge container, certificate, and DNS record. The data model must not enforce a one-to-one relationship between upstream containers and service exposures. Edge container names, network names, and Tailscale hostnames must be unique across all exposures.

Each record must include:

- stable internal service ID
- human-readable name
- upstream container reference
- hostname
- upstream scheme (`http` or `https`)
- upstream port
- docker network strategy
- enabled flag
- status metadata
- edge container naming metadata (must be unique across all services)
- cert metadata
- DNS metadata
- creation/update timestamps

### 8.3 Edge lifecycle management

The backend must be able to:

- create edge container
- start edge container
- stop edge container
- recreate edge container
- remove edge container
- exec into edge container to run `caddy reload`
- collect logs
- detect drift and repair

### 8.4 Docker network management

For each exposed service, backend should create a dedicated docker network:

Suggested convention:

- `edge_net_<service_id>`

The app container and the edge container are attached to this network.

Backend must support:

- creating network if absent
- connecting app container
- ensuring edge container is connected
- not disconnecting app from unrelated networks unless explicitly requested

### 8.5 Certificate management

Backend must centrally manage ACME issuance and renewal.

Required behavior:

- issue cert on service creation
- renew cert before expiry
- track expiry timestamp
- write cert files atomically
- reload edge when cert files change
- surface renewal failures in UI

### 8.6 DNS management

Backend must manage Cloudflare A record per service.

Required behavior:

- create `A` record for hostname to edge Tailscale IPv4
- `proxied=false`
- update if edge IP changes
- surface mismatch/drift in UI
- remove record when service deleted if chosen

### 8.7 Health/status

System should compute service health from multiple sub-checks:

- desired state valid
- app container exists
- app container reachable from edge network
- edge container running
- tailscaled healthy
- Tailscale IP assigned
- cert exists
- cert not near expiry
- DNS record exists and matches observed IP
- edge HTTPS endpoint returns success from within tailnet or local probe path

Overall service status should be derived from these.

### 8.8 Logging and event history

Must provide:

- recent orchestrator events
- recent edge logs
- cert issuance/renewal history
- DNS update history
- edge create/update/delete history

---

## 9. Non-functional requirements

### 9.1 Reliability
- Reconciliation loop must be idempotent
- Repeated runs should converge, not multiply resources
- Writes to service state should be transactional

### 9.2 Performance
- UI container list should load quickly on typical Unraid scale
- Reconciliation should be per-service, not block all services unnecessarily
- Cert renew scan can be periodic, not constant

### 9.3 Maintainability
- Separate domain logic from Docker, Cloudflare, and ACME adapters
- Strong typing for desired state and observed state
- Templated edge config generation

### 9.4 Debuggability
- Every reconcile action should create structured event logs
- Failures should preserve reason and last attempted step
- UI should tell user what is broken and what action was attempted

---

## 10. Detailed domain model

### 10.1 ServiceExposure

A ServiceExposure represents one exposed port of one container. The same container may appear in multiple ServiceExposure records with different ports and hostnames.

```yaml
id: svc_abc123
name: Nextcloud
upstream:
  container_id: docker_container_id
  container_name: nextcloud
  scheme: http
  port: 80                          # one port per exposure; same container may have other exposures
  healthcheck_path: /status.php
domain:
  hostname: nextcloud.mydomain.com
  base_domain: mydomain.com
edge:
  container_name: edge_nextcloud
  image: ghcr.io/your-org/edge-runtime:latest
  network_name: edge_net_svc_abc123
  caddy_config_path: /appdata/orchestrator/generated/svc_abc123/Caddyfile
  cert_mount_path: /appdata/orchestrator/certs/nextcloud.mydomain.com
  tailscale_state_path: /appdata/orchestrator/tailscale/edge_nextcloud
  ts_hostname: edge-nextcloud
dns:
  provider: cloudflare
  zone_id: abcdef
  record_id: optional
  expected_type: A
  expected_value: 100.x.y.z
tls:
  email: admin@mydomain.com
  cert_path: /appdata/orchestrator/certs/nextcloud.mydomain.com/fullchain.pem
  key_path: /appdata/orchestrator/certs/nextcloud.mydomain.com/privkey.pem
  expires_at: 2026-08-01T00:00:00Z
  renew_after: 2026-07-01T00:00:00Z
status:
  phase: healthy
  message: edge running and dns correct
  last_reconciled_at: 2026-04-05T00:00:00Z
  enabled: true
advanced:
  preserve_host_header: true
  custom_caddy_snippet: null
  extra_headers: []
  app_profile: nextcloud
```

### 10.2 GlobalSettings

```yaml
domain:
  base_domain: mydomain.com
  acme_email: admin@mydomain.com
cloudflare:
  zone_id: abcdef
  token_secret_ref: cloudflare_token
tailscale:
  authkey_secret_ref: tailscale_authkey
  control_url: https://controlplane.tailscale.com
docker:
  socket_path: /var/run/docker.sock
runtime:
  reconcile_interval_seconds: 60
  cert_renewal_window_days: 30
  generated_root: /mnt/user/appdata/orchestrator/generated
  cert_root: /mnt/user/appdata/orchestrator/certs
  tailscale_state_root: /mnt/user/appdata/orchestrator/tailscale
```

### 10.3 EventRecord

```yaml
id: evt_123
service_id: svc_abc123
kind: dns_updated
level: info
message: Updated A record to 100.99.88.77
created_at: 2026-04-05T00:00:00Z
details:
  previous_value: 100.88.77.66
  new_value: 100.99.88.77
```

---

## 11. Backend architecture

### 11.1 Suggested backend modules

#### A. API server
Serves frontend and REST/JSON APIs

#### B. Reconciler
Reads desired state and observed state, applies corrective actions

#### C. Docker adapter
Wrapper over Docker API

#### D. Cloudflare adapter
Wrapper over DNS operations

#### E. ACME/cert manager
Wrapper over `lego` or direct ACME library integration

#### F. Edge config renderer
Generates Caddyfiles and runtime env

#### G. Health checker
Aggregates service status

#### H. Job runner
Handles long-running tasks like first issuance or edge creation

#### I. Persistence layer
Stores settings, services, events, observed state snapshots

### 11.2 Suggested stack

This is not mandatory, but a practical stack would be:

- Backend: Go
- Frontend: React + TypeScript
- DB: SQLite
- Container runtime integration: Docker SDK or raw API
- ACME: shelling out to `lego` initially
- Reverse proxy inside edge: Caddy
- Edge supervisor: simple process manager or custom entrypoint script

Reasoning:
- Go fits Docker/network orchestration well
- SQLite is enough for v1
- React is good for the settings + service wizard UI

### 11.3 Reconcile model

The reconciler should be event-driven plus periodic.

#### Triggers
- service created/edited/deleted
- settings updated
- periodic sweep
- explicit user action
- startup recovery

#### Reconcile order for one service

1. Load desired service config
2. Validate references and settings
3. Ensure generated directories exist
4. Ensure docker network exists
5. Ensure app container connected to network
6. Ensure cert exists or renew if needed
7. Render Caddy config
8. Ensure edge container exists with correct spec
9. Ensure edge container running
10. Inspect edge for Tailscale IP
11. Ensure DNS record matches Tailscale IP
12. Reload Caddy if config or cert changed
13. Run health checks
14. Persist observed state and emit events

This must be idempotent.

---

## 12. Edge container specification

### 12.1 Edge responsibilities

The edge is a small runtime, not a control plane.

It should:

- authenticate to Tailscale with persistent state
- expose HTTPS using mounted certs
- proxy to upstream app by Docker DNS name

### 12.2 Edge internals

Suggested processes:

- `tailscaled` in userspace mode
- `caddy run --config /etc/caddy/Caddyfile`

Entrypoint should:

1. start `tailscaled`
2. authenticate if state absent
3. wait until Tailscale is ready
4. start Caddy
5. remain PID 1 or use a supervisor

### 12.3 Edge container inputs

- mounted Tailscale state dir
- mounted cert dir
- mounted generated Caddyfile
- env vars:
  - `TS_AUTHKEY`
  - `TS_HOSTNAME`
  - possibly `TS_EXTRA_ARGS`
- network attachment to per-service bridge network

### 12.4 Edge container outputs

- log stream
- discovered Tailscale IP
- HTTPS listener
- proxy behavior to upstream

### 12.5 Edge naming convention

Container name:
- `edge_<service_slug>`

Hostname inside Tailscale:
- `edge-<service_slug>`

---

## 13. Certificate manager spec

### 13.1 Responsibilities

- create per-service cert directory
- issue cert using DNS-01
- renew before expiry
- store expiry metadata
- reload edge after successful renewal

### 13.2 Renewal model

Daily background scan:

- list all enabled services
- if cert missing: issue
- if expiry <= renewal window: renew
- if renew succeeds:
  - atomically replace files
  - emit event
  - queue edge reload

### 13.3 Atomic file write requirements

Do not write cert files in-place partially.

Required approach:

- write to temp files
- fsync if possible
- rename into place
- only then reload Caddy

### 13.4 ACME failure handling

Failures must not crash orchestrator.

Store:

- last failure time
- last failure reason
- next retry time

Expose in UI.

---

## 14. Cloudflare DNS spec

### 14.1 Required operations

- get zone
- create A record
- update A record
- delete A record
- verify effective desired value in stored state

### 14.2 Record policy

Record type:
- `A`

Value:
- edge Tailscale IPv4

Proxy:
- off (`proxied=false`)

TTL:
- auto or configurable

### 14.3 Failure modes

Possible errors:
- auth failure
- zone mismatch
- duplicate hostname
- API rate limit
- partial drift

Each should be surfaced clearly.

---

## 15. Frontend specification

### 15.1 Frontend goals

The frontend should make orchestration easy enough that the user mostly thinks in terms of:

- which app?
- which domain?
- which internal port?
- is it healthy?

Not in terms of:
- raw Docker APIs
- cert tooling
- Caddy syntax

### 15.2 Main navigation

Suggested sections:

- Dashboard
- Services
- Discover
- Events / Logs
- Settings

### 15.3 Screen: Dashboard

Purpose:
- at-a-glance operational view

Must show:
- total exposed services
- healthy / warning / error counts
- upcoming cert expiries
- recent errors
- recent events

Optional cards:
- Docker connection healthy
- Cloudflare connection healthy
- ACME subsystem healthy

### 15.4 Screen: Discover containers

Purpose:
- show Docker containers eligible for exposure

Columns:
- container name
- image
- status
- ports
- networks
- labels
- “Expose” action

Filters:
- running only
- hide system containers
- search by name/image

### 15.5 Screen: Expose service wizard

This is the most important screen.

#### Step 1: Select container
Fields:
- container dropdown/search
- detected port list
- suggested app profile (if recognized)

#### Step 2: Configure endpoint
Fields:
- service display name
- hostname prefix
- full hostname preview
- upstream port
- upstream scheme
- optional healthcheck path
- enable immediately checkbox

#### Step 3: Advanced
Fields:
- preserve host header toggle
- trusted proxy header behavior
- custom response headers
- custom Caddy snippet textarea
- resource limit hints for edge
- optional “remove host port risk reminder” warning

#### Step 4: Review
Show:
- edge container name
- dns record to be created
- cert to be issued
- docker network to be created

#### Step 5: Submit and live progress
Show progress states:
- saving config
- creating network
- issuing cert
- creating edge
- waiting for Tailscale IP
- updating DNS
- health checking

Failures should appear inline with retry action.

### 15.6 Screen: Service list

Rows:
- service name
- hostname
- upstream container
- status pill
- cert expiry
- edge IP
- actions menu

Actions:
- view details
- edit
- reload edge
- recreate edge
- disable
- delete

### 15.7 Screen: Service detail

Sections:

#### Overview
- service name
- hostname (clickable)
- upstream container
- current status
- last reconciled

#### Health breakdown
- upstream reachable
- edge running
- tailscale ready
- cert valid
- dns correct

#### Runtime details
- edge container name
- Tailscale IP
- docker network name
- cert expiry
- record id

#### Actions
- reload Caddy
- restart edge
- recreate edge
- force renew cert
- re-run reconciliation

#### Logs tabs
- orchestrator events
- edge logs
- cert logs
- dns actions

### 15.8 Screen: Settings

Tabs:

#### General
- base domain
- ACME email
- reconcile interval
- cert renewal window

#### Cloudflare
- zone id
- token secret input/update
- test connection button

#### Tailscale
- auth key input/update
- default tags/hostname policy
- test edge creation button

#### Docker
- socket path or socket proxy URL
- test connection button

#### Paths / storage
- generated config root
- cert root
- tailscale state root

### 15.9 Screen: Events / logs

Capabilities:
- filter by service
- filter by severity
- search text
- view structured event details
- copy raw logs

---

## 16. Backend API specification

This is a logical API spec; exact routes may change.

### 16.1 Settings APIs

#### GET /api/settings
Return current non-secret settings and secret presence flags

#### PUT /api/settings/general
Update domain, email, intervals

#### PUT /api/settings/cloudflare
Update zone and token

#### PUT /api/settings/tailscale
Update auth key and defaults

#### POST /api/settings/test/docker
Test Docker connectivity

#### POST /api/settings/test/cloudflare
Test Cloudflare token/zone

#### POST /api/settings/test/tailscale
Test that a disposable edge can authenticate

### 16.2 Discovery APIs

#### GET /api/discovery/containers
List discovered Docker containers

Query params:
- `running_only`
- `hide_managed`
- `search`

### 16.3 Service APIs

#### GET /api/services
List all service exposures

#### POST /api/services
Create new service exposure

#### GET /api/services/:id
Get full service detail

#### PUT /api/services/:id
Update exposure config

#### POST /api/services/:id/reconcile
Force reconcile

#### POST /api/services/:id/reload
Run Caddy reload in edge

#### POST /api/services/:id/restart-edge
Restart edge container

#### POST /api/services/:id/recreate-edge
Destroy and recreate edge container from desired spec

#### POST /api/services/:id/renew-cert
Force cert renewal attempt

#### POST /api/services/:id/disable
Disable service

#### DELETE /api/services/:id
Delete service

### 16.4 Event/log APIs

#### GET /api/events
List events with filters

#### GET /api/services/:id/events
Service-specific events

#### GET /api/services/:id/logs/edge
Edge container logs

#### GET /api/services/:id/logs/cert
Cert manager logs or events

---

## 17. Service creation backend workflow

When user submits the wizard, backend should perform:

1. Validate hostname uniqueness
2. Validate upstream container exists
3. Validate chosen upstream port is plausible
4. Persist desired service record as `pending`
5. Queue reconcile job
6. Return service ID immediately
7. Frontend polls or streams progress

### Expected progress states
- `pending`
- `validating`
- `creating_network`
- `connecting_upstream`
- `issuing_cert`
- `rendering_edge_config`
- `creating_edge`
- `starting_edge`
- `waiting_for_tailscale`
- `syncing_dns`
- `reloading_caddy`
- `health_checking`
- `healthy`
- `failed`

---

## 18. Health model

### 18.1 Health subchecks

Each service should have at least these booleans:

- `upstream_container_present`
- `upstream_network_connected`
- `edge_container_present`
- `edge_container_running`
- `tailscale_ready`
- `tailscale_ip_present`
- `cert_present`
- `cert_not_expiring`
- `dns_record_present`
- `dns_matches_ip`
- `caddy_config_present`
- `https_probe_ok`

#### Design note: `dns_matches_ip` uses stored state

The regular health check compares the stored `DnsRecord.value` in the database
against the current Tailscale IP.  It does **not** make a live Cloudflare API
call on every reconcile cycle.  This is a deliberate trade-off:

- **Avoids Cloudflare API rate limits** — the reconciler runs every 60 seconds
  for every enabled service.
- **Stored state is authoritative** — the orchestrator is the only actor that
  creates or updates these DNS records.  External drift (someone manually
  editing Cloudflare) is an unusual edge case.
- **Live verification is available on demand** — the manual
  `POST /api/services/:id/health-check-full` endpoint performs a live Cloudflare
  API lookup and reports whether the live record matches the expected IP.

If external DNS mutations become a concern, a periodic (e.g. hourly) full-health
sweep could be added as a background task without impacting the per-minute
reconcile loop.

### 18.2 Aggregate status rules

#### Healthy
All critical checks pass

#### Warning
Service works but non-critical issue exists

Examples:
- cert expiry within 14 days
- DNS drift detected but connectivity still works
- edge recreated recently and probe pending

#### Error
Any critical check fails

Examples:
- edge down
- no Tailscale IP
- missing cert
- DNS update failed
- upstream unreachable

---

## 19. App-profile system

Some apps need app-level canonical URL configuration.

The orchestrator should support **profiles** that do not fully auto-configure the app in v1, but can provide guidance and maybe future automation hooks.

### Example app profiles
- generic
- nextcloud
- immich
- jellyfin
- calibre-web

### Profile responsibilities
- recommended upstream port
- recommended healthcheck path
- recommended proxy headers
- display app-specific post-setup reminders

### Example reminder for Nextcloud
“Set trusted domains and overwrite URL to `https://nextcloud.mydomain.com`.”

This matters because the edge alone does not guarantee app-generated URLs are correct.

---

## 20. Security specification

### 20.1 Secret storage

V1 acceptable approach:

- store secrets in files under appdata with restricted perms
- do not expose secrets back to frontend after write
- frontend only receives boolean presence flags

Stored secrets:
- Cloudflare API token
- Tailscale auth key

### 20.2 Docker socket exposure

If direct Docker socket is used, this is high privilege.

Preferred option:
- support docker-socket-proxy
Fallback:
- direct socket mount

UI should warn user about privilege implications during setup.

### 20.3 Edge isolation

Each edge should only have:
- its per-service network
- mounted cert dir
- mounted generated Caddyfile
- mounted tailscale state dir

It should not have:
- Docker socket
- global generated config root
- orchestrator DB
- Cloudflare token

### 20.4 Input validation

Validate:
- hostname format
- domain ownership consistency
- Caddy snippet safety constraints if supported
- container name references
- resource limit inputs

---

## 21. Persistence specification

### 21.1 Database tables (logical)

#### settings
Stores non-secret global settings

#### secret_refs
Stores metadata about secrets, not secret contents themselves

#### services
Core desired state per service

#### service_status
Latest observed state and aggregate health

#### events
Structured event log

#### jobs
Async long-running tasks and progress

#### dns_records
Observed DNS metadata per service

#### certificates
Observed cert metadata per service

### 21.2 Filesystem layout

Suggested root:
`/mnt/user/appdata/orchestrator/`

Subdirs:
- `db/`
- `secrets/`
- `generated/`
- `certs/`
- `tailscale/`
- `tmp/`

Example:
```text
appdata/orchestrator/
  db/orchestrator.sqlite
  secrets/cloudflare_token
  secrets/tailscale_authkey
  generated/svc_abc123/Caddyfile
  certs/nextcloud.mydomain.com/fullchain.pem
  certs/nextcloud.mydomain.com/privkey.pem
  tailscale/edge_nextcloud/
```

---

## 22. Detailed edge configuration rendering

### 22.1 Base Caddyfile template

```caddyfile
{
  auto_https off
}

https://{{HOSTNAME}} {
  tls /certs/fullchain.pem /certs/privkey.pem

  reverse_proxy {{UPSTREAM_NAME}}:{{UPSTREAM_PORT}} {
    {{PRESERVE_HOST_BLOCK}}
    header_up X-Forwarded-Proto https
    header_up X-Forwarded-Host {host}
    header_up X-Real-IP {remote_host}
  }

  {{CUSTOM_SNIPPET}}
}
```

### 22.2 Templating rules

- hostname must exactly match service hostname
- upstream container hostname should resolve on per-service Docker network
- custom snippet insertion must be optional and validated
- write config deterministically so diffing is easy

---

## 23. Error handling specification

### 23.1 User-facing principles

Errors must answer:
- what failed?
- where did it fail?
- what automatic retry, if any, will occur?
- what can user do now?

### 23.2 Common error classes

#### Docker errors
- socket unavailable
- container missing
- network create failed
- edge create failed

#### Tailscale errors
- auth key invalid
- edge failed to obtain IP
- tailscaled not healthy

#### ACME errors
- DNS challenge failed
- rate limited
- invalid email/contact
- file write failed

#### Cloudflare errors
- auth denied
- zone mismatch
- hostname collision
- update failed

#### Config errors
- hostname invalid
- duplicate service hostname
- upstream port missing
- Caddy config invalid

---

## 24. Observability specification

### 24.1 Metrics (optional v1.5)

Could expose Prometheus metrics later:
- `service_health`
- `cert_days_until_expiry`
- `reconcile_duration`
- `reconcile_failures_total`
- `dns_update_total`
- `edge_reload_total`

### 24.2 Structured eventing

Every significant state transition should emit events.

Examples:
- `service_created`
- `network_created`
- `cert_issued`
- `edge_started`
- `tailscale_ip_acquired`
- `dns_updated`
- `caddy_reloaded`
- `health_probe_failed`

---

## 25. MVP scope vs later phases

### 25.1 MVP must include

- setup screen
- docker discovery
- service wizard
- per-service edge container creation
- cert issuance via Cloudflare DNS-01
- DNS record creation/update
- service list and detail pages
- health status
- reload/restart/reconcile actions
- logs/events view

### 25.2 Post-MVP nice-to-haves

- app profile library with partial auto-configuration assistance
- import/export config
- backup/restore
- multi-domain support
- Funnel option per service
- edge image auto-update strategy
- WebSocket live progress streams instead of polling
- bulk operations

---

## 26. Acceptance criteria

A build is acceptable if the following are all true:

1. User can install orchestrator on Unraid
2. User can configure base domain, Cloudflare, and Tailscale credentials
3. User can discover an existing app container
4. User can expose it via a form in under a few minutes
5. Backend creates:
   - service config
   - network
   - edge container
   - cert
   - DNS record
6. Resulting service is reachable at:
   `https://<service>.mydomain.com`
   from a device on Tailscale
7. Edge is independently manageable and separately shareable at the Tailscale level
8. Cert renewals happen centrally without Cloudflare credentials inside edges
9. Updated certs cause Caddy reload without full edge recreation
10. UI clearly shows broken states and recent actions

---

## 27. Suggested implementation milestones

### Milestone 1: Skeleton
- backend project
- frontend shell
- SQLite schema
- settings page
- Docker discovery API

### Milestone 2: Service CRUD
- create/edit/delete service records
- service list/detail pages
- discovery to wizard flow

### Milestone 3: Edge runtime
- build edge image
- create network
- create/start edge container
- basic Caddy config generation

### Milestone 4: Tailscale readiness
- persistent Tailscale state
- detect edge Tailscale IP
- surface status in UI

### Milestone 5: Cert manager
- integrate `lego`
- create/renew certs
- atomic file writes
- Caddy reload action

### Milestone 6: DNS manager
- Cloudflare create/update/delete A record
- drift detection

### Milestone 7: Reconciler and health
- full converge loop
- health model
- event history

### Milestone 8: UX polish
- live progress
- better errors
- app profiles
- warnings and hints

---

## 28. Open design questions left intentionally flexible

These do not block implementation, but final choices should be made during build:

- whether backend should shell out to `lego` or use an ACME library directly
- whether to use direct Docker socket or docker-socket-proxy by default
- whether edge uses a custom supervisor binary or shell entrypoint
- whether logs are stored in DB or read live from Docker only
- whether health probing should originate from orchestrator or edge

---

## 29. Implementation note for another AI or engineer

Do not implement this as ad-hoc button handlers that directly mutate Docker state with no source of truth.

Implement:

- a clear desired-state model
- a per-service reconciliation loop
- adapters for Docker / Cloudflare / ACME
- deterministic generated files
- a UI that edits desired state and observes progress

The user experience should feel simple, but the internals should behave like a small infrastructure controller.

---

## 30. Final summary

This orchestrator is a **Unraid-native control plane for per-service Tailscale edges**.

The product should let the user take an existing self-hosted application container and, with a simple form, turn it into:

- a dedicated edge container
- a dedicated Tailscale identity
- a valid HTTPS endpoint under `mydomain.com`
- a centrally managed cert lifecycle
- an automatically reconciled DNS record

The frontend should optimize for ease and visibility.  
The backend should optimize for idempotent reconciliation, clean state, and safe separation of secrets from edge runtimes.
