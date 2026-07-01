"""Regression tests for deploy script syntax."""

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_bash_deploy_scripts_parse():
    for script in ("deploy.sh", "redeploy.sh"):
        result = subprocess.run(
            ["bash", "-n", str(ROOT / script)],
            check=False,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr

def test_redeploy_forwards_documented_runtime_env():
    script = (ROOT / "redeploy.sh").read_text(encoding="utf-8")

    for name in (
        "HOST",
        "PORT",
        "JWT_EXPIRY_HOURS",
        "COOKIE_SECURE",
        "CORS_ORIGINS",
        "HOST_DATA_DIR",
        "DOCKER_SOCKET",
    ):
        assert f"-e {name}" in script

def test_redeploy_preserves_empty_cors_origins():
    script = (ROOT / "redeploy.sh").read_text(encoding="utf-8")

    assert 'CORS_ORIGINS="${CORS_ORIGINS-}"' in script
    assert 'CORS_ORIGINS="${CORS_ORIGINS:-*}"' not in script

def test_prod_compose_preserves_empty_cors_origins():
    compose = (ROOT / "docker-compose.prod.yml").read_text(encoding="utf-8")

    assert "CORS_ORIGINS=${CORS_ORIGINS-}" in compose
    assert "CORS_ORIGINS=${CORS_ORIGINS:-*}" not in compose

def test_frontend_container_node_version_matches_toolchain():
    node_major = (ROOT / ".node-version").read_text(encoding="utf-8").strip()
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    dev_compose = (ROOT / "docker-compose.dev.yml").read_text(encoding="utf-8")

    assert f"FROM node:{node_major}-alpine AS frontend-build" in dockerfile
    assert f"image: node:{node_major}-alpine" in dev_compose

def test_redeploy_mounts_configured_unix_docker_socket():
    script = (ROOT / "redeploy.sh").read_text(encoding="utf-8")

    assert 'DOCKER_SOCKET="${DOCKER_SOCKET:-unix:///var/run/docker.sock}"' in script
    assert 'DOCKER_SOCKET_PATH="${DOCKER_SOCKET#unix://}"' in script
    assert 'DOCKER_SOCKET_MOUNT_ARGS=(-v "${DOCKER_SOCKET_PATH}:${DOCKER_SOCKET_PATH}")' in script
    assert '-e DOCKER_SOCKET="${DOCKER_SOCKET}"' in script


def test_compose_requires_explicit_host_data_dir():
    for compose_file in ("docker-compose.prod.yml", "docker-compose.dev.yml"):
        compose = (ROOT / compose_file).read_text(encoding="utf-8")
        assert "${PWD}" not in compose
        assert "HOST_DATA_DIR:?" in compose


def test_edge_entrypoint_parses_as_posix_shell():
    result = subprocess.run(
        ["sh", "-n", str(ROOT / "edge" / "entrypoint.sh")],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_edge_entrypoint_cleanup_preserves_failure_exit_code():
    script = (ROOT / "edge" / "entrypoint.sh").read_text(encoding="utf-8")
    start = script.index("cleanup() {")
    end = script.index("\n}\n", start) + 3
    cleanup_function = script[start:end]

    result = subprocess.run(
        [
            "sh",
            "-c",
            f'TAILSCALED_PID=""\nCADDY_PID=""\n{cleanup_function}\ncleanup 1\n',
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "[edge] Shutting down..." in result.stdout


def test_edge_dockerfile_pins_caddy_and_tailscale():
    dockerfile = (ROOT / "edge" / "Dockerfile").read_text(encoding="utf-8")

    # Base image and Caddy must be pinned (reproducible builds), never floating.
    assert "tailscale/tailscale:latest" not in dockerfile
    assert "FROM tailscale/tailscale:v" in dockerfile
    assert "ARG CADDY_VERSION=" in dockerfile

    # Caddy comes from a pinned GitHub release tarball using GitHub's asset
    # arm-variant naming (armv6/armv7), not the old caddyserver.com download API.
    assert "github.com/caddyserver/caddy/releases/download" in dockerfile
    assert "caddyserver.com/api/download" not in dockerfile
    assert 'caddy_arch="armv7"' in dockerfile
    assert 'caddy_arch="armv6"' in dockerfile

def test_env_example_documents_login_rate_limit_settings():
    """.env.example must document the login brute-force settings that
    config.py exposes as overridable Settings fields (LOGIN_MAX_FAILURES /
    LOGIN_LOCKOUT_SECONDS), with example values matching config.py defaults."""
    env_example = (ROOT / "backend" / ".env.example").read_text(encoding="utf-8")
    config = (ROOT / "backend" / "app" / "config.py").read_text(encoding="utf-8")

    for env_name, field in (
        ("LOGIN_MAX_FAILURES", "login_max_failures"),
        ("LOGIN_LOCKOUT_SECONDS", "login_lockout_seconds"),
    ):
        m = re.search(rf"^\s*{field}:\s*int\s*=\s*(\d+)", config, re.MULTILINE)
        assert m, f"{field} default not found in config.py"
        assert f"{env_name}={m.group(1)}" in env_example, (
            f"{env_name} missing or out of sync with config.py default {m.group(1)}"
        )


def test_env_example_docker_socket_marked_not_app_read():
    """DOCKER_SOCKET is a legacy/deploy-only var: the orchestrator ignores it
    (config.py has no such field; the effective socket lives in the DB settings).
    The .env.example entry must say so, not imply the app reads it."""
    env_example = (ROOT / "backend" / ".env.example").read_text(encoding="utf-8")
    assert "DOCKER_SOCKET=unix:///var/run/docker.sock" in env_example
    assert "does NOT read this from the environment" in env_example


def test_manual_testing_cert_expiry_threshold_matches_code():
    """MANUAL_TESTING's Services-list cert color legend must match the single
    source of truth (frontend CERT_SOON_DAYS) and never resurrect the stale
    'green (>7d) / yellow (<7d)' wording (actual: gray default, no green)."""
    cert_status = (ROOT / "frontend" / "src" / "lib" / "certStatus.ts").read_text(encoding="utf-8")
    m = re.search(r"CERT_SOON_DAYS\s*=\s*(\d+)", cert_status)
    assert m, "CERT_SOON_DAYS not found in certStatus.ts"
    soon_days = m.group(1)

    manual = (ROOT / "MANUAL_TESTING.md").read_text(encoding="utf-8")
    assert f"{soon_days} days" in manual, "cert-expiry threshold not documented with the code value"
    assert "green (>7d)" not in manual
    assert "yellow (<7d)" not in manual


def test_manual_testing_has_no_fictional_progress_tracker():
    """The create flow navigates straight to the service detail page; there is no
    named-step progress tracker UI. Guard against re-introducing the fictional
    'Queued -> Validating -> Creating Network -> ...' stepper description."""
    manual = (ROOT / "MANUAL_TESTING.md").read_text(encoding="utf-8")
    assert "Queued → Validating → Creating Network" not in manual
    assert "progress tracker with steps" not in manual
