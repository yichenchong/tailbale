"""Tests for the edge image builder."""

from unittest.mock import MagicMock, patch

import docker.errors
import pytest


class TestBuildEdgeImage:
    @patch("app.edge.image_builder._EDGE_CONTEXT")
    @patch("app.edge.image_builder.docker.DockerClient")
    def test_builds_and_returns_id(self, mock_cls, mock_ctx):
        from app.edge.image_builder import _build_edge_image
        from app.version import __version__

        mock_ctx.is_dir.return_value = True
        mock_ctx.__str__ = lambda _: "/app/edge-image"
        mock_client = MagicMock()
        mock_cls.from_env.return_value = mock_client
        mock_image = MagicMock()
        mock_image.id = "sha256:abc123"
        mock_client.images.build.return_value = (mock_image, [])

        result = _build_edge_image()
        assert result == "sha256:abc123"
        mock_client.images.build.assert_called_once_with(
            path="/app/edge-image",
            tag="tailbale-edge:latest",
            rm=True,
            labels={"tailbale.version": __version__},
        )

    @patch("app.edge.image_builder._EDGE_CONTEXT")
    def test_raises_if_context_missing(self, mock_ctx):
        from app.edge.image_builder import _build_edge_image

        mock_ctx.is_dir.return_value = False
        with pytest.raises(RuntimeError, match="build context not found"):
            _build_edge_image()


class TestEnsureEdgeImage:
    @patch("app.edge.image_builder._build_edge_image")
    @patch("app.edge.image_builder.docker.DockerClient")
    def test_skips_build_if_exists_and_current(self, mock_cls, mock_build):
        """If image exists and version label matches, no rebuild."""
        from app.edge.image_builder import ensure_edge_image
        from app.version import __version__

        mock_client = MagicMock()
        mock_cls.from_env.return_value = mock_client
        mock_image = MagicMock()
        mock_image.labels = {"tailbale.version": __version__}
        mock_client.images.get.return_value = mock_image

        ensure_edge_image()
        mock_build.assert_not_called()

    @patch("app.edge.image_builder._build_edge_image")
    @patch("app.edge.image_builder.docker.DockerClient")
    def test_builds_if_missing(self, mock_cls, mock_build):
        """If image does not exist, it should be built."""
        from app.edge.image_builder import ensure_edge_image

        mock_client = MagicMock()
        mock_cls.from_env.return_value = mock_client
        mock_client.images.get.side_effect = docker.errors.ImageNotFound("not found")

        ensure_edge_image()
        mock_build.assert_called_once()

    @patch("app.edge.image_builder._build_edge_image")
    @patch("app.edge.image_builder.docker.DockerClient")
    def test_rebuilds_if_version_mismatch(self, mock_cls, mock_build):
        """If image exists but version label is outdated, rebuild."""
        from app.edge.image_builder import ensure_edge_image

        mock_client = MagicMock()
        mock_cls.from_env.return_value = mock_client
        mock_image = MagicMock()
        mock_image.labels = {"tailbale.version": "0.0.1-old"}
        mock_client.images.get.return_value = mock_image

        mock_build.return_value = "sha256:new"
        mock_image.id = "sha256:old"
        ensure_edge_image()
        mock_build.assert_called_once()
        mock_client.images.remove.assert_called_once_with(image="sha256:old", force=True)

    @patch("app.edge.image_builder._build_edge_image")
    @patch("app.edge.image_builder.docker.DockerClient")
    def test_rebuilds_if_no_version_label(self, mock_cls, mock_build):
        """If image exists but has no version label, rebuild."""
        from app.edge.image_builder import ensure_edge_image

        mock_client = MagicMock()
        mock_cls.from_env.return_value = mock_client
        mock_image = MagicMock()
        mock_image.labels = {}
        mock_client.images.get.return_value = mock_image

        mock_build.return_value = "sha256:new"
        mock_image.id = "sha256:old"
        ensure_edge_image()
        mock_build.assert_called_once()
        mock_client.images.remove.assert_called_once_with(image="sha256:old", force=True)

    @patch("app.edge.image_builder._build_edge_image")
    @patch("app.edge.image_builder.docker.DockerClient")
    def test_no_removal_when_rebuilt_id_matches_previous(self, mock_cls, mock_build):
        """Defensive guard: when a rebuild yields the same image id as the
        existing one, the freshly-built image must NOT be removed (otherwise we
        would delete the very image we just produced)."""
        from app.edge.image_builder import ensure_edge_image

        mock_client = MagicMock()
        mock_cls.from_env.return_value = mock_client
        mock_image = MagicMock()
        mock_image.labels = {"tailbale.version": "0.0.1-old"}
        mock_image.id = "sha256:same"
        mock_client.images.get.return_value = mock_image

        mock_build.return_value = "sha256:same"
        ensure_edge_image()

        mock_build.assert_called_once()
        mock_client.images.remove.assert_not_called()


class TestBuildEdgeImagePrivacy:
    def test_no_public_build_edge_image_symbol(self):
        """The lock-free builder must not be exposed under a public name."""
        import app.edge.image_builder as mod

        assert not hasattr(mod, "build_edge_image")
        assert hasattr(mod, "_build_edge_image")
