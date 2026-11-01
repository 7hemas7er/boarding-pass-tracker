"""The backup platform (INTEGRATION_PLAN.md 4.6, C12): HA calls these around every backup.

Before: the WAL is folded into tracker.db and automatic checkpoints stop, so tracker.db and tracker.db-wal (both in
HA's backups, and -shm too: HA's *.db-shm exclude matches one folder level only) stay consistent while the backup
copies them. Writes go on: no write gate, a backup of a large /config can last minutes. After: automatic checkpoints
again. A safety timer (30 min) does the same if the "after" never comes: the loaded runtime's, re-armed by the next
setup when a reload happens during a backup; while no runtime is loaded (an entry waiting for the backup's end to
upgrade its database, or unloaded during a backup), the domain's own, cancelled by a setup that succeeds.

An exception here would abort HA's backup: both hooks log and return.
"""
from __future__ import annotations

import logging

from homeassistant.core import HassJob, HomeAssistant, callback
from homeassistant.helpers.event import async_call_later

from .const import BACKUP_SAFETY, DOMAIN
from .core import engine

_LOGGER = logging.getLogger(__name__)


def _runtime(hass: HomeAssistant):
    entries = hass.config_entries.async_loaded_entries(DOMAIN)
    rt = getattr(entries[0], "runtime_data", None) if entries else None
    return None if rt is None or rt.stopping else rt


@callback
def arm_domain_safety(hass: HomeAssistant) -> None:
    """The safety timer of a backup while no runtime is loaded (its unsub on the domain data): without it a backup
    whose end never comes would keep the setup that waits for it (EBUSY: the stage-B upgrade) retrying until HA
    restarts. Cancelled at HA stop."""
    data = hass.data.get(DOMAIN)
    if data is None:
        return
    disarm_domain_safety(hass)

    async def _fired(_now) -> None:
        data.backup_unsub = None
        _LOGGER.warning("No end of the backup after %s minutes: automatic checkpoints are back on",
                        int(BACKUP_SAFETY.total_seconds() // 60))
        await hass.async_add_executor_job(engine.backup_end)

    data.backup_unsub = async_call_later(hass, BACKUP_SAFETY, HassJob(_fired, f"{DOMAIN} backup safety",
                                                                      cancel_on_shutdown=True))


@callback
def disarm_domain_safety(hass: HomeAssistant) -> None:
    data = hass.data.get(DOMAIN)
    if data is not None and data.backup_unsub is not None:
        unsub, data.backup_unsub = data.backup_unsub, None
        unsub()


async def async_pre_backup(hass: HomeAssistant) -> None:
    try:
        await hass.async_add_executor_job(engine.backup_begin)
    except Exception as e:                                  # never abort HA's backup
        _LOGGER.warning("Backup start: %s", type(e).__name__)
    if (rt := _runtime(hass)) is not None:
        rt.arm_backup_safety()
    else:
        arm_domain_safety(hass)


async def async_post_backup(hass: HomeAssistant) -> None:
    if (rt := _runtime(hass)) is not None:
        rt.disarm_backup_safety()
    disarm_domain_safety(hass)
    try:
        await hass.async_add_executor_job(engine.backup_end)
    except Exception as e:
        _LOGGER.warning("Backup end: %s", type(e).__name__)
