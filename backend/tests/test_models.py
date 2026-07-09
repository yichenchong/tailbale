"""Tests for SQLAlchemy models — verify table creation and basic CRUD."""

from datetime import UTC, datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy import event as sa_event
from sqlalchemy.exc import IntegrityError

from app.database import _set_sqlite_pragma, engine, run_migrations
from app.models import (
    Certificate,
    DnsRecord,
    Event,
    Job,
    Service,
    ServiceStatus,
    Setting,
    User,
)
from app.models.service import generate_id
from app.models.types import NaiveUTCDateTime
from tests._services_helpers import create_service_db


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

        db_session.add(Setting(key="k1", value="v2"))
        with pytest.raises(IntegrityError):
            db_session.commit()


class TestUserModel:
    def test_defaults(self, db_session):
        # A fresh User must pick up the ORM column defaults. token_version in
        # particular is auth-critical: auth compares the JWT `ver` claim against
        # it, so a non-zero / NULL default would silently invalidate every token.
        u = User(username="admin", password_hash="hashed")
        db_session.add(u)
        db_session.commit()
        uid = u.id
        db_session.expire_all()  # force a fresh DB load, not the identity map

        row = db_session.get(User, uid)
        assert row.id.startswith("usr_")
        assert row.token_version == 0
        assert row.is_active is True
        assert row.role == "admin"
        assert row.display_name is None
        # Timestamp columns are stored tz-naive (NaiveUTCDateTime contract).
        assert row.created_at.tzinfo is None
        assert row.updated_at.tzinfo is None

    def test_token_version_explicit_value_persists(self, db_session):
        db_session.add(User(username="u2", password_hash="h", token_version=7))
        db_session.commit()
        db_session.expire_all()

        row = db_session.query(User).filter_by(username="u2").one()
        assert row.token_version == 7

    def test_unique_username(self, db_session):
        db_session.add(User(username="dup", password_hash="h1"))
        db_session.commit()

        db_session.add(User(username="dup", password_hash="h2"))
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

        generated = generate_id()
        assert generated.startswith("svc_")
        assert len(generated) == 16  # "svc_" + 12 hex chars

        # Verify uniqueness
        another = generate_id()
        assert generated != another

    def test_unique_hostname(self, db_session):

        db_session.add(self._make_service(id="svc_1"))
        db_session.commit()

        db_session.add(self._make_service(id="svc_2", hostname="test.example.com"))
        with pytest.raises(IntegrityError):
            db_session.commit()

    def test_unique_edge_container_name(self, db_session):

        db_session.add(self._make_service(id="svc_1", hostname="a.example.com"))
        db_session.commit()

        db_session.add(self._make_service(
            id="svc_2", hostname="b.example.com",
            edge_container_name="edge_testapp",  # same as svc_1
        ))
        with pytest.raises(IntegrityError):
            db_session.commit()

    def test_unique_network_name(self, db_session):

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
            message="DNS updated", details={"old": "1.2.3.4", "new": "5.6.7.8"},
        )
        db_session.add(evt)
        db_session.commit()
        db_session.expire_all()

        row = db_session.get(Event, "evt_test2")
        assert row.service_id == "svc_evt"
        assert row.details == {"old": "1.2.3.4", "new": "5.6.7.8"}


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


# ---------------------------------------------------------------------------
# SQLite foreign key CASCADE enforcement
# ---------------------------------------------------------------------------


class TestSqliteForeignKeyCascade:
    """Verify that PRAGMA foreign_keys=ON is active, making CASCADE deletes work."""


    def test_delete_service_cascades_status(self, db_session):
        svc = create_service_db(db_session)
        svc_id = svc.id
        assert db_session.get(ServiceStatus, svc_id) is not None
        db_session.delete(svc)
        db_session.commit()
        assert db_session.get(ServiceStatus, svc_id) is None

    def test_delete_service_cascades_certificate(self, db_session):
        svc = create_service_db(db_session)
        svc_id = svc.id
        cert = Certificate(service_id=svc_id, hostname=svc.hostname)
        db_session.add(cert)
        db_session.commit()
        assert db_session.get(Certificate, svc_id) is not None
        db_session.delete(svc)
        db_session.commit()
        assert db_session.get(Certificate, svc_id) is None

    def test_delete_service_cascades_dns_record(self, db_session):
        svc = create_service_db(db_session)
        svc_id = svc.id
        dns = DnsRecord(service_id=svc_id, hostname=svc.hostname, record_id="cf_rec_1")
        db_session.add(dns)
        db_session.commit()
        assert db_session.get(DnsRecord, svc_id) is not None
        db_session.delete(svc)
        db_session.commit()
        assert db_session.get(DnsRecord, svc_id) is None

    def test_cascade_deletes_all_related_rows_at_once(self, db_session):
        svc = create_service_db(db_session)
        svc_id = svc.id
        db_session.add(Certificate(service_id=svc_id, hostname=svc.hostname))
        db_session.add(DnsRecord(service_id=svc_id, hostname=svc.hostname))
        db_session.commit()
        db_session.delete(svc)
        db_session.commit()
        assert db_session.get(ServiceStatus, svc_id) is None
        assert db_session.get(Certificate, svc_id) is None
        assert db_session.get(DnsRecord, svc_id) is None

    def test_pragma_is_active_on_test_engine(self, db_engine):
        with db_engine.connect() as conn:
            result = conn.exec_driver_sql("PRAGMA foreign_keys")
            row = result.fetchone()
            assert row is not None
            assert row[0] == 1, "PRAGMA foreign_keys should be ON (1)"

    def test_production_engine_has_pragma_listener(self):


        assert sa_event.contains(engine, "connect", _set_sqlite_pragma)

# ---------------------------------------------------------------------------
# Service delete: history rows (Event/Job) are preserved, not orphaned
# ---------------------------------------------------------------------------


class TestServiceDeleteSetNull:
    """Deleting a Service must SET NULL (not dangle / not delete) Event & Job rows.

    Event/Job carry ``ondelete="SET NULL"`` so the audit history outlives the
    service. A dangling FK (PRAGMA off) or an accidental CASCADE would both be
    bugs: the former leaves a row pointing at a non-existent service, the latter
    silently destroys history.
    """

    def _make_service(self, db, svc_id="svc_hist"):
        svc = Service(
            id=svc_id, name="Hist", upstream_container_id="c1",
            upstream_container_name="hist", upstream_port=80,
            hostname=f"{svc_id}.example.com", base_domain="example.com",
            edge_container_name=f"edge_{svc_id}", network_name=f"net_{svc_id}",
            ts_hostname=f"edge-{svc_id}",
        )
        db.add(svc)
        db.commit()
        return svc

    def test_delete_service_nulls_event_service_id(self, db_session):
        svc = self._make_service(db_session, "svc_evt_del")
        db_session.add(Event(
            id="evt_keep", service_id=svc.id, kind="service_created",
            message="created",
        ))
        db_session.commit()

        db_session.delete(svc)
        db_session.commit()
        db_session.expire_all()

        evt = db_session.get(Event, "evt_keep")
        assert evt is not None, "Event history must survive service deletion"
        assert evt.service_id is None, "service_id must be SET NULL, not dangling"

    def test_delete_service_nulls_job_service_id(self, db_session):
        svc = self._make_service(db_session, "svc_job_del")
        db_session.add(Job(id="job_keep", service_id=svc.id, kind="create_edge"))
        db_session.commit()

        db_session.delete(svc)
        db_session.commit()
        db_session.expire_all()

        job = db_session.get(Job, "job_keep")
        assert job is not None, "Job history must survive service deletion"
        assert job.service_id is None, "service_id must be SET NULL, not dangling"


# ---------------------------------------------------------------------------
# NaiveUTCDateTime: tz-aware writes are normalized to naive UTC
# ---------------------------------------------------------------------------


class TestNaiveUTCDateTime:
    """A tz-aware datetime written to a model timestamp column must read back as
    the equivalent naive UTC value, so later comparisons against the naive
    stored values never raise."""

    def test_aware_datetime_persists_as_naive_utc(self, db_session):

        svc = Service(
            id="svc_tz", name="Tz", upstream_container_id="c1",
            upstream_container_name="tz", upstream_port=443,
            hostname="tz.example.com", base_domain="example.com",
            edge_container_name="edge_tz", network_name="net_tz",
            ts_hostname="edge-tz",
        )
        db_session.add(svc)
        db_session.commit()

        # 17:30 at UTC+5 == 12:30 UTC.
        aware = datetime(2030, 1, 1, 17, 30, tzinfo=timezone(timedelta(hours=5)))
        db_session.add(
            Certificate(service_id="svc_tz", hostname="tz.example.com", expires_at=aware)
        )
        db_session.commit()
        db_session.expire_all()  # force a fresh load from the DB, not the identity map

        row = db_session.get(Certificate, "svc_tz")
        assert row.expires_at.tzinfo is None
        assert row.expires_at == aware.astimezone(UTC).replace(tzinfo=None)
        assert row.expires_at == datetime(2030, 1, 1, 12, 30)

    def test_naive_and_none_pass_through_unchanged(self):
        # The decorator's other half of the contract (docstring: "leaving naive
        # datetimes and None untouched"). Guards against a "simplification" to an
        # unconditional value.astimezone(UTC).replace(tzinfo=None): a naive value
        # would then be silently shifted by the host's local offset (astimezone
        # treats naive input as LOCAL time), and None would raise AttributeError.
        col = NaiveUTCDateTime()
        naive = datetime(2030, 1, 1, 12, 30)
        assert col.process_bind_param(naive, None) == naive
        assert col.process_bind_param(naive, None).tzinfo is None
        assert col.process_bind_param(None, None) is None


# ---------------------------------------------------------------------------
# JSONEncodedDict: dicts round-trip; a corrupt legacy row decodes to None
# ---------------------------------------------------------------------------


class TestJSONEncodedDict:
    """The JSON payload columns transparently encode/decode Python values and
    never raise on a corrupt row already in the database — a bad value reads
    back as ``None`` instead of blowing up a listing."""

    def test_dict_round_trips(self, db_session):
        db_session.add(
            Event(id="evt_json1", kind="dns_created", message="m",
                  details={"hostname": "a.example.com", "count": 3})
        )
        db_session.commit()
        db_session.expire_all()

        assert db_session.get(Event, "evt_json1").details == {
            "hostname": "a.example.com",
            "count": 3,
        }

    def test_none_round_trips(self, db_session):
        db_session.add(Event(id="evt_json2", kind="dns_created", message="m", details=None))
        db_session.commit()
        db_session.expire_all()

        assert db_session.get(Event, "evt_json2").details is None

    def test_empty_dict_round_trips(self, db_session):
        db_session.add(Event(id="evt_json3", kind="dns_created", message="m", details={}))
        db_session.commit()
        db_session.expire_all()

        assert db_session.get(Event, "evt_json3").details == {}

    def test_corrupt_json_in_db_decodes_to_none(self, db_session):

        db_session.add(
            Event(id="evt_json4", kind="dns_created", message="m", details={"ok": True})
        )
        db_session.commit()

        # Simulate a legacy/corrupt row by writing raw non-JSON text straight to
        # the column, bypassing the bind-param encoder.
        db_session.execute(
            text("UPDATE events SET details = :d WHERE id = :id"),
            {"d": "{not valid json", "id": "evt_json4"},
        )
        db_session.commit()
        db_session.expire_all()

        assert db_session.get(Event, "evt_json4").details is None

# ---------------------------------------------------------------------------
# AR13: hot dashboard query columns must be indexed on the models
# ---------------------------------------------------------------------------


class TestDashboardHotPathIndexes:
    """The dashboard scans certificate.expires_at, orders events by created_at,
    and counts service_status.phase; those columns must declare ``index=True``
    so a fresh DB gets the index and run_migrations back-fills legacy DBs."""

    def _indexed_columns(self, model):
        return {
            col.name
            for index in model.__table__.indexes
            for col in index.columns
        }

    def test_certificate_expires_at_indexed(self):
        assert "expires_at" in self._indexed_columns(Certificate)

    def test_service_status_phase_indexed(self):
        assert "phase" in self._indexed_columns(ServiceStatus)

    def test_event_created_at_indexed(self):
        assert "created_at" in self._indexed_columns(Event)


# ---------------------------------------------------------------------------
# AR13: run_migrations must BACK-FILL the hot-path indexes onto a legacy DB
# (create_all only adds them to fresh DBs). Guards the model<->migration
# pairing: dropping the _INDEX_MIGRATIONS entry for a model-declared index
# would leave every existing production DB silently unindexed, and the
# declaration-only tests above would not catch it.
# ---------------------------------------------------------------------------


class TestDashboardHotPathIndexBackfill:
    """A DB created before AR13 has the tables but not the expires_at / phase
    indexes. run_migrations must add them (idempotently) so upgraded installs
    get the same indexes a fresh create_all produces."""

    @staticmethod
    def _legacy_engine():
        # Build the two tables WITHOUT the AR13 indexes, mirroring a pre-AR13 DB.

        eng = create_engine("sqlite:///:memory:")
        with eng.begin() as conn:
            conn.execute(text(
                "CREATE TABLE certificates (service_id TEXT PRIMARY KEY, hostname TEXT, "
                "expires_at DATETIME, last_renewed_at DATETIME, last_failure TEXT, "
                "next_retry_at DATETIME, updated_at DATETIME)"
            ))
            conn.execute(text(
                "CREATE TABLE service_status (service_id TEXT PRIMARY KEY, phase TEXT, "
                "message TEXT, tailscale_ip TEXT, edge_container_id TEXT, health_checks TEXT, "
                "last_reconciled_at DATETIME, probe_retry_at DATETIME, "
                "probe_retry_attempt INTEGER, last_probe_at DATETIME, updated_at DATETIME)"
            ))
        return eng

    @staticmethod
    def _indexed_columns(eng, table):

        return {
            col
            for index in inspect(eng).get_indexes(table)
            for col in index["column_names"]
        }

    def test_backfills_certificate_expires_at_index(self):

        eng = self._legacy_engine()
        assert "expires_at" not in self._indexed_columns(eng, "certificates")
        run_migrations(eng)
        assert "expires_at" in self._indexed_columns(eng, "certificates")

    def test_backfills_service_status_phase_index(self):

        eng = self._legacy_engine()
        assert "phase" not in self._indexed_columns(eng, "service_status")
        run_migrations(eng)
        assert "phase" in self._indexed_columns(eng, "service_status")

    def test_backfill_is_idempotent(self):

        eng = self._legacy_engine()
        run_migrations(eng)
        # A second pass must not raise (CREATE INDEX IF NOT EXISTS) and must leave
        # the indexes intact.
        run_migrations(eng)
        assert "expires_at" in self._indexed_columns(eng, "certificates")
        assert "phase" in self._indexed_columns(eng, "service_status")


# ---------------------------------------------------------------------------
# AS3: run_migrations must BACK-FILL the token_version column onto a legacy DB
# (create_all only adds it to fresh DBs). Without it, upgraded installs cannot
# invalidate outstanding JWTs on password change.
# ---------------------------------------------------------------------------


class TestTokenVersionMigration:
    """A users table created before token_version existed must gain the column
    (idempotently) via run_migrations."""

    @staticmethod
    def _legacy_engine():
        # Build the users table WITHOUT token_version, mirroring a pre-AS3 DB.
        eng = create_engine("sqlite:///:memory:")
        with eng.begin() as conn:
            conn.execute(text(
                "CREATE TABLE users (id TEXT PRIMARY KEY, username TEXT, "
                "password_hash TEXT, display_name TEXT, role TEXT, is_active BOOLEAN, "
                "created_at DATETIME, updated_at DATETIME)"
            ))
        return eng

    @staticmethod
    def _columns(eng, table):
        return {c["name"] for c in inspect(eng).get_columns(table)}

    def test_backfills_token_version_column(self):
        eng = self._legacy_engine()
        assert "token_version" not in self._columns(eng, "users")
        run_migrations(eng)
        assert "token_version" in self._columns(eng, "users")

    def test_backfill_is_idempotent(self):
        eng = self._legacy_engine()
        run_migrations(eng)
        # A second pass must not raise (has-column guard) and leave it intact.
        run_migrations(eng)
        assert "token_version" in self._columns(eng, "users")


# ---------------------------------------------------------------------------
# MS1: run_migrations must BACK-FILL the probe-retry columns onto a legacy DB.
# service_status.probe_retry_at / probe_retry_attempt / last_probe_at were added
# after the probe-retry feature shipped; create_all only adds them to fresh DBs.
# These are the only _MIGRATIONS *column* entries with no backfill guard —
# dropping any of them would leave every upgraded install raising "no such
# column" on every ServiceStatus read, and no other test would catch it.
# ---------------------------------------------------------------------------


class TestServiceStatusProbeColumnsMigration:
    """A service_status table created before probe-retry tracking must gain the
    probe_retry_at / probe_retry_attempt / last_probe_at columns (idempotently)
    via run_migrations."""

    _PROBE_COLUMNS = ("probe_retry_at", "probe_retry_attempt", "last_probe_at")

    @staticmethod
    def _legacy_engine():
        # Build the service_status table WITHOUT the probe columns, mirroring a
        # DB created before probe-retry tracking existed.
        eng = create_engine("sqlite:///:memory:")
        with eng.begin() as conn:
            conn.execute(text(
                "CREATE TABLE service_status (service_id TEXT PRIMARY KEY, phase TEXT, "
                "message TEXT, tailscale_ip TEXT, edge_container_id TEXT, "
                "health_checks TEXT, last_reconciled_at DATETIME, updated_at DATETIME)"
            ))
        return eng

    @staticmethod
    def _columns(eng, table):
        return {c["name"] for c in inspect(eng).get_columns(table)}

    def test_backfills_probe_columns(self):
        eng = self._legacy_engine()
        before = self._columns(eng, "service_status")
        for col in self._PROBE_COLUMNS:
            assert col not in before
        run_migrations(eng)
        after = self._columns(eng, "service_status")
        for col in self._PROBE_COLUMNS:
            assert col in after

    def test_backfill_is_idempotent(self):
        eng = self._legacy_engine()
        run_migrations(eng)
        # A second pass must not raise (has-column guard) and leave them intact.
        run_migrations(eng)
        after = self._columns(eng, "service_status")
        for col in self._PROBE_COLUMNS:
            assert col in after
