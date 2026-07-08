"""Tests for the Dashboard summary API endpoint."""

from datetime import UTC, datetime, timedelta

from app.models.certificate import Certificate
from app.models.event import Event
from app.models.service import Service
from app.models.service_status import ServiceStatus


def _create_service(db, name="TestApp", phase="pending"):
    slug = name.lower().replace(" ", "")
    svc = Service(
        name=name, upstream_container_id="abc123",
        upstream_container_name=slug, upstream_scheme="http",
        upstream_port=80, hostname=f"{slug}.example.com",
        base_domain="example.com", edge_container_name=f"edge_{slug}",
        network_name=f"edge_net_{slug}", ts_hostname=f"edge-{slug}",
    )
    db.add(svc)
    db.flush()
    db.add(ServiceStatus(service_id=svc.id, phase=phase))
    db.commit()
    return svc


class TestDashboardSummary:
    def test_empty_dashboard(self, client):
        resp = client.get("/api/dashboard/summary")
        assert resp.status_code == 200
        data = resp.json()
        assert data["services"]["total"] == 0
        assert data["services"]["healthy"] == 0
        assert len(data["expiring_certs"]) == 0
        assert len(data["recent_errors"]) == 0
        assert len(data["recent_events"]) == 0

    def test_service_counts(self, client, db_session):
        _create_service(db_session, "App1", phase="healthy")
        _create_service(db_session, "App2", phase="healthy")
        _create_service(db_session, "App3", phase="warning")
        _create_service(db_session, "App4", phase="failed")

        resp = client.get("/api/dashboard/summary")
        data = resp.json()
        assert data["services"]["total"] == 4
        assert data["services"]["healthy"] == 2
        assert data["services"]["warning"] == 1
        assert data["services"]["error"] == 1

    def test_service_without_status_counted_in_total_only(self, client, db_session):
        # A never-reconciled service (no ServiceStatus row) must still count
        # toward `total` but contribute to none of the phase tallies. The
        # phase_counts GROUP BY inner-joins ServiceStatus to Service, so a
        # statusless service produces no aggregate row (lands in no bucket),
        # while `total = db.query(Service).count()` still counts it. Built by
        # hand because _create_service always adds a status row.
        svc = Service(
            name="NoStatus", upstream_container_id="abc123",
            upstream_container_name="nostatus", upstream_scheme="http",
            upstream_port=80, hostname="nostatus.example.com",
            base_domain="example.com", edge_container_name="edge_nostatus",
            network_name="edge_net_nostatus", ts_hostname="edge-nostatus",
        )
        db_session.add(svc)
        db_session.commit()
        assert db_session.get(ServiceStatus, svc.id) is None

        resp = client.get("/api/dashboard/summary")
        data = resp.json()
        assert data["services"]["total"] == 1
        assert data["services"]["healthy"] == 0
        assert data["services"]["warning"] == 0
        assert data["services"]["error"] == 0

    def test_phase_aggregate_folds_error_and_failed_and_excludes_other_phases(
        self, client, db_session
    ):
        # AR13 (ST-R2): the GROUP BY aggregate must bucket exactly like the old
        # per-service Python loop. 'healthy'/'warning' map 1:1; BOTH the current
        # 'error' phase and the legacy 'failed' phase fold into `error`; any
        # other phase (e.g. an in-progress 'provisioning') counts toward `total`
        # only, never a status bucket. A regression dropping the error/failed
        # fold, or mis-bucketing a non-status phase, passes every other
        # dashboard test — this pins it.
        _create_service(db_session, "H", phase="healthy")
        _create_service(db_session, "W", phase="warning")
        _create_service(db_session, "E", phase="error")       # literal error
        _create_service(db_session, "F", phase="failed")      # legacy -> error
        _create_service(db_session, "P", phase="provisioning")  # non-bucketed

        data = client.get("/api/dashboard/summary").json()
        assert data["services"]["total"] == 5
        assert data["services"]["healthy"] == 1
        assert data["services"]["warning"] == 1
        assert data["services"]["error"] == 2  # error + failed folded together

    def test_huge_cert_window_does_not_500_the_dashboard(self, client, db_session):
        # ST-R2-3 regression (cross-flagged by HE-R2): cert_renewal_window_days
        # has no write-time upper bound (schema ge=1 only, by design — same as
        # event_retention_days). An absurdly large stored value pushes
        # `datetime.now(UTC) + timedelta(days=window)` past the max
        # representable date -> OverflowError -> the whole /dashboard/summary
        # 500s. The consumer must clamp (matching events/retention_task.py), so
        # an unbounded horizon flags every cert instead of crashing.
        from app.settings_store import set_setting

        svc = _create_service(db_session, "Overflow")
        db_session.add(Certificate(
            service_id=svc.id,
            hostname=svc.hostname,
            expires_at=datetime.now(UTC) + timedelta(days=90),
        ))
        set_setting(db_session, "cert_renewal_window_days", str(10**9))
        db_session.commit()

        resp = client.get("/api/dashboard/summary")
        assert resp.status_code == 200
        data = resp.json()
        # Unbounded horizon -> the cert is within range and flagged.
        assert len(data["expiring_certs"]) == 1
        assert data["expiring_certs"][0]["service_name"] == "Overflow"

    def test_expiring_certs(self, client, db_session):
        svc = _create_service(db_session, "Expiring")
        cert = Certificate(
            service_id=svc.id,
            hostname=svc.hostname,
            expires_at=datetime.now(UTC) + timedelta(days=10),
        )
        db_session.add(cert)
        db_session.commit()

        resp = client.get("/api/dashboard/summary")
        data = resp.json()
        assert len(data["expiring_certs"]) == 1
        assert data["expiring_certs"][0]["service_name"] == "Expiring"

    def test_non_expiring_cert_excluded(self, client, db_session):
        svc = _create_service(db_session, "Healthy")
        cert = Certificate(
            service_id=svc.id,
            hostname=svc.hostname,
            expires_at=datetime.now(UTC) + timedelta(days=60),
        )
        db_session.add(cert)
        db_session.commit()

        resp = client.get("/api/dashboard/summary")
        data = resp.json()
        assert len(data["expiring_certs"]) == 0

    def test_attention_window_follows_configured_setting(self, client, db_session):
        # AR3: the dashboard attention threshold must track the operator's
        # cert_renewal_window_days, not a hard-coded 30. With the window widened
        # to 45, a cert 40 days out (excluded under the old 30-day literal) is
        # now flagged for attention.
        from app.settings_store import set_setting

        svc = _create_service(db_session, "Wide")
        db_session.add(Certificate(
            service_id=svc.id,
            hostname=svc.hostname,
            expires_at=datetime.now(UTC) + timedelta(days=40),
        ))
        set_setting(db_session, "cert_renewal_window_days", "45")
        db_session.commit()

        resp = client.get("/api/dashboard/summary")
        data = resp.json()
        assert len(data["expiring_certs"]) == 1
        assert data["expiring_certs"][0]["service_name"] == "Wide"

    def test_attention_window_narrowed_excludes_cert(self, client, db_session):
        # AR3, other direction: narrowing the window to 7 days drops a cert
        # 20 days out that the old 30-day literal would have flagged.
        from app.settings_store import set_setting

        svc = _create_service(db_session, "Narrow")
        db_session.add(Certificate(
            service_id=svc.id,
            hostname=svc.hostname,
            expires_at=datetime.now(UTC) + timedelta(days=20),
        ))
        set_setting(db_session, "cert_renewal_window_days", "7")
        db_session.commit()

        resp = client.get("/api/dashboard/summary")
        data = resp.json()
        assert len(data["expiring_certs"]) == 0

    def test_expiring_certs_ordered_most_urgent_first(self, client, db_session):
        # The dashboard surfaces certs needing attention; the most urgent
        # (soonest-to-expire, including already-expired) MUST head the list.
        # Insert out of urgency order to prove the query orders, not insert order.
        now = datetime.now(UTC)
        specs = [
            ("Later", now + timedelta(days=25)),
            ("Expired", now - timedelta(days=3)),
            ("Soon", now + timedelta(days=5)),
        ]
        for name, expires in specs:
            svc = _create_service(db_session, name)
            db_session.add(Certificate(
                service_id=svc.id, hostname=svc.hostname, expires_at=expires,
            ))
        db_session.commit()

        resp = client.get("/api/dashboard/summary")
        names = [c["service_name"] for c in resp.json()["expiring_certs"]]
        assert names == ["Expired", "Soon", "Later"]

    def test_recent_errors(self, client, db_session):
        db_session.add(Event(kind="reconcile_failed", level="error", message="Failed!"))
        db_session.add(Event(kind="service_created", level="info", message="Created"))
        db_session.commit()

        resp = client.get("/api/dashboard/summary")
        data = resp.json()
        assert len(data["recent_errors"]) == 1
        assert data["recent_errors"][0]["message"] == "Failed!"

    def test_recent_events(self, client, db_session):
        for i in range(25):
            db_session.add(Event(kind="test", level="info", message=f"Event {i}"))
        db_session.commit()

        resp = client.get("/api/dashboard/summary")
        data = resp.json()
        assert len(data["recent_events"]) == 20  # limited to 20
