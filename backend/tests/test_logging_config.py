"""Tests for backend logging configuration."""

import logging

from app.logging_config import LOG_FORMAT, configure_logging


def test_configure_logging_adds_timestamp_formatter_to_root(monkeypatch):
    root = logging.getLogger()
    original_handlers = list(root.handlers)
    original_level = root.level
    monkeypatch.setattr(root, "handlers", [])

    try:
        configure_logging()

        assert len(root.handlers) == 1
        formatter = root.handlers[0].formatter
        assert formatter is not None
        assert formatter._fmt == LOG_FORMAT
    finally:
        root.handlers = original_handlers
        root.setLevel(original_level)


def test_configure_logging_sets_info_level_so_app_logs_emit(monkeypatch):
    """Without an explicit level the root stays at WARNING and app INFO logs
    are silently dropped. configure_logging must enable INFO by default."""
    monkeypatch.delenv("LOG_LEVEL", raising=False)
    root = logging.getLogger()
    original_level = root.level
    try:
        configure_logging()
        assert root.level == logging.INFO
        assert logging.getLogger("app.anything").isEnabledFor(logging.INFO)
    finally:
        root.setLevel(original_level)


def test_configure_logging_honors_log_level_env(monkeypatch):
    monkeypatch.setenv("LOG_LEVEL", "WARNING")
    root = logging.getLogger()
    original_level = root.level
    try:
        configure_logging()
        assert root.level == logging.WARNING
        assert not logging.getLogger("app.anything").isEnabledFor(logging.INFO)
    finally:
        root.setLevel(original_level)


def test_configure_logging_updates_uvicorn_access_handler_without_propagation():
    logger = logging.getLogger("uvicorn.access")
    original_handlers = list(logger.handlers)
    original_propagate = logger.propagate
    handler = logging.StreamHandler()
    logger.handlers = [handler]
    logger.propagate = True

    try:
        configure_logging()

        assert handler.formatter is not None
        assert handler.formatter._fmt == LOG_FORMAT
        assert logger.propagate is False
    finally:
        logger.handlers = original_handlers
        logger.propagate = original_propagate
