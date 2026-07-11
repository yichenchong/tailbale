#!/usr/bin/env bash
set -euo pipefail

# First-time deployment entrypoint. redeploy.sh is idempotent and also handles
# upgrades by replacing the existing tailbale container. Invoke it through bash
# so a fresh checkout still works if redeploy.sh is not executable.
SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
exec bash "${SCRIPT_DIR}/redeploy.sh"
