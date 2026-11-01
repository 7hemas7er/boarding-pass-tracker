"""WP2 acceptance first (INTEGRATION_PLAN.md 7.3): a MockConfigEntry setup reaches LOADED with `frontend` set up from
the installed wheel (CHA-02), and the database is where the plan says, with the plan's modes."""
import os
import stat

from homeassistant.config_entries import ConfigEntryState

from custom_components.boarding_pass_tracker.const import DOMAIN
from custom_components.boarding_pass_tracker.core import engine

from bpt_helpers import People, async_setup, make_entry


async def test_setup_reaches_loaded_with_frontend(hass):
    people = await People().async_create(hass)
    entry = await async_setup(hass, make_entry(people.subentries()))
    assert entry.state is ConfigEntryState.LOADED
    assert "frontend" in hass.config.components            # from the home-assistant-frontend wheel
    import hass_frontend  # noqa: F401  (the wheel is installed: «No module named 'hass_frontend'» otherwise)
    data_dir = hass.config.path("boarding_pass_tracker")
    assert engine.DATA_DIR == data_dir
    db = os.path.join(data_dir, "tracker.db")
    assert os.path.isfile(db)
    assert stat.S_IMODE(os.stat(data_dir).st_mode) == 0o700
    rt = entry.runtime_data
    assert rt.cfg() == {"users": {people.users["alice"].id: {"name": "alice", "notify": "mobile_app_alice",
                                                             "person": "person.alice"},
                                  people.users["bob"].id: {"name": "bob", "notify": "mobile_app_bob",
                                                           "person": "person.bob"}}}
    assert engine._ha is rt.bridge and engine._sensors is rt.sink and engine._wakeup is rt.wakeup
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert entry.state is ConfigEntryState.NOT_LOADED
    assert engine._ha is None and engine._sensors is None and engine._db is None
    assert DOMAIN in hass.data
