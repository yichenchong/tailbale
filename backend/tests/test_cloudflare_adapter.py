"""Tests for the Cloudflare API v4 adapter."""

from unittest.mock import MagicMock, patch

import pytest


def _cf_response(success=True, result=None, errors=None):
    """Create a mock httpx response matching Cloudflare API format."""
    mock = MagicMock()
    mock.json.return_value = {
        "success": success,
        "result": result or {},
        "errors": errors or [],
    }
    return mock


class TestListZones:
    @patch("app.adapters.cloudflare_adapter.httpx.get")
    def test_returns_zones(self, mock_get):
        from app.adapters.cloudflare_adapter import list_zones

        zones = [{"id": "z1", "name": "example.com"}, {"id": "z2", "name": "test.com"}]
        mock_get.return_value = _cf_response(result=zones)

        result = list_zones("cf-token")
        assert len(result) == 2
        assert result[0]["name"] == "example.com"

        # Verify auth header
        call_kwargs = mock_get.call_args
        assert "Bearer cf-token" in call_kwargs.kwargs["headers"]["Authorization"]

    @patch("app.adapters.cloudflare_adapter.httpx.get")
    def test_raises_on_error(self, mock_get):
        from app.adapters.cloudflare_adapter import list_zones

        mock_get.return_value = _cf_response(
            success=False, errors=[{"message": "Invalid token"}]
        )

        with pytest.raises(RuntimeError, match="Invalid token"):
            list_zones("bad-token")


class TestGetZone:
    @patch("app.adapters.cloudflare_adapter.httpx.get")
    def test_returns_zone(self, mock_get):
        from app.adapters.cloudflare_adapter import get_zone

        zone = {"id": "z1", "name": "example.com", "status": "active"}
        mock_get.return_value = _cf_response(result=zone)

        result = get_zone("cf-token", "z1")
        assert result["name"] == "example.com"

    @patch("app.adapters.cloudflare_adapter.httpx.get")
    def test_raises_on_not_found(self, mock_get):
        from app.adapters.cloudflare_adapter import get_zone

        mock_get.return_value = _cf_response(
            success=False, errors=[{"message": "Zone not found"}]
        )

        with pytest.raises(RuntimeError, match="Zone not found"):
            get_zone("cf-token", "nonexistent")


class TestFindRecord:
    @patch("app.adapters.cloudflare_adapter.httpx.get")
    def test_finds_existing_record(self, mock_get):
        from app.adapters.cloudflare_adapter import find_record

        record = {"id": "r1", "type": "A", "name": "app.example.com", "content": "100.64.0.1"}
        mock_get.return_value = _cf_response(result=[record])

        result = find_record("cf-token", "z1", "app.example.com")
        assert result is not None
        assert result["id"] == "r1"
        assert result["content"] == "100.64.0.1"

    @patch("app.adapters.cloudflare_adapter.httpx.get")
    def test_returns_none_when_not_found(self, mock_get):
        from app.adapters.cloudflare_adapter import find_record

        mock_get.return_value = _cf_response(result=[])

        result = find_record("cf-token", "z1", "nonexistent.example.com")
        assert result is None

    @patch("app.adapters.cloudflare_adapter.httpx.get")
    def test_passes_type_and_name_params(self, mock_get):
        from app.adapters.cloudflare_adapter import find_record

        mock_get.return_value = _cf_response(result=[])

        find_record("cf-token", "z1", "app.example.com", "CNAME")
        call_kwargs = mock_get.call_args
        assert call_kwargs.kwargs["params"]["type"] == "CNAME"
        assert call_kwargs.kwargs["params"]["name"] == "app.example.com"


class TestCreateARecord:
    @patch("app.adapters.cloudflare_adapter.httpx.post")
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

    @patch("app.adapters.cloudflare_adapter.httpx.post")
    def test_raises_on_failure(self, mock_post):
        from app.adapters.cloudflare_adapter import create_a_record

        mock_post.return_value = _cf_response(
            success=False, errors=[{"message": "Record already exists"}]
        )

        with pytest.raises(RuntimeError, match="Record already exists"):
            create_a_record("cf-token", "z1", "dup.example.com", "100.64.0.1")


class TestUpdateARecord:
    @patch("app.adapters.cloudflare_adapter.httpx.patch")
    def test_updates_record(self, mock_patch):
        from app.adapters.cloudflare_adapter import update_a_record

        updated = {"id": "r1", "content": "100.64.0.2"}
        mock_patch.return_value = _cf_response(result=updated)

        result = update_a_record("cf-token", "z1", "r1", "100.64.0.2")
        assert result["content"] == "100.64.0.2"

        call_kwargs = mock_patch.call_args
        assert call_kwargs.kwargs["json"]["content"] == "100.64.0.2"
        assert "r1" in call_kwargs.args[0]  # record_id in URL


class TestDeleteARecord:
    @patch("app.adapters.cloudflare_adapter.httpx.delete")
    def test_deletes_record(self, mock_delete):
        from app.adapters.cloudflare_adapter import delete_a_record

        mock_delete.return_value = _cf_response(result={"id": "r1"})

        # Should not raise
        delete_a_record("cf-token", "z1", "r1")
        mock_delete.assert_called_once()

    @patch("app.adapters.cloudflare_adapter.httpx.delete")
    def test_raises_on_failure(self, mock_delete):
        from app.adapters.cloudflare_adapter import delete_a_record

        mock_delete.return_value = _cf_response(
            success=False, errors=[{"message": "Record not found"}]
        )

        with pytest.raises(RuntimeError, match="Record not found"):
            delete_a_record("cf-token", "z1", "bad_id")
