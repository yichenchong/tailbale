# tailBale Manual Testing Checklist

Test these journeys on the Unraid box after deployment. Work through them in
order — each section builds on the previous one.

---

## Prerequisites

- [ ] tailBale container is running (`docker logs tailbale` shows startup)
- [ ] `/api/health` returns `{"status":"ok"}` (curl from Unraid shell)
- [ ] You have a Cloudflare zone ID + API token with DNS edit permissions
- [ ] You have a Tailscale auth key (reusable, tagged for the tailnet)
- [ ] At least one other Docker container is running on the host (e.g. Nextcloud,
      Plex, or any simple container like `nginx`)

---

## 1. First Launch — Setup Wizard

Open the UI in a browser. You should be redirected to `/setup`.

### 1a. Create admin account (Step 0)

- [ ] Enter a username and password (8+ characters)
- [ ] Confirm password must match — try a mismatch and verify the Next button
      stays disabled
- [ ] Click Next — should advance to Step 1 (Domain)
- [ ] Verify you are now logged in (the session cookie is set automatically)

### 1b. Base domain (Step 1)

- [ ] Enter your base domain (e.g. `mydomain.com`)
- [ ] Click Next

### 1c. Cloudflare (Step 2)

- [ ] Enter your Cloudflare zone ID
- [ ] Enter your Cloudflare API token
- [ ] Click Next — should run a connection test automatically
- [ ] **Pass**: green banner shows zone name (e.g. "Connected to zone: mydomain.com")
- [ ] **Fail**: red banner with error message — fix credentials and retry

### 1d. ACME email (Step 3)

- [ ] Enter an email address for Let's Encrypt notifications
- [ ] Click Next

### 1e. Tailscale (Step 4)

- [ ] Enter your Tailscale auth key
- [ ] Click Next — runs format validation
- [ ] **Pass**: green banner ("Auth key format looks valid")
- [ ] **Fail**: red banner if key doesn't start with `tskey-auth-` or
      `tskey-reusable-`

### 1f. Docker (Step 5)

- [ ] Default socket path should be pre-filled (`unix:///var/run/docker.sock`)
- [ ] Click Next — runs a live Docker connection test
- [ ] **Pass**: green banner shows Docker version
- [ ] **Fail**: check that `/var/run/docker.sock` is mounted into the tailBale
      container

### 1g. Completion

- [ ] After Docker step succeeds, you should be redirected to the Dashboard (`/`)
- [ ] Dashboard loads without errors — shows 0 total services

---

## 2. Dashboard — Empty State

- [ ] Four cards show: Total 0, Healthy 0, Warning 0, Error 0
- [ ] "No certificates expiring" message
- [ ] "No recent errors" message
- [ ] "No events yet" message

---

## 3. Discover Containers

Navigate to **Discover** in the sidebar.

- [ ] Page loads and shows running Docker containers from the host
- [ ] The tailBale container itself is hidden (it has `tailbale.managed` label)
- [ ] Each container shows: name, image, state (running), ports, networks
- [ ] Toggle "Running only" off — stopped containers appear
- [ ] Toggle it back on — only running containers shown
- [ ] Type a container name in the search box, press Enter — list filters
- [ ] Clear search, press Enter — full list returns
- [ ] Pick a container you want to expose — note its name and ports
- [ ] Click **Expose** — you are taken to `/expose?container_id=...&...`

---

## 4. Expose a Service (Happy Path)

You should now be on the Expose Service wizard with fields pre-filled.

### 4a. Form defaults

- [ ] Service Name is pre-filled from the container name
- [ ] Hostname Prefix is pre-filled (slugified container name)
- [ ] Full URL preview shows `https://{prefix}.{your-base-domain}`
- [ ] Port dropdown shows available container ports (if the container exposes any)
- [ ] If the container image matches a known profile (e.g. Nextcloud), a blue
      banner appears: "Detected {Profile} profile. Defaults have been applied"
      and the port/healthcheck are auto-set

### 4b. Review and create

- [ ] Check the Review section at the bottom: edge container name, DNS record,
      upstream, network all look correct
- [ ] Click **Create Service**

### 4c. Progress screen

- [ ] Screen switches to the progress tracker with steps:
      Queued → Validating → Creating Network → Issuing Certificate → Generating
      Config → Creating Edge → Waiting for Tailscale → Syncing DNS → Reloading
      Caddy → Health Checks → Healthy
- [ ] Steps complete one by one with green checkmarks
- [ ] **Certificate step may take 30–60 seconds** (ACME DNS-01 challenge)
- [ ] **Tailscale step may take 10–30 seconds** (auth + IP assignment)
- [ ] Final state should be **Healthy** with all steps green
- [ ] If a profile was detected, a post-setup reminder may appear (blue banner)
- [ ] Click **View Service** to go to the detail page

### 4d. Verify the service actually works

- [ ] Open `https://{hostname}` in a browser on a device connected to your
      tailnet
- [ ] The upstream application should load via HTTPS with a valid Let's Encrypt
      certificate
- [ ] Check the certificate in the browser — it should be issued by Let's
      Encrypt for your hostname

---

## 5. Service Detail Page

### 5a. Configuration panel

- [ ] Shows upstream URL, hostname, base domain, healthcheck, preserve host,
      app profile
- [ ] Click **Edit** — form fields become editable
- [ ] Change the upstream port to a wrong value (e.g. 9999)
- [ ] Click **Save** — service updates, reconciler should re-run
- [ ] Health checks may now show failures (upstream unreachable on wrong port)
- [ ] Click **Edit** again, change port back to the correct value
- [ ] Click **Save** — service should recover on next reconcile
- [ ] Click **Edit**, make a change, then click **Cancel** — form resets to saved
      values (change is discarded)

### 5b. Runtime panel

- [ ] Shows edge container name, Docker network, TS hostname
- [ ] Tailscale IP should be a `100.x.x.x` address
- [ ] Cert Expiry should show a date ~90 days from now
- [ ] Phase should be "healthy"
- [ ] Last Reconciled should show a recent timestamp

### 5c. Health checks grid

- [ ] All 11 checks should show green checkmarks when healthy:
  - Upstream Container, Network Connected, Edge Container, Edge Running
  - Tailscale Ready, Tailscale IP
  - Certificate, Cert Valid
  - DNS Record, DNS Matches IP
  - Caddy Config
- [ ] If any check fails, a red suggestion banner explains what to do

### 5d. Actions

Test each action button:

- [ ] **Reload Caddy** — should succeed silently, refreshes the page
- [ ] **Restart Edge** — brief pause, then page refreshes, service stays healthy
- [ ] **Recreate Edge** — click, see confirmation prompt ("Recreate edge? This
      will cause brief downtime"), confirm — container is destroyed and recreated.
      Tailscale IP may change. Service should return to healthy within 30–60s.
      **Verify the service URL still works after recreation.**
- [ ] **Force Renew Cert** — triggers ACME renewal. May take 30–60s. Check that
      cert expiry date updates.
- [ ] **Re-run Reconcile** — manually triggers the full 12-step reconciliation.
      Phase should end at "healthy".

---

## 6. Disable / Enable a Service

### 6a. Disable

- [ ] On the service detail page, click **Disable**
- [ ] Confirmation prompt appears: "Disable this service? The edge container will
      stop receiving traffic."
- [ ] Confirm — service status badge changes to "Disabled"
- [ ] The service URL should stop working (edge container no longer gets traffic
      from the reconciler)

### 6b. Enable

- [ ] Click **Enable** (no confirmation needed)
- [ ] Service status badge changes back to "Enabled"
- [ ] Wait for the next reconciliation cycle (or click Re-run Reconcile)
- [ ] Service should return to healthy
- [ ] Verify the service URL works again

---

## 7. Delete a Service

- [ ] On the service detail page, click **Delete**
- [ ] Confirmation panel appears with a checkbox: "Remove DNS record from
      Cloudflare" (checked by default)
- [ ] Click **Delete Service**
- [ ] Redirected to the Services list — the service is gone
- [ ] Verify in Cloudflare dashboard that the DNS A record was removed
- [ ] Verify on the host that the edge container was stopped/removed
      (`docker ps -a | grep edge_`)

---

## 8. Expose a Second Service

Repeat section 4 with a different container to verify:

- [ ] Hostname uniqueness — try reusing the first service's hostname, expect a
      409 error ("Hostname is already in use")
- [ ] Use a different hostname prefix — creation should succeed
- [ ] Dashboard now shows Total: 1 (or 2 if you didn't delete the first)

---

## 9. Break and Recover

These tests verify the system detects and recovers from problems.

### 9a. Stop the upstream container

- [ ] `docker stop {upstream_container_name}` from the Unraid shell
- [ ] Wait for the next reconcile cycle (or trigger manually)
- [ ] Service detail should show:
  - Phase: "error"
  - Health check `upstream_container_present`: red X
  - Suggestion: "The upstream Docker container is not found"
- [ ] Dashboard should show Error count: 1

### 9b. Restart the upstream container

- [ ] `docker start {upstream_container_name}`
- [ ] Trigger reconcile or wait for the next cycle
- [ ] Service should return to "healthy"
- [ ] Dashboard Error count returns to 0

### 9c. Stop the edge container manually

- [ ] `docker stop edge_{slug}` from the Unraid shell
- [ ] Wait for reconcile — the reconciler should detect it's stopped and restart
      it automatically
- [ ] Service should return to healthy without manual intervention

### 9d. Remove the edge container manually

- [ ] `docker rm -f edge_{slug}` from the Unraid shell
- [ ] Wait for reconcile — the reconciler should detect it's missing and
      recreate it from scratch (new container, re-auth to Tailscale)
- [ ] Service should return to healthy (may take 30–60s for Tailscale IP)

---

## 10. Events Page

Navigate to **Events** in the sidebar.

- [ ] Events from all the actions above should appear (service_created,
      edge_started, tailscale_ip_acquired, dns_created, cert_issued,
      reconcile_completed, etc.)
- [ ] Filter by **Level: Error** — should show any reconcile failures from
      section 9
- [ ] Filter by **Kind: cert_issued** — should show certificate events
- [ ] Search for a service name — events filter by message content
- [ ] Click an event row with details — expanded view shows JSON details
- [ ] Pagination works if you have >50 events

---

## 11. Settings Page

Navigate to **Settings** in the sidebar.

### 11a. General tab

- [ ] Shows current base domain and ACME email (from setup)
- [ ] Reconcile interval shows default (60 seconds)
- [ ] Cert renewal window shows default (30 days)
- [ ] Change reconcile interval to 120, click Save — should save and refresh
- [ ] Change it back to 60

### 11b. Cloudflare tab

- [ ] Zone ID shown, token shows "Configured" badge (green checkmark)
- [ ] Click **Test Connection** — should show green "Connected to zone: ..."
- [ ] Token field is blank (write-only) — entering a new value overwrites the
      stored token

### 11c. Tailscale tab

- [ ] Auth key shows "Configured" badge
- [ ] Click **Validate Key** — should show green "Auth key format looks valid"

### 11d. Docker tab

- [ ] Socket path shown
- [ ] Click **Test Connection** — should show green "Connected to Docker {version}"

### 11e. Paths tab

- [ ] Shows generated config root, cert root, Tailscale state root
- [ ] These are informational — only change if you know what you're doing

---

## 12. Services List Page

Navigate to **Services** in the sidebar.

- [ ] All exposed services appear in a table
- [ ] Columns: name (clickable), hostname, upstream, status badge, Tailscale IP,
      cert expiry
- [ ] Cert expiry is color-coded: green (>7d), yellow (<7d), red (expired)
- [ ] Click the "..." menu on a service — shows quick actions:
  - View Details, Reload Caddy, Restart Edge, Recreate Edge, Disable, Delete
- [ ] **Reload Caddy** from the menu — success message appears briefly
- [ ] **Restart Edge** from the menu — success message
- [ ] Click service name — navigates to detail page

---

## 13. Auth & Session

### 13a. Logout

- [ ] Open browser dev tools, check that `tailbale_session` cookie exists under
      `/api` path
- [ ] Log out (if there's a logout button in the sidebar/header)
- [ ] Should redirect to `/login`
- [ ] Cookie should be cleared
- [ ] Navigating to `/services` should redirect to `/login`

### 13b. Login

- [ ] Enter your admin username and password
- [ ] Click **Sign In** — redirected to Dashboard
- [ ] Wrong password — shows "Invalid credentials" error
- [ ] Wrong username — shows "Invalid credentials" error

### 13c. Session expiry

- [ ] After JWT expiry (24h by default), the next API call should return 401
- [ ] Frontend should redirect to `/login`

---

## 14. Edge Cases

- [ ] Create a service with a **custom Caddy snippet** (e.g.
      `header X-Custom "test"`) — verify the header appears in responses
- [ ] Create a service with **HTTPS upstream scheme** — if the upstream app
      serves HTTPS, reverse proxy should work
- [ ] Create a service with a **healthcheck path** (e.g. `/api/health`) — the
      reconciler uses this for future health monitoring
- [ ] Try creating a service with an empty name — should be rejected (validation)
- [ ] Try creating a service with port 0 or 99999 — should be rejected
- [ ] Try accessing `/api/services` without being logged in (clear cookies
      first) — should return 401

---

## 15. Container Restart Resilience

- [ ] Restart the tailBale container itself: `docker restart tailbale`
- [ ] Wait for it to come back up (health check passes)
- [ ] Dashboard should load with all previous services still present (SQLite is
      persisted in `/data`)
- [ ] All services should still be healthy (edge containers have
      `restart: unless-stopped`)
- [ ] The reconcile loop should resume automatically

---

## Summary

| Section | What it tests |
|---------|--------------|
| 1 | Setup wizard, initial configuration |
| 2 | Dashboard empty state |
| 3 | Container discovery |
| 4 | Service creation (full happy path) |
| 5 | Service detail, editing, actions |
| 6 | Disable/enable lifecycle |
| 7 | Service deletion with DNS cleanup |
| 8 | Hostname uniqueness |
| 9 | Failure detection and auto-recovery |
| 10 | Event log and filtering |
| 11 | Settings management |
| 12 | Services list and quick actions |
| 13 | Authentication and sessions |
| 14 | Edge cases and validation |
| 15 | Persistence across restarts |
