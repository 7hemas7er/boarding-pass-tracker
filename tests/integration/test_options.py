"""The options flow (INTEGRATION_PLAN.md 5.5 «Options», 7.3 «options + subentries»): OptionsFlowWithReload with the
current values suggested; a change reloads the entry and the data stays; the decoder URL is checked only when it
changes; the Flightradar24 entities fall back to the registry, then to the fixed ids. Fictional people only.
"""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import CoreState, HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er

from custom_components.boarding_pass_tracker.core import engine

from bpt_helpers import People, async_setup, make_entry

DECODER = "http://decoder.example:8099"
OTHER = "https://other-decoder.example"


def suggested(result, key):
    for marker in result["data_schema"].schema:
        if marker == key:
            return (marker.description or {}).get("suggested_value")
    raise KeyError(key)


def _add_flight() -> int:
    with engine.transaction() as c:
        return c.execute("INSERT INTO flights (airline, number, date, origin, destination, created_at) "
                         "VALUES ('FR', '1234', '2026-11-05', 'BLQ', 'STN', ?)", (engine.now_iso(),)).lastrowid


def _flights() -> int:
    return engine.q("SELECT count(*) AS n FROM flights", one=True)["n"]


async def _options(hass: HomeAssistant, entry, data: dict):
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert (result["type"], result["step_id"]) == (FlowResultType.FORM, "init")
    hass.set_state(CoreState.starting)          # the reload: no worker or clock in these tests
    try:
        out = await hass.config_entries.options.async_configure(result["flow_id"], data)
        await hass.async_block_till_done()
    finally:
        hass.set_state(CoreState.running)
    return result, out


async def test_a_change_reloads_and_the_data_stays(hass: HomeAssistant) -> None:
    people = await People().async_create(hass)
    entry = await async_setup(hass, make_entry(people.subentries()))
    rt = entry.runtime_data
    await rt.run(_add_flight)
    form, result = await _options(hass, entry, {"monthly_cap": 12, "flights_enabled": False,
                                                "notify_enabled": False})
    assert (suggested(form, "monthly_cap"), suggested(form, "flights_enabled"),
            suggested(form, "notify_enabled")) == (95, True, True)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == {"decoder_url": None, "monthly_cap": 12,
                             "fr24_add": "text.flightradar24_add_to_track",
                             "fr24_remove": "text.flightradar24_remove_from_track",
                             "fr24_tracked": "sensor.flightradar24_additional_tracked",
                             "flights_enabled": False, "notify_enabled": False}
    assert entry.state is ConfigEntryState.LOADED
    assert entry.runtime_data is not rt                                    # reloaded
    assert (engine.MAX_API_CALLS_PER_MONTH, engine.NO_FLIGHTS, engine.NO_NOTIFY) == (12, True, True)
    assert await entry.runtime_data.run(_flights) == 1                     # the data folder is the same
    assert len(entry.subentries) == 2
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_the_decoder_url_is_checked_when_it_changes(hass: HomeAssistant, aioclient_mock) -> None:
    entry = await async_setup(hass, make_entry(options={"decoder_url": DECODER}))
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert suggested(result, "decoder_url") == DECODER
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"decoder_url": "http://x/y"})
    assert result["errors"] == {"decoder_url": "invalid_url"}
    aioclient_mock.get(f"{OTHER}/health", status=404)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"decoder_url": OTHER})
    assert (result["errors"], result["description_placeholders"]) == ({"decoder_url": "decoder_wrong_api"},
                                                                       {"url": OTHER})
    hass.set_state(CoreState.starting)
    try:
        result = await hass.config_entries.options.async_configure(result["flow_id"],
                                                                   {"decoder_url": f"{DECODER}/", "monthly_cap": 95})
        await hass.async_block_till_done()
    finally:
        hass.set_state(CoreState.running)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert [str(u) for _, u, *_ in aioclient_mock.mock_calls] == [f"{OTHER}/health"]   # the unchanged one: no call
    assert entry.options["decoder_url"] == DECODER
    assert (engine.barcodes.DECODER_URL, engine.READ_PHOTOS) == (DECODER, True)

    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{OTHER}/health", json={"ok": True, "api": 1})
    _, result = await _options(hass, entry, {"decoder_url": OTHER})
    assert result["type"] is FlowResultType.CREATE_ENTRY and entry.options["decoder_url"] == OTHER
    _, result = await _options(hass, entry, {})
    assert entry.options["decoder_url"] is None                            # emptied: no decoder
    assert (engine.barcodes.DECODER_URL, engine.READ_PHOTOS) == (None, False)
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_the_flightradar24_entities(hass: HomeAssistant) -> None:
    reg = er.async_get(hass)
    add = reg.async_get_or_create("text", "flightradar24", "c1_flightradar24_add_track",
                                  suggested_object_id="flightradar24_aggiungi").entity_id
    tracked = reg.async_get_or_create("sensor", "flightradar24", "c1_flightradar24_additional_tracked",
                                      suggested_object_id="flightradar24_seguiti_extra").entity_id
    entry = await async_setup(hass, make_entry())
    form, result = await _options(hass, entry, {"fr24_remove": "text.flightradar24_togli"})
    assert (suggested(form, "fr24_add"), suggested(form, "fr24_remove"), suggested(form, "fr24_tracked")) == (
        add, None, tracked)                                                # nothing stored yet: the registry
    assert (entry.options["fr24_add"], entry.options["fr24_remove"], entry.options["fr24_tracked"]) == (
        add, "text.flightradar24_togli", tracked)
    assert (engine.FR24_ADD, engine.FR24_REMOVE, engine.FR24_TRACKED) == (add, "text.flightradar24_togli", tracked)
    form, _ = await _options(hass, entry, {"fr24_add": reg.async_get(add).id})      # a registry id resolves
    assert suggested(form, "fr24_remove") == "text.flightradar24_togli"    # stored: shown again
    assert entry.options["fr24_add"] == add
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_the_flightradar24_defaults_skip_leftovers(hass: HomeAssistant) -> None:
    """The live case of 2026-11-01: Flightradar24 v2.4 names the tracked sensor <entry>_flightradar24_additional_
    tracked; the entity of an older version (<entry>_flightradar24_tracked, id sensor.flightradar24_additional_
    tracked, only restored) must not be proposed. Among two matching entities the one with a live state wins over a
    restored or disabled one, whatever the order of their ids."""
    from custom_components.boarding_pass_tracker.config_flow import fr24_registry_defaults
    reg = er.async_get(hass)
    old = reg.async_get_or_create("sensor", "flightradar24", "c1_flightradar24_tracked",
                                  suggested_object_id="flightradar24_additional_tracked").entity_id
    hass.states.async_set(old, "unavailable", {"restored": True})
    new = reg.async_get_or_create("sensor", "flightradar24", "c1_flightradar24_additional_tracked",
                                  suggested_object_id="flightradar24_seguiti_extra").entity_id
    hass.states.async_set(new, "0", {"flights": []})
    assert fr24_registry_defaults(hass)["fr24_tracked"] == new
    gone = reg.async_get_or_create("text", "flightradar24", "c0_flightradar24_add_track",
                                   suggested_object_id="flightradar24_a_old").entity_id
    hass.states.async_set(gone, "unavailable", {"restored": True})
    off = reg.async_get_or_create("text", "flightradar24", "c2_flightradar24_add_track",
                                  suggested_object_id="flightradar24_b_off",
                                  disabled_by=er.RegistryEntryDisabler.USER).entity_id
    live = reg.async_get_or_create("text", "flightradar24", "c3_flightradar24_add_track",
                                   suggested_object_id="flightradar24_c_live").entity_id
    hass.states.async_set(live, "")
    assert (gone, off) < (live, live)                                    # sorted by id, the live one is last
    assert fr24_registry_defaults(hass)["fr24_add"] == live
    assert "fr24_remove" not in fr24_registry_defaults(hass)


async def test_a_reload_keeps_a_queued_document(hass: HomeAssistant) -> None:
    people = await People().async_create(hass)
    entry = await async_setup(hass, make_entry(people.subentries()))
    rt = entry.runtime_data
    alice = rt.cfg()["users"][people.users["alice"].id]
    doc_id, status, _ = await rt.run(engine.save_upload, b"%PDF-1.4 fictional", "pdf", alice)
    assert status == "in_coda"
    _, result = await _options(hass, entry, {"monthly_cap": 95, "notify_enabled": False})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    row = await entry.runtime_data.run(engine.q, "SELECT status, uploaded_by FROM documents WHERE id=?", (doc_id,),
                                       True)
    assert row == {"status": "in_coda", "uploaded_by": "alice"}
    assert await hass.config_entries.async_unload(entry.entry_id)
