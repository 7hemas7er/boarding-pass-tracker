"""T2 lifecycle (WP2, INTEGRATION_PLAN.md 3.6, C2, C11, 7.3): a reload keeps the queue and the pending
notifications; a reload while the worker is mid-job (no ERROR, one worker after it, CHA-06); a job that outlives the
shutdown gets Closed; a reload before HA started (the old runtime never starts); HA stop closes the database; the
removal keeps the folder and says so (CMS-06); an options reload keeps a 6 h back-off, a new key does not (C11);
a refused database is a setup error with the file untouched."""
import asyncio
import datetime
import hashlib
import json
import logging
import os
import sqlite3
import threading
from unittest.mock import patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED, EVENT_HOMEASSISTANT_STOP
from homeassistant.core import CoreState
from homeassistant.helpers import issue_registry as ir

from pytest_homeassistant_custom_component.common import async_mock_service

from custom_components.boarding_pass_tracker import runtime
from custom_components.boarding_pass_tracker.const import DOMAIN
from custom_components.boarding_pass_tracker.core import api, engine

from bpt_helpers import People, async_setup, bcbp, julian, make_entry, pkpass, wait_round


async def _setup(hass, start=False, mock_notify=True, **kwargs):
    people = await People().async_create(hass, mock_notify=mock_notify)
    entry = await async_setup(hass, make_entry(people.subentries(), **kwargs), start=start)
    return people, entry, getattr(entry, "runtime_data", None)


def _write_bad_db(path):
    os.makedirs(os.path.dirname(path))
    c = sqlite3.connect(path)
    c.execute("CREATE TABLE x (a)")
    c.execute("PRAGMA user_version = 7")
    c.commit()
    c.close()


def _sha(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def _files(folder):
    return sorted(os.listdir(folder)), len([n for _, _, names in os.walk(os.path.join(folder, "file")) for n in names])


def _write(path, text):
    with open(path, "w") as f:
        f.write(text)


def _pass(n=0):
    day = datetime.date.today() + datetime.timedelta(days=2 + n)
    return pkpass(bcbp(day=julian(day), number=str(1234 + n)))


async def _until(hass, predicate, seconds=10.0):
    """Poll (real time, the loop free) until predicate() is true."""
    for _ in range(int(seconds / 0.02)):
        if await predicate():
            return True
        await hass.async_add_executor_job(threading.Event().wait, 0.02)
    return False


async def test_a_reload_keeps_the_queue_and_the_pending_notifications(hass):
    people, entry, rt = await _setup(hass, mock_notify=False)
    alice = rt.person_for(people.users["alice"].id)
    assert (await rt.run(api.upload, None, _pass(0), alice))[0] == 200
    await rt.async_drain()                                  # read; notification pending (no notify service)
    assert (await rt.run(api.upload, None, _pass(1), alice))[0] == 200          # queued
    hass.set_state(CoreState.starting)              # no worker after the reload: the test drains (one consumer)
    try:
        await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
    finally:
        hass.set_state(CoreState.running)
    rt2 = entry.runtime_data
    assert rt2 is not rt and entry.state is ConfigEntryState.LOADED
    rows = await rt2.run(engine.q, "SELECT id, status, notified FROM documents ORDER BY id")
    assert [(r["status"], r["notified"]) for r in rows] == [("letto", 0), ("in_coda", 0)]
    calls = async_mock_service(hass, "notify", "mobile_app_alice")
    await rt2.async_drain()
    rows = await rt2.run(engine.q, "SELECT status, notified, outcome FROM documents ORDER BY id")
    assert [(r["status"], r["notified"]) for r in rows] == [("letto", 1), ("letto", 1)], (rows, calls)
    assert [c.data["data"]["tag"] for c in calls] == ["boarding_pass_tracker-d1", "boarding_pass_tracker-d2"]


async def test_a_reload_while_the_worker_is_mid_job(hass, caplog):
    """CHA-06: the job finishes (it cannot be cancelled), no ERROR is logged, one worker runs afterwards."""
    caplog.set_level(logging.INFO)
    gate, entered = threading.Event(), threading.Event()
    real = engine.read_document

    def held(doc):
        entered.set()
        assert gate.wait(30)
        return real(doc)
    with patch.object(engine, "read_document", held):
        people, entry, rt = await _setup(hass, start=True)
        await wait_round(rt)
        alice = rt.person_for(people.users["alice"].id)
        assert (await rt.run(api.upload, None, _pass(0), alice))[0] == 200
        assert await hass.async_add_executor_job(entered.wait, 10)            # the worker is reading it
        reload = hass.async_create_task(hass.config_entries.async_reload(entry.entry_id))
        assert await _until(hass, lambda: asyncio.sleep(0, engine._closing))   # unload waits in shutdown
        assert not reload.done()
        gate.set()
        assert await reload
        await hass.async_block_till_done()
        rt2 = entry.runtime_data
        assert rt2 is not rt and entry.state is ConfigEntryState.LOADED
        assert rt._worker_task.done() and rt2._worker_task is not None and not rt2._worker_task.done()
        rows = await rt2.run(engine.q, "SELECT status, attempts FROM documents")
        assert [(r["status"], r["attempts"]) for r in rows] == [("letto", 1)]
        assert await hass.config_entries.async_unload(entry.entry_id)
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert errors == [], [r.getMessage() for r in errors]


async def test_a_job_that_outlives_the_shutdown_gets_closed(hass, caplog):
    """C2: a slow job (a decoder read can last 120 s) never writes into the next setup's database."""
    gate, entered, seen = threading.Event(), threading.Event(), []
    real = engine.read_document

    def held(doc):
        if not entered.is_set():
            entered.set()
            assert gate.wait(30)
            try:
                return real(doc)
            except engine.Closed as e:
                seen.append(e)
                raise
        return real(doc)
    with patch.object(engine, "read_document", held), patch.object(runtime, "SHUTDOWN_TIMEOUT_S", 0.2):
        people, entry, rt = await _setup(hass, start=True)
        await wait_round(rt)
        alice = rt.person_for(people.users["alice"].id)
        assert (await rt.run(api.upload, None, _pass(0), alice))[0] == 200
        assert await hass.async_add_executor_job(entered.wait, 10)
        await hass.config_entries.async_reload(entry.entry_id)          # shutdown gives up after 0.2 s
        await hass.async_block_till_done()
        rt2 = entry.runtime_data
        assert entry.state is ConfigEntryState.LOADED and rt2 is not rt
        gate.set()                                                       # the old job goes on: Closed at db()
        assert await _until(hass, lambda: asyncio.sleep(0, bool(seen)))
        assert isinstance(seen[0], engine.Closed)

        async def read():
            rows = await rt2.run(engine.q, "SELECT status FROM documents")
            return rows[0]["status"] == "letto"
        assert await _until(hass, read)                                  # the new worker read it
        rows = await rt2.run(engine.q, "SELECT attempts FROM documents")
        assert rows[0]["attempts"] == 2                                  # the cut read counted (today's semantics)
        assert await hass.config_entries.async_unload(entry.entry_id)
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


async def test_a_reload_before_ha_started(hass):
    """The async_at_started callback of the old runtime never fires: one worker and one clock afterwards."""
    people = await People().async_create(hass)
    entry = make_entry(people.subentries())
    entry.add_to_hass(hass)
    hass.set_state(CoreState.starting)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    old = entry.runtime_data
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    new = entry.runtime_data
    assert new is not old
    hass.set_state(CoreState.running)
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    await hass.async_block_till_done()
    await wait_round(new)
    assert old._worker_task is None and old._round_task is None and not old._started
    assert new._started and new._worker_task is not None and not new._worker_task.done()
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_ha_stop_closes_the_database(hass):
    people, entry, rt = await _setup(hass, start=True)
    await wait_round(rt)
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STOP)
    await hass.async_block_till_done()
    assert rt.stopping and engine._db is None and engine._closed
    assert rt._worker_task.done()
    assert entry.state is ConfigEntryState.LOADED                       # HA stop does not unload the entries
    assert engine._ha is None and engine._sensors is None
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_removal_keeps_the_folder_and_says_so(hass, caplog):
    people, entry, rt = await _setup(hass)
    alice = rt.person_for(people.users["alice"].id)
    assert (await rt.run(api.upload, None, _pass(0), alice))[0] == 200
    folder = hass.config.path("boarding_pass_tracker")
    ir.async_create_issue(hass, DOMAIN, "decoder_unreachable", is_fixable=False, severity=ir.IssueSeverity.WARNING,
                          translation_key="decoder_unreachable")
    assert await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    names, files = await hass.async_add_executor_job(_files, folder)
    assert "tracker.db" in names and files == 1
    from homeassistant.components import persistent_notification  # noqa: PLC0415
    notes = persistent_notification._async_get_or_create_notifications(hass)
    note = notes[f"{DOMAIN}_removed"]
    assert note["title"] == "Boarding Pass Tracker"
    assert note["message"].startswith(f"Boarding Pass Tracker è stato rimosso, ma i suoi dati restano in {folder} (")
    assert "foto e carte d'imbarco con i nomi completi" in note["message"]
    assert "I backup di Home Assistant fatti finora li contengono ancora." in note["message"]
    assert f"its data folder stays: {folder}" in caplog.text
    assert [i for (d, i) in ir.async_get(hass).issues if d == DOMAIN] == []


async def test_an_options_reload_keeps_a_6_h_back_off_and_a_new_key_does_not(hass):
    """C11 / IBC23: reschedule_checks runs at the first setup of the HA run and after a key change, not on an
    options reload."""
    people, entry, rt = await _setup(hass, data={"aviationstack_key": "key-one"})
    user = rt.person_for(people.users["alice"].id)
    day = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()
    add = json.dumps({"action": "add", "flight": "FR 1234", "date": day, "origin": "BLQ", "destination": "STN",
                      "passengers": "Alice"}).encode()
    assert (await rt.run(engine.change_flight, add, user, rt.cfg()))[0] == 200
    now = datetime.datetime.now(datetime.UTC)
    back_off = engine.utc(now + datetime.timedelta(hours=6))
    await rt.run(engine.q, "UPDATE flights SET updated_at=?, next_check=?, error=? WHERE id=1",
                 (engine.now_iso(), back_off, "AviationStack 429: rate limit"))     # checked once, then a 429
    hass.config_entries.async_update_entry(entry, options={**entry.options, "monthly_cap": 50})
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    rt = entry.runtime_data
    assert rt.settings["max_api_calls_per_month"] == 50
    assert (await rt.run(engine.q, "SELECT next_check FROM flights"))[0]["next_check"] == back_off
    hass.config_entries.async_update_entry(entry, data={"aviationstack_key": "key-two"})
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    rt = entry.runtime_data
    assert (await rt.run(engine.q, "SELECT next_check FROM flights"))[0]["next_check"] != back_off
    assert hass.data[DOMAIN].rescheduled_for == runtime.key_digest("key-two")


async def test_a_refused_database_is_a_setup_error_and_stays_untouched(hass):
    folder = hass.config.path("boarding_pass_tracker")
    path = os.path.join(folder, "tracker.db")
    await hass.async_add_executor_job(_write_bad_db, path)               # test file I/O off the loop too (R-A guard)
    digest = await hass.async_add_executor_job(_sha, path)
    people, entry, rt = await _setup(hass)
    assert entry.state is ConfigEntryState.SETUP_ERROR
    assert entry.error_reason_translation_key == "newer_database"
    assert await hass.async_add_executor_job(_sha, path) == digest
    assert (await hass.async_add_executor_job(_files, folder))[0] == ["tracker.db"]   # no -wal, nothing written
    assert engine._ha is None and engine._sensors is None


async def test_a_data_folder_that_cannot_be_used_is_not_ready(hass):
    await hass.async_add_executor_job(_write, hass.config.path("boarding_pass_tracker"), "x")   # a file, not a folder
    people, entry, rt = await _setup(hass)
    assert entry.state is ConfigEntryState.SETUP_RETRY
    assert engine._ha is None


def _write_garbage(path):
    os.makedirs(os.path.dirname(path))
    with open(path, "wb") as f:
        f.write(b"not a database" * 100)


async def test_a_broken_database_is_a_setup_error(hass):
    path = os.path.join(hass.config.path("boarding_pass_tracker"), "tracker.db")
    await hass.async_add_executor_job(_write_garbage, path)
    digest = await hass.async_add_executor_job(_sha, path)
    people, entry, rt = await _setup(hass)
    assert entry.state is ConfigEntryState.SETUP_ERROR
    assert await hass.async_add_executor_job(_sha, path) == digest
    assert engine._ha is None


def _db_state(path):
    return _sha(path), (os.path.getsize(path + "-wal") if os.path.exists(path + "-wal") else None)


async def test_a_setup_that_fails_after_the_database_opened_closes_it(hass):
    """PARITY-06 (C12): a failure after the database opened (here reschedule_checks writes, then the database is
    «locked») is ConfigEntryNotReady, and the engine is shut down at once, as an unload: during a backup its close
    skips the checkpoint, so tracker.db and the -wal stay as the backup is copying them. Before, the connection was
    left open and the retry's configure() closed it with a checkpoint."""
    people, entry, rt = await _setup(hass)
    assert await hass.config_entries.async_unload(entry.entry_id)
    path = os.path.join(hass.config.path("boarding_pass_tracker"), "tracker.db")
    before = await hass.async_add_executor_job(_db_state, path)
    assert before[1] is None                                       # closed by the unload: no -wal
    hass.data[DOMAIN].rescheduled_for = None                       # the next setup reschedules
    engine._backup_active = True                                   # a backup of HA is running

    def failing(now=None):
        engine.q("INSERT INTO api_usage (month, calls) VALUES ('2026-11', 1)")    # a write, then the failure
        raise sqlite3.OperationalError("database is locked")
    with patch.object(engine, "reschedule_checks", failing):
        assert not await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_RETRY
    assert engine._db is None and engine._closed is True              # shut down, not left open for the retry
    after = await hass.async_add_executor_job(_db_state, path)
    assert after[0] == before[0] and after[1] > 0                   # no checkpoint: the write only in the -wal
    engine._backup_active = False


def _modes(folder):
    import stat  # noqa: PLC0415
    names = ("", "tracker.db", "tracker.db-wal", "tracker.db-shm")
    return {n or ".": stat.S_IMODE(os.lstat(os.path.join(folder, n)).st_mode) for n in names
            if os.path.lexists(os.path.join(folder, n))}


def _loosen(folder):
    os.chmod(folder, 0o755)
    for n in ("tracker.db", "tracker.db-wal", "tracker.db-shm"):
        if os.path.exists(os.path.join(folder, n)):
            os.chmod(os.path.join(folder, n), 0o644)


async def test_the_data_folder_and_the_database_are_private_under_has_umask(hass):
    """PLATFORM-02 / PARITY-01 / SECURITY-01 (plan 4.1, L3 E8): under HA's umask 022 the folder is 0700 and
    tracker.db, -wal and -shm 0600; a folder and files left 0755/0644 (by hand, by a copy) are made private at the
    next setup."""
    old = os.umask(0o022)
    try:
        people, entry, rt = await _setup(hass)
        alice = rt.person_for(people.users["alice"].id)
        assert (await rt.run(api.upload, None, _pass(), alice))[0] == 200            # a write: -wal and -shm
        folder = hass.config.path("boarding_pass_tracker")
        assert await hass.async_add_executor_job(_modes, folder) == {
            ".": 0o700, "tracker.db": 0o600, "tracker.db-wal": 0o600, "tracker.db-shm": 0o600}
        assert await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_add_executor_job(_loosen, folder)
        assert (await hass.async_add_executor_job(_modes, folder))["."] == 0o755
        hass.set_state(CoreState.starting)                       # as bpt_helpers.async_setup: no worker or clock
        try:
            assert await hass.config_entries.async_setup(entry.entry_id)
            await hass.async_block_till_done()
        finally:
            hass.set_state(CoreState.running)
        rt = entry.runtime_data
        assert (await rt.run(api.upload, None, _pass(1), alice))[0] == 200
        assert await hass.async_add_executor_job(_modes, folder) == {
            ".": 0o700, "tracker.db": 0o600, "tracker.db-wal": 0o600, "tracker.db-shm": 0o600}
    finally:
        os.umask(old)
