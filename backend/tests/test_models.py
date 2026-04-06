"""Tests for SQLAlchemy models — verify table creation and basic CRUD."""

from app.models import (
    Certificate,
    DnsRecord,
    Event,
    Job,
    Service,
    ServiceStatus,
    Setting,
)


class TestSettingModel:
    def test_create_and_read(self, db_session):
        db_session.add(Setting(key="base_domain", value="example.com"))
        db_session.commit()

        row = db_session.get(Setting, "base_domain")
        assert row is not None
        assert row.value == "example.com"

    def test_update(self, db_session):
        db_session.add(Setting(key="base_domain", value="old.com"))
        db_session.commit()

        row = db_session.get(Setting, "base_domain")
        row.value = "new.com"
        db_session.commit()

        row = db_session.get(Setting, "base_domain")
        assert row.value == "new.com"

    def test_unique_key(self, db_session):
        db_session.add(Setting(key="k1", value="v1"))
        db_session.commit()
        # Adding same key should raise
        from sqlalchemy.exc import IntegrityError
        import pytest

        db_session.add(Setting(key="k1", value="v2"))
        with pytest.raises(IntegrityError):
            db_session.commit()


class TestServiceModel:
    def _make_service(self, **overrides):
        defaults = dict(
            id="svc_test123",
            name="TestApp",
            upstream_container_id="abc123",
            upstream_container_name="testapp",
            upstream_port=8080,
            hostname="test.example.com",
            base_domain="example.com",
            edge_container_name="edge_testapp",
            network_name="edge_net_svc_test123",
            ts_hostname="edge-testapp",
        )
        defaults.update(overrides)
        return Service(**defaults)

    def test_create_service(self, db_session):
        svc = self._make_service()
        db_session.add(svc)
        db_session.commit()

        row = db_session.get(Service, "svc_test123")
        assert row is not None
        assert row.name == "TestApp"
        assert row.upstream_port == 8080
        assert row.hostname == "test.example.com"
        assert row.enabled is True
        assert row.upstream_scheme == "http"
        assert row.preserve_host_header is True

    def test_auto_id_generation(self, db_session):
        from app.models.service import generate_id

        generated = generate_id()
        assert generated.startswith("svc_")
        assert len(generated) == 16  # "svc_" + 12 hex chars

        # Verify uniqueness
        another = generate_id()
        assert generated != another

    def test_unique_hostname(self, db_session):
        from sqlalchemy.exc import IntegrityError
        import pytest

        db_session.add(self._make_service(id="svc_1"))
        db_session.commit()

        db_session.add(self._make_service(id="svc_2", hostname="test.example.com"))
        with pytest.raises(IntegrityError):
            db_session.commit()

    def test_unique_edge_container_name(self, db_session):
        from sqlalchemy.exc import IntegrityError
        import pytest

        db_session.add(self._make_service(id="svc_1", hostname="a.example.com"))
        db_session.commit()

        db_session.add(self._make_service(
            id="svc_2", hostname="b.example.com",
            edge_container_name="edge_testapp",  # same as svc_1
        ))
        with pytest.raises(IntegrityError):
            db_session.commit()

    def test_unique_network_name(self, db_session):
        from sqlalchemy.exc import IntegrityError
        import pytest

        db_session.add(self._make_service(id="svc_1", hostname="a.example.com"))
        db_session.commit()

        db_session.add(self._make_service(
            id="svc_2", hostname="b.example.com",
            edge_container_name="edge_other",
            ts_hostname="edge-other",
            network_name="edge_net_svc_test123",  # same as svc_1
        ))
        with pytest.raises(IntegrityError):
            db_session.commit()

    def test_unique_ts_hostname(self, db_session):
        from sqlalchemy.exc import IntegrityError
        import pytest

        db_session.add(self._make_service(id="svc_1", hostname="a.example.com"))
        db_session.commit()

        db_session.add(self._make_service(
            id="svc_2", hostname="b.example.com",
            edge_container_name="edge_other",
            network_name="edge_net_other",
            ts_hostname="edge-testapp",  # same as svc_1
        ))
        with pytest.raises(IntegrityError):
            db_session.commit()

    def test_same_container_multiple_exposures(self, db_session):
        """Multiple services for the same upstream container should work."""
        db_session.add(self._make_service(
            id="svc_1", hostname="a.example.com",
            edge_container_name="edge_a", network_name="edge_net_a",
            ts_hostname="edge-a",
        ))
        db_session.add(self._make_service(
            id="svc_2", hostname="b.example.com", upstream_port=443,
            edge_container_name="edge_b", network_name="edge_net_b",
            ts_hostname="edge-b",
        ))
        db_session.commit()

        svcs = db_session.query(Service).all()
        assert len(svcs) == 2
        # Both reference same upstream container
        assert svcs[0].upstream_container_id == svcs[1].upstream_container_id

    def test_optional_fields(self, db_session):
        svc = self._make_service(
            healthcheck_path=None,
            custom_caddy_snippet=None,
            app_profile=None,
        )
        db_session.add(svc)
        db_session.commit()

        row = db_session.get(Service, "svc_test123")
        assert row.healthcheck_path is None
        assert row.custom_caddy_snippet is None
        assert row.app_profile is None


class TestServiceStatusModel:
    def test_create_status(self, db_session):
        # Need a service first due to FK
        svc = Service(
            id="svc_test1", name="Test", upstream_container_id="c1",
            upstream_container_name="test", upstream_port=80,
            hostname="test.example.com", base_domain="example.com",
            edge_container_name="edge_test", network_name="net_test",
            ts_hostname="edge-test",
        )
        db_session.add(svc)
        db_session.commit()

        status = ServiceStatus(
            service_id="svc_test1",
            phase="healthy",
            message="All checks passing",
            tailscale_ip="100.64.0.1",
        )
        db_session.add(status)
        db_session.commit()

        row = db_session.get(ServiceStatus, "svc_test1")
        assert row.phase == "healthy"
        assert row.tailscale_ip == "100.64.0.1"

    def test_cascade_delete(self, db_session):
        svc = Service(
            id="svc_del", name="Del", upstream_container_id="c1",
            upstream_container_name="del", upstream_port=80,
            hostname="del.example.com", base_domain="example.com",
            edge_container_name="edge_del", network_name="net_del",
            ts_hostname="edge-del",
        )
        db_session.add(svc)
        db_session.commit()

        db_session.add(ServiceStatus(service_id="svc_del", phase="pending"))
        db_session.commit()

        db_session.delete(svc)
        db_session.commit()

        assert db_session.get(ServiceStatus, "svc_del") is None


class TestEventModel:
    def test_create_event(self, db_session):
        evt = Event(
            id="evt_test1",
            kind="service_created",
            level="info",
            message="Service created",
        )
        db_session.add(evt)
        db_session.commit()

        row = db_session.get(Event, "evt_test1")
        assert row.kind == "service_created"
        assert row.level == "info"
        assert row.service_id is None  # Global event

    def test_event_with_service(self, db_session):
        svc = Service(
            id="svc_evt", name="Evt", upstream_container_id="c1",
            upstream_container_name="evt", upstream_port=80,
            hostname="evt.example.com", base_domain="example.com",
            edge_container_name="edge_evt", network_name="net_evt",
            ts_hostname="edge-evt",
        )
        db_session.add(svc)
        db_session.commit()

        evt = Event(
            id="evt_test2", service_id="svc_evt",
            kind="dns_updated", level="info",
            message="DNS updated", details='{"old": "1.2.3.4", "new": "5.6.7.8"}',
        )
        db_session.add(evt)
        db_session.commit()

        row = db_session.get(Event, "evt_test2")
        assert row.service_id == "svc_evt"
        assert row.details is not None


class TestDnsRecordModel:
    def test_create_dns_record(self, db_session):
        svc = Service(
            id="svc_dns", name="Dns", upstream_container_id="c1",
            upstream_container_name="dns", upstream_port=80,
            hostname="dns.example.com", base_domain="example.com",
            edge_container_name="edge_dns", network_name="net_dns",
            ts_hostname="edge-dns",
        )
        db_session.add(svc)
        db_session.commit()

        rec = DnsRecord(
            service_id="svc_dns",
            record_id="cf_rec_123",
            hostname="dns.example.com",
            value="100.64.0.5",
        )
        db_session.add(rec)
        db_session.commit()

        row = db_session.get(DnsRecord, "svc_dns")
        assert row.value == "100.64.0.5"
        assert row.record_type == "A"


class TestCertificateModel:
    def test_create_certificate(self, db_session):
        svc = Service(
            id="svc_cert", name="Cert", upstream_container_id="c1",
            upstream_container_name="cert", upstream_port=443,
            hostname="cert.example.com", base_domain="example.com",
            edge_container_name="edge_cert", network_name="net_cert",
            ts_hostname="edge-cert",
        )
        db_session.add(svc)
        db_session.commit()

        cert = Certificate(
            service_id="svc_cert",
            hostname="cert.example.com",
        )
        db_session.add(cert)
        db_session.commit()

        row = db_session.get(Certificate, "svc_cert")
        assert row.hostname == "cert.example.com"
        assert row.expires_at is None  # Not yet issued


class TestJobModel:
    def test_create_job(self, db_session):
        job = Job(
            id="job_test1",
            kind="create_edge",
            status="pending",
        )
        db_session.add(job)
        db_session.commit()

        row = db_session.get(Job, "job_test1")
        assert row.status == "pending"
        assert row.progress == 0

    def test_update_job_progress(self, db_session):
        job = Job(id="job_prog", kind="create_edge", status="running", progress=0)
        db_session.add(job)
        db_session.commit()

        job.progress = 50
        job.message = "Creating network..."
        db_session.commit()

        row = db_session.get(Job, "job_prog")
        assert row.progress == 50
        assert row.message == "Creating network..."
