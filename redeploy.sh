#!/usr/bin/env bash
set -euo pipefail

# Resolve the absolute path to the data directory on the host.
# HOST_DATA_DIR tells the orchestrator where Docker can find bind-mount
# sources (the host-side equivalent of /data inside the container).
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
HOST_DATA="${HOST_DATA_DIR:-${SCRIPT_DIR}/data}"

docker build -t tailbale:latest .
docker build -t tailbale-edge:latest ./edge
docker rm -f tailbale
docker run -d \
  --name tailbale \
  --restart unless-stopped \
  -p 6780:8080 \
  -v "${HOST_DATA}":/data \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -e DATA_DIR=/data \
  -e HOST_DATA_DIR="${HOST_DATA}" \
  -e DOCKER_SOCKET=unix:///var/run/docker.sock \
  -e PORT=8080 \
  tailbale:latest