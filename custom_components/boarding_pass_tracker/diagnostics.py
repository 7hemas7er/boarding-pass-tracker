"""Diagnostics of the config entry (INTEGRATION_PLAN.md 5.7): counts only. No flight code, passenger name or file
name; the key, the person names, the notify services, the person entities and the HA user ids are redacted. HA
adds every issue of the domain to the download with its id: the ids hold no personal data (C13).
"""
from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .const import (CONF_AVIATIONSTACK_KEY, CONF_HA_USER, CONF_NAME, CONF_NOTIFY, CONF_PERSON, DATA_DIR_NAME,
                    SUBENTRY_PERSON)
from .core import barcodes, engine

TO_REDACT = {CONF_AVIATIONSTACK_KEY, CONF_NAME, CONF_NOTIFY, CONF_PERSON, CONF_HA_USER, "user_id", "title",
             "unique_id"}


def _collect() -> dict:
    """Executor: the engine's counts and the decoder client's state."""
    return {"database": engine.diagnostics(),
            "decoder": {"status": barcodes.status(), "down_since": barcodes.down_since(),
                        "health": barcodes.last_health()}}


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry) -> dict[str, Any]:
    rt = getattr(entry, "runtime_data", None)
    settings = (rt.settings if rt is not None else None) or {}
    try:
        found = await rt.run(_collect) if rt is not None and not rt.stopping else None
    except engine.Closed:
        found = None
    registry = er.async_get(hass)
    fr24 = {}
    for key in ("fr24_add", "fr24_remove", "fr24_tracked"):
        entity_id = settings.get(key)
        fr24[key] = bool(entity_id) and (registry.async_get(entity_id) is not None
                                         or hass.states.get(entity_id) is not None)
    persons = [s for s in entry.subentries.values() if s.subentry_type == SUBENTRY_PERSON]
    data = {
        "entry": {"version": entry.version, "minor_version": entry.minor_version, "options": dict(entry.options),
                  "persons": len(persons), "persons_with_ha_user": sum(bool(s.data.get(CONF_HA_USER)) for s in persons),
                  "key_set": bool(entry.data.get(CONF_AVIATIONSTACK_KEY))},
        "loaded": found is not None,
        "database": found["database"] if found else None,
        "decoder": {"configured": bool(settings.get("decoder_url")), **(found["decoder"] if found else {})},
        "data_folder": hass.config.path(DATA_DIR_NAME),
        "time_zone": hass.config.time_zone,
        "language": hass.config.language,
        "fr24": fr24,
        "last_clock_report": rt.last_report if rt is not None else None,
    }
    return async_redact_data(data, TO_REDACT)
