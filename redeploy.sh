#!/usr/bin/env bash
set -euo pipefail

# Resolve the absolute path to the data directory on the host.
# HOST_DATA_DIR tells the orchestrator where Docker can find bind-mount
# sources (the host-side equivalent of /data inside the container).
SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
HOST_DATA_INPUT="${HOST_DATA_DIR:-${SCRIPT_DIR}/data}"
HOST_PORT="${HOST_PORT:-6780}"
PORT="${PORT:-8080}"
HOST="${HOST:-0.0.0.0}"
JWT_EXPIRY_HOURS="${JWT_EXPIRY_HOURS:-24}"
COOKIE_SECURE="${COOKIE_SECURE:-false}"
CORS_ORIGINS="${CORS_ORIGINS-}"
DOCKER_SOCKET="${DOCKER_SOCKET:-unix:///var/run/docker.sock}"
TAILBALE_VERSION="$(<"${SCRIPT_DIR}/VERSION")"

mkdir -p "${HOST_DATA_INPUT}"
HOST_DATA="$(cd "${HOST_DATA_INPUT}" && pwd -P)"

docker build -t tailbale:latest -f "${SCRIPT_DIR}/Dockerfile" "${SCRIPT_DIR}"
docker build -t tailbale-edge:latest --label "tailbale.version=${TAILBALE_VERSION}" "${SCRIPT_DIR}/edge"
docker rm -f tailbale || true
DOCKER_SOCKET_MOUNT_ARGS=()
if [[ "${DOCKER_SOCKET}" == unix://* ]]; then
  DOCKER_SOCKET_PATH="${DOCKER_SOCKET#unix://}"
  DOCKER_SOCKET_MOUNT_ARGS=(-v "${DOCKER_SOCKET_PATH}:${DOCKER_SOCKET_PATH}")
fi

docker run -d \
  --name tailbale \
  --label tailbale.main=true \
  --restart unless-stopped \
  -p "${HOST_PORT}:${PORT}" \
  -v "${HOST_DATA}":/data \
  "${DOCKER_SOCKET_MOUNT_ARGS[@]}" \
  -e DATA_DIR=/data \
  -e HOST_DATA_DIR="${HOST_DATA}" \
  -e DOCKER_SOCKET="${DOCKER_SOCKET}" \
  -e PORT="${PORT}" \
  -e HOST="${HOST}" \
  -e JWT_EXPIRY_HOURS="${JWT_EXPIRY_HOURS}" \
  -e COOKIE_SECURE="${COOKIE_SECURE}" \
  -e CORS_ORIGINS="${CORS_ORIGINS}" \
  tailbale:latest