"""The runtime of the config entry (INTEGRATION_PLAN.md 3.2-3.6): the engine's settings, the persons as the engine
sees them (cfg), the executor jobs, the worker task, the clock, the backup safety timer, and the stop.

Rules (3.3):
  R-A  no core function runs on the loop: every call goes through run() (an executor job, engine.job);
  R-B  the loop never takes a thread lock and never waits for an executor future synchronously;
  R-D  what the core calls from the executor (the reauth hook) only hops to the loop; every function handed to an HA
       helper here is a @callback or a coroutine function.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import hashlib
import logging
import re
from typing import Any

from homeassistant.core import CALLBACK_TYPE, Event, HomeAssistant, callback
from homeassistant.helpers.event import async_call_later, async_track_time_interval
from homeassistant.util import dt as dt_util

from . import issues
from .bridge import LoopWakeup
from .const import (BACKUP_SAFETY, CLOCK_INTERVAL, CONF_AVIATIONSTACK_KEY, CONF_DECODER_URL, CONF_FLIGHTS_ENABLED,
                    CONF_HA_USER, CONF_MONTHLY_CAP, CONF_NAME, CONF_NOTIFY_ENABLED, CONF_PERSON, DATA_DIR_NAME,
                    DEFAULT_FLIGHTS_ENABLED, DEFAULT_FR24, DEFAULT_MONTHLY_CAP, DEFAULT_NOTIFY_ENABLED, DOMAIN,
                    MAX_MONTHLY_CAP, SHUTDOWN_TIMEOUT_S, SUBENTRY_PERSON, TASK_STOP_TIMEOUT_S, WORKER_ERROR_S,
                    WORKER_IDLE_S)
from .core import engine

_LOGGER = logging.getLogger(__name__)
_ENTITY = re.compile(r"[a-z_]+\.[a-z0-9_]+")       # engine.configure's rule for the three Flightradar24 ids


@dataclass
class DomainData:
    """hass.data[DOMAIN]: what lives for the whole HA run (created once, in async_setup)."""

    version: str                          # the manifest version: device sw_version, ?v= of the cards
    views_registered: bool = False
    static_registered: bool = False
    rescheduled_for: str | None = None    # key_digest() at the last reschedule_checks (C11)
    backup_unsub: CALLBACK_TYPE | None = None   # the backup safety timer while no runtime is loaded (backup.py)


def key_digest(key: str | None) -> str:
    """sha256[:12] of the AviationStack key ("" for none): a new key runs reschedule_checks again (C11)."""
    return hashlib.sha256((key or "").encode()).hexdigest()[:12]


def _decoder_url(value: Any) -> str | None:
    value = (value or "").strip() if isinstance(value, str) else None
    return value.rstrip("/") or None if value else None


def settings_from_entry(hass: HomeAssistant, entry, tz) -> dict:
    """The engine's settings (engine.SETTINGS_KEYS, every key) from the config entry: the key from entry.data, the
    rest from entry.options with the defaults of const.py."""
    o = entry.options
    try:
        cap = int(o.get(CONF_MONTHLY_CAP, DEFAULT_MONTHLY_CAP))
    except (TypeError, ValueError):
        cap = DEFAULT_MONTHLY_CAP
    fr24 = {}
    for option, default in DEFAULT_FR24.items():
        value = o.get(option)
        if value and not (isinstance(value, str) and _ENTITY.fullmatch(value)):
            _LOGGER.warning("Option %s is not an entity id: using %s", option, default)
            value = None
        fr24[option] = value or default
    decoder = _decoder_url(o.get(CONF_DECODER_URL))
    return {
        "data_dir": hass.config.path(DATA_DIR_NAME),
        "local_tz": tz,
        "no_notify": not bool(o.get(CONF_NOTIFY_ENABLED, DEFAULT_NOTIFY_ENABLED)),
        "no_flights": not bool(o.get(CONF_FLIGHTS_ENABLED, DEFAULT_FLIGHTS_ENABLED)),
        "max_api_calls_per_month": max(0, min(cap, MAX_MONTHLY_CAP)),
        "aviationstack_key": str(entry.data.get(CONF_AVIATIONSTACK_KEY) or ""),
        "fr24_add": fr24["fr24_add"], "fr24_remove": fr24["fr24_remove"], "fr24_tracked": fr24["fr24_tracked"],
        "decoder_url": decoder,
        "read_photos": decoder is not None,
    }


def person_of(sub) -> dict:
    """A person subentry as the engine's users: {"name", "notify", "person"} (the standalone's config shape)."""
    name = str(sub.data.get(CONF_NAME) or sub.unique_id or sub.title)
    return {"name": name, "notify": issues.notify_service_of(sub.data),
            "person": sub.data.get(CONF_PERSON) or f"person.{name}"}


class Runtime:
    """entry.runtime_data."""

    def __init__(self, hass: HomeAssistant, entry, data: DomainData) -> None:
        self.hass = hass
        self.entry = entry
        self.data = data
        self.version = data.version
        self.settings: dict | None = None
        self.sink = None                     # sensor.EntitySink, set in setup
        self.bridge = None                   # bridge.HassBridge
        self.wakeup = LoopWakeup(hass.loop, asyncio.Event())
        self.clock_state = engine.ClockState()
        self.stopping = False
        self.last_report: dict | None = None
        self._round_task: asyncio.Task | None = None
        self._worker_task: asyncio.Task | None = None
        self._wake = self.wakeup.event
        self._reauth_started = False
        self._started = False
        self._closed = False
        self._unsubs: list[CALLBACK_TYPE] = []
        self._backup_unsub: CALLBACK_TYPE | None = None

    # ------------------------------------------------------------ persons
    def cfg(self) -> dict:
        """The engine's cfg, built from the person subentries on every call (they change with no reload)."""
        users = {}
        for sub in self.entry.subentries.values():
            if sub.subentry_type != SUBENTRY_PERSON:
                continue
            u = person_of(sub)
            users[sub.data.get(CONF_HA_USER) or f"name:{u['name']}"] = u
        return {"users": users}

    def person_for(self, user_id: str) -> dict | None:
        """The person whose HA user is `user_id`, None if there is none."""
        for sub in self.entry.subentries.values():
            if sub.subentry_type == SUBENTRY_PERSON and user_id and sub.data.get(CONF_HA_USER) == user_id:
                return person_of(sub)
        return None

    # ------------------------------------------------------------ executor jobs
    def run(self, fn, *args):
        """fn(*args) as an executor job of the engine (engine.job): raises engine.Closed after the shutdown."""
        return self.hass.async_add_executor_job(engine.job, fn, *args)

    def on_auth_error(self) -> None:
        """engine._on_auth_error, CALLED IN THE EXECUTOR (check_flights, after the 6 h wait is written): it only hops
        to the loop (R-D)."""
        self.hass.loop.call_soon_threadsafe(self._start_reauth_once)

    @callback
    def _start_reauth_once(self) -> None:
        if self._reauth_started or self.stopping:
            return
        self._reauth_started = True
        _LOGGER.warning("AviationStack refused the key: starting the reauthentication")
        self.entry.async_start_reauth(self.hass)

    # ------------------------------------------------------------ listeners
    @callback
    def add_unsub(self, unsub: CALLBACK_TYPE) -> None:
        self._unsubs.append(unsub)

    @callback
    def cancel_listeners(self) -> None:
        """Every timer and listener of this runtime, each once (also passed to entry.async_on_unload)."""
        while self._unsubs:
            unsub = self._unsubs.pop()
            try:
                unsub()
            except Exception:                       # never stop an unload for a listener that is already gone
                _LOGGER.debug("listener already removed", exc_info=True)
        self.disarm_backup_safety()

    @callback
    def start_background(self, _hass: HomeAssistant | None = None) -> None:
        """At HA started (async_at_started): the worker and the clock; the first clock round at once."""
        if self.stopping or self._started:
            return
        self._started = True
        self._worker_task = self.entry.async_create_background_task(self.hass, self._worker_loop(),
                                                                    f"{DOMAIN} worker")
        self.add_unsub(async_track_time_interval(self.hass, self._clock_tick, CLOCK_INTERVAL,
                                                 name=f"{DOMAIN} clock", cancel_on_shutdown=True))
        self._clock_tick()

    @callback
    def on_core_config_update(self, event: Event) -> None:
        """EVENT_CORE_CONFIG_UPDATE: the engine follows HA's time zone."""
        tz = dt_util.get_default_time_zone()
        if self.stopping or (self.settings and self.settings.get("local_tz") is tz):
            return
        if self.settings is not None:
            self.settings["local_tz"] = tz
        self.entry.async_create_task(self.hass, self._async_set_time_zone(tz), f"{DOMAIN} time zone")

    async def _async_set_time_zone(self, tz) -> None:
        try:
            await self.run(engine.set_time_zone, tz)
        except engine.Closed:
            pass

    # ------------------------------------------------------------ worker (3.5)
    async def _worker_loop(self) -> None:
        """One document at a time, oldest first (engine.work_one): an upload wakes it, else it looks again every
        WORKER_IDLE_S. The event is cleared before the next round, so no wake-up is lost."""
        while not self.stopping:
            try:
                wait = await self.run(engine.work_one, self.cfg())
            except engine.Closed:
                return
            except Exception:
                _LOGGER.exception("worker round failed")
                wait = WORKER_ERROR_S
            if self.stopping:
                return
            if wait is None:
                try:
                    async with asyncio.timeout(WORKER_IDLE_S):
                        await self._wake.wait()
                except TimeoutError:
                    pass
                self._wake.clear()
            elif wait:
                await asyncio.sleep(wait)

    # ------------------------------------------------------------ clock (3.5)
    @callback
    def _clock_tick(self, _now=None) -> None:
        if self.stopping or (self._round_task is not None and not self._round_task.done()):
            return                                  # a round still running: this tick is skipped
        self._round_task = self.entry.async_create_background_task(self.hass, self._clock_round(),
                                                                   f"{DOMAIN} clock round")

    async def _clock_round(self) -> None:
        try:
            report = await self.run(engine.clock_round, self.cfg(), self.clock_state)
        except engine.Closed:
            return
        except Exception:
            _LOGGER.exception("clock round failed")
            return
        if self.stopping:
            return
        self.last_report = report
        issues.async_decoder_report(self.hass, self.settings and self.settings.get("decoder_url"), report)
        issues.async_prune_people(self.hass, self.entry)

    # ------------------------------------------------------------ tests and drain
    async def async_drain(self) -> None:
        """work_one until the queue is empty (tests, L2): bounded by the attempts rule and the decoder pause."""
        while await self.run(engine.work_one, self.cfg()) is not None:
            pass

    # ------------------------------------------------------------ backups (4.6, C12)
    @callback
    def arm_backup_safety(self) -> None:
        self.disarm_backup_safety()
        self._backup_unsub = async_call_later(self.hass, BACKUP_SAFETY, self._backup_safety_fired)

    @callback
    def disarm_backup_safety(self) -> None:
        if self._backup_unsub is not None:
            unsub, self._backup_unsub = self._backup_unsub, None
            unsub()

    async def _backup_safety_fired(self, _now=None) -> None:
        self._backup_unsub = None
        _LOGGER.warning("No end of the backup after %s minutes: automatic checkpoints are back on",
                        int(BACKUP_SAFETY.total_seconds() // 60))
        await self.hass.async_add_executor_job(engine.backup_end)

    # ------------------------------------------------------------ stop and unload (3.6)
    async def async_cancel_tasks(self) -> None:
        """Unload step 1: nothing new starts; the clock, the round and the worker are cancelled (HA cancels the
        entry's background tasks only after async_unload_entry), and given TASK_STOP_TIMEOUT_S to end. An executor
        job they were waiting for keeps running: async_close waits for it."""
        self.stopping = True
        self.cancel_listeners()
        tasks = [t for t in (self._round_task, self._worker_task) if t is not None and not t.done()]
        for t in tasks:
            t.cancel()
        if tasks:
            await asyncio.wait(tasks, timeout=TASK_STOP_TIMEOUT_S)

    async def async_close(self) -> None:
        """Unload step 3 and 6 (and HA stop): the engine refuses new jobs, waits for the running ones, closes the
        database; then the seams go."""
        if self._closed:
            return
        self._closed = True
        try:
            await self.hass.async_add_executor_job(engine.shutdown, SHUTDOWN_TIMEOUT_S)
        finally:
            self.clear_seams()

    @callback
    def clear_seams(self) -> None:
        if engine._ha is self.bridge:
            engine._ha = None
        if engine._sensors is self.sink:
            engine._sensors = None
        if engine._on_auth_error == self.on_auth_error:
            engine._on_auth_error = engine._no_auth_hook
        if engine._wakeup is self.wakeup:
            engine._wakeup = _IDLE_WAKEUP

    async def async_stop(self, _event: Event | None = None) -> None:
        """EVENT_HOMEASSISTANT_STOP: HA does not unload the entries, so the engine stops here (unload steps 1, 3,
        6). The entry stays loaded: the views answer 503 not_ready."""
        await self.async_cancel_tasks()
        await self.async_close()


class _IdleWakeup:
    """engine._wakeup with no runtime: nothing to wake."""

    def set(self) -> None:
        pass


_IDLE_WAKEUP = _IdleWakeup()
