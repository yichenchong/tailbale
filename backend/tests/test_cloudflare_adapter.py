"""Tests for the Cloudflare API v4 adapter."""

from unittest.mock import MagicMock, patch

import pytest


def _cf_response(success=True, result=None, errors=None):
    """Create a mock httpx response matching Cloudflare API format."""
    mock = MagicMock()
    mock.json.return_value = {
        "success": success,
        "result": result if result is not None else {},
        "errors": errors or [],
    }
    return mock

class TestRequestHelper:
    @patch("app.adapters.cloudflare_adapter.httpx2.post")
    def test_composes_post_request_and_checks_action(self, mock_post):
        from app.adapters import cloudflare_adapter as cf

        response = _cf_response(result={"id": "r1"})
        mock_post.return_value = response

        with patch(
            "app.adapters.cloudflare_adapter._check_response", return_value={"ok": True}
        ) as mock_check:
            result = cf._request(
                "POST",
                "/zones/z1/dns_records",
                token="cf-token",
                json={"name": "app.example.com"},
                timeout=12.5,
                action="create_a_record",
            )

        assert result == {"ok": True}
        mock_post.assert_called_once_with(
            f"{cf.CF_API_BASE}/zones/z1/dns_records",
            headers=cf._headers("cf-token"),
            json={"name": "app.example.com"},
            timeout=12.5,
        )
        mock_check.assert_called_once_with(response, "create_a_record")

    @patch("app.adapters.cloudflare_adapter.httpx2.delete")
    def test_omits_absent_optional_kwargs(self, mock_delete):
        from app.adapters import cloudflare_adapter as cf

        response = _cf_response(result={"id": "r1"})
        mock_delete.return_value = response

        with patch("app.adapters.cloudflare_adapter._check_response", return_value={}):
            cf._request(
                "DELETE",
                "/zones/z1/dns_records/r1",
                token="cf-token",
                timeout=10.0,
                action="delete_a_record",
            )

        call_kwargs = mock_delete.call_args.kwargs
        assert "json" not in call_kwargs
        assert "params" not in call_kwargs


class TestSharedErrorHandling:
    @patch("app.adapters.cloudflare_adapter.httpx2.get")
    def test_raises_on_error(self, mock_get):
        from app.adapters.cloudflare_adapter import find_record

        mock_get.return_value = _cf_response(
            success=False, errors=[{"message": "Invalid token"}]
        )

        with pytest.raises(RuntimeError, match="Invalid token"):
            find_record("bad-token", "z1", "app.example.com")

    @patch("app.adapters.cloudflare_adapter.httpx2.get")
    def test_formats_non_dict_cloudflare_errors(self, mock_get):
        from app.adapters.cloudflare_adapter import find_record

        mock_get.return_value = _cf_response(
            success=False,
            errors=[
                {"code": 1001, "message": "Record not found"},
                "rate limited",
            ],
        )

        with pytest.raises(RuntimeError, match="Record not found \\(code 1001\\); rate limited"):
            find_record("bad-token", "z1", "app.example.com")

    def test_record_not_found_detection_does_not_match_zone_errors(self):
        from app.adapters.cloudflare_adapter import CloudflareAPIError, is_not_found_error

        assert is_not_found_error(
            CloudflareAPIError(
                "delete_a_record",
                "Record not found",
                errors=[{"message": "Record not found"}],
            )
        )
        assert not is_not_found_error(
            CloudflareAPIError(
                "delete_a_record",
                "Zone not found",
                errors=[{"message": "Zone not found"}],
            )
        )

    def test_record_does_not_exist_81044_is_treated_as_gone(self):
        from app.adapters.cloudflare_adapter import CloudflareAPIError, is_not_found_error

        # Cloudflare returns code 81044 / "Record does not exist." when deleting
        # an already-removed record; cleanup must treat that as already gone.
        assert is_not_found_error(
            CloudflareAPIError(
                "delete_a_record",
                "Record does not exist. (code 81044)",
                errors=[{"code": 81044, "message": "Record does not exist."}],
            )
        )
        # Matched by code even if the message wording shifts.
        assert is_not_found_error(
            CloudflareAPIError(
                "delete_a_record",
                "boom",
                errors=[{"code": 81044, "message": "boom"}],
            )
        )
        # A zone-level "does not exist" must NOT be treated as a record removal.
        assert not is_not_found_error(
            CloudflareAPIError(
                "delete_a_record",
                "Zone does not exist",
                errors=[{"message": "Zone does not exist"}],
            )
        )

    def test_bare_generic_1001_is_not_gone(self):
        from app.adapters.cloudflare_adapter import CloudflareAPIError, is_not_found_error

        # 1001 is a GENERIC Cloudflare error code, not record-not-found (that's
        # 81044). A delete failing for an unrelated reason that happens to carry
        # code 1001 must NOT be swallowed, or the real CF record is orphaned
        # while the local row is dropped.
        assert not is_not_found_error(
            CloudflareAPIError(
                "delete_a_record",
                "Invalid request headers (code 1001)",
                errors=[{"code": 1001, "message": "Invalid request headers"}],
            )
        )

    def test_1001_with_record_not_found_message_is_gone(self):
        from app.adapters.cloudflare_adapter import CloudflareAPIError, is_not_found_error

        # A 1001 carrying an actual record-not-found MESSAGE is still treated as
        # already-gone via the message path, even though the bare code does not.
        assert is_not_found_error(
            CloudflareAPIError(
                "delete_a_record",
                "Record does not exist. (code 1001)",
                errors=[{"code": 1001, "message": "Record does not exist."}],
            )
        )

    def test_generic_exception_with_record_message_is_gone(self):
        from app.adapters.cloudflare_adapter import is_not_found_error

        # A plain (non-CloudflareAPIError) exception surfaced by delete whose
        # message says the record is gone is still treated as already-removed.
        assert is_not_found_error(RuntimeError("DNS record does not exist"))

    def test_transient_edge_5xx_is_not_gone(self):
        from app.adapters.cloudflare_adapter import is_not_found_error

        # A non-JSON edge 5xx during delete must NOT be mistaken for "record
        # gone", or cleanup would drop the local row on a transient outage.
        assert not is_not_found_error(
            RuntimeError(
                "Cloudflare delete_a_record failed: non-JSON response (HTTP 502): 502 Bad Gateway"
            )
        )

    def test_non_delete_action_not_found_is_not_gone(self):
        from app.adapters.cloudflare_adapter import CloudflareAPIError, is_not_found_error

        # A not-found surfaced by a non-delete action (e.g. find_record) must not
        # be treated as a delete-time "already gone", even with code 81044.
        assert not is_not_found_error(
            CloudflareAPIError(
                "find_record",
                "Record does not exist.",
                errors=[{"code": 81044, "message": "Record does not exist."}],
            )
        )

    def test_delete_error_empty_errors_falls_back_to_message(self):
        from app.adapters.cloudflare_adapter import CloudflareAPIError, is_not_found_error

        # With no structured errors, fall back to the CloudflareAPIError message.
        assert is_not_found_error(
            CloudflareAPIError("delete_a_record", "Record does not exist.", errors=[])
        )
        assert not is_not_found_error(
            CloudflareAPIError("delete_a_record", "Unauthorized", errors=[])
        )

    def test_non_dict_error_body_record_gone_via_message(self):
        from app.adapters.cloudflare_adapter import CloudflareAPIError, is_not_found_error

        # Cloudflare normally returns structured dict errors, but the classifier
        # also handles a non-dict (bare string) error entry: a string saying the
        # DNS record is gone must still classify as already-removed (the `elif`
        # str-path), so cleanup drops the stale local row instead of looping.
        assert is_not_found_error(
            CloudflareAPIError("delete_a_record", "boom", errors=["The DNS record does not exist"])
        )

    def test_non_dict_error_body_unrelated_is_not_gone(self):
        from app.adapters.cloudflare_adapter import CloudflareAPIError, is_not_found_error

        # A non-dict error entry that is NOT a record-not-found message must NOT be
        # swallowed, or a transient failure would orphan the real CF record.
        assert not is_not_found_error(
            CloudflareAPIError("delete_a_record", "boom", errors=["totally unrelated failure"])
        )

    def test_mixed_errors_any_not_found_match_is_gone(self):
        from app.adapters.cloudflare_adapter import CloudflareAPIError, is_not_found_error

        # A delete can fail with MULTIPLE errors. The classifier scans the whole
        # list, not just the first entry: an unrelated 1001 ahead of a real 81044
        # must still resolve to already-gone via the trailing record-not-found.
        assert is_not_found_error(
            CloudflareAPIError(
                "delete_a_record",
                "Rate limited; Record does not exist.",
                errors=[
                    {"code": 1001, "message": "Rate limited"},
                    {"code": 81044, "message": "Record does not exist."},
                ],
            )
        )

    def test_mixed_errors_none_match_is_not_gone(self):
        from app.adapters.cloudflare_adapter import CloudflareAPIError, is_not_found_error

        # When NO entry in a multi-error list is record-not-found, the loop must
        # fall through to False -- never default to "gone" on an unrelated batch.
        assert not is_not_found_error(
            CloudflareAPIError(
                "delete_a_record",
                "Rate limited; Internal error",
                errors=[
                    {"code": 1001, "message": "Rate limited"},
                    {"code": 1002, "message": "Internal error"},
                ],
            )
        )

    @patch("app.adapters.cloudflare_adapter.httpx2.get")
    def test_non_json_edge_error_raises_typed_cloudflare_error(self, mock_get):
        import json as _json

        from app.adapters.cloudflare_adapter import CloudflareAPIError, find_record

        # Cloudflare's edge can return an HTML 5xx page instead of JSON. The
        # adapter must translate that into the SAME typed CloudflareAPIError every
        # other hard API failure raises (not a bare RuntimeError), so a caller can
        # catch one exception type and never leak a raw JSONDecodeError.
        resp = MagicMock()
        resp.status_code = 502
        resp.text = "<html><body>502 Bad Gateway</body></html>"
        resp.json.side_effect = _json.JSONDecodeError("Expecting value", "doc", 0)
        mock_get.return_value = resp

        with pytest.raises(CloudflareAPIError, match="non-JSON response \\(HTTP 502\\)") as exc_info:
            find_record("cf-token", "z1", "app.example.com")
        assert exc_info.value.action == "find_record"

    @patch("app.adapters.cloudflare_adapter.httpx2.get")
    def test_unexpected_response_shape_raises_typed_cloudflare_error(self, mock_get):
        from app.adapters.cloudflare_adapter import CloudflareAPIError, find_record

        # A 200 whose JSON body is not an object (a bare list/string) must surface
        # the same typed CloudflareAPIError, not an opaque ``.get`` AttributeError.
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = ["unexpected"]
        mock_get.return_value = resp

        with pytest.raises(CloudflareAPIError, match="unexpected response shape \\(HTTP 200\\)") as exc_info:
            find_record("cf-token", "z1", "app.example.com")
        assert exc_info.value.action == "find_record"

    @patch("app.adapters.cloudflare_adapter.httpx2.get")
    def test_failure_without_structured_errors_falls_back_to_body_text(self, mock_get):
        from app.adapters.cloudflare_adapter import CloudflareAPIError, find_record

        # A Cloudflare failure envelope (``success: false``) can arrive with an EMPTY
        # ``errors`` list. _check_response must still raise the typed
        # CloudflareAPIError, falling back to the raw response body so the operator
        # gets a concrete failure reason instead of an empty one (and find_record
        # never silently returns "no record" on a hard failure).
        resp = MagicMock()
        resp.status_code = 500
        resp.text = "internal failure, no structured errors"
        resp.json.return_value = {"success": False, "errors": []}
        mock_get.return_value = resp

        with pytest.raises(
            CloudflareAPIError, match="internal failure, no structured errors"
        ) as exc_info:
            find_record("cf-token", "z1", "app.example.com")
        assert exc_info.value.action == "find_record"


class TestFindRecord:
    @patch("app.adapters.cloudflare_adapter.httpx2.get")
    def test_finds_existing_record(self, mock_get):
        from app.adapters.cloudflare_adapter import find_record

        record = {"id": "r1", "type": "A", "name": "app.example.com", "content": "100.64.0.1"}
        mock_get.return_value = _cf_response(result=[record])

        result = find_record("cf-token", "z1", "app.example.com")
        assert result is not None
        assert result["id"] == "r1"
        assert result["content"] == "100.64.0.1"
        assert "Bearer cf-token" in mock_get.call_args.kwargs["headers"]["Authorization"]

    @patch("app.adapters.cloudflare_adapter.httpx2.get")
    def test_returns_none_when_not_found(self, mock_get):
        from app.adapters.cloudflare_adapter import find_record

        mock_get.return_value = _cf_response(result=[])

        result = find_record("cf-token", "z1", "nonexistent.example.com")
        assert result is None

    @patch("app.adapters.cloudflare_adapter.httpx2.get")
    def test_passes_type_and_name_params(self, mock_get):
        from app.adapters.cloudflare_adapter import find_record

        mock_get.return_value = _cf_response(result=[])

        find_record("cf-token", "z1", "app.example.com", "CNAME")
        call_kwargs = mock_get.call_args
        assert call_kwargs.kwargs["params"]["type"] == "CNAME"
        assert call_kwargs.kwargs["params"]["name"] == "app.example.com"

    @patch("app.adapters.cloudflare_adapter.httpx2.get")
    def test_multiple_records_pick_is_deterministic_and_warns(self, mock_get, caplog):
        import logging

        from app.adapters.cloudflare_adapter import find_record

        records = [
            {"id": "r3", "type": "A", "name": "dup.example.com", "content": "100.64.0.3"},
            {"id": "r1", "type": "A", "name": "dup.example.com", "content": "100.64.0.1"},
            {"id": "r2", "type": "A", "name": "dup.example.com", "content": "100.64.0.2"},
        ]
        mock_get.return_value = _cf_response(result=records)

        with caplog.at_level(logging.WARNING, logger="app.adapters.cloudflare_adapter"):
            result = find_record("cf-token", "z1", "dup.example.com")

        # Deterministic: lowest id by sort order, regardless of API ordering.
        assert result["id"] == "r1"
        # Observable: warning mentions the hostname and the count.
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1
        assert "dup.example.com" in warnings[0].getMessage()
        assert "3" in warnings[0].getMessage()

    @patch("app.adapters.cloudflare_adapter.httpx2.get")
    def test_multiple_records_pick_stable_under_reordering(self, mock_get):
        from app.adapters.cloudflare_adapter import find_record

        records = [
            {"id": "r1", "type": "A", "name": "dup.example.com", "content": "100.64.0.1"},
            {"id": "r2", "type": "A", "name": "dup.example.com", "content": "100.64.0.2"},
            {"id": "r3", "type": "A", "name": "dup.example.com", "content": "100.64.0.3"},
        ]

        mock_get.return_value = _cf_response(result=list(records))
        forward = find_record("cf-token", "z1", "dup.example.com")

        mock_get.return_value = _cf_response(result=list(reversed(records)))
        reverse = find_record("cf-token", "z1", "dup.example.com")

        # Reversed input order yields the same pick -> selection is deterministic.
        assert forward["id"] == reverse["id"] == "r1"

    @patch("app.adapters.cloudflare_adapter.httpx2.get")
    def test_single_record_does_not_warn(self, mock_get, caplog):
        import logging

        from app.adapters.cloudflare_adapter import find_record

        record = {"id": "r1", "type": "A", "name": "app.example.com", "content": "100.64.0.1"}
        mock_get.return_value = _cf_response(result=[record])

        with caplog.at_level(logging.WARNING, logger="app.adapters.cloudflare_adapter"):
            result = find_record("cf-token", "z1", "app.example.com")

        assert result["id"] == "r1"
        assert [r for r in caplog.records if r.levelno == logging.WARNING] == []

    @patch("app.adapters.cloudflare_adapter.httpx2.get")
    def test_default_timeout_is_30(self, mock_get):
        from app.adapters.cloudflare_adapter import find_record

        mock_get.return_value = _cf_response(result=[])
        find_record("cf-token", "z1", "app.example.com")
        assert mock_get.call_args.kwargs["timeout"] == 30.0

    @patch("app.adapters.cloudflare_adapter.httpx2.get")
    def test_custom_timeout_is_threaded_through(self, mock_get):
        from app.adapters.cloudflare_adapter import find_record

        mock_get.return_value = _cf_response(result=[])
        find_record("cf-token", "z1", "app.example.com", "A", timeout=10.0)
        assert mock_get.call_args.kwargs["timeout"] == 10.0

    @patch("app.adapters.cloudflare_adapter.httpx2.get")
    def test_requests_larger_per_page(self, mock_get):
        from app.adapters.cloudflare_adapter import CF_FIND_PER_PAGE, find_record

        # Cloudflare's list endpoint defaults to per_page=20; find_record must
        # request a larger page explicitly so a hostname with up to CF_FIND_PER_PAGE
        # matching records is fully seen in ONE round-trip (keeping the deterministic
        # pick global) without looping pages under the lifecycle lock.
        mock_get.return_value = _cf_response(result=[])
        find_record("cf-token", "z1", "app.example.com")
        assert mock_get.call_args.kwargs["params"]["per_page"] == CF_FIND_PER_PAGE
        assert CF_FIND_PER_PAGE >= 100

    @patch("app.adapters.cloudflare_adapter.httpx2.get")
    def test_warns_when_result_set_truncated(self, mock_get, caplog):
        import logging

        from app.adapters.cloudflare_adapter import find_record

        # Cloudflare reports MORE matching records (total_count) than the single
        # page returned: the deterministic pick is no longer guaranteed global, so
        # find_record must surface a truncation warning (a lower id could live on an
        # unseen page) while still returning the deterministic pick of what it saw.
        records = [
            {"id": "r5", "type": "A", "name": "dup.example.com", "content": "100.64.0.5"},
            {"id": "r4", "type": "A", "name": "dup.example.com", "content": "100.64.0.4"},
        ]
        resp = _cf_response(result=records)
        resp.json.return_value["result_info"] = {"total_count": 105, "count": 2}
        mock_get.return_value = resp

        with caplog.at_level(logging.WARNING, logger="app.adapters.cloudflare_adapter"):
            result = find_record("cf-token", "z1", "dup.example.com")

        assert result["id"] == "r4"  # deterministic pick of the returned page
        msgs = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
        assert any("105" in m and "dup.example.com" in m for m in msgs)

    @patch("app.adapters.cloudflare_adapter.httpx2.get")
    def test_no_truncation_warning_when_full_set_returned(self, mock_get, caplog):
        import logging

        from app.adapters.cloudflare_adapter import find_record

        # total_count equals what was returned -> nothing was truncated -> the only
        # warning allowed is the multi-record determinism note, never a truncation one.
        records = [
            {"id": "r2", "type": "A", "name": "dup.example.com", "content": "100.64.0.2"},
            {"id": "r1", "type": "A", "name": "dup.example.com", "content": "100.64.0.1"},
        ]
        resp = _cf_response(result=records)
        resp.json.return_value["result_info"] = {"total_count": 2, "count": 2}
        mock_get.return_value = resp

        with caplog.at_level(logging.WARNING, logger="app.adapters.cloudflare_adapter"):
            result = find_record("cf-token", "z1", "dup.example.com")

        assert result["id"] == "r1"
        msgs = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
        assert not any("returned" in m and "per_page" in m for m in msgs)


class TestCreateARecord:
    @patch("app.adapters.cloudflare_adapter.httpx2.post")
    def test_creates_record(self, mock_post):
        from app.adapters.cloudflare_adapter import create_a_record

        created = {"id": "new_r1", "type": "A", "name": "app.example.com", "content": "100.64.0.1"}
        mock_post.return_value = _cf_response(result=created)

        result = create_a_record("cf-token", "z1", "app.example.com", "100.64.0.1")
        assert result["id"] == "new_r1"

        # Verify body
        call_kwargs = mock_post.call_args
        body = call_kwargs.kwargs["json"]
        assert body["type"] == "A"
        assert body["name"] == "app.example.com"
        assert body["content"] == "100.64.0.1"
        assert body["proxied"] is False

    @patch("app.adapters.cloudflare_adapter.httpx2.post")
    def test_raises_on_failure(self, mock_post):
        from app.adapters.cloudflare_adapter import create_a_record

        mock_post.return_value = _cf_response(
            success=False, errors=[{"message": "Record already exists"}]
        )

        with pytest.raises(RuntimeError, match="Record already exists"):
            create_a_record("cf-token", "z1", "dup.example.com", "100.64.0.1")

    @patch("app.adapters.cloudflare_adapter.httpx2.post")
    def test_honors_explicit_timeout(self, mock_post):
        from app.adapters.cloudflare_adapter import create_a_record

        mock_post.return_value = _cf_response(result={"id": "new_r1"})

        create_a_record("cf-token", "z1", "app.example.com", "100.64.0.1", timeout=10)
        assert mock_post.call_args.kwargs["timeout"] == 10

    @patch("app.adapters.cloudflare_adapter.httpx2.post")
    def test_defaults_to_30s_timeout(self, mock_post):
        from app.adapters.cloudflare_adapter import create_a_record

        mock_post.return_value = _cf_response(result={"id": "new_r1"})

        create_a_record("cf-token", "z1", "app.example.com", "100.64.0.1")
        assert mock_post.call_args.kwargs["timeout"] == 30

    @patch("app.adapters.cloudflare_adapter.httpx2.post")
    def test_null_result_returns_empty_dict_not_attribute_error(self, mock_post):
        """Cloudflare success with a literal ``"result": null`` must not crash.

        ``data.get("result", {})`` only defaults when the key is ABSENT, so a
        present None value would make the ``result.get("id")`` log line raise a
        cryptic AttributeError. The adapter coerces None to {} so the caller gets
        a clean record-id check instead.
        """
        from app.adapters.cloudflare_adapter import create_a_record

        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"success": True, "result": None, "errors": []}
        mock_post.return_value = resp

        result = create_a_record("cf-token", "z1", "app.example.com", "100.64.0.1")
        assert result == {}

    @patch("app.adapters.cloudflare_adapter.httpx2.post")
    def test_includes_comment_in_body_when_passed(self, mock_post):
        from app.adapters.cloudflare_adapter import create_a_record

        mock_post.return_value = _cf_response(result={"id": "new_r1"})

        create_a_record(
            "cf-token", "z1", "app.example.com", "100.64.0.1", comment="tailbale-managed:svc1"
        )
        body = mock_post.call_args.kwargs["json"]
        assert body["comment"] == "tailbale-managed:svc1"

    @patch("app.adapters.cloudflare_adapter.httpx2.post")
    def test_omits_comment_key_when_not_passed(self, mock_post):
        from app.adapters.cloudflare_adapter import create_a_record

        mock_post.return_value = _cf_response(result={"id": "new_r1"})

        create_a_record("cf-token", "z1", "app.example.com", "100.64.0.1")
        body = mock_post.call_args.kwargs["json"]
        assert "comment" not in body  # backward compatible: no comment key


class TestUpdateARecord:
    @patch("app.adapters.cloudflare_adapter.httpx2.patch")
    def test_updates_record(self, mock_patch):
        from app.adapters.cloudflare_adapter import update_a_record

        updated = {"id": "r1", "content": "100.64.0.2"}
        mock_patch.return_value = _cf_response(result=updated)

        result = update_a_record("cf-token", "z1", "r1", "100.64.0.2")
        assert result["content"] == "100.64.0.2"

        call_kwargs = mock_patch.call_args
        assert call_kwargs.kwargs["json"]["content"] == "100.64.0.2"
        assert "r1" in call_kwargs.args[0]  # record_id in URL

    @patch("app.adapters.cloudflare_adapter.httpx2.patch")
    def test_honors_explicit_timeout(self, mock_patch):
        from app.adapters.cloudflare_adapter import update_a_record

        mock_patch.return_value = _cf_response(result={"id": "r1", "content": "100.64.0.2"})

        update_a_record("cf-token", "z1", "r1", "100.64.0.2", timeout=10)
        assert mock_patch.call_args.kwargs["timeout"] == 10

    @patch("app.adapters.cloudflare_adapter.httpx2.patch")
    def test_defaults_to_30s_timeout(self, mock_patch):
        from app.adapters.cloudflare_adapter import update_a_record

        mock_patch.return_value = _cf_response(result={"id": "r1", "content": "100.64.0.2"})

        update_a_record("cf-token", "z1", "r1", "100.64.0.2")
        assert mock_patch.call_args.kwargs["timeout"] == 30

    @patch("app.adapters.cloudflare_adapter.httpx2.patch")
    def test_null_result_returns_empty_dict(self, mock_patch):
        """A present-but-null ``result`` is coerced to {} (never returned as None),
        keeping the documented dict return type intact."""
        from app.adapters.cloudflare_adapter import update_a_record

        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"success": True, "result": None, "errors": []}
        mock_patch.return_value = resp

        result = update_a_record("cf-token", "z1", "r1", "100.64.0.2")
        assert result == {}

    @patch("app.adapters.cloudflare_adapter.httpx2.patch")
    def test_includes_comment_in_body_when_passed(self, mock_patch):
        from app.adapters.cloudflare_adapter import update_a_record

        mock_patch.return_value = _cf_response(result={"id": "r1", "content": "100.64.0.2"})

        update_a_record(
            "cf-token", "z1", "r1", "100.64.0.2", comment="tailbale-managed:svc1"
        )
        body = mock_patch.call_args.kwargs["json"]
        assert body["content"] == "100.64.0.2"
        assert body["comment"] == "tailbale-managed:svc1"

    @patch("app.adapters.cloudflare_adapter.httpx2.patch")
    def test_omits_comment_key_when_not_passed(self, mock_patch):
        from app.adapters.cloudflare_adapter import update_a_record

        mock_patch.return_value = _cf_response(result={"id": "r1", "content": "100.64.0.2"})

        update_a_record("cf-token", "z1", "r1", "100.64.0.2")
        body = mock_patch.call_args.kwargs["json"]
        assert "comment" not in body  # backward compatible: no comment key


class TestDeleteARecord:
    @patch("app.adapters.cloudflare_adapter.httpx2.delete")
    def test_deletes_record(self, mock_delete):
        from app.adapters.cloudflare_adapter import delete_a_record

        mock_delete.return_value = _cf_response(result={"id": "r1"})

        # Should not raise
        delete_a_record("cf-token", "z1", "r1")
        mock_delete.assert_called_once()

    @patch("app.adapters.cloudflare_adapter.httpx2.delete")
    def test_raises_on_failure(self, mock_delete):
        from app.adapters.cloudflare_adapter import delete_a_record

        mock_delete.return_value = _cf_response(
            success=False, errors=[{"message": "Record not found"}]
        )

        with pytest.raises(RuntimeError, match="Record not found"):
            delete_a_record("cf-token", "z1", "bad_id")

    @patch("app.adapters.cloudflare_adapter.httpx2.delete")
    def test_default_timeout_is_30(self, mock_delete):
        from app.adapters.cloudflare_adapter import delete_a_record

        mock_delete.return_value = _cf_response(result={"id": "r1"})
        delete_a_record("cf-token", "z1", "r1")
        assert mock_delete.call_args.kwargs["timeout"] == 30.0

    @patch("app.adapters.cloudflare_adapter.httpx2.delete")
    def test_custom_timeout_is_threaded_through(self, mock_delete):
        from app.adapters.cloudflare_adapter import delete_a_record

        mock_delete.return_value = _cf_response(result={"id": "r1"})
        delete_a_record("cf-token", "z1", "r1", timeout=10.0)
        assert mock_delete.call_args.kwargs["timeout"] == 10.0


class TestCanonicalTimeouts:
    """The CF timeouts are defined ONCE in the adapter and reused everywhere."""

    def test_constants_have_expected_values_and_are_float(self):
        from app.adapters import cloudflare_adapter as cf

        assert cf.CF_DEFAULT_TIMEOUT == 30.0
        assert cf.CF_CLEANUP_TIMEOUT == 10.0
        assert isinstance(cf.CF_DEFAULT_TIMEOUT, float)
        assert isinstance(cf.CF_CLEANUP_TIMEOUT, float)

    def test_all_adapter_defaults_use_canonical_default_timeout(self):
        import inspect

        from app.adapters import cloudflare_adapter as cf

        for fn in (cf.find_record, cf.create_a_record, cf.update_a_record, cf.delete_a_record):
            default = inspect.signature(fn).parameters["timeout"].default
            assert default is cf.CF_DEFAULT_TIMEOUT, fn.__name__
            assert isinstance(default, float), fn.__name__

    def test_cleanup_timeout_is_single_source_of_truth(self):
        from app.adapters import cloudflare_adapter as cf
        from app.adapters import dns_reconciler
        from app.routers import jobs

        # dns_reconciler and jobs must reuse the adapter's constant, not redefine it.
        assert dns_reconciler.CF_CLEANUP_TIMEOUT is cf.CF_CLEANUP_TIMEOUT
        assert jobs.CF_CLEANUP_TIMEOUT is cf.CF_CLEANUP_TIMEOUT
        assert not hasattr(dns_reconciler, "CF_CLEANUP_TIMEOUT_SECONDS")
        assert not hasattr(jobs, "CF_CLEANUP_TIMEOUT_SECONDS")


class TestOwnershipComment:
    def test_marker_format(self):
        from app.adapters.cloudflare_adapter import ownership_comment

        assert ownership_comment("svc123") == "tailbale-managed:svc123"

    def test_marker_within_cloudflare_100_char_limit(self):
        from app.adapters.cloudflare_adapter import ownership_comment

        # Cloudflare caps the comment field at 100 chars; service ids are short, so
        # the marker must stay well under the limit even for an oversized id.
        assert len(ownership_comment("s" * 32)) <= 100


class TestListARecords:
    @patch("app.adapters.cloudflare_adapter.httpx2.get")
    def test_returns_all_matches_including_comment(self, mock_get):
        from app.adapters.cloudflare_adapter import list_a_records

        records = [
            {"id": "r2", "content": "100.64.0.2", "comment": "tailbale-managed:svc"},
            {"id": "r1", "content": "100.64.0.1", "comment": None},
        ]
        mock_get.return_value = _cf_response(result=records)

        result = list_a_records("cf-token", "z1", "dup.example.com")
        # ALL matches returned (not just one), sorted by id for determinism.
        assert [r["id"] for r in result] == ["r1", "r2"]
        # The comment field is preserved, never stripped.
        assert result[1]["comment"] == "tailbale-managed:svc"

    @patch("app.adapters.cloudflare_adapter.httpx2.get")
    def test_returns_empty_list_when_none(self, mock_get):
        from app.adapters.cloudflare_adapter import list_a_records

        mock_get.return_value = _cf_response(result=[])
        assert list_a_records("cf-token", "z1", "none.example.com") == []

    @patch("app.adapters.cloudflare_adapter.httpx2.get")
    def test_requests_larger_per_page(self, mock_get):
        from app.adapters.cloudflare_adapter import CF_FIND_PER_PAGE, list_a_records

        mock_get.return_value = _cf_response(result=[])
        list_a_records("cf-token", "z1", "app.example.com")
        assert mock_get.call_args.kwargs["params"]["per_page"] == CF_FIND_PER_PAGE
