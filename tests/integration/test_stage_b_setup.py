"""T2 stage B (INTEGRATION_PLAN.md 4.4, 4.6, C12; FIX-repo-w2r for W2-STAGEB-07 and W2-PLATFORM-02/-03/-04/-06): the
v1 -> v2 upgrade at the first async_setup_entry, through the real shell.

- A v1 data folder is upgraded once, with its copy in backups/; a reload changes nothing.
- The shell's mapping: MigrationFailed is a setup error with translation key migration_failed (no retry); an
  OSError while upgrading is ConfigEntryNotReady (retried, the copy of the first try reused, its strerror in the
  reason); a v1 file that fails its quick_check is a setup error (no retry, no copy).
- A backup of HA that began before the setup: the upgrade waits (EBUSY, SETUP_RETRY) for its end; with no
  post-backup the domain's safety timer ends the backup state after 30 minutes and a retry upgrades; a setup that
  succeeds takes the timer over, an unload during a backup hands it back.
- A backup that begins while the setup upgrades waits for the open, then holds the new connection
  (wal_autocheckpoint 0).

Fictional data only (one typed flight FR 3456 BLQ -> STN, passenger Alice; the bpt_helpers users)."""
import asyncio
import datetime
import hashlib
import os
import sqlite3
import threading
from unittest.mock import patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.setup import async_setup_component

from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.boarding_pass_tracker import backup
from custom_components.boarding_pass_tracker.const import DOMAIN
from custom_components.boarding_pass_tracker.core import engine

from bpt_helpers import People, async_reload, async_setup, make_entry

V2_LINES = ("  pass_arrival TEXT,              -- v2: ISO instant with the destination's offset: typed or pass "
            "semantics\n  pass_terminal TEXT,             -- v2: departure terminal: typed or pass semantics "
            "(departureTerminal)\n")


def _v1_folder(folder, rows=1, damaged=False):
    """A v1 file as the standalone leaves it (the core's SCHEMA without the two v2 columns): `rows` typed flights,
    Alice on the first. `damaged`: its last page (a flights leaf) overwritten, page 1 and sqlite_master intact, so
    open_db's read-only checks pass. Returns (path, the first 12 hex of its sha256)."""
    os.makedirs(folder, mode=0o700, exist_ok=True)
    path = os.path.join(folder, "tracker.db")
    c = sqlite3.connect(path, isolation_level=None)
    c.executescript(engine.SCHEMA.replace(V2_LINES, ""))
    c.execute("PRAGMA user_version = 1")
    c.execute(f"PRAGMA application_id = {engine.APPLICATION_ID}")
    if not damaged:
        c.execute("PRAGMA journal_mode=WAL")
    for i in range(rows):
        c.execute("INSERT INTO flights (airline, number, date, origin, destination, snapshot, created_at) VALUES "
                  "('FR', ?, '2026-12-20', 'BLQ', 'STN', ?, '2026-11-01T10:00:00+01:00')",
                  (str(3456 + i), "{}" + " " * (200 if damaged else 0)))
    c.execute("INSERT INTO boarding_passes (document_id, flight_id, passenger, first_name, uploaded_by) VALUES "
              "(NULL, 1, 'Alice', 'Alice', 'alice')")
    c.close()
    if damaged:
        size = os.path.getsize(path)
        with open(path, "r+b") as f:
            f.seek(size - 4096 + 8)
            f.write(b"\xff" * 200)
    with open(path, "rb") as f:
        return path, hashlib.sha256(f.read()).hexdigest()[:12]


def _state(path):
    """(user_version, the last two columns of flights, flights rows) of the file, read with plain sqlite3."""
    c = sqlite3.connect(path)
    try:
        cols = [r[1] for r in c.execute("PRAGMA table_info(flights)")]
        return c.execute("PRAGMA user_version").fetchone()[0], cols[-2:], c.execute(
            "SELECT count(*) FROM flights").fetchone()[0]
    finally:
        c.close()


def _version(path):
    c = sqlite3.connect(path)
    try:
        return c.execute("PRAGMA user_version").fetchone()[0]
    finally:
        c.close()


def _backups(folder):
    """Every name in backups/ (a partial copy too)."""
    b = os.path.join(folder, "backups")
    return sorted(os.listdir(b)) if os.path.isdir(b) else []


async def _entry(hass):
    people = await People().async_create(hass)
    return make_entry(people.subentries())


async def _component(hass):
    """The integration set up with no entry yet (async_setup: the domain data), as HA has it when its backup platform
    is called: HA loads the platform only for an integration it set up."""
    assert await async_setup_component(hass, DOMAIN, {})
    await hass.async_block_till_done()


async def _tick(hass, freezer, minutes):
    freezer.tick(datetime.timedelta(minutes=minutes))
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)


async def _settle(hass, entry):
    """Real time, the loop free, until a setup in progress is over (HA's retry runs as a background task; once the
    entry is loaded its worker is one too, so wait_background_tasks would wait for ever)."""
    for _ in range(500):
        await hass.async_block_till_done()
        if entry.state is not ConfigEntryState.SETUP_IN_PROGRESS and not entry.setup_lock.locked():
            return
        await hass.async_add_executor_job(threading.Event().wait, 0.02)
    raise AssertionError("the setup did not end")


# ---------------------------------------------------------------- the upgrade and the shell's mapping (4.4)
async def test_the_first_setup_upgrades_once(hass):
    folder = hass.config.path("boarding_pass_tracker")
    path, src12 = await hass.async_add_executor_job(_v1_folder, folder)
    entry = await async_setup(hass, await _entry(hass))
    assert entry.state is ConfigEntryState.LOADED
    assert await hass.async_add_executor_job(_state, path) == (2, ["pass_arrival", "pass_terminal"], 1)
    (name,) = await hass.async_add_executor_job(_backups, folder)
    assert name.startswith("tracker-v1-") and name.endswith(f"-{src12}.db")
    assert await hass.async_add_executor_job(_version, os.path.join(folder, "backups", name)) == 1
    await async_reload(hass, entry)
    assert entry.state is ConfigEntryState.LOADED
    assert await hass.async_add_executor_job(_backups, folder) == [name]          # idempotent: no other copy
    assert (await entry.runtime_data.run(engine.diagnostics))["schema"] == 2


async def test_a_check_that_repeats_is_migration_failed(hass):
    folder = hass.config.path("boarding_pass_tracker")
    path, _ = await hass.async_add_executor_job(_v1_folder, folder)
    with patch.object(engine, "_fresh_layout", return_value=[]):                    # every layout check fails
        entry = await async_setup(hass, await _entry(hass))
    assert entry.state is ConfigEntryState.SETUP_ERROR
    assert entry.error_reason_translation_key == "migration_failed"
    assert (await hass.async_add_executor_job(_state, path))[0] == 1               # left at v1
    assert len(await hass.async_add_executor_job(_backups, folder)) == 1
    assert engine._db is None and engine._closed is True


async def test_an_os_error_is_retried_with_the_same_copy(hass):
    folder = hass.config.path("boarding_pass_tracker")
    path, src12 = await hass.async_add_executor_job(_v1_folder, folder)
    with patch.object(engine, "_migrate_v1", side_effect=OSError(28, "No space left on device")):
        entry = await async_setup(hass, await _entry(hass))
        assert entry.state is ConfigEntryState.SETUP_RETRY
        assert entry.reason == "data folder not ready: OSError: No space left on device"
        assert entry.error_reason_translation_key is None
        assert (await hass.async_add_executor_job(_state, path))[0] == 1
        first = await hass.async_add_executor_job(_backups, folder)
        assert len(first) == 1 and first[0].endswith(f"-{src12}.db")
    await async_reload(hass, entry)                                                  # the retry, now working
    assert entry.state is ConfigEntryState.LOADED
    assert (await hass.async_add_executor_job(_state, path))[0] == 2
    assert await hass.async_add_executor_job(_backups, folder) == first


async def test_a_damaged_v1_file_is_a_setup_error_with_no_retry(hass, freezer):
    """W2-PLATFORM-02: the copy's quick_check fails because the source fails it too, the same on every try: a setup
    error (not «data folder not ready», retried for ever), the file left at v1, no copy and no partial left."""
    folder = hass.config.path("boarding_pass_tracker")
    path, _ = await hass.async_add_executor_job(_v1_folder, folder, 400, True)
    entry = await async_setup(hass, await _entry(hass), start=True)
    assert entry.state is ConfigEntryState.SETUP_ERROR
    assert entry.reason.startswith("database not readable: DatabaseError: database disk image is malformed"), \
        entry.reason
    for minutes in (1, 5, 15, 31):                                                  # HA does not retry it
        await _tick(hass, freezer, minutes)
    assert entry.state is ConfigEntryState.SETUP_ERROR
    assert await hass.async_add_executor_job(_version, path) == 1
    assert await hass.async_add_executor_job(_backups, folder) == []
    assert engine._db is None


# ---------------------------------------------------------------- the backup hooks around the upgrade (4.6, C12)
async def test_a_backup_before_the_first_setup_makes_the_upgrade_wait_for_its_end(hass):
    folder = hass.config.path("boarding_pass_tracker")
    path, src12 = await hass.async_add_executor_job(_v1_folder, folder)
    await _component(hass)
    await backup.async_pre_backup(hass)                       # no runtime loaded: the domain's safety timer
    assert engine._backup_active is True and hass.data[DOMAIN].backup_unsub is not None
    entry = await async_setup(hass, await _entry(hass))
    assert entry.state is ConfigEntryState.SETUP_RETRY
    assert entry.reason == ("data folder not ready: OSError: a backup of Home Assistant is running: the upgrade "
                            "waits for its end")
    assert await hass.async_add_executor_job(_version, path) == 1
    assert await hass.async_add_executor_job(_backups, folder) == []                # nothing copied during it
    await backup.async_post_backup(hass)
    assert engine._backup_active is False and hass.data[DOMAIN].backup_unsub is None
    await async_reload(hass, entry)
    assert entry.state is ConfigEntryState.LOADED
    assert await hass.async_add_executor_job(_version, path) == 2
    (name,) = await hass.async_add_executor_job(_backups, folder)
    assert name.endswith(f"-{src12}.db")


async def test_with_no_post_backup_the_safety_timer_ends_the_wait(hass, freezer, caplog):
    """W2-PLATFORM-03: the post-backup never comes (plan 4.6's case for the safety timer) and no runtime is loaded:
    after 30 minutes the domain's timer ends the backup state, and HA's next retry upgrades the file."""
    folder = hass.config.path("boarding_pass_tracker")
    path, _ = await hass.async_add_executor_job(_v1_folder, folder)
    await _component(hass)
    await backup.async_pre_backup(hass)
    assert hass.data[DOMAIN].backup_unsub is not None
    entry = await async_setup(hass, await _entry(hass), start=True)     # HA running: retried by HA's timer
    assert entry.state is ConfigEntryState.SETUP_RETRY
    for minutes in (1, 5, 15):
        await _tick(hass, freezer, minutes)
    assert entry.state is ConfigEntryState.SETUP_RETRY and engine._backup_active is True       # 21 min: still waits
    for _ in range(15):                                 # past 30 min: the timer, then HA's next retry (<= 10 min)
        freezer.tick(datetime.timedelta(minutes=2))
        async_fire_time_changed(hass)
        await _settle(hass, entry)
        if entry.state is ConfigEntryState.LOADED:
            break
    assert engine._backup_active is False
    assert "No end of the backup after 30 minutes: automatic checkpoints are back on" in caplog.text
    assert entry.state is ConfigEntryState.LOADED
    assert await hass.async_add_executor_job(_version, path) == 2
    assert len(await hass.async_add_executor_job(_backups, folder)) == 1
    assert hass.data[DOMAIN].backup_unsub is None and entry.runtime_data._backup_unsub is None


async def test_a_setup_during_a_backup_takes_over_the_domains_timer(hass):
    await _component(hass)
    await backup.async_pre_backup(hass)
    assert hass.data[DOMAIN].backup_unsub is not None
    entry = await async_setup(hass, await _entry(hass))                 # a new data folder: v2, nothing to wait for
    assert entry.state is ConfigEntryState.LOADED
    assert hass.data[DOMAIN].backup_unsub is None and entry.runtime_data._backup_unsub is not None
    assert (await entry.runtime_data.run(engine.q, "PRAGMA wal_autocheckpoint"))[0]["wal_autocheckpoint"] == 0
    await backup.async_post_backup(hass)
    assert entry.runtime_data._backup_unsub is None and engine._backup_active is False


async def test_an_unload_during_a_backup_keeps_a_safety_timer(hass, freezer, caplog):
    entry = await async_setup(hass, await _entry(hass))
    rt = entry.runtime_data
    await backup.async_pre_backup(hass)
    assert rt._backup_unsub is not None and hass.data[DOMAIN].backup_unsub is None    # the runtime's timer
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert rt._backup_unsub is None and hass.data[DOMAIN].backup_unsub is not None    # the domain's takes over
    await _tick(hass, freezer, 31)
    assert engine._backup_active is False and hass.data[DOMAIN].backup_unsub is None
    assert caplog.text.count("No end of the backup after 30 minutes") == 1


async def test_a_backup_that_begins_during_the_upgrade_holds_the_new_connection(hass):
    """W2-PLATFORM-04 (C12: «the reopened connection gets wal_autocheckpoint=0»): HA's backup starts while the
    stage-B open is upgrading (after open_db looked at _backup_active): backup_begin waits for the open, then folds
    the WAL in and stops the automatic checkpoints on the new connection."""
    folder = hass.config.path("boarding_pass_tracker")
    path, _ = await hass.async_add_executor_job(_v1_folder, folder)
    arrived, go = threading.Event(), threading.Event()
    real = engine._migrate_v1

    def held(c, p):
        arrived.set()
        go.wait(20)
        return real(c, p)

    with patch.object(engine, "_migrate_v1", held):
        setup = hass.async_create_task(async_setup(hass, await _entry(hass)))
        assert await hass.async_add_executor_job(arrived.wait, 20)
        begin = hass.async_create_task(backup.async_pre_backup(hass))           # HA's backup starts now
        for _ in range(500):
            if engine._backup_active:
                break
            await hass.async_add_executor_job(threading.Event().wait, 0.01)
        assert engine._backup_active is True
        await asyncio.sleep(0.2)
        assert not begin.done()                                                  # it waits for the open
        go.set()
        entry = await setup
        await begin
    assert entry.state is ConfigEntryState.LOADED and engine._backup_active is True
    rt = entry.runtime_data
    assert rt._backup_unsub is not None                                          # the runtime's timer
    assert (await rt.run(engine.q, "PRAGMA wal_autocheckpoint"))[0]["wal_autocheckpoint"] == 0
    assert await hass.async_add_executor_job(_version, path) == 2
    await backup.async_post_backup(hass)
    assert (await rt.run(engine.q, "PRAGMA wal_autocheckpoint"))[0]["wal_autocheckpoint"] == 1000
