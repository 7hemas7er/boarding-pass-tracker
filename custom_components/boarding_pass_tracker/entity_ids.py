"""The three pinned sensor ids (INTEGRATION_PLAN.md 5.4 «Id control»), read from the in-process registry on the loop.

ids_taken(hass): the config flow's guard. An id is taken when the registry has it for another platform, or a state
holds it with no registry entry (a YAML or MQTT-without-unique-id entity). Without the guard HA would silently give
our sensor an id ending with _2, and the cards and the history would not follow it.

async_check_pinned(hass, entry): after the setup, the repair issue entity_id_taken when a sensor did not get its id
because another entity holds it; a sensor the user renamed on purpose, with the pinned id free, raises nothing.
"""
from __future__ import annotations

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er

from . import issues
from .const import DOMAIN, PINNED_ENTITY_IDS


@callback
def ids_taken(hass: HomeAssistant) -> list[str]:
    """The pinned ids held by something that is not this integration, in the order of the sensors."""
    registry = er.async_get(hass)
    taken = []
    for entity_id in PINNED_ENTITY_IDS:
        entry = registry.async_get(entity_id)
        if entry is not None:
            if entry.platform != DOMAIN:
                taken.append(entity_id)
        elif hass.states.get(entity_id) is not None:
            taken.append(entity_id)
    return taken


@callback
def async_check_pinned(hass: HomeAssistant, entry=None) -> list[tuple[str, str]]:
    """Raise or delete the issue entity_id_taken; returns [(wanted, actual)] of the sensors it is about."""
    registry = er.async_get(hass)
    pairs = []
    for wanted, unique_id in PINNED_ENTITY_IDS.items():
        actual = registry.async_get_entity_id("sensor", DOMAIN, unique_id)
        if actual is None or actual == wanted:
            continue
        # ours has another id: the pinned one is held by another registry entry (the MQTT sensors had the same
        # unique ids, on another platform) or by a state without one
        if registry.async_get(wanted) is not None or hass.states.get(wanted) is not None:
            pairs.append((wanted, actual))
    issues.async_entity_ids_taken(hass, pairs)
    return pairs
