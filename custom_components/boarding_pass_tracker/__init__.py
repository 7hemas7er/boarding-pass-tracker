"""Boarding Pass Tracker: boarding passes from the phone, flight status, «go to the gate», Flightradar24, statistics.

The shell of the integration (INTEGRATION_PLAN.md 3): setup, unload, stop and removal of the single config entry
around the ported core (core/, which never imports Home Assistant and runs only in the executor).

async_setup (once per HA run): the HTTP views, the static path of the cards, the domain data.
async_setup_entry:
  1. the engine's settings from the entry; 2. configure + open + prepare in the executor (a refused database is a
  ConfigEntryError with its reason, nothing written; an OSError or a locked database: ConfigEntryNotReady; on any
  failure the engine is shut down, so a database opened before it is closed as at an unload);
  3. the seams (bridge, sink, wakeup, reauth hook); 4. the first publish, before the platform (C3); 5. the sensor
  platform; 6. the pinned ids checked; 7. the cards loaded on every page; 8. the stop and time-zone listeners and,
  at HA started, the worker and the clock; 9. the backup safety timer, if a backup began before this setup (the
  runtime's: the domain's one, armed while no runtime was loaded, is cancelled).
async_unload_entry: cancel the tasks, unload the platform, shut the engine down (waits for the running jobs, closes
  the database), remove the cards and the runtime issues, clear the seams; during a backup the domain's safety
  timer takes over from the runtime's (backup.arm_domain_safety).
async_remove_entry: the data folder is kept (photos, the rollback path); a persistent notification says where.
"""
from __future__ import annotations

import logging
import os
import sqlite3

from homeassistant.components import persistent_notification
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_CORE_CONFIG_UPDATE, EVENT_HOMEASSISTANT_STOP, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryError, ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.start import async_at_started
from homeassistant.helpers.typing import ConfigType
from homeassistant.loader import async_get_integration
from homeassistant.util import dt as dt_util

from . import backup, cards, entity_ids, issues, views
from .bridge import HassBridge
from .const import DATA_DIR_NAME, DOMAIN, TITLE
from .core import engine
from .runtime import DomainData, Runtime, key_digest, settings_from_entry
from .sensor import EntitySink

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)
PLATFORMS = [Platform.SENSOR]

type BoardingPassTrackerConfigEntry = ConfigEntry[Runtime]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    integration = await async_get_integration(hass, DOMAIN)
    data = DomainData(version=str(integration.version or "0"))
    hass.data[DOMAIN] = data
    views.async_register(hass)
    data.views_registered = True
    await cards.async_register_static(hass)
    data.static_registered = True
    return True


async def async_setup_entry(hass: HomeAssistant, entry: BoardingPassTrackerConfigEntry) -> bool:
    data: DomainData = hass.data[DOMAIN]
    rt = Runtime(hass, entry, data)
    rt.settings = settings_from_entry(hass, entry, dt_util.get_default_time_zone())
    digest = key_digest(rt.settings["aviationstack_key"])
    try:
        try:
            await rt.run(engine.open_and_prepare, rt.settings, data.rescheduled_for != digest)
        except (Exception, engine.DatabaseRefused):
            # a failure after the database opened (reschedule_checks, the counts): closed now by shutdown, which
            # skips the checkpoint while a backup runs (C12), not left open for the retry's configure()
            await rt.async_close()
            raise
    except engine.DatabaseRefused as e:
        _LOGGER.error("Database refused (%s): %s", e.reason, e)
        raise ConfigEntryError(translation_domain=DOMAIN, translation_key=e.reason) from None
    except engine.MigrationFailed as e:
        _LOGGER.error("Database upgrade failed: %s", e)
        raise ConfigEntryError(translation_domain=DOMAIN, translation_key="migration_failed") from None
    except sqlite3.OperationalError as e:            # «database is locked», cannot open: retried
        raise ConfigEntryNotReady(f"database not ready: {type(e).__name__}: {e}") from None
    except sqlite3.DatabaseError as e:               # «file is not a database», malformed: no retry helps
        raise ConfigEntryError(f"database not readable: {type(e).__name__}: {e}") from None
    except OSError as e:                             # the reason without a path: strerror, not str(e)
        raise ConfigEntryNotReady(f"data folder not ready: {type(e).__name__}"
                                  + (f": {e.strerror}" if e.strerror else "")) from None
    except ValueError as e:                        # engine.configure refused a setting
        raise ConfigEntryError(f"invalid settings: {e}") from None
    data.rescheduled_for = digest
    try:
        rt.sink = EntitySink(hass)
        rt.bridge = HassBridge(hass, entry)
        engine._ha, engine._sensors, engine._wakeup = rt.bridge, rt.sink, rt.wakeup
        engine._on_auth_error = rt.on_auth_error
        entry.async_on_unload(rt.cancel_listeners)
        await rt.run(engine.publish_now)
        entry.runtime_data = rt
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
        entity_ids.async_check_pinned(hass, entry)
        issues.async_prune_people(hass, entry)
        cards.async_add(hass, rt.version)
        rt.add_unsub(hass.bus.async_listen(EVENT_HOMEASSISTANT_STOP, rt.async_stop))
        rt.add_unsub(hass.bus.async_listen(EVENT_CORE_CONFIG_UPDATE, rt.on_core_config_update))
        rt.add_unsub(async_at_started(hass, rt.start_background))
        backup.disarm_domain_safety(hass)
        if engine._backup_active:                  # a backup began before this setup (a reload during it)
            rt.arm_backup_safety()
    except BaseException:
        await rt.async_cancel_tasks()
        await rt.async_close()
        cards.async_remove(hass, rt.version)
        raise
    return True


async def async_unload_entry(hass: HomeAssistant, entry: BoardingPassTrackerConfigEntry) -> bool:
    rt: Runtime = entry.runtime_data
    await rt.async_cancel_tasks()
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    await rt.async_close()
    cards.async_remove(hass, rt.version)
    issues.async_delete_decoder(hass)
    if engine._backup_active:                      # the runtime's safety timer is gone: the domain's takes over
        backup.arm_domain_safety(hass)
    return unloaded


def _folder_usage(path: str) -> tuple[int, int]:
    """Executor: (files, bytes) under the data folder."""
    files = size = 0
    for folder, _, names in os.walk(path):
        for name in names:
            try:
                size += os.path.getsize(os.path.join(folder, name))
                files += 1
            except OSError:
                pass
    return files, size


def _size_text(size: int) -> str:
    if size < 1024 * 1024:
        return f"{size / 1024:.0f} KB"
    return f"{size / (1024 * 1024):.1f} MB".replace(".", ",")


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """The data folder stays (photos and boarding passes with full names; the rollback needs it): a WARNING and a
    persistent notification say where it is and how to delete it."""
    issues.async_delete_all(hass)
    path = hass.config.path(DATA_DIR_NAME)
    files, size = await hass.async_add_executor_job(_folder_usage, path)
    _LOGGER.warning("Removed; its data folder stays: %s (%d files)", path, files)
    text = (f"Boarding Pass Tracker è stato rimosso, ma i suoi dati restano in {path} ({files} file, "
            f"{_size_text(size)}): foto e carte d'imbarco con i nomi completi. Se non ti servono più, cancella la "
            "cartella (app SSH o File editor). I backup di Home Assistant fatti finora li contengono ancora.")
    persistent_notification.async_create(hass, text, title=TITLE, notification_id=f"{DOMAIN}_removed")
