"""Structural regression guard: every persisted setting key must be read by
at least one production code path outside the settings API layer itself.

Motivated by bughunt round 11 (TS1/TS2): ``ts_default_hostname_prefix`` and
``ts_control_url`` were both persisted in ``settings_store.DEFAULTS``, exposed
through the settings API and the Settings UI, and documented as controlling
edge Tailscale behavior — but no production code path outside the settings
API actually read either value back. Changing them in the UI silently did
nothing. That gap was invisible to normal test coverage because the settings
API round-trip (write -> read back the same value) passed fine; only a
*production consumer* check catches it.

This test statically scans ``backend/app`` for direct settings-store reads
(``get_setting``/``get_positive_int_setting``/``db.get(Setting, ...)``) and
asserts every key in ``settings_store.DEFAULTS`` is read by at least one file
outside the settings API layer (``settings_store.py``, ``routers/settings.py``,
``schemas/settings.py``) — or falls into one of the explicit, verified
exception categories below.

A new setting added to ``DEFAULTS`` without a real production reader will
fail this test, forcing the author to either wire it up or explicitly
document (and verify) why it doesn't need a direct one.
"""

from __future__ import annotations

import ast
from pathlib import Path

from app import settings_store

APP_DIR = Path(__file__).resolve().parent.parent / "app"

# Files that define/expose settings but must NOT count as "production
# consumers" — a key referenced only here is persisted/exposed but unused.
_SETTINGS_API_FILES = {
    APP_DIR / "settings_store.py",
    APP_DIR / "routers" / "settings.py",
    APP_DIR / "schemas" / "settings.py",
}

# generated_root/cert_root/tailscale_state_root are read directly ONLY inside
# settings_store.get_runtime_paths(); every real consumer calls
# get_runtime_paths() and reads its returned dict, never the raw setting key.
# Verified below: satisfied only if get_runtime_paths is CALLED from outside
# the settings-API files.
_RUNTIME_PATH_KEYS = {"generated_root", "cert_root", "tailscale_state_root"}

# timezone is consumed by the FRONTEND (frontend/src/lib/useTimezone.ts reads
# GET /settings general.timezone to format every displayed date), not by any
# backend Python code path. Documented exception, not a gap — no backend
# static check is possible for a frontend consumer.
_FRONTEND_ONLY_KEYS = {"timezone"}

# reconcile_interval_seconds / health_check_interval_seconds are read via
# `get_positive_int_setting(db, interval_setting)` inside
# reconciler/reconcile_loop.py's `_periodic_loop`, where `interval_setting`
# is a PARAMETER, not a string literal at the read call site — the plain
# literal-argument scan below cannot see through that indirection. Verified
# below instead: satisfied only if the literal key name is passed as the
# `interval_setting=` keyword argument somewhere outside the settings API
# (i.e. at a `_periodic_loop(..., interval_setting="...")` call site).
_INDIRECT_CONSUMER_KEYS = {
    "reconcile_interval_seconds": "interval_setting",
    "health_check_interval_seconds": "interval_setting",
}


def _iter_py_files():
    for path in APP_DIR.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        yield path


def _string_args(call: ast.Call) -> list[str]:
    return [
        a.value
        for a in call.args
        if isinstance(a, ast.Constant) and isinstance(a.value, str)
    ]


def _string_keywords(call: ast.Call) -> dict[str, str]:
    return {
        kw.arg: kw.value.value
        for kw in call.keywords
        if kw.arg is not None and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str)
    }


def _call_name(node: ast.Call) -> str | None:
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return None


def _is_db_get_setting_call(node: ast.Call) -> bool:
    """Matches ``db.get(Setting, "key")`` (any receiver named ``db``)."""
    func = node.func
    if not (isinstance(func, ast.Attribute) and func.attr == "get"):
        return False
    if not (isinstance(func.value, ast.Name) and func.value.id == "db"):
        return False
    return bool(node.args) and isinstance(node.args[0], ast.Name) and node.args[0].id == "Setting"


def _collect_setting_key_readers() -> dict[str, set[Path]]:
    """Map each settings-store key to the files that read it directly."""
    readers: dict[str, set[Path]] = {}
    for path in _iter_py_files():
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = _call_name(node)
            if name in ("get_setting", "get_positive_int_setting") or _is_db_get_setting_call(node):
                for key in _string_args(node):
                    readers.setdefault(key, set()).add(path)
    return readers


def _runtime_paths_called_outside_settings_api() -> bool:
    for path in _iter_py_files():
        if path in _SETTINGS_API_FILES:
            continue
        if "get_runtime_paths(" in path.read_text():
            return True
    return False


def _keyword_literal_used_outside_settings_api(keyword: str, value: str) -> bool:
    """True if some call outside the settings API passes ``keyword=value``
    (a string literal) — e.g. ``_periodic_loop(interval_setting="...")``."""
    for path in _iter_py_files():
        if path in _SETTINGS_API_FILES:
            continue
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and _string_keywords(node).get(keyword) == value:
                return True
    return False


class TestEverySettingHasAProductionConsumer:
    """Regression guard for bughunt round 11 (TS1/TS2)."""

    def test_every_default_setting_key_is_consumed_outside_the_settings_api(self):
        readers = _collect_setting_key_readers()
        runtime_paths_wired = _runtime_paths_called_outside_settings_api()

        unconsumed = []
        for key in settings_store.DEFAULTS:
            if key in _FRONTEND_ONLY_KEYS:
                continue
            if key in _RUNTIME_PATH_KEYS:
                if not runtime_paths_wired:
                    unconsumed.append(key)
                continue
            if key in _INDIRECT_CONSUMER_KEYS:
                if not _keyword_literal_used_outside_settings_api(_INDIRECT_CONSUMER_KEYS[key], key):
                    unconsumed.append(key)
                continue
            outside = readers.get(key, set()) - _SETTINGS_API_FILES
            if not outside:
                unconsumed.append(key)

        assert not unconsumed, (
            f"Setting(s) {unconsumed} are persisted in settings_store.DEFAULTS "
            "and exposed via the settings API, but no production code path "
            "outside the settings API (settings_store.py / routers/settings.py "
            "/ schemas/settings.py) reads them back — the exact TS1/TS2 class "
            "of bug from bughunt round 11 (a UI-editable setting that silently "
            "does nothing). Wire the setting into a real consumer, or add it "
            "to an explicit exception category in this test with a citation."
        )

    def test_no_stale_or_typoed_exception_keys(self):
        """Every exception-set entry must still be a real DEFAULTS key, so a
        future DEFAULTS rename can't leave a dangling, silently-unchecked
        exception behind."""
        known = set(settings_store.DEFAULTS)
        stale = (_FRONTEND_ONLY_KEYS | _RUNTIME_PATH_KEYS | set(_INDIRECT_CONSUMER_KEYS)) - known
        assert not stale, f"Exception key(s) {stale} no longer exist in settings_store.DEFAULTS"
