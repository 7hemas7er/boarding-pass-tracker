"""T2 entities (WP2, INTEGRATION_PLAN.md 5.4, 7.3): exact ids and unique ids, the device, the names under it and en,
the icon attribute, the _2 collision and its repair, the flow guard, the values (C3, unknown, 255), and the P-MQTT
parity: the same values give the same HA state through the integration as through the standalone's MQTT sensors."""
import datetime
import json

import pytest

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import EVENT_STATE_CHANGED
from homeassistant.helpers import device_registry as dr, entity_registry as er, issue_registry as ir

from pytest_homeassistant_custom_component.common import async_capture_events, async_fire_mqtt_message

from custom_components.boarding_pass_tracker import entity_ids
from custom_components.boarding_pass_tracker.const import DOMAIN
from custom_components.boarding_pass_tracker.core import engine, ha

from bpt_helpers import People, async_reload, async_setup, make_entry

IDS = ("sensor.boarding_pass_tracker_next_flight", "sensor.boarding_pass_tracker_upcoming_flights",
       "sensor.boarding_pass_tracker_flight_statistics")
NAMES_IT = ("Boarding Pass Tracker Prossimo volo", "Boarding Pass Tracker Voli", "Boarding Pass Tracker Statistiche voli")
NAMES_EN = ("Boarding Pass Tracker Next flight", "Boarding Pass Tracker Flights", "Boarding Pass Tracker Flight statistics")
ICONS = ("mdi:airplane-clock", "mdi:airplane", "mdi:chart-box-outline")


async def _setup(hass, **kwargs):
    people = await People().async_create(hass)
    return await async_setup(hass, make_entry(people.subentries(), **kwargs))


async def test_exact_ids_unique_ids_device_and_icons(hass):
    entry = await _setup(hass)
    registry = er.async_get(hass)
    for entity_id, icon in zip(IDS, ICONS):
        e = registry.async_get(entity_id)
        assert e is not None and e.platform == DOMAIN and e.unique_id == entity_id.removeprefix("sensor.")
        assert e.config_entry_id == entry.entry_id
        st = hass.states.get(entity_id)
        assert st.attributes["icon"] == icon                       # the icon attribute the MQTT entities had
        assert "device_class" not in st.attributes and "state_class" not in st.attributes
        assert "unit_of_measurement" not in st.attributes
    devices = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
    assert len(devices) == 1
    device = devices[0]
    assert device.identifiers == {(DOMAIN, entry.entry_id)}
    assert (device.name, device.model, device.sw_version, device.entry_type) == (
        "Boarding Pass Tracker", "boarding-pass-tracker", "1.0.0", dr.DeviceEntryType.SERVICE)
    assert {registry.async_get(i).device_id for i in IDS} == {device.id}


@pytest.mark.parametrize(("language", "names"), [("it", NAMES_IT), ("en", NAMES_EN)])
async def test_names_follow_the_language(hass, language, names):
    hass.config.language = language
    await _setup(hass)
    assert tuple(hass.states.get(i).attributes["friendly_name"] for i in IDS) == names
    assert hass.states.get(IDS[0]).entity_id == IDS[0]                 # the id is pinned, not translated


async def test_first_states_are_the_values_not_unknown(hass):
    """C3: the first publish runs before the platform: no «unknown» row at every HA start."""
    events = async_capture_events(hass, EVENT_STATE_CHANGED)
    await _setup(hass)
    first = {}
    for ev in events:
        if ev.data["entity_id"] in IDS and ev.data["entity_id"] not in first:
            first[ev.data["entity_id"]] = ev.data["new_state"].state
    assert first == {IDS[0]: "Nessun volo", IDS[1]: "0", IDS[2]: "0"}


async def test_values_as_mqtt_gave_them(hass):
    entry = await _setup(hass)
    sink = entry.runtime_data.sink
    long_state = "x" * 300
    attrs = {"day": datetime.date(2026, 11, 3), "pair": (1, 2), "ratio": 2.0, "n": 3, "flag": True, "none": None,
             "nested": {"list": [{"gate": None}]}, "half": "a\ud83d"}
    await hass.async_add_executor_job(sink.publish, "next_flight", long_state, attrs)
    st = hass.states.get(IDS[0])
    assert st.state == "x" * 255                                      # cut at 255, as ha._state_payload did
    a = dict(st.attributes)
    assert a.pop("icon") == ICONS[0] and a.pop("friendly_name")
    assert a == {"day": "2026-11-03", "pair": [1, 2], "ratio": 2.0, "n": 3, "flag": True, "none": None,
                 "nested": {"list": [{"gate": None}]}, "half": "a?"}
    await hass.async_add_executor_job(sink.publish, "next_flight", None, None)
    assert hass.states.get(IDS[0]).state == "unknown"                  # "None" -> unknown, as for MQTT
    await hass.async_add_executor_job(sink.publish, "upcoming_flights", 4, {"list": []})
    assert hass.states.get(IDS[1]).state == "4"
    with pytest.raises(ValueError):
        await hass.async_add_executor_job(sink.publish, "other", 1, {})


async def test_a_taken_id_gives_2_and_the_repair_issue(hass):
    registry = er.async_get(hass)
    registry.async_get_or_create("sensor", "mqtt", "boarding_pass_tracker_next_flight",
                                 suggested_object_id="boarding_pass_tracker_next_flight")
    assert registry.async_get(IDS[0]).platform == "mqtt"
    await _setup(hass)
    ours = registry.async_get_entity_id("sensor", DOMAIN, "boarding_pass_tracker_next_flight")
    assert ours == IDS[0] + "_2"
    issue = ir.async_get(hass).async_get_issue(DOMAIN, "entity_id_taken")
    assert issue is not None and issue.translation_key == "entity_id_taken"
    assert issue.translation_placeholders == {"wanted": IDS[0], "actual": IDS[0] + "_2"}
    assert issue.severity == ir.IssueSeverity.WARNING and not issue.is_fixable
    # the other entity goes and ours is renamed to the pinned id: the next setup deletes the issue
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    registry.async_remove(IDS[0])
    registry.async_update_entity(IDS[0] + "_2", new_entity_id=IDS[0])
    await async_reload(hass, entry)
    assert ir.async_get(hass).async_get_issue(DOMAIN, "entity_id_taken") is None


async def test_a_rename_on_purpose_raises_nothing(hass):
    entry = await _setup(hass)
    registry = er.async_get(hass)
    registry.async_update_entity(IDS[1], new_entity_id="sensor.my_flights")
    await async_reload(hass, entry)
    assert entry.state is ConfigEntryState.LOADED
    assert registry.async_get_entity_id("sensor", DOMAIN, "boarding_pass_tracker_upcoming_flights") == "sensor.my_flights"
    assert ir.async_get(hass).async_get_issue(DOMAIN, "entity_id_taken") is None
    assert entity_ids.async_check_pinned(hass) == []


async def test_the_flow_guard(hass):
    assert entity_ids.ids_taken(hass) == []
    hass.states.async_set(IDS[2], "5")                                 # a state with no registry entry
    er.async_get(hass).async_get_or_create("sensor", "mqtt", "boarding_pass_tracker_next_flight",
                                           suggested_object_id="boarding_pass_tracker_next_flight")
    assert entity_ids.ids_taken(hass) == [IDS[0], IDS[2]]
    hass.states.async_remove(IDS[2])
    er.async_get(hass).async_remove(IDS[0])
    await _setup(hass)
    assert entity_ids.ids_taken(hass) == []                            # our own entries are not «taken»


# ---------------------------------------------------------------- P-MQTT parity
def _discovery(prefix, name):
    """The standalone's discovery config (tracker/ha.py Sensors._discovery_config), for another prefix."""
    uid = f"{prefix}_{name}"
    return {**ha.SENSORS[name], "unique_id": uid, "default_entity_id": f"sensor.{uid}",
            "state_topic": f"{prefix}/{name}/state", "json_attributes_topic": f"{prefix}/{name}/attributes",
            "availability": [{"topic": f"{prefix}/availability"}],
            "device": {"identifiers": [f"{prefix}-engine"], "name": f"Boarding Pass Tracker ({prefix})",
                       "model": "boarding-pass-tracker", "sw_version": "0.1.0"},
            "origin": {"name": "boarding-pass-tracker", "sw_version": "0.1.0"}}


# The MQTT client's own 1 s periodic timer stays after its mocked socket (HA's core MQTT tests allow it by default):
# that timer is MQTT's, and this test starts no timer of the integration (no clock: HA «starting» at setup).
@pytest.mark.parametrize("expected_lingering_timers", [True])
async def test_p_mqtt_parity(hass, mqtt_mock, expected_lingering_timers):
    """The values the integration publishes, sent as the standalone did over MQTT (attributes then state, the
    strings of ha.payloads) to an MQTT twin: equal state and equal attributes (but the friendly name)."""
    hass.config.language = "it"
    entry = await _setup(hass)
    rt = entry.runtime_data
    prefix = "bptprova"
    for name in ha.SENSORS:
        async_fire_mqtt_message(hass, f"homeassistant/sensor/{prefix}_{name}/config",
                                json.dumps(_discovery(prefix, name), ensure_ascii=False), retain=True)
    await hass.async_block_till_done()
    async_fire_mqtt_message(hass, f"{prefix}/availability", "online", retain=True)
    await hass.async_block_till_done()

    # a flight typed by hand gives real engine values for the three sensors
    people_tokens = {sub.data["name"]: sub.data["ha_user"] for sub in entry.subentries.values()}
    assert people_tokens
    day = (datetime.date.today() + datetime.timedelta(days=2)).isoformat()
    user = rt.person_for(people_tokens["alice"])
    code, reply = await rt.run(engine.change_flight, json.dumps({"action": "add", "flight": "FR 1234", "date": day,
                                                                 "origin": "BLQ", "destination": "STN",
                                                                 "passengers": "Alice 12A, Bob"}).encode(),
                               user, rt.cfg())
    assert code == 200, reply
    values = await rt.run(engine.compute_sensors)
    cases = [values,
             {"next_flight": (None, {}), "upcoming_flights": (0, {"list": []}),
              "flight_statistics": ("y" * 300, {"d": datetime.date(2026, 1, 2), "t": (1, "a"), "f": 2.0,
                                                "half": "a\ud83d", "n": None})}]
    for case in cases:
        for name, (state, attributes) in case.items():
            await hass.async_add_executor_job(rt.sink.publish, name, state, attributes)
            st_text, attrs_json = ha.payloads(state, attributes)
            # live messages: HA's MQTT client gives a subscriber a retained message of a topic only once
            async_fire_mqtt_message(hass, f"{prefix}/{name}/attributes", attrs_json)
            async_fire_mqtt_message(hass, f"{prefix}/{name}/state", st_text)
        await hass.async_block_till_done()
        for name in ha.SENSORS:
            ours, twin = hass.states.get(f"sensor.boarding_pass_tracker_{name}"), hass.states.get(f"sensor.{prefix}_{name}")
            assert twin is not None, name
            assert ours.state == twin.state, name
            a, b = dict(ours.attributes), dict(twin.attributes)
            assert a.pop("friendly_name") != b.pop("friendly_name")
            assert json.dumps(a, sort_keys=True, default=str) == json.dumps(b, sort_keys=True, default=str), name
            assert a.get("icon") == ha.SENSORS[name]["icon"]
    stats = hass.states.get("sensor.boarding_pass_tracker_flight_statistics")
    assert len(json.dumps(dict(stats.attributes), ensure_ascii=False).encode()) < 16384   # under the recorder cap
