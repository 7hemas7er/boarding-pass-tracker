"""System health (INTEGRATION_PLAN.md 5.7): documents in the queue, the decoder (reachable or not), the AviationStack
calls of the month against the cap, the data folder."""
from __future__ import annotations

import datetime
from typing import Any

from homeassistant.components import system_health
from homeassistant.core import HomeAssistant, callback

from .const import DATA_DIR_NAME, DOMAIN
from .core import engine


@callback
def async_register(hass: HomeAssistant, register: system_health.SystemHealthRegistration) -> None:
    register.async_register_info(system_health_info)


def _counts() -> dict:
    """Executor: the queue and this month's AviationStack calls (the month check_flights counts in: UTC)."""
    month = datetime.datetime.now(datetime.UTC).strftime("%Y-%m")
    row = engine.q("SELECT calls FROM api_usage WHERE month=?", (month,), one=True)
    return {"queued": engine.queued_counts()["queued"], "calls": row["calls"] if row else 0}


async def system_health_info(hass: HomeAssistant) -> dict[str, Any]:
    entries = hass.config_entries.async_loaded_entries(DOMAIN)
    rt = getattr(entries[0], "runtime_data", None) if entries else None
    info: dict[str, Any] = {"data_folder": hass.config.path(DATA_DIR_NAME)}
    if rt is None or rt.stopping:
        info["loaded"] = False
        return info
    try:
        counts = await rt.run(_counts)
    except engine.Closed:
        info["loaded"] = False
        return info
    url = rt.settings.get("decoder_url")
    info["queued"] = counts["queued"]
    info["decoder"] = system_health.async_check_can_reach_url(hass, f"{url}/health") if url else "off"
    info["aviationstack_calls"] = f"{counts['calls']}/{rt.settings['max_api_calls_per_month']}"
    return info
