"""Tests for the Jobs API — orphaned DNS cleanup management."""

from unittest.mock import patch

from app.models.dns_record import DnsRecord
from app.models.event import Event
from app.models.job import Job


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
        "details": {
            "record_id": "cf_abc123",
            "hostname": "test.example.com",
            "zone_id": "zone1",
            "value": "100.64.0.1",
            "service_name": "TestApp",
        },
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

    def test_lists_job_with_invalid_details_without_crashing(self, client, db_session):
        from sqlalchemy import text

        job = _create_orphan_job(db_session)
        db_session.execute(
            text("UPDATE jobs SET details = :d WHERE id = :id"),
            {"d": "{not json", "id": job.id},
        )
        db_session.commit()

        resp = client.get("/api/jobs")

        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["jobs"][0]["id"] == job.id
        assert data["jobs"][0]["details"] is None

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

    def test_rejects_invalid_pagination_bounds(self, client):
        resp = client.get("/api/jobs?limit=0&offset=-1")
        assert resp.status_code == 422

        resp = client.get("/api/jobs?limit=501")
        assert resp.status_code == 422



# ---------------------------------------------------------------------------
# Get single job
# ---------------------------------------------------------------------------


class TestGetJob:
    def test_get_existing_job(self, client, db_session):
        job = _create_orphan_job(db_session)
        resp = client.get(f"/api/jobs/{job.id}")
        assert resp.status_code == 200
        assert resp.json()["id"] == job.id

    def test_get_job_with_invalid_details_without_crashing(self, client, db_session):
        from sqlalchemy import text

        job = _create_orphan_job(db_session)
        db_session.execute(
            text("UPDATE jobs SET details = :d WHERE id = :id"),
            {"d": "{not json", "id": job.id},
        )
        db_session.commit()

        resp = client.get(f"/api/jobs/{job.id}")

        assert resp.status_code == 200
        assert resp.json()["details"] is None

    def test_get_nonexistent_job(self, client):
        resp = client.get("/api/jobs/job_nonexistent")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Retry orphan cleanup
# ---------------------------------------------------------------------------


class TestRetryOrphanCleanup:
    @patch("app.adapters.cloudflare_adapter.delete_a_record")
    @patch("app.adapters.cloudflare_adapter.list_a_records")
    @patch("app.secrets.read_secret", return_value="cf-token-123")
    def test_retry_deletes_existing_record(
        self, mock_secret, mock_list, mock_delete, client, db_session
    ):
        job = _create_orphan_job(db_session)
        job_id = job.id
        mock_list.return_value = [{"id": "cf_abc123", "content": "100.64.0.1"}]

        resp = client.post(f"/api/jobs/{job_id}/retry")
        assert resp.status_code == 200
        assert resp.json()["success"] is True

        from app.routers.jobs import CF_CLEANUP_TIMEOUT
        mock_delete.assert_called_once_with(
            "cf-token-123", "zone1", "cf_abc123", timeout=CF_CLEANUP_TIMEOUT
        )

        # Job should be deleted
        db_session.expire_all()
        assert db_session.get(Job, job_id) is None

        # Should emit dns_orphan_resolved event
        events = db_session.query(Event).filter(Event.kind == "dns_orphan_resolved").all()
        assert len(events) == 1
        assert "successfully deleted" in events[0].message

    @patch("app.adapters.cloudflare_adapter.list_a_records")
    @patch("app.secrets.read_secret", return_value="cf-token-123")
    def test_retry_succeeds_when_record_already_gone(
        self, mock_secret, mock_list, client, db_session
    ):
        job = _create_orphan_job(db_session)
        job_id = job.id
        mock_list.return_value = []  # record no longer exists

        resp = client.post(f"/api/jobs/{job_id}/retry")
        assert resp.status_code == 200
        assert resp.json()["success"] is True

        # Job should be deleted
        db_session.expire_all()
        assert db_session.get(Job, job_id) is None

        events = db_session.query(Event).filter(Event.kind == "dns_orphan_resolved").all()
        assert len(events) == 1
        assert "already removed" in events[0].message

    @patch("app.adapters.cloudflare_adapter.list_a_records")
    @patch("app.secrets.read_secret", return_value="cf-token-123")
    def test_retry_succeeds_when_different_record_at_hostname(
        self, mock_secret, mock_list, client, db_session
    ):
        """If our record_id is absent from the hostname's A records, the orphan is gone."""
        job = _create_orphan_job(db_session)
        job_id = job.id
        # Only a different record exists at the hostname — our orphan is gone
        mock_list.return_value = [{"id": "cf_different", "content": "100.64.0.2"}]

        resp = client.post(f"/api/jobs/{job_id}/retry")
        assert resp.status_code == 200

        # Job should be deleted — the orphaned record is gone
        db_session.expire_all()
        assert db_session.get(Job, job_id) is None

    @patch("app.adapters.cloudflare_adapter.delete_a_record")
    @patch("app.adapters.cloudflare_adapter.list_a_records")
    @patch("app.secrets.read_secret", return_value="cf-token-123")
    def test_retry_deletes_orphan_when_not_lowest_id_among_multiple_records(
        self, mock_secret, mock_list, mock_delete, client, db_session
    ):
        """DN5 regression: when a hostname carries MORE THAN ONE A record and the
        orphaned record_id is NOT the lowest-id one, the retry must still find and
        delete the specific orphan. The old code used find_record(), which returns
        only the lowest-id match; the orphan's id would then not match, control
        fell through to the "already cleaned up" branch, the job was dropped, and
        the orphaned Cloudflare record silently survived. list_a_records() returns
        the full set (sorted by id), so the orphan is matched by id regardless of
        rank and actually deleted."""
        job = _create_orphan_job(db_session)  # details.record_id == "cf_abc123"
        job_id = job.id
        # "cf_abc123" sorts AFTER "cf_000lowest": find_record() would have picked
        # the lowest and missed our orphan entirely.
        mock_list.return_value = [
            {"id": "cf_000lowest", "content": "100.64.0.5"},
            {"id": "cf_abc123", "content": "100.64.0.1"},
        ]

        resp = client.post(f"/api/jobs/{job_id}/retry")
        assert resp.status_code == 200
        assert resp.json()["success"] is True

        # The SPECIFIC orphaned record must be deleted (not the lowest-id one).
        from app.routers.jobs import CF_CLEANUP_TIMEOUT
        mock_delete.assert_called_once_with(
            "cf-token-123", "zone1", "cf_abc123", timeout=CF_CLEANUP_TIMEOUT
        )

        # Job cleared only because the orphan was genuinely removed.
        db_session.expire_all()
        assert db_session.get(Job, job_id) is None
        events = db_session.query(Event).filter(Event.kind == "dns_orphan_resolved").all()
        assert len(events) == 1
        assert "successfully deleted" in events[0].message

    @patch("app.adapters.cloudflare_adapter.list_a_records", side_effect=RuntimeError("API timeout"))
    @patch("app.secrets.read_secret", return_value="cf-token-123")
    def test_retry_fails_on_cf_error(self, mock_secret, mock_list, client, db_session):
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

    @patch("app.secrets.read_secret", return_value="cf-token-123")
    def test_retry_rejects_running_job(self, mock_secret, client, db_session):
        job = _create_orphan_job(db_session, status="running")

        resp = client.post(f"/api/jobs/{job.id}/retry")

        assert resp.status_code == 409
        assert "already running" in resp.json()["detail"]


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

    @patch("app.secrets.read_secret", return_value="cf-token-123")
    def test_retry_rejects_details_without_hostname(self, mock_secret, client, db_session):
        job = _create_orphan_job(db_session, details={
            "record_id": "cf_abc123",
            "zone_id": "zone1",
            "value": "100.64.0.1",
        })

        resp = client.post(f"/api/jobs/{job.id}/retry")

        assert resp.status_code == 422
        assert "hostname" in resp.json()["detail"]
        db_session.expire_all()
        assert db_session.get(Job, job.id).status == "pending"

    def test_retry_rejects_invalid_json_details(self, client, db_session):
        from sqlalchemy import text

        job = _create_orphan_job(db_session)
        db_session.execute(
            text("UPDATE jobs SET details = :d WHERE id = :id"),
            {"d": "{not json", "id": job.id},
        )
        db_session.commit()

        resp = client.post(f"/api/jobs/{job.id}/retry")

        assert resp.status_code == 422
        assert "malformed" in resp.json()["detail"].lower()
        db_session.expire_all()
        assert db_session.get(Job, job.id).status == "pending"

    def test_retry_rejects_non_object_details(self, client, db_session):
        from sqlalchemy import text

        job = _create_orphan_job(db_session)
        db_session.execute(
            text("UPDATE jobs SET details = :d WHERE id = :id"),
            {"d": '["cf_abc123"]', "id": job.id},
        )
        db_session.commit()

        resp = client.post(f"/api/jobs/{job.id}/retry")

        assert resp.status_code == 422
        assert "malformed" in resp.json()["detail"].lower()
        db_session.expire_all()
        assert db_session.get(Job, job.id).status == "pending"

    @patch("app.adapters.cloudflare_adapter.list_a_records")
    @patch("app.secrets.read_secret", return_value="cf-token-123")
    def test_retry_uses_current_zone_when_job_details_lack_zone(
        self, mock_secret, mock_list, client, db_session
    ):
        from app.settings_store import set_setting

        set_setting(db_session, "cf_zone_id", "zone-from-settings")
        db_session.commit()
        job = _create_orphan_job(db_session, details={
            "record_id": "cf_abc123",
            "hostname": "test.example.com",
            "value": "100.64.0.1",
            "service_name": "TestApp",
        })
        job_id = job.id
        mock_list.return_value = []

        resp = client.post(f"/api/jobs/{job_id}/retry")

        assert resp.status_code == 200
        from app.routers.jobs import CF_CLEANUP_TIMEOUT
        mock_list.assert_called_once_with(
            "cf-token-123", "zone-from-settings", "test.example.com", "A",
            timeout=CF_CLEANUP_TIMEOUT,
        )
        db_session.expire_all()
        assert db_session.get(Job, job_id) is None

    @patch("app.adapters.cloudflare_adapter.delete_a_record")
    @patch("app.adapters.cloudflare_adapter.list_a_records")
    @patch("app.secrets.read_secret", return_value="cf-token-123")
    def test_retry_skips_deletion_when_record_reclaimed_by_active_service(
        self, mock_secret, mock_list, mock_delete, client, db_session
    ):
        """If a live service now owns the same Cloudflare record id, retry must
        NOT delete it (the hostname was reclaimed; deletion would cause an outage)."""
        job = _create_orphan_job(db_session)
        job_id = job.id
        # list_a_records returns our orphan's id — but a live DnsRecord now owns it.
        mock_list.return_value = [{"id": "cf_abc123", "content": "100.64.0.9"}]
        svc = _create_service(client, hostname="test.example.com").json()
        db_session.add(DnsRecord(
            service_id=svc["id"],
            hostname="test.example.com",
            record_id="cf_abc123",
            value="100.64.0.9",
        ))
        db_session.commit()

        resp = client.post(f"/api/jobs/{job_id}/retry")
        assert resp.status_code == 200

        # The live record must NOT have been deleted.
        mock_delete.assert_not_called()
        # The stale orphan job is cleared since it's no longer orphaned.
        db_session.expire_all()
        assert db_session.get(Job, job_id) is None

    @patch("app.adapters.cloudflare_adapter.delete_a_record")
    @patch("app.adapters.cloudflare_adapter.list_a_records")
    @patch("app.secrets.read_secret", return_value="cf-token-123")
    def test_retry_caps_cloudflare_timeout_to_short_value(
        self, mock_secret, mock_list, mock_delete, client, db_session
    ):
        """Both Cloudflare calls on the lock-held retry path MUST use the short
        cleanup timeout, not the adapter's 30s default, so a slow/unreachable
        Cloudflare cannot stall all service lifecycle work for ~60s."""
        from app.routers.jobs import CF_CLEANUP_TIMEOUT

        assert CF_CLEANUP_TIMEOUT < 30.0
        job = _create_orphan_job(db_session)
        job_id = job.id
        mock_list.return_value = [{"id": "cf_abc123", "content": "100.64.0.1"}]

        resp = client.post(f"/api/jobs/{job_id}/retry")
        assert resp.status_code == 200

        assert mock_list.call_args.kwargs["timeout"] == CF_CLEANUP_TIMEOUT
        assert mock_delete.call_args.kwargs["timeout"] == CF_CLEANUP_TIMEOUT

    @patch("app.adapters.cloudflare_adapter.delete_a_record")
    @patch("app.adapters.cloudflare_adapter.list_a_records")
    @patch("app.secrets.read_secret", return_value="cf-token-123")
    def test_retry_delete_timeout_resets_job_to_failed_not_running(
        self, mock_secret, mock_list, mock_delete, client, db_session
    ):
        """A timeout raised by delete_a_record AFTER list_a_records succeeds (and the
        owner guard passes) must not leave the job wedged in 'running'. The job is
        set to 'running' before the Cloudflare I/O; if the shortened cleanup
        timeout fires mid-delete the shared except handler MUST roll it back to
        'failed' so a subsequent retry/dismiss is accepted (both reject 'running')."""
        from app.routers.jobs import CF_CLEANUP_TIMEOUT

        job = _create_orphan_job(db_session)
        job_id = job.id
        # Record still present and orphaned (no live DnsRecord owner) -> we reach
        # the delete call, which then times out.
        mock_list.return_value = [{"id": "cf_abc123", "content": "100.64.0.1"}]
        mock_delete.side_effect = RuntimeError("Cloudflare delete_a_record failed: read timed out")

        resp = client.post(f"/api/jobs/{job_id}/retry")
        assert resp.status_code == 502
        detail = resp.json()["detail"]
        # AR-R3-2: the 502 detail is a static, non-leaking string — str(exc) no
        # longer reaches the client (it still lands in the persisted job.message
        # asserted below and is logged server-side).
        assert detail == "Cloudflare API error"
        assert "timed out" not in detail

        # Delete was attempted with the short cleanup timeout, then failed.
        assert mock_delete.call_args.kwargs["timeout"] == CF_CLEANUP_TIMEOUT

        # Critical invariant: job rolled back to 'failed', NOT stuck 'running'.
        db_session.expire_all()
        updated = db_session.get(Job, job_id)
        assert updated is not None
        assert updated.status == "failed"
        assert "timed out" in updated.message

        # And a failed job can then be dismissed (would 409 if still 'running').
        assert client.delete(f"/api/jobs/{job_id}").status_code == 204


class TestResetStaleRunningJobs:
    def test_reset_flips_running_jobs_to_failed(self, db_session):
        from app.routers.jobs import reset_stale_running_jobs

        running = _create_orphan_job(db_session, status="running")
        pending = _create_orphan_job(db_session, status="pending")

        count = reset_stale_running_jobs(db_session)
        assert count == 1

        db_session.expire_all()
        assert db_session.get(Job, running.id).status == "failed"
        # Non-running jobs are untouched.
        assert db_session.get(Job, pending.id).status == "pending"

    def test_reset_allows_dismiss_after_recovery(self, client, db_session):
        stuck = _create_orphan_job(db_session, status="running")
        # Dismiss is rejected while running...
        assert client.delete(f"/api/jobs/{stuck.id}").status_code == 409

        from app.routers.jobs import reset_stale_running_jobs
        reset_stale_running_jobs(db_session)

        # ...but works once the stale job is reclaimed.
        assert client.delete(f"/api/jobs/{stuck.id}").status_code == 204


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

    def test_dismiss_rejects_running_job(self, client, db_session):
        job = _create_orphan_job(db_session, status="running")

        resp = client.delete(f"/api/jobs/{job.id}")

        assert resp.status_code == 409
        db_session.expire_all()
        assert db_session.get(Job, job.id) is not None

    def test_dismiss_deletes_job_with_invalid_details(self, client, db_session):
        from sqlalchemy import text

        job = _create_orphan_job(db_session)
        job_id = job.id
        db_session.execute(
            text("UPDATE jobs SET details = :d WHERE id = :id"),
            {"d": "{not json", "id": job_id},
        )
        db_session.commit()

        resp = client.delete(f"/api/jobs/{job_id}")

        assert resp.status_code == 204
        db_session.expire_all()
        assert db_session.get(Job, job_id) is None


# ---------------------------------------------------------------------------
# Integration: delete_service creates orphan job that retry can process
# ---------------------------------------------------------------------------


class TestDeleteCreatesRetryableOrphan:
    """End-to-end: delete a service with surviving DNS -> retry cleans up."""

    @patch("app.adapters.cloudflare_adapter.delete_a_record")
    @patch("app.adapters.cloudflare_adapter.list_a_records")
    @patch("app.secrets.read_secret", return_value="cf-token-123")
    def test_full_lifecycle(self, mock_secret, mock_list, mock_delete, client, db_session):
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
        details = job.details
        assert details["record_id"] == "cf_r1"

        # Now retry the job — this time CF is fine
        mock_delete.side_effect = None
        mock_list.return_value = [{"id": "cf_r1", "content": "100.64.0.1"}]

        resp = client.post(f"/api/jobs/{job_id}/retry")
        assert resp.status_code == 200
        assert resp.json()["success"] is True

        # Job should be gone
        db_session.expire_all()
        assert db_session.get(Job, job_id) is None

    @patch("app.adapters.cloudflare_adapter.list_a_records")
    @patch("app.secrets.read_secret", return_value="cf-token-123")
    def test_delete_without_zone_creates_job_retryable_after_zone_configured(
        self, mock_secret, mock_list, client, db_session
    ):
        from app.settings_store import set_setting

        svc_id = _create_service(client).json()["id"]
        dns = DnsRecord(
            service_id=svc_id, hostname="nextcloud.example.com",
            record_id="cf_late_zone", value="100.64.0.1",
        )
        db_session.add(dns)
        db_session.commit()

        resp = client.delete(f"/api/services/{svc_id}")
        assert resp.status_code == 204

        db_session.expire_all()
        jobs = db_session.query(Job).filter(Job.kind == "dns_orphan_cleanup").all()
        assert len(jobs) == 1
        job_id = jobs[0].id
        details = jobs[0].details
        assert details["zone_id"] == ""

        set_setting(db_session, "cf_zone_id", "zone-configured-later")
        db_session.commit()
        mock_list.return_value = []

        resp = client.post(f"/api/jobs/{job_id}/retry")

        assert resp.status_code == 200
        from app.routers.jobs import CF_CLEANUP_TIMEOUT
        mock_list.assert_called_once_with(
            "cf-token-123", "zone-configured-later", "nextcloud.example.com", "A",
            timeout=CF_CLEANUP_TIMEOUT,
        )
        db_session.expire_all()
        assert db_session.get(Job, job_id) is None
