"""Integration-seam fixtures.

The shared ``tests/conftest.py`` installs three **autouse** fixtures that make
the whole suite hermetic against real infrastructure:

* ``_mock_upstream_validation`` — patches ``routers.services._validate_upstream``
* ``_mock_background_reconcile`` — patches ``reconcile_loop.reconcile_one``
* ``_no_real_docker``           — makes ``docker.DockerClient`` raise

Because of them, no existing test drives ``router -> service -> reconciler ->
edge/DNS`` as a unit: the reconcile path, edge container ops, and upstream
checks are stubbed globally.

This nested conftest **opts out** of all three WITHOUT touching the shared file:
pytest resolves fixtures from the closest conftest, so redefining the same
fixture names here shadows the parent's for this directory only. Every override
is still ``autouse`` so it applies to the whole package, but each is now either
a no-op (letting the real code run) or a fake-injecting seam. The parent
autouse fixtures are left completely unchanged and continue to protect the rest
of the suite.
"""

from __future__ import annotations

import json
from collections import namedtuple
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from app.version import __version__

# A real ``container.exec_run`` returns an ``ExecResult(exit_code, output)``
# namedtuple that callers both unpack (``code, out = ...``) and attribute-access
# (``result.exit_code`` / ``result.output``); mirror that exactly.
ExecResult = namedtuple("ExecResult", ["exit_code", "output"])


class FakeImage:
    """Stand-in for ``docker.models.images.Image``."""

    def __init__(self, image_id: str, version: str) -> None:
        self.id = image_id
        # ``ensure_edge_image`` returns early when this matches the running
        # orchestrator version, so the real (context-dependent) build never runs.
        self.labels = {"tailbale.version": version}


class FakeExecMixin:
    """Command dispatch shared by every fake container's ``exec_run``."""

    tailscale_ip = "100.64.0.5"
    backend_state = "Running"
    probe_http_code = "200"

    def _dispatch_exec(self, command) -> ExecResult:
        cmd = " ".join(command) if isinstance(command, (list, tuple)) else str(command)

        if "caddy reload" in cmd:
            # Caddy admin API accepts the reload (exit 0, empty body).
            return ExecResult(0, b"")

        if "tailscale ip" in cmd:
            return ExecResult(0, f"{self.tailscale_ip}\n".encode())

        if "tailscale status" in cmd:
            payload = {
                "BackendState": self.backend_state,
                "Self": {"TailscaleIPs": [self.tailscale_ip]},
            }
            return ExecResult(0, json.dumps(payload).encode())

        if "curl" in cmd:
            # HTTPS probe: curl exits 0 and writes the HTTP status code.
            return ExecResult(0, self.probe_http_code.encode())

        return ExecResult(0, b"")


class FakeContainer(FakeExecMixin):
    """Stateful stand-in for ``docker.models.containers.Container``."""

    def __init__(
        self,
        *,
        container_id: str,
        name: str,
        status: str = "running",
        labels: dict | None = None,
        exposed_ports: dict | None = None,
        networks: dict | None = None,
    ) -> None:
        self.id = container_id
        self.name = name
        self.status = status
        self.labels = labels or {}
        self.attrs = {
            "Config": {"ExposedPorts": exposed_ports or {}},
            "HostConfig": {"PortBindings": {}},
            "NetworkSettings": {"Networks": dict(networks or {})},
        }

    # -- lifecycle ---------------------------------------------------------
    def reload(self) -> None:  # attrs are already authoritative in the fake
        return None

    def start(self) -> None:
        self.status = "running"

    def stop(self, timeout: int = 10) -> None:
        self.status = "exited"

    def restart(self, timeout: int = 10) -> None:
        self.status = "running"

    def remove(self, force: bool = False) -> None:
        return None

    def exec_run(self, command, **kwargs) -> ExecResult:
        return self._dispatch_exec(command)


class FakeNetwork:
    """Stateful stand-in for ``docker.models.networks.Network``."""

    def __init__(self, network_id: str, name: str) -> None:
        self.id = network_id
        self.name = name
        self.attrs = {"Containers": {}}

    def reload(self) -> None:
        return None

    def connect(self, container) -> None:
        cid = container.id if hasattr(container, "id") else str(container)
        self.attrs["Containers"][cid] = {}
        # Reflect the attachment on the container the way the daemon would, so
        # ``_check_upstream_network`` sees the service network on the upstream.
        if hasattr(container, "attrs"):
            networks = container.attrs.setdefault("NetworkSettings", {}).setdefault(
                "Networks", {}
            )
            networks[self.name] = {}

    def disconnect(self, container, force: bool = False) -> None:
        cid = container.id if hasattr(container, "id") else str(container)
        self.attrs["Containers"].pop(cid, None)

    def remove(self) -> None:
        return None


class _Containers:
    def __init__(self, client: FakeDockerClient) -> None:
        self._client = client

    def get(self, ref: str):
        c = self._client._containers.get(ref)
        if c is None:
            for candidate in self._client._containers.values():
                if candidate.name == ref:
                    c = candidate
                    break
        if c is None:
            import docker

            raise docker.errors.NotFound(f"container {ref} not found")
        return c

    def list(self, all: bool = False, filters: dict | None = None):
        label = (filters or {}).get("label")
        result = list(self._client._containers.values())
        if label and "=" in label:
            key, _, value = label.partition("=")
            result = [c for c in result if c.labels.get(key) == value]
        return result

    def create(self, **kwargs):
        name = kwargs.get("name")
        labels = kwargs.get("labels") or {}
        network = kwargs.get("network")
        cid = f"fake_{name}_{len(self._client._containers)}"
        # A freshly created (not-yet-started) container reports "created", so the
        # reconciler's start_edge branch runs — exactly like the real daemon.
        networks = {network: {}} if network else {}
        container = FakeContainer(
            container_id=cid,
            name=name,
            status="created",
            labels=labels,
            networks=networks,
        )
        self._client._containers[cid] = container
        self._client.created_containers.append(container)
        return container


class _Networks:
    def __init__(self, client: FakeDockerClient) -> None:
        self._client = client

    def get(self, name: str):
        net = self._client._networks.get(name)
        if net is None:
            import docker

            raise docker.errors.NotFound(f"network {name} not found")
        return net

    def create(self, name: str, driver: str = "bridge"):
        net = FakeNetwork(f"net_{name}", name)
        self._client._networks[name] = net
        self._client.created_networks.append(name)
        return net


class _Images:
    def __init__(self, client: FakeDockerClient) -> None:
        self._client = client

    def get(self, name: str):
        return FakeImage(f"img_{name}", __version__)

    def remove(self, image=None, force: bool = False):
        return None


class FakeDockerClient(FakeExecMixin):
    """A richer fake Docker client graph: containers + networks + images.

    Injected in place of the real ``docker.DockerClient`` so the reconcile
    pipeline (network_manager / container_manager / health_checker) runs its
    real code against an in-memory, deterministic daemon.
    """

    def __init__(self) -> None:
        self._containers: dict[str, FakeContainer] = {}
        self._networks: dict[str, FakeNetwork] = {}
        self.created_containers: list[FakeContainer] = []
        self.created_networks: list[str] = []
        self.containers = _Containers(self)
        self.networks = _Networks(self)
        self.images = _Images(self)

    def register_upstream(
        self, container_id: str, name: str, *, exposed_ports: dict | None = None
    ) -> FakeContainer:
        """Pre-seed the upstream app container the operator is exposing."""
        c = FakeContainer(
            container_id=container_id,
            name=name,
            status="running",
            labels={},
            exposed_ports=exposed_ports,
        )
        self._containers[container_id] = c
        return c

    def edge_container(self) -> FakeContainer | None:
        for c in self._containers.values():
            if c.labels.get("tailbale.managed") == "true":
                return c
        return None

    def close(self) -> None:
        return None


def write_valid_cert(certs_dir: Path, hostname: str) -> str:
    """Write a self-signed fullchain/privkey pair under ``<hostname>/current``.

    Returns the hostname. The cert is valid now and well outside the 30-day
    renewal window, so ``_ensure_cert`` treats it as current and never calls the
    (unavailable) ACME/lego path.
    """
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, hostname)])
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=825))
        .sign(key, hashes.SHA256())
    )
    current = certs_dir / hostname / "current"
    current.mkdir(parents=True, exist_ok=True)
    (current / "fullchain.pem").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    (current / "privkey.pem").write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )
    return hostname


# ---------------------------------------------------------------------------
# Autouse fixture overrides: shadow the shared conftest's autouse mocks so the
# real code runs for this package only. The shared conftest is untouched.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _mock_upstream_validation():
    """Override: run the REAL upstream validation against the fake daemon."""
    yield


@pytest.fixture(autouse=True)
def _mock_background_reconcile():
    """Override: run the REAL reconcile path (no global stub of reconcile_one)."""
    yield


@pytest.fixture(autouse=True)
def _no_real_docker():
    """Override the shared ``_no_real_docker`` refuse-fixture: inject a working
    fake Docker client instead of raising.

    The reconcile pipeline builds its client via ``docker.DockerClient(...)`` /
    ``docker.DockerClient.from_env()`` (through ``edge.docker_client.connect``).
    Both are patched to return one shared, stateful fake so every subsystem in a
    single reconcile talks to the same in-memory daemon. Named exactly like the
    parent fixture so pytest resolves THIS one for the integration package,
    leaving the shared conftest's fixture in force everywhere else.
    """
    client = FakeDockerClient()

    def _return_client(*_args, **_kwargs):
        return client

    with patch("docker.DockerClient", side_effect=_return_client) as mock_cls:
        mock_cls.from_env = _return_client
        yield client


@pytest.fixture()
def fake_docker(_no_real_docker):
    """Expose the injected fake Docker client to tests that assert on its graph."""
    return _no_real_docker
