"""The three sensors (INTEGRATION_PLAN.md 5.4) and EntitySink, the engine's `_sensors`.

The engine computes the values in the executor, under its _publish_lock, and hands them to EntitySink.publish, which
turns them into the exact strings the standalone published over MQTT (ha.payloads) and waits until the loop applied
them (at most SINK_TIMEOUT_S, R-C): the order of the writes is the order of the computes. A state "None" is unknown,
as for an MQTT sensor; the attributes are the JSON the MQTT sensor parsed, so the types are the same (dates as
strings, tuples as lists). Called on the loop thread, publish raises RuntimeError at once (C10).

The first publish of a setup runs before this platform is set up (C3): the entities start from the cached values, so
an HA start adds no «unknown» row to the history.
"""
from __future__ import annotations

import json
import logging

from homeassistant.components.sensor import SensorEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.util.async_ import run_callback_threadsafe

from .bridge import refuse_loop_thread
from .const import DEVICE_MODEL, DOMAIN, SENSOR_KEYS, SINK_TIMEOUT_S
from .core import ha

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 0


class EntitySink:
    """engine._sensors: the last value of each sensor, and the entities that show them."""

    connected = True

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self.values: dict[str, tuple[str | None, dict]] = {}
        self.entities: dict[str, TrackerSensor] = {}

    def publish(self, name, state, attributes=None):
        """Executor side (engine.publish_sensors, under _publish_lock): returns once the loop applied the value."""
        refuse_loop_thread(self.hass, "EntitySink")
        if name not in ha.SENSORS:
            raise ValueError(f"unknown sensor: {name!r}")
        state_text, attributes_json = ha.payloads(state, attributes)
        value = None if state_text == "None" else state_text
        run_callback_threadsafe(self.hass.loop, self._apply, name, value,
                                json.loads(attributes_json)).result(SINK_TIMEOUT_S)

    @callback
    def _apply(self, name: str, value: str | None, attributes: dict) -> None:
        self.values[name] = (value, attributes)
        entity = self.entities.get(name)
        if entity is not None:
            entity.async_show(value, attributes)


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities) -> None:
    rt = entry.runtime_data
    async_add_entities([TrackerSensor(entry, rt.sink, key, rt.version) for key in SENSOR_KEYS])


class TrackerSensor(SensorEntity):
    """One of the three sensors: pinned entity id, the MQTT unique id, translated name, the MQTT icon."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, entry, sink: EntitySink, key: str, version: str) -> None:
        self._sink = sink
        self._key = key
        self.entity_id = f"sensor.{DOMAIN}_{key}"          # used only when the registry entry is created
        self._attr_unique_id = f"{DOMAIN}_{key}"           # the MQTT sensors' unique ids: another platform, no clash
        self._attr_translation_key = key
        self._attr_icon = ha.SENSORS[key]["icon"]         # the `icon` attribute the MQTT entities had
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, entry.entry_id)}, name=ha.PRODUCT,
                                            model=DEVICE_MODEL, sw_version=version,
                                            entry_type=DeviceEntryType.SERVICE)
        value, attributes = sink.values.get(key, (None, {}))
        self._attr_native_value = value
        self._attr_extra_state_attributes = attributes

    async def async_added_to_hass(self) -> None:
        self._sink.entities[self._key] = self
        value, attributes = self._sink.values.get(self._key, (None, {}))
        self._attr_native_value, self._attr_extra_state_attributes = value, attributes

    async def async_will_remove_from_hass(self) -> None:
        if self._sink.entities.get(self._key) is self:
            del self._sink.entities[self._key]

    @callback
    def async_show(self, value: str | None, attributes: dict) -> None:
        self._attr_native_value = value
        self._attr_extra_state_attributes = attributes
        if self.hass is not None and self.platform is not None:
            self.async_write_ha_state()
