"""Regression tests for deploy script syntax."""

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


def test_edge_dockerfile_uses_caddy_arm_variant_query():
    dockerfile = (ROOT / "edge" / "Dockerfile").read_text(encoding="utf-8")

    assert "arch=armv7" not in dockerfile
    assert "arch=arm&arm=7" in dockerfile
    assert "arch=arm&arm=6" in dockerfile
