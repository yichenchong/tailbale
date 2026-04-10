#!/bin/sh
set -e

# Tell the tailscale CLI where to find the tailscaled socket.
# Newer Tailscale versions (≥1.96) removed the --socket flag from most
# subcommands; the TS_SOCKET env var is the supported replacement.
export TS_SOCKET="/var/run/tailscale/tailscaled.sock"

# Graceful shutdown handler
cleanup() {
    echo "[edge] Shutting down..."
    if [ -n "$CADDY_PID" ]; then
        kill "$CADDY_PID" 2>/dev/null || true
        wait "$CADDY_PID" 2>/dev/null || true
    fi
    tailscale down 2>/dev/null || true
    if [ -n "$TAILSCALED_PID" ]; then
        kill "$TAILSCALED_PID" 2>/dev/null || true
        wait "$TAILSCALED_PID" 2>/dev/null || true
    fi
    exit 0
}

trap cleanup TERM INT QUIT

# 1. Start tailscaled in userspace networking mode
echo "[edge] Starting tailscaled in userspace mode..."
tailscaled --tun=userspace-networking --statedir=/var/lib/tailscale --socket="$TS_SOCKET" &
TAILSCALED_PID=$!

# Wait for tailscaled socket to be ready
for i in $(seq 1 30); do
    if [ -S "$TS_SOCKET" ]; then
        break
    fi
    sleep 0.5
done

if [ ! -S "$TS_SOCKET" ]; then
    echo "[edge] ERROR: tailscaled socket not ready after 15s"
    exit 1
fi

# 2. Authenticate with TS_AUTHKEY if state is fresh
if [ -n "$TS_AUTHKEY" ]; then
    echo "[edge] Authenticating with Tailscale (hostname: ${TS_HOSTNAME:-edge})..."
    tailscale up \
        --authkey="$TS_AUTHKEY" \
        --hostname="${TS_HOSTNAME:-edge}" \
        ${TS_EXTRA_ARGS:-}
else
    echo "[edge] No TS_AUTHKEY set, assuming existing state..."
    tailscale up \
        --hostname="${TS_HOSTNAME:-edge}" \
        ${TS_EXTRA_ARGS:-} || true
fi

# 3. Wait for Tailscale to be ready
echo "[edge] Waiting for Tailscale to be ready..."
TS_READY=false
for i in $(seq 1 60); do
    # Match with optional whitespace — tailscale status --json may
    # pretty-print ("BackendState": "Running") or compact the output.
    if tailscale status --json 2>/dev/null | grep -q '"BackendState"[[:space:]]*:[[:space:]]*"Running"'; then
        TS_READY=true
        echo "[edge] Tailscale is ready."
        tailscale ip -4 2>/dev/null || true
        break
    fi
    sleep 1
done

if [ "$TS_READY" = false ]; then
    echo "[edge] WARNING: Tailscale did not reach Running state after 60s, starting Caddy anyway..."
fi

# 4. Start Caddy with the generated Caddyfile
echo "[edge] Starting Caddy..."
caddy run --config /etc/caddy/Caddyfile &
CADDY_PID=$!

echo "[edge] Edge container running. tailscaled=$TAILSCALED_PID caddy=$CADDY_PID"

# 5. Wait for any child to exit
wait -n "$TAILSCALED_PID" "$CADDY_PID" 2>/dev/null || wait
echo "[edge] A process exited unexpectedly, shutting down..."
cleanup
