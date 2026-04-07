#!/bin/sh
set -e

# Graceful shutdown handler
cleanup() {
    echo "[edge] Shutting down..."
    if [ -n "$CADDY_PID" ]; then
        kill "$CADDY_PID" 2>/dev/null || true
        wait "$CADDY_PID" 2>/dev/null || true
    fi
    tailscale down --socket=/var/run/tailscale/tailscaled.sock 2>/dev/null || true
    if [ -n "$TAILSCALED_PID" ]; then
        kill "$TAILSCALED_PID" 2>/dev/null || true
        wait "$TAILSCALED_PID" 2>/dev/null || true
    fi
    exit 0
}

trap cleanup TERM INT QUIT

# 1. Start tailscaled in userspace networking mode
echo "[edge] Starting tailscaled in userspace mode..."
tailscaled --tun=userspace-networking --statedir=/var/lib/tailscale --socket=/var/run/tailscale/tailscaled.sock &
TAILSCALED_PID=$!

# Wait for tailscaled socket to be ready
for i in $(seq 1 30); do
    if [ -S /var/run/tailscale/tailscaled.sock ]; then
        break
    fi
    sleep 0.5
done

if [ ! -S /var/run/tailscale/tailscaled.sock ]; then
    echo "[edge] ERROR: tailscaled socket not ready after 15s"
    exit 1
fi

# 2. Authenticate with TS_AUTHKEY if state is fresh
if [ -n "$TS_AUTHKEY" ]; then
    echo "[edge] Authenticating with Tailscale (hostname: ${TS_HOSTNAME:-edge})..."
    tailscale up \
        --authkey="$TS_AUTHKEY" \
        --hostname="${TS_HOSTNAME:-edge}" \
        --socket=/var/run/tailscale/tailscaled.sock \
        ${TS_EXTRA_ARGS:-}
else
    echo "[edge] No TS_AUTHKEY set, assuming existing state..."
    tailscale up \
        --hostname="${TS_HOSTNAME:-edge}" \
        --socket=/var/run/tailscale/tailscaled.sock \
        ${TS_EXTRA_ARGS:-} || true
fi

# 3. Wait for Tailscale to be ready
echo "[edge] Waiting for Tailscale to be ready..."
for i in $(seq 1 60); do
    if tailscale status --socket=/var/run/tailscale/tailscaled.sock --json 2>/dev/null | grep -q '"BackendState":"Running"'; then
        echo "[edge] Tailscale is ready."
        tailscale ip -4 --socket=/var/run/tailscale/tailscaled.sock 2>/dev/null || true
        break
    fi
    sleep 1
done

# 4. Start Caddy with the generated Caddyfile
echo "[edge] Starting Caddy..."
caddy run --config /etc/caddy/Caddyfile &
CADDY_PID=$!

echo "[edge] Edge container running. tailscaled=$TAILSCALED_PID caddy=$CADDY_PID"

# 5. Wait for any child to exit
wait -n "$TAILSCALED_PID" "$CADDY_PID" 2>/dev/null || wait
echo "[edge] A process exited unexpectedly, shutting down..."
cleanup
