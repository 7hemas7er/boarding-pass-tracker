"""T2 race (WP2, CBV-08, INTEGRATION_PLAN.md 3.4, 7.3), through the real sink and view: a clock publish is held
inside compute_sensors, after it computed the values from before the edit; meanwhile POST /flight adds a flight. The
reply comes only after the clock's write, the final state is the edit's, and nothing logs «sensors not published».
Without engine._publish_lock (the §7.5 T2 mutant) the stale clock values land after the edit's and this fails."""
import datetime
import json
import threading
from unittest.mock import patch

from homeassistant.util import dt as dt_util

from custom_components.boarding_pass_tracker.core import engine

from bpt_helpers import API, People, async_setup, make_entry, wait_round


async def test_a_clock_publish_does_not_hide_a_change(hass, hass_client, caplog):
    people = await People().async_create(hass)
    entry = await async_setup(hass, make_entry(people.subentries()))
    rt = entry.runtime_data
    client = await hass_client(people.tokens["alice"])
    held, computed, order = threading.Event(), threading.Event(), []
    real = engine.compute_sensors
    first = []

    def compute(*args, **kwargs):
        values = real(*args, **kwargs)
        if not first:                                   # the clock's compute: before the edit, then held
            first.append(values["upcoming_flights"][0])
            computed.set()
            assert held.wait(30)
            order.append("clock values released")
        return values

    states = []
    real_show = type(rt.sink.entities["upcoming_flights"]).async_show

    def show(self, value, attributes):
        if self._key == "upcoming_flights":
            states.append(value)
        return real_show(self, value, attributes)

    day = (dt_util.now().date() + datetime.timedelta(days=2)).isoformat()
    body = json.dumps({"action": "add", "flight": "FR 1234", "date": day, "origin": "BLQ", "destination": "STN",
                       "passengers": "Alice 12A"}).encode()
    with patch.object(engine, "compute_sensors", compute), \
            patch.object(type(rt.sink.entities["upcoming_flights"]), "async_show", show):
        rt._clock_tick()                                # the real clock path: a round, publish_now inside
        assert await hass.async_add_executor_job(computed.wait, 10)
        post = hass.async_create_task(client.post(f"{API}/flight", data=body))
        await hass.async_add_executor_job(threading.Event().wait, 0.5)
        assert not post.done()                          # the edit's publish waits for the clock's
        rows = await rt.run(engine.q, "SELECT count(*) AS n FROM flights")
        assert rows[0]["n"] == 1                        # committed, the reply not sent yet
        held.set()
        r = await post
        order.append("reply")
        assert r.status == 200 and (await r.json())["result"] == "added"
        await wait_round(rt)
    assert first == [0]
    assert order == ["clock values released", "reply"]
    assert states == ["0", "1"]                         # the stale write first, then the edit's
    assert hass.states.get("sensor.boarding_pass_tracker_upcoming_flights").state == "1"
    assert "sensors not published" not in caplog.text
