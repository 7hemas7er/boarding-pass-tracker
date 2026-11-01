"""T2 bridge + threads (WP2, INTEGRATION_PLAN.md 3.3, 5.3, 7.3): the notify data dict and group, a missing notify
service (pending + repair issue, on the loop), text.set_value, the person state, HA stopping, the timeout, C10 (a
bridge or sink method on the loop raises at once), and R-D (the reauth hook from the executor reaches
async_start_reauth on the loop thread, once; a 401 over 3 rounds costs one call and waits 6 h)."""
import asyncio
import datetime
import json
import threading
import time
from unittest.mock import patch

import pytest

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CoreState
from homeassistant.helpers import issue_registry as ir

from pytest_homeassistant_custom_component.common import async_mock_service

from custom_components.boarding_pass_tracker.const import DOMAIN
from custom_components.boarding_pass_tracker.core import engine, flights, ha

from bpt_helpers import People, async_setup, bcbp, julian, make_entry, pkpass

TAG_D1 = "boarding_pass_tracker-d1"


async def _setup(hass, mock_notify=True, **kwargs):
    people = await People().async_create(hass, mock_notify=mock_notify)
    entry = await async_setup(hass, make_entry(people.subentries(), **kwargs))
    return people, entry, entry.runtime_data


def _today(hass):
    from homeassistant.util import dt as dt_util  # noqa: PLC0415
    return dt_util.now().date()


async def _upload_and_read(hass, rt, people, user="alice"):
    day = _today(hass) + datetime.timedelta(days=2)
    code, reply = await rt.run(_upload, pkpass(bcbp(day=julian(day))), rt.person_for(people.users[user].id))
    assert code == 200, reply
    await rt.async_drain()
    return reply["id"]


def _upload(data, u):
    from custom_components.boarding_pass_tracker.core import api  # noqa: PLC0415
    return api.upload("application/vnd.apple.pkpass", data, u)


async def test_notify_data_dict_and_group(hass):
    people, entry, rt = await _setup(hass)
    assert rt.bridge.group == "boarding_pass_tracker"                 # hunk 7's assertion, moved here
    assert isinstance(rt.bridge, ha.HA)
    doc = await _upload_and_read(hass, rt, people)
    calls = people.notify["alice"]
    assert [c.data for c in calls] == [{"title": "Carta d'imbarco letta", "message": calls[0].data["message"],
                                        "data": {"group": "boarding_pass_tracker", "tag": f"boarding_pass_tracker-d{doc}"}}]
    assert people.notify["bob"] == []
    assert (await rt.run(engine.q, "SELECT notified FROM documents"))[0]["notified"] == 1
    sent = await hass.async_add_executor_job(rt.bridge.notify, "mobile_app_bob", "Titolo", "Messaggio", None, "t-1")
    assert sent is True
    assert people.notify["bob"][0].data == {"title": "Titolo", "message": "Messaggio",
                                            "data": {"group": "boarding_pass_tracker", "tag": "t-1"}}


async def test_a_missing_notify_service_stays_pending_and_raises_the_issue(hass, caplog):
    people, entry, rt = await _setup(hass, mock_notify=False)
    alice_sub = next(s for s in entry.subentries.values() if s.data["name"] == "alice")
    await _upload_and_read(hass, rt, people)
    assert (await rt.run(engine.q, "SELECT notified FROM documents"))[0]["notified"] == 0      # pending (P9)
    registry = ir.async_get(hass)
    issue = registry.async_get_issue(DOMAIN, f"notify_service_missing_{alice_sub.subentry_id}")
    assert issue is not None and issue.translation_key == "notify_service_missing"
    assert issue.translation_placeholders == {"name": "alice", "service": "mobile_app_alice"}
    assert "alice" not in issue.issue_id                                     # C13: no personal data in the id
    assert registry.async_get_issue(DOMAIN, "notify_service_missing_" + next(
        s.subentry_id for s in entry.subentries.values() if s.data["name"] == "bob")) is None
    assert "RuntimeError" not in caplog.text and "Detected" not in caplog.text
    assert "notification not sent (HAError" in caplog.text
    # the phone is back: the next round sends it and the issue goes
    calls = async_mock_service(hass, "notify", "mobile_app_alice")
    await rt.run(engine.send_pending_notifications, rt.cfg())
    assert len(calls) == 1 and calls[0].data["data"]["tag"] == TAG_D1
    assert (await rt.run(engine.q, "SELECT notified FROM documents"))[0]["notified"] == 1
    assert registry.async_get_issue(DOMAIN, f"notify_service_missing_{alice_sub.subentry_id}") is None


async def test_text_set_value_through_track_fr24(hass):
    people, entry, rt = await _setup(hass)
    set_value = async_mock_service(hass, "text", "set_value")
    hass.states.async_set("sensor.flightradar24_additional_tracked", "1", {"flights": [{"flight_number": "FR1234"}]})
    day = (_today(hass) + datetime.timedelta(days=2)).isoformat()
    user = rt.person_for(people.users["alice"].id)
    add = json.dumps({"action": "add", "flight": "FR 1234", "date": day, "origin": "BLQ", "destination": "STN",
                      "passengers": "Alice"}).encode()
    assert (await rt.run(engine.change_flight, add, user, rt.cfg()))[0] == 200
    ref = (await rt.run(engine.flight_choices))["details"]
    ref = next(iter(ref.values()))["ref"]
    code, reply = await rt.run(engine.change_flight, json.dumps({"action": "delete", "ref": ref}).encode(), user,
                               rt.cfg())
    assert (code, reply["result"]) == (200, "deleted")
    assert await rt.run(engine.track_fr24) == (0, 1)
    assert [c.data for c in set_value] == [{"entity_id": "text.flightradar24_remove_from_track", "value": "FR1234"}]
    await hass.async_add_executor_job(rt.bridge.service, "text", "set_value",
                                      {"entity_id": "text.flightradar24_add_to_track", "value": "FR1234"})
    assert set_value[-1].data == {"entity_id": "text.flightradar24_add_to_track", "value": "FR1234"}


async def test_person_state_as_rest_gave_it(hass):
    people, entry, rt = await _setup(hass)
    hass.states.async_set("person.alice", "not_home", {"latitude": 44.53, "longitude": 11.29, "gps_accuracy": 12,
                                                       "user_id": people.users["alice"].id})
    st = await hass.async_add_executor_job(rt.bridge.state, "person.alice")
    assert set(st) >= {"entity_id", "state", "attributes", "last_changed", "last_updated", "context"}
    assert (st["entity_id"], st["state"], st["attributes"]["latitude"]) == ("person.alice", "not_home", 44.53)
    assert await hass.async_add_executor_job(rt.bridge.state, "person.nobody") is None
    pos = await rt.run(engine.position, rt.person_for(people.users["alice"].id))
    assert pos == {"lat": 44.53, "lon": 11.29, "accuracy": 12}
    with pytest.raises(ValueError):
        await hass.async_add_executor_job(rt.bridge.state, "not an entity")


async def test_ha_stopping_refuses_the_service_calls(hass, caplog):
    people, entry, rt = await _setup(hass)
    hass.set_state(CoreState.stopping)
    try:
        sent = await hass.async_add_executor_job(rt.bridge.notify, "mobile_app_alice", "T", "M")
        with pytest.raises(ha.HAError, match="stopping"):
            await hass.async_add_executor_job(rt.bridge.service, "text", "set_value", {"value": "x"})
    finally:
        hass.set_state(CoreState.running)
    assert sent is False and people.notify["alice"] == []
    assert "Home Assistant is stopping" in caplog.text


async def test_a_service_that_does_not_answer_times_out(hass):
    people, entry, rt = await _setup(hass)
    cancelled = asyncio.Event()

    async def never(call):
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise
    hass.services.async_register("notify", "mobile_app_slow", never)
    rt.bridge.timeout = 0.3
    t0 = time.monotonic()
    with pytest.raises(ha.HAError, match="timeout"):
        await hass.async_add_executor_job(rt.bridge.service, "notify", "mobile_app_slow", {"message": "x"})
    assert time.monotonic() - t0 < 5
    await asyncio.wait_for(cancelled.wait(), 5)                     # the call on the loop is cancelled too
    assert await hass.async_add_executor_job(rt.bridge.notify, "mobile_app_slow", "T", "M") is False


async def test_bridge_and_sink_refuse_the_loop_thread_at_once(hass):
    """C10: on the loop thread a wait for the loop would block it for the whole timeout (20 s / 10 s)."""
    people, entry, rt = await _setup(hass)
    assert threading.get_ident() == hass.loop_thread_id
    for call in (lambda: rt.bridge.service("notify", "mobile_app_alice", {"message": "x"}),
                 lambda: rt.bridge._call_service("notify", "mobile_app_alice", {"message": "x"}),
                 lambda: rt.bridge.state("person.alice"),
                 lambda: rt.bridge._get_state("person.alice"),
                 lambda: rt.sink.publish("next_flight", "x", {})):
        t0 = time.monotonic()
        with pytest.raises(RuntimeError, match="event loop"):
            call()
        assert time.monotonic() - t0 < 0.1
    t0 = time.monotonic()
    assert rt.bridge.notify("mobile_app_alice", "T", "M") is False           # notify never raises: False at once
    assert time.monotonic() - t0 < 0.1
    await hass.async_block_till_done()
    assert people.notify["alice"] == []


async def test_the_reauth_hook_hops_to_the_loop_once_and_401_costs_one_call(hass, freezer):
    """R-D (CBV-01): check_flights calls the hook in the executor after the 6 h wait is written; the hook only hops;
    async_start_reauth runs on the loop thread, once per runtime. Over 3 rounds 60 s apart: 1 counted call."""
    freezer.move_to("2026-11-10T08:00:00+00:00")
    people, entry, rt = await _setup(hass, data={"aviationstack_key": "test-key"})
    day = _today(hass).isoformat()
    user = rt.person_for(people.users["alice"].id)
    add = json.dumps({"action": "add", "flight": "FR 1234", "date": day, "origin": "BLQ", "destination": "STN",
                      "passengers": "Alice"}).encode()
    assert (await rt.run(engine.change_flight, add, user, rt.cfg()))[0] == 200
    threads, lookups = [], []

    def spy(self, hass_, *args, **kwargs):
        threads.append(threading.get_ident())

    def refused(key, airline, number, date, origin, now):
        lookups.append((airline, number))
        raise flights.APIError("AviationStack 401: invalid_access_key", 401)
    start = datetime.datetime.now(datetime.UTC)
    with patch.object(ConfigEntry, "async_start_reauth", autospec=True, side_effect=spy), \
            patch.object(flights, "lookup_flight", refused):
        for _ in range(3):
            await rt.run(engine.clock_round, rt.cfg(), rt.clock_state)
            await hass.async_block_till_done()
            freezer.tick(60)
    assert lookups == [("FR", "1234")]
    assert threads == [hass.loop_thread_id]                           # once, and on the loop thread
    usage = await rt.run(engine.q, "SELECT month, calls FROM api_usage")
    assert usage == [{"month": "2026-11", "calls": 1}]
    v = (await rt.run(engine.q, "SELECT next_check, error FROM flights"))[0]
    assert v["next_check"] == engine.utc(start + datetime.timedelta(hours=6))
    assert "401" in v["error"]


async def test_a_busy_loop_makes_the_sink_give_up_not_hang(hass, caplog):
    """R-C: an executor thread waits for the loop at most SINK_TIMEOUT_S; publish_sensors then logs and goes on."""
    people, entry, rt = await _setup(hass)
    started = threading.Event()

    def publish():
        started.set()
        return engine.publish_now()
    with patch("custom_components.boarding_pass_tracker.sensor.SINK_TIMEOUT_S", 0.2):
        job = hass.async_add_executor_job(publish)
        assert started.wait(5)
        t0 = time.monotonic()
        while time.monotonic() - t0 < 0.6:                       # the loop is busy (no await) for 0.6 s
            pass
        assert await job is None                                 # publish_sensors swallowed the timeout
    assert "sensors not published: TimeoutError" in caplog.text
    await rt.run(engine.publish_now)                             # the next one goes through
    assert hass.states.get("sensor.boarding_pass_tracker_upcoming_flights").state == "0"


async def test_notifications_off_log_instead_of_sending(hass, caplog):
    """The option notify_enabled off is the engine's NO_NOTIFY: a dry run that logs and marks them sent."""
    caplog.set_level("INFO")
    people, entry, rt = await _setup(hass, options={"notify_enabled": False})
    assert rt.settings["no_notify"] is True
    await _upload_and_read(hass, rt, people)
    assert people.notify["alice"] == [] and people.notify["bob"] == []
    assert (await rt.run(engine.q, "SELECT notified FROM documents"))[0]["notified"] == 1
    assert "[notification to alice] Carta d'imbarco letta" in caplog.text


async def test_the_issue_of_a_removed_person_goes_at_the_next_round(hass):
    people, entry, rt = await _setup(hass, mock_notify=False)
    alice_sub = next(s for s in entry.subentries.values() if s.data["name"] == "alice")
    assert await hass.async_add_executor_job(rt.bridge.notify, "mobile_app_alice", "T", "M") is False
    registry = ir.async_get(hass)
    issue_id = f"notify_service_missing_{alice_sub.subentry_id}"
    assert registry.async_get_issue(DOMAIN, issue_id) is not None
    assert hass.config_entries.async_remove_subentry(entry, alice_sub.subentry_id)    # from the UI: no flow runs
    await hass.async_block_till_done()
    assert entry.runtime_data is rt                                                   # no reload for a person
    from bpt_helpers import wait_round  # noqa: PLC0415
    rt._clock_tick()
    await wait_round(rt)
    assert registry.async_get_issue(DOMAIN, issue_id) is None
    assert list(rt.cfg()["users"].values()) == [{"name": "bob", "notify": "mobile_app_bob", "person": "person.bob"}]
