"""Tests for the Jobs API — orphaned DNS cleanup management."""

import json
from unittest.mock import patch

from app.models.dns_record import DnsRecord
from app.models.event import Event
from app.models.job import Job
from app.models.service import Service
from app.models.service_status import ServiceStatus


def _create_service(client, **overrides):
    body = {
        "name": "Nextcloud",
        "upstream_container_id": "abc123def456",
        "upstream_container_name": "nextcloud",
        "upstream_scheme": "http",
        "upstream_port": 80,
        "hostname": "nextcloud.example.com",
        "base_domain": "example.com",
    }
    body.update(overrides)
    return client.post("/api/services", json=body)


def _create_orphan_job(db, **overrides):
    """Insert a dns_orphan_cleanup job directly into the DB."""
    defaults = {
        "kind": "dns_orphan_cleanup",
        "status": "pending",
        "message": "Orphaned DNS record for deleted service 'TestApp'",
        "details": json.dumps({
            "record_id": "cf_abc123",
            "hostname": "test.example.com",
            "zone_id": "zone1",
            "value": "100.64.0.1",
            "service_name": "TestApp",
        }),
    }
    defaults.update(overrides)
    job = Job(**defaults)
    db.add(job)
    db.commit()
    return job


# ---------------------------------------------------------------------------
# List jobs
# ---------------------------------------------------------------------------


class TestListJobs:
    def test_empty_list(self, client):
        resp = client.get("/api/jobs")
        assert resp.status_code == 200
        data = resp.json()
        assert data["jobs"] == []
        assert data["total"] == 0

    def test_lists_orphan_jobs(self, client, db_session):
        job = _create_orphan_job(db_session)
        resp = client.get("/api/jobs")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["jobs"][0]["id"] == job.id
        assert data["jobs"][0]["kind"] == "dns_orphan_cleanup"
        assert data["jobs"][0]["details"]["record_id"] == "cf_abc123"

    def test_filter_by_status(self, client, db_session):
        _create_orphan_job(db_session, status="pending")
        _create_orphan_job(db_session, status="failed")

        resp = client.get("/api/jobs?status=pending")
        assert resp.json()["total"] == 1

        resp = client.get("/api/jobs?status=failed")
        assert resp.json()["total"] == 1

    def test_filter_by_kind(self, client, db_session):
        _create_orphan_job(db_session)
        other = Job(kind="some_other_kind", status="pending")
        db_session.add(other)
        db_session.commit()

        resp = client.get("/api/jobs?kind=dns_orphan_cleanup")
        assert resp.json()["total"] == 1

    def test_pagination(self, client, db_session):
        for _ in range(5):
            _create_orphan_job(db_session)

        resp = client.get("/api/jobs?limit=2&offset=0")
        assert len(resp.json()["jobs"]) == 2
        assert resp.json()["total"] == 5

        resp = client.get("/api/jobs?limit=2&offset=4")
        assert len(resp.json()["jobs"]) == 1


# ---------------------------------------------------------------------------
# Get single job
# ---------------------------------------------------------------------------


class TestGetJob:
    def test_get_existing_job(self, client, db_session):
        job = _create_orphan_job(db_session)
        resp = client.get(f"/api/jobs/{job.id}")
        assert resp.status_code == 200
        assert resp.json()["id"] == job.id

    def test_get_nonexistent_job(self, client):
        resp = client.get("/api/jobs/job_nonexistent")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Retry orphan cleanup
# ---------------------------------------------------------------------------


class TestRetryOrphanCleanup:
    @patch("app.adapters.cloudflare_adapter.delete_a_record")
    @patch("app.adapters.cloudflare_adapter.find_record")
    @patch("app.secrets.read_secret", return_value="cf-token-123")
    def test_retry_deletes_existing_record(
        self, mock_secret, mock_find, mock_delete, client, db_session
    ):
        job = _create_orphan_job(db_session)
        job_id = job.id
        mock_find.return_value = {"id": "cf_abc123", "content": "100.64.0.1"}

        resp = client.post(f"/api/jobs/{job_id}/retry")
        assert resp.status_code == 200
        assert resp.json()["success"] is True

        mock_delete.assert_called_once_with("cf-token-123", "zone1", "cf_abc123")

        # Job should be deleted
        db_session.expire_all()
        assert db_session.get(Job, job_id) is None

        # Should emit dns_orphan_resolved event
        events = db_session.query(Event).filter(Event.kind == "dns_orphan_resolved").all()
        assert len(events) == 1
        assert "successfully deleted" in events[0].message

    @patch("app.adapters.cloudflare_adapter.find_record")
    @patch("app.secrets.read_secret", return_value="cf-token-123")
    def test_retry_succeeds_when_record_already_gone(
        self, mock_secret, mock_find, client, db_session
    ):
        job = _create_orphan_job(db_session)
        job_id = job.id
        mock_find.return_value = None  # record no longer exists

        resp = client.post(f"/api/jobs/{job_id}/retry")
        assert resp.status_code == 200
        assert resp.json()["success"] is True

        # Job should be deleted
        db_session.expire_all()
        assert db_session.get(Job, job_id) is None

        events = db_session.query(Event).filter(Event.kind == "dns_orphan_resolved").all()
        assert len(events) == 1
        assert "already removed" in events[0].message

    @patch("app.adapters.cloudflare_adapter.find_record")
    @patch("app.secrets.read_secret", return_value="cf-token-123")
    def test_retry_succeeds_when_different_record_at_hostname(
        self, mock_secret, mock_find, client, db_session
    ):
        """If find_record returns a different record_id, it means our orphan is gone."""
        job = _create_orphan_job(db_session)
        job_id = job.id
        # Different id — someone re-created the record
        mock_find.return_value = {"id": "cf_different", "content": "100.64.0.2"}

        resp = client.post(f"/api/jobs/{job_id}/retry")
        assert resp.status_code == 200

        # Job should be deleted — the orphaned record is gone
        db_session.expire_all()
        assert db_session.get(Job, job_id) is None

    @patch("app.adapters.cloudflare_adapter.find_record", side_effect=RuntimeError("API timeout"))
    @patch("app.secrets.read_secret", return_value="cf-token-123")
    def test_retry_fails_on_cf_error(self, mock_secret, mock_find, client, db_session):
        job = _create_orphan_job(db_session)

        resp = client.post(f"/api/jobs/{job.id}/retry")
        assert resp.status_code == 502

        # Job should still exist with failed status
        db_session.expire_all()
        updated = db_session.get(Job, job.id)
        assert updated is not None
        assert updated.status == "failed"
        assert "API timeout" in updated.message

        events = db_session.query(Event).filter(Event.kind == "dns_orphan_retry_failed").all()
        assert len(events) == 1

    @patch("app.secrets.read_secret", return_value=None)
    def test_retry_fails_without_cf_token(self, mock_secret, client, db_session):
        job = _create_orphan_job(db_session)

        resp = client.post(f"/api/jobs/{job.id}/retry")
        assert resp.status_code == 422
        assert "not configured" in resp.json()["detail"]

    def test_retry_nonexistent_job(self, client):
        resp = client.post("/api/jobs/job_nonexistent/retry")
        assert resp.status_code == 404

    def test_retry_rejects_non_orphan_job(self, client, db_session):
        job = Job(kind="some_other_kind", status="pending")
        db_session.add(job)
        db_session.commit()

        resp = client.post(f"/api/jobs/{job.id}/retry")
        assert resp.status_code == 422
        assert "dns_orphan_cleanup" in resp.json()["detail"]

    def test_retry_rejects_missing_details(self, client, db_session):
        job = Job(kind="dns_orphan_cleanup", status="pending", details=None)
        db_session.add(job)
        db_session.commit()

        resp = client.post(f"/api/jobs/{job.id}/retry")
        assert resp.status_code == 422
        assert "missing" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# Dismiss orphan job
# ---------------------------------------------------------------------------


class TestDismissOrphanJob:
    def test_dismiss_deletes_job(self, client, db_session):
        job = _create_orphan_job(db_session)
        job_id = job.id

        resp = client.delete(f"/api/jobs/{job_id}")
        assert resp.status_code == 204

        db_session.expire_all()
        assert db_session.get(Job, job_id) is None

        events = db_session.query(Event).filter(Event.kind == "dns_orphan_dismissed").all()
        assert len(events) == 1

    def test_dismiss_nonexistent_job(self, client):
        resp = client.delete("/api/jobs/job_nonexistent")
        assert resp.status_code == 404

    def test_dismiss_rejects_non_orphan_job(self, client, db_session):
        job = Job(kind="some_other_kind", status="pending")
        db_session.add(job)
        db_session.commit()

        resp = client.delete(f"/api/jobs/{job.id}")
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Integration: delete_service creates orphan job that retry can process
# ---------------------------------------------------------------------------


class TestDeleteCreatesRetryableOrphan:
    """End-to-end: delete a service with surviving DNS -> retry cleans up."""

    @patch("app.adapters.cloudflare_adapter.delete_a_record")
    @patch("app.adapters.cloudflare_adapter.find_record")
    @patch("app.secrets.read_secret", return_value="cf-token-123")
    def test_full_lifecycle(self, mock_secret, mock_find, mock_delete, client, db_session):
        from app.settings_store import set_setting
        set_setting(db_session, "cf_zone_id", "zone1")

        svc_id = _create_service(client).json()["id"]

        # Add a DNS record for this service
        dns = DnsRecord(
            service_id=svc_id, hostname="nextcloud.example.com",
            record_id="cf_r1", value="100.64.0.1",
        )
        db_session.add(dns)
        db_session.commit()

        # Delete fails CF cleanup
        mock_delete.side_effect = RuntimeError("CF down")
        resp = client.delete(f"/api/services/{svc_id}?cleanup_dns=true")
        assert resp.status_code == 204

        # An orphan job should have been created
        db_session.expire_all()
        jobs = db_session.query(Job).filter(Job.kind == "dns_orphan_cleanup").all()
        assert len(jobs) == 1
        job = jobs[0]
        job_id = job.id
        details = json.loads(job.details)
        assert details["record_id"] == "cf_r1"

        # Now retry the job — this time CF is fine
        mock_delete.side_effect = None
        mock_find.return_value = {"id": "cf_r1", "content": "100.64.0.1"}

        resp = client.post(f"/api/jobs/{job_id}/retry")
        assert resp.status_code == 200
        assert resp.json()["success"] is True

        # Job should be gone
        db_session.expire_all()
        assert db_session.get(Job, job_id) is None
