"""Fixtures of the integration's tests (T2, INTEGRATION_PLAN.md 7.1, 7.3).

- The repository root goes first on sys.path and `custom_components` is imported before Home Assistant puts the
  plugin's testing_config first (else «Integration not found»).
- Loop protection (R-A): homeassistant.block_async_io is enabled (time.sleep and urllib in the loop raise), and every
  test runs under two guards of our own, asserted after hass's teardown (so the unload it does is guarded too):
    * the sqlite guard: sqlite3.connect is wrapped; a connect on the loop thread is recorded, and every connection
      to a file under the data folder gets a trace callback that records each statement run on the loop thread
      (sqlite3 is not covered by HA's detector, and Connection.execute cannot be patched);
    * the open guard: builtins.open on the loop thread is recorded;
  plus no «Detected blocking call», no «thread other than the event loop» and no «Error unloading entry» in the
  log. «On the loop» = the
  thread has a running asyncio loop (the executor's threads have none). test_guards.py shows they are not vacuous.
- `verify_cleanup` (the plugin's, autouse) fails a test that leaves tasks, timers or threads.
- Fictional people only: alice and bob.
"""
import asyncio
import builtins
import os
import sqlite3
import sys
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
sys.modules.pop("custom_components", None)
import custom_components  # noqa: E402,F401  (cache the repo's package before HA's testing_config shadows it)

import pytest  # noqa: E402

from homeassistant import block_async_io  # noqa: E402

pytest_plugins = ["pytest_homeassistant_custom_component"]

block_async_io.enable()             # the plugin's hass fixture does not enable it

from custom_components.boarding_pass_tracker.core import barcodes, engine  # noqa: E402

LOG_FORBIDDEN = ("Detected blocking call", "thread other than the event loop", "from a thread other than",
                 "Error unloading entry")   # HA's line for an exception in async_unload_entry (also at teardown)


class LoopGuard:
    """What ran on an event loop thread (the thread with a running asyncio loop: the test's loop, hass.loop)."""

    def __init__(self):
        self.sql: list[tuple[str, str]] = []
        self.opens: list[tuple[str, str]] = []

    @staticmethod
    def on_loop() -> bool:
        return asyncio._get_running_loop() is not None


def _ours(target: str) -> bool:
    """A file of the integration's data folder (<config>/boarding_pass_tracker/...)."""
    try:
        return f"{os.sep}boarding_pass_tracker{os.sep}" in os.path.realpath(target)
    except (TypeError, ValueError):
        return False


@pytest.fixture(autouse=True)
def loop_guard(caplog):
    """The R-A guards (see the module docstring). Set up before `hass` and torn down after it (the autouse fixture
    that brings hass, auto_enable_custom_integrations, requests this one first; pytest orders the autouse fixtures
    of a conftest by name, not by definition), so the unload at hass's teardown is guarded too."""
    guard = LoopGuard()
    real_connect, real_open = sqlite3.connect, builtins.open

    def connect(*args, **kwargs):
        target = str(args[0] if args else kwargs.get("database", ""))
        on_loop = guard.on_loop()
        if on_loop:
            guard.sql.append(("connect", target))
        conn = real_connect(*args, **kwargs)
        if on_loop or _ours(target):
            def trace(statement, _guard=guard):
                if _guard.on_loop():
                    _guard.sql.append(("sql", str(statement)[:120]))
            conn.set_trace_callback(trace)
        return conn

    def guarded_open(*args, **kwargs):
        if guard.on_loop():
            guard.opens.append((str(args[0] if args else kwargs.get("file")),
                                "".join(traceback.format_stack(limit=8)[:-1])))
        return real_open(*args, **kwargs)

    sqlite3.connect = connect
    builtins.open = guarded_open
    try:
        yield guard
    finally:
        sqlite3.connect = real_connect
        builtins.open = real_open
    problems = []
    if guard.sql:
        problems.append(f"sqlite on the event loop: {guard.sql[:5]}")
    if guard.opens:
        problems.append(f"open() on the event loop: {[f for f, _ in guard.opens[:5]]}\n{guard.opens[0][1]}")
    records = caplog.get_records("setup") + caplog.get_records("call") + caplog.get_records("teardown")
    bad = [r.getMessage()[:300] for r in records if any(s in r.getMessage() for s in LOG_FORBIDDEN)]
    if bad:
        problems.append(f"forbidden log lines: {bad[:3]}")
    if problems:
        pytest.fail("R-A guard: " + "; ".join(problems))


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(loop_guard, enable_custom_integrations):
    """loop_guard first: it must be set up before hass (which enable_custom_integrations brings) to see its
    teardown."""
    yield


@pytest.fixture
def hass_config_dir(hass_tmp_config_dir):
    """A temporary config dir per test: the data folder is <config>/boarding_pass_tracker."""
    return hass_tmp_config_dir


@pytest.fixture(autouse=True)
def engine_globals():
    """The engine is a module with globals (one runtime per process): each test starts clean. Reset before the test,
    when the previous test's hass (and its unload) is gone and no executor thread is left (verify_cleanup)."""
    if engine._db is not None:
        try:
            engine._db.close()
        except sqlite3.Error:
            pass
        engine._db = None
    engine._backup_active = False
    engine._ha = None
    engine._sensors = None
    engine._on_auth_error = engine._no_auth_hook
    engine._fr24_forget.clear()
    engine._fr24_attempts.clear()
    barcodes.configure(None)
    yield
