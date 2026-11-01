"""The R-A guards are not vacuous: a statement on the integration's database and an open() run on the event loop are
both recorded (then cleared here, so this test passes; every other test fails on them at teardown)."""
import os

from custom_components.boarding_pass_tracker.core import engine

from bpt_helpers import People, async_setup, make_entry


async def test_the_guards_see_sqlite_and_open_on_the_loop(hass, loop_guard):
    people = await People().async_create(hass)
    await async_setup(hass, make_entry(people.subentries()))
    assert loop_guard.sql == [] and loop_guard.opens == []
    engine.q("SELECT count(*) AS n FROM flights")                  # deliberately on the loop
    assert ("sql", "SELECT count(*) AS n FROM flights") in loop_guard.sql
    with open(os.path.join(engine.DATA_DIR, "tracker.db"), "rb") as f:   # deliberately on the loop
        f.read(16)
    assert loop_guard.opens and loop_guard.opens[0][0].endswith("tracker.db")
    loop_guard.sql.clear()
    loop_guard.opens.clear()
