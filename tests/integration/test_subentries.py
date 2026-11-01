"""The person subentries (INTEGRATION_PLAN.md 5.5 «Subentry type person», 7.3 «options + subentries»): add,
reconfigure and remove a person with no reload (the runtime reads the subentries on every use); a name the rows use
cannot be changed (name_in_use), from the running engine or, when the entry is not loaded, from a copy of the
folder; a reconfigure deletes the person's notify issue. Fictional people only: alice, bob.
"""
from __future__ import annotations

from homeassistant.config_entries import SOURCE_RECONFIGURE, SOURCE_USER, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import issue_registry as ir

from pytest_homeassistant_custom_component.common import async_mock_service

from custom_components.boarding_pass_tracker import issues
from custom_components.boarding_pass_tracker.const import DOMAIN
from custom_components.boarding_pass_tracker.core import engine

from bpt_helpers import People, async_setup, make_entry


def suggested(result, key):
    for marker in result["data_schema"].schema:
        if marker == key:
            return (marker.description or {}).get("suggested_value")
    raise KeyError(key)


def sub_of(entry, name):
    (sub,) = [s for s in entry.subentries.values() if s.data["name"] == name]
    return sub


async def add(hass: HomeAssistant, entry, data: dict):
    result = await hass.config_entries.subentries.async_init((entry.entry_id, "person"),
                                                             context={"source": SOURCE_USER})
    assert (result["type"], result["step_id"]) == (FlowResultType.FORM, "user")
    out = await hass.config_entries.subentries.async_configure(result["flow_id"], data)
    await hass.async_block_till_done()
    return result, out


async def reconfigure(hass: HomeAssistant, entry, name: str, data: dict):
    sub = sub_of(entry, name)
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "person"), context={"source": SOURCE_RECONFIGURE, "subentry_id": sub.subentry_id})
    assert (result["type"], result["step_id"]) == (FlowResultType.FORM, "reconfigure")
    out = await hass.config_entries.subentries.async_configure(result["flow_id"], data)
    await hass.async_block_till_done()
    return result, out


async def two_people(hass: HomeAssistant, names=("alice",)):
    people = await People().async_create(hass)
    entry = await async_setup(hass, make_entry(people.subentries(list(names))))
    assert entry.state is ConfigEntryState.LOADED
    return people, entry, entry.runtime_data


async def test_add_a_person_with_no_reload(hass: HomeAssistant) -> None:
    people, entry, rt = await two_people(hass)
    bob = people.users["bob"]
    form, result = await add(hass, entry, {"name": "bob", "ha_user": bob.id, "notify": "notify.mobile_app_bob",
                                           "person": "person.bob"})
    assert [str(m) for m in form["data_schema"].schema] == ["name", "ha_user", "notify", "person"]
    assert result["type"] is FlowResultType.CREATE_ENTRY
    sub = sub_of(entry, "bob")
    assert (sub.title, sub.unique_id, dict(sub.data)) == ("bob", "bob", {
        "name": "bob", "ha_user": bob.id, "notify": "mobile_app_bob", "person": "person.bob"})
    assert entry.runtime_data is rt and entry.state is ConfigEntryState.LOADED           # no reload
    assert rt.cfg()["users"][bob.id] == {"name": "bob", "notify": "mobile_app_bob", "person": "person.bob"}
    assert rt.person_for(bob.id)["name"] == "bob"
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_a_person_without_an_ha_user(hass: HomeAssistant) -> None:
    _, entry, rt = await two_people(hass)
    _, result = await add(hass, entry, {"name": "carol", "notify": "mobile_app_carol"})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert rt.cfg()["users"]["name:carol"] == {"name": "carol", "notify": "mobile_app_carol",
                                               "person": "person.carol"}
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_add_errors(hass: HomeAssistant) -> None:
    people, entry, _ = await two_people(hass)
    alice, bob = people.users["alice"], people.users["bob"]
    result = await hass.config_entries.subentries.async_init((entry.entry_id, "person"),
                                                             context={"source": SOURCE_USER})
    for data, errors in (
            ({"name": "alice", "notify": "mobile_app_bob"}, {"name": "name_taken"}),
            ({"name": "bob", "ha_user": alice.id, "notify": "mobile_app_bob"}, {"ha_user": "user_taken"}),
            ({"name": "Bob", "notify": "mobile_app_bob"}, {"name": "invalid_name"}),
            ({"name": "bob", "notify": "mobile_app_bob", "person": "0123456789abcdef0123456789abcdef"},
             {"person": "invalid_person"}),
            ({"name": "bob", "notify": "bob_phone"}, {"notify": "invalid_notify"}),
            ({"name": "bob"}, {"notify": "invalid_notify"})):
        result = await hass.config_entries.subentries.async_configure(result["flow_id"], data)
        assert (result["type"], result["errors"]) == (FlowResultType.FORM, errors), data
        assert suggested(result, "name") == data["name"]                   # what was typed stays
    result = await hass.config_entries.subentries.async_configure(result["flow_id"], {
        "name": "bob", "ha_user": bob.id, "notify": "mobile_app_bob"})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_reconfigure_with_no_reload_and_the_issue_goes(hass: HomeAssistant) -> None:
    people, entry, rt = await two_people(hass, ("alice", "bob"))
    alice = people.users["alice"]
    sub = sub_of(entry, "alice")
    issues.async_notify_missing(hass, entry, "mobile_app_alice")
    issue_id = f"notify_service_missing_{sub.subentry_id}"
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None
    async_mock_service(hass, "notify", "mobile_app_alice_nuovo")
    form, result = await reconfigure(hass, entry, "alice", {"name": "alice", "ha_user": alice.id,
                                                            "notify": "mobile_app_alice_nuovo"})
    assert (suggested(form, "name"), suggested(form, "ha_user"), suggested(form, "notify"),
            suggested(form, "person")) == ("alice", alice.id, "mobile_app_alice", "person.alice")
    assert form["description_placeholders"] == {"name": "alice"}
    assert (result["type"], result["reason"]) == (FlowResultType.ABORT, "reconfigure_successful")
    assert dict(sub_of(entry, "alice").data) == {"name": "alice", "ha_user": alice.id,
                                                 "notify": "mobile_app_alice_nuovo", "person": None}
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None
    assert entry.runtime_data is rt                                         # no reload
    assert rt.cfg()["users"][alice.id]["notify"] == "mobile_app_alice_nuovo"
    _, result = await reconfigure(hass, entry, "alice", {"name": "alice", "ha_user": people.users["bob"].id,
                                                         "notify": "mobile_app_alice"})
    assert result["errors"] == {"ha_user": "user_taken"}
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_a_name_in_the_rows_cannot_change(hass: HomeAssistant) -> None:
    people, entry, rt = await two_people(hass, ("alice", "bob"))
    alice, bob = people.users["alice"], people.users["bob"]
    await rt.run(engine.save_upload, b"%PDF-1.4 fictional", "pdf", rt.cfg()["users"][alice.id])
    _, result = await reconfigure(hass, entry, "alice", {"name": "alicia", "ha_user": alice.id,
                                                         "notify": "mobile_app_alice"})
    assert (result["type"], result["errors"]) == (FlowResultType.FORM, {"name": "name_in_use"})
    _, result = await reconfigure(hass, entry, "bob", {"name": "alice", "ha_user": bob.id,
                                                       "notify": "mobile_app_bob"})
    assert result["errors"] == {"name": "name_taken"}
    _, result = await reconfigure(hass, entry, "bob", {"name": "roberto", "ha_user": bob.id,
                                                       "notify": "mobile_app_bob"})
    assert result["reason"] == "reconfigure_successful"                    # bob has no rows: renamed
    sub = sub_of(entry, "roberto")
    assert (sub.title, sub.unique_id) == ("roberto", "roberto")
    assert entry.runtime_data is rt
    assert rt.cfg()["users"][bob.id]["name"] == "roberto"
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_a_name_in_the_rows_when_the_entry_is_not_loaded(hass: HomeAssistant) -> None:
    people, entry, rt = await two_people(hass, ("alice", "bob"))
    alice, bob = people.users["alice"], people.users["bob"]
    await rt.run(engine.save_upload, b"%PDF-1.4 fictional", "pdf", rt.cfg()["users"][alice.id])
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert entry.state is ConfigEntryState.NOT_LOADED
    _, result = await reconfigure(hass, entry, "alice", {"name": "alicia", "ha_user": alice.id,
                                                         "notify": "mobile_app_alice"})
    assert result["errors"] == {"name": "name_in_use"}                     # read from a copy of the folder
    _, result = await reconfigure(hass, entry, "bob", {"name": "roberto", "ha_user": bob.id,
                                                       "notify": "mobile_app_bob"})
    assert result["reason"] == "reconfigure_successful"
    assert entry.state is ConfigEntryState.NOT_LOADED


async def test_remove_a_person_with_no_reload_keeps_the_rows(hass: HomeAssistant) -> None:
    people, entry, rt = await two_people(hass, ("alice", "bob"))
    bob = people.users["bob"]
    await rt.run(engine.save_upload, b"%PDF-1.4 fictional", "pdf", rt.cfg()["users"][bob.id])
    assert hass.config_entries.async_remove_subentry(entry, sub_of(entry, "bob").subentry_id)
    await hass.async_block_till_done()
    assert entry.runtime_data is rt and entry.state is ConfigEntryState.LOADED
    assert bob.id not in rt.cfg()["users"] and rt.person_for(bob.id) is None
    rows = await rt.run(engine.q, "SELECT uploaded_by FROM documents")
    assert rows == [{"uploaded_by": "bob"}]                                # the rows stay
    _, result = await add(hass, entry, {"name": "bob", "ha_user": bob.id, "notify": "mobile_app_bob"})
    assert result["type"] is FlowResultType.CREATE_ENTRY                   # the same name links them again
    assert rt.cfg()["users"][bob.id]["name"] == "bob"
    assert await hass.config_entries.async_unload(entry.entry_id)
