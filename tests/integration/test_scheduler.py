"""T2 scheduler (WP2, INTEGRATION_PLAN.md 3.5, 7.3), with freezer and async_fire_time_changed: the first clock round
at HA started and then every 60 s; a tick skipped while a round runs; nothing before HA started and nothing after
the unload; the worker woken by an upload and looking again every 60 s; the decoder issue after the rounds."""
import datetime
import threading
from unittest.mock import patch


from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.helpers import issue_registry as ir

from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.boarding_pass_tracker.const import DOMAIN
from custom_components.boarding_pass_tracker.core import api, barcodes, engine

from bpt_helpers import People, async_setup, bcbp, julian, make_entry, pkpass, wait_round


class Counting:
    """Wraps a core job, counting its calls (optionally holding them until released)."""

    def __init__(self, real, hold=False):
        self.real, self.calls = real, 0
        self.release = threading.Event()
        if not hold:
            self.release.set()
        self.entered = threading.Event()

    def __call__(self, *args, **kwargs):
        self.calls += 1
        self.entered.set()
        assert self.release.wait(30)
        return self.real(*args, **kwargs)


async def pause(hass, seconds):
    """A real-time wait that leaves the loop free (under freezer the loop's clock is frozen: no asyncio.sleep)."""
    await hass.async_add_executor_job(threading.Event().wait, seconds)
    await hass.async_block_till_done()


async def _tick(hass, freezer, seconds=60):
    freezer.tick(datetime.timedelta(seconds=seconds))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


async def _setup(hass, start=True, **kwargs):
    people = await People().async_create(hass)
    entry = await async_setup(hass, make_entry(people.subentries(), **kwargs), start=start)
    return people, entry, entry.runtime_data


async def test_first_round_at_started_then_every_60_s(hass, freezer):
    rounds = Counting(engine.clock_round)
    with patch.object(engine, "clock_round", rounds):
        people, entry, rt = await _setup(hass)
        await wait_round(rt)
        assert rounds.calls == 1                                    # at once: HA is already running
        await _tick(hass, freezer, 30)
        await wait_round(rt)
        assert rounds.calls == 1
        await _tick(hass, freezer, 30)
        await wait_round(rt)
        assert rounds.calls == 2
        for n in (3, 4):
            await _tick(hass, freezer, 60)
            await wait_round(rt)
            assert rounds.calls == n
        assert await hass.config_entries.async_unload(entry.entry_id)


async def test_a_tick_is_skipped_while_a_round_runs(hass, freezer):
    rounds = Counting(engine.clock_round, hold=True)
    with patch.object(engine, "clock_round", rounds):
        people, entry, rt = await _setup(hass)
        assert await hass.async_add_executor_job(rounds.entered.wait, 10)
        first = rt._round_task
        for _ in range(3):                                           # three ticks while the first round runs
            await _tick(hass, freezer, 60)
        assert rounds.calls == 1 and rt._round_task is first and not first.done()
        rounds.release.set()
        await wait_round(rt)
        await _tick(hass, freezer, 60)
        await wait_round(rt)
        assert rounds.calls == 2 and rt._round_task is not first
        assert await hass.config_entries.async_unload(entry.entry_id)


async def test_nothing_before_ha_started_then_the_worker_and_the_clock(hass, freezer):
    rounds, works = Counting(engine.clock_round), Counting(engine.work_one)
    with patch.object(engine, "clock_round", rounds), patch.object(engine, "work_one", works):
        people, entry, rt = await _setup(hass, start=False)
        await _tick(hass, freezer, 120)
        assert (rounds.calls, works.calls, rt._worker_task) == (0, 0, None)
        hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
        await hass.async_block_till_done()
        await wait_round(rt)
        assert rounds.calls == 1 and works.calls >= 1
        assert rt._worker_task is not None and not rt._worker_task.done()
        assert await hass.config_entries.async_unload(entry.entry_id)


async def test_nothing_after_the_unload(hass, freezer):
    rounds, works = Counting(engine.clock_round), Counting(engine.work_one)
    with patch.object(engine, "clock_round", rounds), patch.object(engine, "work_one", works):
        people, entry, rt = await _setup(hass)
        await wait_round(rt)
        worker = rt._worker_task
        assert await hass.config_entries.async_unload(entry.entry_id)
        assert worker.done() and rt._round_task.done()
        before = (rounds.calls, works.calls)
        for _ in range(3):
            await _tick(hass, freezer, 60)
        await pause(hass, 0.1)
        assert (rounds.calls, works.calls) == before
        assert rt._unsubs == [] and rt.stopping


async def test_the_worker_wakes_on_an_upload_and_looks_again_every_60_s(hass, freezer):
    works = Counting(engine.work_one)
    with patch.object(engine, "work_one", works):
        people, entry, rt = await _setup(hass)
        await wait_round(rt)
        await pause(hass, 0.2)
        idle = works.calls
        assert idle >= 1
        await pause(hass, 0.3)
        assert works.calls == idle                                   # idle: waiting for an upload or 60 s
        day = datetime.date.today() + datetime.timedelta(days=2)
        u = rt.person_for(people.users["alice"].id)
        code, reply = await rt.run(api.upload, None, pkpass(bcbp(day=julian(day))), u)
        assert code == 200
        for _ in range(100):                                         # woken at once, not after 60 s
            rows = await rt.run(engine.q, "SELECT status FROM documents")
            if rows[0]["status"] != "in_coda":
                break
            await pause(hass, 0.05)
        assert rows[0]["status"] == "letto"
        await pause(hass, 0.2)
        idle = works.calls
        await _tick(hass, freezer, 60)                               # the 60 s wait ends: one more look
        for _ in range(50):
            if works.calls > idle:
                break
            await pause(hass, 0.05)
        assert works.calls == idle + 1
        assert await hass.config_entries.async_unload(entry.entry_id)


async def test_the_decoder_issue_after_15_minutes_down_with_photos_waiting(hass, freezer, socket_enabled):
    """C8: the round's report becomes the decoder_unreachable issue, on the loop (port 9 refuses: down)."""
    url = "http://127.0.0.1:9"
    people, entry, rt = await _setup(hass, start=False, options={"decoder_url": url})
    u = rt.person_for(people.users["alice"].id)
    code, reply = await rt.run(api.upload, None, b"\xff\xd8\xff\xe0" + b"\x00" * 64, u)
    assert code == 200
    registry = ir.async_get(hass)
    rt._clock_tick()
    await wait_round(rt)
    assert rt.last_report["decoder_status"] == "down" and rt.last_report["queued_photos"] == 1
    assert registry.async_get_issue(DOMAIN, "decoder_unreachable") is None          # down for less than 15 min
    freezer.tick(datetime.timedelta(minutes=16))
    rt._clock_tick()
    await wait_round(rt)
    issue = registry.async_get_issue(DOMAIN, "decoder_unreachable")
    assert issue is not None and issue.translation_placeholders == {"url": url, "minutes": "16", "n": "1"}
    with patch.object(barcodes, "probe", lambda: barcodes._mark(True, {"ok": True, "api": 1})):
        freezer.tick(datetime.timedelta(minutes=11))
        rt._clock_tick()
        await wait_round(rt)
    assert rt.last_report["decoder_status"] == "ok"
    assert registry.async_get_issue(DOMAIN, "decoder_unreachable") is None          # up again: deleted
