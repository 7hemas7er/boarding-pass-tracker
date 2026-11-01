"""The config flow (INTEGRATION_PLAN.md 4.5, 5.5, 7.3 «config flow»): every step, the abort of the pinned sensor ids
and each adopt refusal, the single instance, the decoder URL check on HA's session (aioclient_mock), reconfigure,
reauth by hand, and the reauth started by the real hook: a 401 from AviationStack inside
rt.run(engine.check_flights, ...) gives exactly one reauth flow. Fictional people only: alice, bob (and carol, a name
of a fictional old config without rows).
"""
from __future__ import annotations

import datetime
import hashlib
import json
import os
import sqlite3
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest

from homeassistant.config_entries import SOURCE_REAUTH, SOURCE_USER, ConfigEntryState
from homeassistant.core import CoreState, HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er

from pytest_homeassistant_custom_component.common import MockConfigEntry, async_mock_service

from custom_components.boarding_pass_tracker import config_flow
from custom_components.boarding_pass_tracker.const import DATA_DIR_NAME, DOMAIN
from custom_components.boarding_pass_tracker.core import engine

from bpt_helpers import People, async_setup, make_entry

SETUP = "custom_components.boarding_pass_tracker.async_setup_entry"
UNLOAD = "custom_components.boarding_pass_tracker.async_unload_entry"
DECODER = "http://decoder.example:8099"
PASSENGER = "ROSSI/MARIO MR"          # the tf.bcbp() default: never in a flow result


@pytest.fixture
async def no_setup(hass: HomeAssistant):
    """The entry a flow creates is not set up (the flow is what these tests are about). It is unloaded again under
    the same patches: else hass's teardown runs the real async_unload_entry on an entry that was never set up (no
    runtime_data), and HA logs «Error unloading entry» (the loop guard fails on that line)."""
    with patch(SETUP, return_value=True) as setup, patch(UNLOAD, return_value=True):
        yield setup
        for entry in hass.config_entries.async_entries(DOMAIN):
            if entry.state is ConfigEntryState.LOADED:
                assert await hass.config_entries.async_unload(entry.entry_id)


def suggested(result, key):
    """The suggested value of a field of a shown form (None when there is none)."""
    for marker in result["data_schema"].schema:
        if marker == key:
            return (marker.description or {}).get("suggested_value")
    raise KeyError(key)


def fields(result) -> list[str]:
    return [str(m) for m in result["data_schema"].schema]


async def start(hass: HomeAssistant):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "user"
    assert result["data_schema"].schema == {}
    return result


async def configure(hass: HomeAssistant, result, data=None):
    return await hass.config_entries.flow.async_configure(result["flow_id"], data or {})


def fr24_registry(hass: HomeAssistant) -> dict[str, str]:
    reg = er.async_get(hass)
    return {"fr24_add": reg.async_get_or_create("text", "flightradar24", "c1_flightradar24_add_track",
                                                suggested_object_id="flightradar24_aggiungi").entity_id,
            "fr24_remove": reg.async_get_or_create("text", "flightradar24", "c1_flightradar24_remove_track",
                                                   suggested_object_id="flightradar24_togli").entity_id,
            "fr24_tracked": reg.async_get_or_create("sensor", "flightradar24", "c1_flightradar24_additional_tracked",
                                                    suggested_object_id="flightradar24_seguiti").entity_id}


# ---------------------------------------------------------------- data folders (built in the executor)
def build_folder(folder: str, *, kind: str = "v1", config_users=None, bad_file: bool = False) -> None:
    """A fictional data folder: kind v1 (2 flights, 2 documents with their files, 1 boarding pass of alice; bob in a
    gate reminder; one document queued and one not notified), newer, foreign, v0, garbage."""
    os.makedirs(folder, 0o700, exist_ok=True)
    path = os.path.join(folder, "tracker.db")
    if kind == "garbage":
        with open(path, "wb") as f:
            f.write(b"not a database " * 300)
        return
    if kind == "v0":
        c = sqlite3.connect(path)
        c.executescript("CREATE TABLE documenti (id INTEGER PRIMARY KEY, file TEXT);")
        c.commit()
        c.close()
        return
    c = engine.open_db(path)
    now = "2026-10-30T10:00:00+01:00"
    files = {}
    for i, (data, ext, status, notified) in enumerate(((b"%PDF-1.4 fictional one", "pdf", "letto", 1),
                                                       (b"\x89PNG fictional two", "png", "in_coda", 0))):
        sha = hashlib.sha256(data).hexdigest()
        rel = f"file/2026/10/{sha[:16]}.{ext}"
        os.makedirs(os.path.join(folder, "file", "2026", "10"), 0o700, exist_ok=True)
        with open(os.path.join(folder, rel), "wb") as f:
            f.write(b"changed" if bad_file and i == 0 else data)
        files[rel] = sha
        c.execute("INSERT INTO documents (sha256, uploaded_by, uploaded_at, file, file_type, status, notified) "
                  "VALUES (?, 'alice', ?, ?, ?, ?, ?)", (sha, now, rel, ext, status, notified))
    c.execute("UPDATE documents SET notified=0 WHERE id=1")
    for number, snapshot in (("1234", {"gate_sent_to": ["bob"]}), ("4321", {})):
        c.execute("INSERT INTO flights (airline, number, date, origin, destination, snapshot, created_at) "
                  "VALUES ('FR', ?, '2026-11-05', 'BLQ', 'STN', ?, ?)", (number, json.dumps(snapshot), now))
    c.execute("INSERT INTO boarding_passes (document_id, flight_id, passenger, first_name, uploaded_by) "
              "VALUES (1, 1, ?, 'Mario', 'alice')", (PASSENGER,))
    if kind == "newer":
        c.execute(f"PRAGMA user_version = {engine.SCHEMA_VERSION + 1}")
    if kind == "foreign":
        c.execute("PRAGMA application_id = 1212501065")
    c.close()
    if config_users is not None:
        with open(os.path.join(folder, "manifest.json"), "w", encoding="utf-8") as f:
            json.dump({"format": 1, "schema": 1, "users": ["alice", "bob"], "config_users": config_users,
                       "files": files}, f)


async def async_folder(hass: HomeAssistant, **kw) -> str:
    folder = hass.config.path(DATA_DIR_NAME)
    await hass.async_add_executor_job(lambda: build_folder(folder, **kw))
    return folder


# ---------------------------------------------------------------- fresh install
async def test_fresh_install_every_step(hass: HomeAssistant, no_setup, aioclient_mock) -> None:
    people = await People().async_create(hass, mock_notify=False)
    alice, bob = people.users["alice"], people.users["bob"]
    MockConfigEntry(domain="mobile_app", data={"user_id": alice.id, "device_name": "Alice iPhone"}).add_to_hass(hass)
    async_mock_service(hass, "notify", "mobile_app_alice_iphone")
    async_mock_service(hass, "notify", "mobile_app_bob")
    fr24 = fr24_registry(hass)
    aioclient_mock.get(f"{DECODER}/health", json={"ok": True, "api": 1, "name": "barcode-decoder"})

    result = await configure(hass, await start(hass))
    assert (result["type"], result["step_id"]) == (FlowResultType.FORM, "people")
    options = result["data_schema"].schema["users"].config["options"]
    assert [o["value"] for o in options] == [alice.id, bob.id]           # active, not system-generated users

    result = await configure(hass, result, {"users": [alice.id, bob.id]})
    assert (result["step_id"], result["description_placeholders"]) == ("person", {"user": "alice"})
    assert fields(result) == ["name", "notify", "person"]
    assert (suggested(result, "name"), suggested(result, "notify"), suggested(result, "person")) == (
        "alice", "mobile_app_alice_iphone", "person.alice")
    assert result["data_schema"].schema["notify"].config["options"] == ["mobile_app_alice_iphone", "mobile_app_bob"]

    result = await configure(hass, result, {"name": "alice", "notify": "mobile_app_alice_iphone",
                                            "person": "person.alice"})
    assert result["description_placeholders"] == {"user": "bob"}
    assert suggested(result, "notify") is None                            # bob has no mobile_app entry
    result = await configure(hass, result, {"name": "bob", "notify": "notify.mobile_app_bob"})

    assert result["step_id"] == "services"
    for key, entity_id in fr24.items():
        assert suggested(result, key) == entity_id                       # from the registry, any language
    assert suggested(result, "decoder_url") is None                       # not HA OS: no suggestion
    result = await configure(hass, result, {"aviationstack_key": " fake-key ", "monthly_cap": 50.0,
                                            "decoder_url": f"{DECODER}/", **fr24})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Boarding Pass Tracker"
    assert result["data"] == {"aviationstack_key": "fake-key"}
    assert result["options"] == {"decoder_url": DECODER, "monthly_cap": 50, **fr24, "flights_enabled": True,
                                 "notify_enabled": True}
    assert isinstance(result["options"]["monthly_cap"], int)
    assert [(str(m).lower(), str(u)) for m, u, *_ in aioclient_mock.mock_calls] == [("get", f"{DECODER}/health")]
    entry = result["result"]
    subs = sorted(entry.subentries.values(), key=lambda s: s.title)
    assert [(s.subentry_type, s.title, s.unique_id, dict(s.data)) for s in subs] == [
        ("person", "alice", "alice", {"name": "alice", "ha_user": alice.id, "notify": "mobile_app_alice_iphone",
                                      "person": "person.alice"}),
        ("person", "bob", "bob", {"name": "bob", "ha_user": bob.id, "notify": "mobile_app_bob",
                                  "person": None})]
    assert no_setup.call_count == 1


async def test_fresh_install_with_nobody_and_no_services(hass: HomeAssistant, no_setup, aioclient_mock) -> None:
    await People().async_create(hass)
    result = await configure(hass, await start(hass))
    result = await configure(hass, result, {"users": []})
    assert result["step_id"] == "services"
    result = await configure(hass, result, {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {"aviationstack_key": None}
    assert result["options"] == {"decoder_url": None, "monthly_cap": 95,
                                 "fr24_add": "text.flightradar24_add_to_track",
                                 "fr24_remove": "text.flightradar24_remove_from_track",
                                 "fr24_tracked": "sensor.flightradar24_additional_tracked",
                                 "flights_enabled": True, "notify_enabled": True}
    assert result["result"].subentries == {}
    assert aioclient_mock.call_count == 0                                 # no URL: nothing checked


async def test_no_user_to_choose_goes_on(hass: HomeAssistant, no_setup) -> None:
    result = await configure(hass, await start(hass))                     # only the plugin's system users
    assert result["step_id"] == "services"


async def test_single_instance(hass: HomeAssistant, no_setup) -> None:
    make_entry().add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert (result["type"], result["reason"]) == (FlowResultType.ABORT, "single_instance_allowed")


async def test_entity_ids_taken(hass: HomeAssistant, no_setup) -> None:
    reg = er.async_get(hass)
    reg.async_get_or_create("sensor", "mqtt", "bpt-next", suggested_object_id="boarding_pass_tracker_next_flight")
    hass.states.async_set("sensor.boarding_pass_tracker_flight_statistics", "3")
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert (result["type"], result["reason"]) == (FlowResultType.ABORT, "entity_ids_taken")
    assert result["description_placeholders"] == {
        "ids": "sensor.boarding_pass_tracker_next_flight, sensor.boarding_pass_tracker_flight_statistics"}


async def test_entity_ids_checked_again_before_the_entry(hass: HomeAssistant, no_setup) -> None:
    result = await configure(hass, await start(hass))
    assert result["step_id"] == "services"
    hass.states.async_set("sensor.boarding_pass_tracker_upcoming_flights", "1")      # the old service came back
    result = await configure(hass, result, {})
    assert (result["type"], result["reason"]) == (FlowResultType.ABORT, "entity_ids_taken")
    assert hass.config_entries.async_entries(DOMAIN) == []


# ---------------------------------------------------------------- adopt
@pytest.mark.parametrize(("kind", "reason", "placeholders"), [
    ("newer", "newer_database", {"n": "0"}),
    ("foreign", "foreign_database", {"n": "0"}),
    ("v0", "old_database_v0", {"n": "0"}),
    ("garbage", "broken_database", {"n": "0"}),
    ("bad_file", "file_mismatch", {"n": "1"}),
])
async def test_each_adopt_refusal(hass: HomeAssistant, no_setup, kind, reason, placeholders) -> None:
    folder = await async_folder(hass, **({"kind": "v1", "bad_file": True} if kind == "bad_file" else {"kind": kind}))

    def state():
        out = {}
        for d, _, names in os.walk(folder):
            for n in names:
                with open(os.path.join(d, n), "rb") as f:
                    out[os.path.relpath(os.path.join(d, n), folder)] = hashlib.sha256(f.read()).hexdigest()
        return out
    before = await hass.async_add_executor_job(state)
    result = await configure(hass, await start(hass))
    assert (result["type"], result["reason"]) == (FlowResultType.ABORT, reason)
    assert result["description_placeholders"] == placeholders
    assert await hass.async_add_executor_job(state) == before             # nothing written
    assert hass.config_entries.async_entries(DOMAIN) == []


async def test_adopt_then_the_people_of_the_data(hass: HomeAssistant, no_setup) -> None:
    """Names found in the rows first (bob is skipped: no subentry), then the config names without rows (carol),
    then the multi-select of the HA users not linked yet."""
    people = await People().async_create(hass)
    alice, bob = people.users["alice"], people.users["bob"]
    await async_folder(hass, config_users=["alice", "bob", "carol"])
    async_mock_service(hass, "notify", "mobile_app_carol")

    result = await configure(hass, await start(hass))
    assert (result["type"], result["step_id"]) == (FlowResultType.FORM, "adopt")
    assert result["description_placeholders"] == {"flights": "2", "documents": "2", "files": "2", "passes": "1",
                                                  "schema": str(engine.SCHEMA_VERSION), "queued": "1",
                                                  "unnotified": "1"}
    assert PASSENGER not in json.dumps(result["description_placeholders"])

    result = await configure(hass, result)
    assert (result["step_id"], result["description_placeholders"]) == ("person_found", {"name": "alice"})
    assert fields(result) == ["ha_user", "notify", "person", "skip"]      # the stored name is fixed
    assert (suggested(result, "ha_user"), suggested(result, "person")) == (alice.id, "person.alice")
    result = await configure(hass, result, {"ha_user": alice.id, "notify": "mobile_app_alice",
                                            "person": "person.alice"})

    assert result["description_placeholders"] == {"name": "bob"}
    assert suggested(result, "ha_user") == bob.id
    result = await configure(hass, result, {"skip": True})

    assert (result["step_id"], result["description_placeholders"]) == ("person_config", {"name": "carol"})
    assert suggested(result, "name") == "carol"
    result = await configure(hass, result, {"name": "carol", "notify": "mobile_app_carol"})

    assert result["step_id"] == "people"                                  # bob was skipped: still offered
    assert [o["value"] for o in result["data_schema"].schema["users"].config["options"]] == [bob.id]
    result = await configure(hass, result, {"users": []})
    assert result["step_id"] == "services"
    result = await configure(hass, result, {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    subs = {s.title: dict(s.data) for s in result["result"].subentries.values()}
    assert subs == {"alice": {"name": "alice", "ha_user": alice.id, "notify": "mobile_app_alice",
                              "person": "person.alice"},
                    "carol": {"name": "carol", "ha_user": None, "notify": "mobile_app_carol", "person": None}}


async def test_adopt_without_a_manifest(hass: HomeAssistant, no_setup) -> None:
    await People().async_create(hass)
    await async_folder(hass)
    result = await configure(hass, await configure(hass, await start(hass)))
    assert result["description_placeholders"] == {"name": "alice"}
    result = await configure(hass, result, {"skip": True})
    assert result["description_placeholders"] == {"name": "bob"}
    result = await configure(hass, result, {"skip": True})
    assert result["step_id"] == "people"                                   # no person_config without a manifest


async def test_person_form_errors(hass: HomeAssistant, no_setup) -> None:
    people = await People().async_create(hass)
    alice, bob = people.users["alice"], people.users["bob"]
    await async_folder(hass)
    result = await configure(hass, await configure(hass, await start(hass)))
    result = await configure(hass, result, {"notify": "telefono"})
    assert result["errors"] == {"notify": "invalid_notify"}
    result = await configure(hass, result, {})
    assert result["errors"] == {"notify": "invalid_notify"}               # required unless skip
    result = await configure(hass, result, {"notify": "mobile_app_alice", "person": "0123456789abcdef0123456789abcdef"})
    assert result["errors"] == {"person": "invalid_person"}                # a registry id that resolves to nothing
    result = await configure(hass, result, {"ha_user": alice.id, "notify": "mobile_app_alice"})
    assert result["description_placeholders"] == {"name": "bob"}
    result = await configure(hass, result, {"ha_user": alice.id, "notify": "mobile_app_bob"})
    assert result["errors"] == {"ha_user": "user_taken"}
    result = await configure(hass, result, {"skip": True})
    result = await configure(hass, result, {"users": [bob.id]})
    assert result["step_id"] == "person"
    for name, error in (("Bob", "invalid_name"), ("bob 2", "invalid_name"), ("", "invalid_name"),
                        ("alice", "name_taken")):
        result = await configure(hass, result, {"name": name, "notify": "mobile_app_bob"})
        assert result["errors"] == {"name": error}, name
    result = await configure(hass, result, {"name": "roberto", "notify": "mobile_app_bob"})
    assert result["step_id"] == "services"


# ---------------------------------------------------------------- services: the decoder URL
@pytest.mark.parametrize("url", ["ftp://decoder.example:8099", "http://decoder.example:8099/v1",
                                 "http://user:pw@decoder.example:8099", "http://decoder.example:8099?x=1",
                                 "http://decoder.example:99999", "decoder.example:8099", "http://:8099",
                                 "http://decoder.example:8099#top"])
async def test_decoder_url_refused(hass: HomeAssistant, no_setup, aioclient_mock, url) -> None:
    result = await configure(hass, await start(hass))
    result = await configure(hass, result, {"decoder_url": url})
    assert (result["step_id"], result["errors"]) == ("services", {"decoder_url": "invalid_url"})
    assert aioclient_mock.call_count == 0


@pytest.mark.parametrize(("mock", "error"), [
    ({"exc": aiohttp.ClientConnectionError()}, "decoder_unreachable"),
    ({"exc": TimeoutError()}, "decoder_unreachable"),
    ({"status": 503, "text": "busy"}, "decoder_unreachable"),
    ({"status": 404, "text": "not here"}, "decoder_wrong_api"),
    ({"status": 302, "headers": {"Location": "http://elsewhere.example/"}}, "decoder_wrong_api"),
    ({"text": "<html>router</html>"}, "decoder_wrong_api"),
    ({"json": {"ok": True, "api": 2}}, "decoder_wrong_api"),
    ({"json": {"ok": True, "api": True}}, "decoder_wrong_api"),
    ({"json": [1]}, "decoder_wrong_api"),
    ({"text": json.dumps({"api": 1, "pad": "x" * 70000})}, "decoder_wrong_api"),
])
async def test_decoder_health_errors(hass: HomeAssistant, no_setup, aioclient_mock, mock, error) -> None:
    aioclient_mock.get(f"{DECODER}/health", **mock)
    result = await configure(hass, await start(hass))
    result = await configure(hass, result, {"decoder_url": DECODER, "aviationstack_key": "fake-key"})
    assert (result["step_id"], result["errors"]) == ("services", {"decoder_url": error})
    assert result["description_placeholders"] == {"url": DECODER}
    assert suggested(result, "aviationstack_key") is None                  # the key is not echoed back
    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{DECODER}/health", json={"ok": True, "api": 1})
    result = await configure(hass, result, {"decoder_url": DECODER})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["options"]["decoder_url"] == DECODER


async def test_decoder_suggested_from_the_supervisor(hass: HomeAssistant, no_setup) -> None:
    client = MagicMock()
    client.addons.list = AsyncMock(return_value=[MagicMock(slug="a0d7b954_ssh"),
                                                 MagicMock(slug="5c53de3b_barcode_decoder"),
                                                 MagicMock(slug="local_barcode_decoder")])
    client.addons.addon_info = AsyncMock(return_value=MagicMock(hostname="local-barcode-decoder"))
    with patch.object(config_flow, "is_hassio", return_value=True), \
            patch("homeassistant.components.hassio.get_supervisor_client", return_value=client):
        result = await configure(hass, await start(hass))
    assert suggested(result, "decoder_url") == "http://local-barcode-decoder:8099"
    client.addons.addon_info.assert_awaited_once_with("local_barcode_decoder")
    client.addons.list = AsyncMock(side_effect=RuntimeError("no supervisor"))
    with patch.object(config_flow, "is_hassio", return_value=True), \
            patch("homeassistant.components.hassio.get_supervisor_client", return_value=client):
        result = await configure(hass, await start(hass))
    assert suggested(result, "decoder_url") is None


# ---------------------------------------------------------------- reconfigure and reauth
async def test_reconfigure_the_key(hass: HomeAssistant, no_setup) -> None:
    entry = make_entry(data={"aviationstack_key": "old-key"})
    entry.add_to_hass(hass)
    result = await entry.start_reconfigure_flow(hass)
    assert (result["type"], result["step_id"]) == (FlowResultType.FORM, "reconfigure")
    assert suggested(result, "aviationstack_key") is None                  # never shown
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"aviationstack_key": "new-key"})
    assert (result["type"], result["reason"]) == (FlowResultType.ABORT, "reconfigure_successful")
    assert entry.data == {"aviationstack_key": "new-key"}
    result = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["reason"] == "reconfigure_successful"
    assert entry.data == {"aviationstack_key": None}                        # empty: no AviationStack
    await hass.async_block_till_done()


async def test_reauth_by_hand(hass: HomeAssistant, no_setup) -> None:
    entry = make_entry(data={"aviationstack_key": "refused-key"})
    entry.add_to_hass(hass)
    result = await entry.start_reauth_flow(hass)
    assert (result["type"], result["step_id"]) == (FlowResultType.FORM, "reauth_confirm")
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"aviationstack_key": "  "})
    assert result["errors"] == {"aviationstack_key": "invalid_key"}
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"aviationstack_key": "good-key"})
    assert (result["type"], result["reason"]) == (FlowResultType.ABORT, "reauth_successful")
    assert entry.data == {"aviationstack_key": "good-key"}
    await hass.async_block_till_done()


def _due_flight() -> int:
    """A flight of today whose check is due (executor, inside engine.job)."""
    today = datetime.datetime.now(datetime.UTC).date().isoformat()
    with engine.transaction() as c:
        return c.execute("INSERT INTO flights (airline, number, date, origin, destination, next_check, created_at) "
                         "VALUES ('FR', '1234', ?, 'BLQ', 'STN', '2000-01-01T00:00:00Z', ?)",
                         (today, engine.now_iso())).lastrowid


def _make_due(flight_id: int) -> None:
    engine.update_flight(flight_id, next_check="2000-01-01T00:00:00Z")


def _calls() -> int:
    return engine.q("SELECT coalesce(sum(calls), 0) AS n FROM api_usage", one=True)["n"]


async def test_reauth_started_by_the_real_hook(hass: HomeAssistant) -> None:
    """INTEGRATION_PLAN.md 7.3: a 401 from a patched flights.lookup_flight through rt.run(engine.check_flights, ...)
    gives exactly one reauth flow (R-D: the hook only hops to the loop; once per runtime; HA also skips a second
    flow while one is active). The new key reaches the engine after the reload."""
    people = await People().async_create(hass)
    entry = await async_setup(hass, make_entry(people.subentries(), data={"aviationstack_key": "refused-key"}))
    assert entry.state is ConfigEntryState.LOADED
    rt = entry.runtime_data
    assert engine._on_auth_error == rt.on_auth_error
    lookups = []

    def refused(*args):
        lookups.append(args[0])
        raise engine.flights.APIError("AviationStack 401: invalid_access_key", 401)

    flight = await rt.run(_due_flight)
    with patch.object(engine.flights, "lookup_flight", refused):
        assert await rt.run(engine.check_flights, rt.cfg()) == 1
        await hass.async_block_till_done()
        assert await rt.run(engine.check_flights, rt.cfg()) == 0           # the 6 h wait is written: no call
        await rt.run(_make_due, flight)
        assert await rt.run(engine.check_flights, rt.cfg()) == 1           # refused again: the hook again
        await hass.async_block_till_done()
    assert lookups == ["refused-key", "refused-key"]
    assert await rt.run(_calls) == 2
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [(f["context"]["source"], f["context"]["entry_id"], f["step_id"]) for f in flows] == [
        (SOURCE_REAUTH, entry.entry_id, "reauth_confirm")]

    hass.set_state(CoreState.starting)           # the reload below: no worker or clock in this test
    try:
        result = await hass.config_entries.flow.async_configure(flows[0]["flow_id"],
                                                                {"aviationstack_key": "new-key"})
        assert (result["type"], result["reason"]) == (FlowResultType.ABORT, "reauth_successful")
        await hass.async_block_till_done()
    finally:
        hass.set_state(CoreState.running)
    assert entry.state is ConfigEntryState.LOADED
    assert entry.runtime_data is not rt and entry.data == {"aviationstack_key": "new-key"}
    assert engine.AVIATIONSTACK_KEY == "new-key"
    assert not entry.runtime_data._reauth_started
    assert hass.config_entries.flow.async_progress_by_handler(DOMAIN) == []
    assert await hass.config_entries.async_unload(entry.entry_id)


# ---------------------------------------------------------------- the texts (translations/en.json, it.json)
TRANSLATIONS = os.path.join(os.path.dirname(config_flow.__file__), "translations")
PLAN_TEXTS = {   # INTEGRATION_PLAN.md 4.4, 5.5, 5.6: the Italian texts, verbatim
    "config.step.user.description": "Boarding Pass Tracker legge le carte d'imbarco, segue i voli e ti avvisa. Foto, "
    "carte d'imbarco e nomi restano in Home Assistant. Escono solo: il codice del volo verso AviationStack e "
    "Flightradar24 (tramite la sua integrazione), la posizione del gate cercata su OpenStreetMap, e il testo delle "
    "notifiche, che passa dal servizio push dell'app Home Assistant (Apple/Google). Le foto vanno solo al "
    "decodificatore che indichi.",
    "config.abort.entity_ids_taken": "Questi id sono già usati da un'altra integrazione: {ids}. Se sono i sensori "
    "MQTT del vecchio servizio, fermalo e cancella i suoi topic (guida, passo 2.5), poi riprova.",
    "config.step.adopt.description": "Ho trovato i dati di un'installazione precedente: {flights} voli, {documents} "
    "documenti, {files} file, {passes} carte d'imbarco (schema v{schema}); {queued} in coda, {unnotified} da "
    "notificare. Li uso? I dati restano dove sono; se serve aggiornarli, prima ne faccio una copia in backups/.",
    "config.abort.newer_database": "Questi dati sono di una versione più recente di Boarding Pass Tracker: aggiorna "
    "l'integrazione.",
    "config.abort.foreign_database": "Il file tracker.db non è di Boarding Pass Tracker: spostalo e riprova.",
    "config.abort.old_database_v0": "Questi dati sono della vecchia App Casa (schema v0): convertili prima con il "
    "servizio boarding-pass-tracker 0.1.0.",
    "config.abort.broken_database": "Il file tracker.db è danneggiato: rifai l'esportazione.",
    "config.abort.file_mismatch": "{n} file mancano o non corrispondono: rifai l'esportazione.",
    "config.step.person_found.data.skip": "Nessuna notifica per questo nome",
    "config.step.person_found.description": "Persona «{name}» trovata nei dati: scegli il suo utente di Home "
    "Assistant (per le Scorciatoie), il telefono per le notifiche e la persona per il promemoria del gate.",
    "config.error.invalid_name": "Il nome deve avere solo lettere minuscole, senza spazi (per esempio alice).",
    "config.error.name_taken": "C'è già una persona con questo nome.",
    "config.error.user_taken": "Questo utente di Home Assistant è già collegato a un'altra persona.",
    "config.error.invalid_notify": "Il servizio di notifica deve essere un notify.mobile_app_… (l'app Home "
    "Assistant sul telefono).",
    "config.error.invalid_person": "Scegli un'entità person.",
    "config.error.invalid_url": "Indirizzo non valido: scrivi http://host:porta, senza percorso.",
    "config.error.decoder_unreachable": "Il decodificatore non risponde a {url}: controlla l'app Barcode Decoder o "
    "lascia vuoto il campo.",
    "config.error.decoder_wrong_api": "A {url} risponde qualcosa che non è il decodificatore (api 1).",
    "config.step.reauth_confirm.description": "AviationStack ha rifiutato la chiave: incollane una valida (piano "
    "gratuito: 100 chiamate al mese).",
    "config_subentries.person.entry_type": "Persona",
    "config_subentries.person.initiate_flow.user": "Aggiungi persona",
    "config_subentries.person.error.name_in_use": "Questo nome è già nei dati dei voli: non si può cambiare. "
    "Aggiungi un'altra persona se serve.",
    "issues.entity_id_taken.title": "Sensore con id diverso",
    "issues.entity_id_taken.description": "Il sensore {actual} doveva chiamarsi {wanted}, che è già usato da "
    "un'altra entità: le card e le dashboard usano {wanted}. Elimina o rinomina l'altra entità, poi rinomina questo "
    "sensore in {wanted}.",
    "issues.decoder_unreachable.title": "Decodificatore non raggiungibile",
    "issues.decoder_unreachable.description": "Il decodificatore ({url}) non risponde da {minutes} minuti e {n} foto "
    "aspettano: controlla l'app Barcode Decoder.",
    "issues.notify_service_missing.title": "Notifiche per {name} ferme",
    "issues.notify_service_missing.description": "Le notifiche per {name} non partono: il servizio "
    "notify.{service} non esiste più (telefono rinominato o tolto). Aggiorna la persona in Boarding Pass Tracker.",
    "exceptions.migration_failed.message": "L'aggiornamento dei dati alla versione 2 non è riuscito e i dati sono "
    "rimasti come prima: c'è una copia in backups/. Apri una segnalazione con i log.",
    "entity.sensor.next_flight.name": "Prossimo volo",
    "entity.sensor.upcoming_flights.name": "Voli",
    "entity.sensor.flight_statistics.name": "Statistiche voli",
}
PLAN_EN = {"issues.entity_id_taken.title": "Sensor has a different id",
           "issues.decoder_unreachable.title": "Decoder unreachable",
           "issues.notify_service_missing.title": "Notifications for {name} stopped",
           "entity.sensor.next_flight.name": "Next flight", "entity.sensor.upcoming_flights.name": "Flights",
           "entity.sensor.flight_statistics.name": "Flight statistics"}


def _flat(d, prefix=""):
    out = {}
    for k, v in d.items():
        out.update(_flat(v, f"{prefix}{k}.") if isinstance(v, dict) else {f"{prefix}{k}": v})
    return out


def _load(lang):
    with open(os.path.join(TRANSLATIONS, f"{lang}.json"), encoding="utf-8") as f:
        return _flat(json.load(f))


def _placeholders(text):
    import string
    return {f for _, f, _, _ in string.Formatter().parse(text) if f}


def test_the_texts() -> None:
    """Both languages have the same keys and placeholders; the plan's texts are verbatim; every step, error and
    abort of the flows is translated (read from config_flow.py's AST). A plain test: no hass, no event loop."""
    import ast
    it, en = _load("it"), _load("en")
    assert set(it) == set(en)
    for key in it:
        assert _placeholders(it[key]) == _placeholders(en[key]), key
    for key, text in PLAN_TEXTS.items():
        assert it[key] == text, key
    for key in ("newer_database", "foreign_database", "old_database_v0"):
        assert it[f"exceptions.{key}.message"] == it[f"config.abort.{key}"]
    for key, text in PLAN_EN.items():
        assert en[key] == text, key
    with open(config_flow.__file__, encoding="utf-8") as f:
        tree = ast.parse(f.read())
    used = {"step": set(), "error": set(), "abort": set()}
    for node in ast.walk(tree):
        if isinstance(node, ast.keyword) and isinstance(node.value, ast.Constant):
            if node.arg == "step_id":
                used["step"].add(node.value.value)
            if node.arg == "reason":
                used["abort"].add(node.value.value)
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value in (
                "invalid_name", "name_taken", "user_taken", "invalid_notify", "invalid_person", "invalid_url",
                "decoder_unreachable", "decoder_wrong_api", "invalid_key", "name_in_use"):
            used["error"].add(node.value)
    assert used["step"] == {"user", "adopt", "person_found", "person_config", "people", "person", "services",
                            "reconfigure", "reauth_confirm", "init"}
    flow = {k.split(".")[2] for k in it if k.startswith("config.step.")} | {"init", "user", "reconfigure"}
    assert used["step"] <= flow
    for reason in used["abort"] | set(config_flow.adopt.REASONS):
        assert f"config.abort.{reason}" in it, reason
    person_errors = {"invalid_name", "name_taken", "user_taken", "invalid_notify", "invalid_person"}
    decoder_errors = {"invalid_url", "decoder_unreachable", "decoder_wrong_api"}
    assert used["error"] == person_errors | decoder_errors | {"invalid_key", "name_in_use"}
    for error in person_errors:
        assert {f"config.error.{error}", f"config_subentries.person.error.{error}"} <= set(it), error
    for error in decoder_errors:
        assert {f"config.error.{error}", f"options.error.{error}"} <= set(it), error
    assert {"config.error.invalid_key", "config_subentries.person.error.name_in_use"} <= set(it)
    for issue in ("entity_id_taken", "decoder_unreachable", "notify_service_missing"):
        assert {f"issues.{issue}.title", f"issues.{issue}.description"} <= set(it)


async def test_home_assistant_loads_the_italian_texts(hass: HomeAssistant) -> None:
    from homeassistant.helpers.translation import async_get_translations
    texts = await async_get_translations(hass, "it", "config", [DOMAIN])
    assert texts[f"component.{DOMAIN}.config.abort.broken_database"] == PLAN_TEXTS["config.abort.broken_database"]
    texts = await async_get_translations(hass, "it", "entity", [DOMAIN])
    assert texts[f"component.{DOMAIN}.entity.sensor.next_flight.name"] == "Prossimo volo"
    texts = await async_get_translations(hass, "it", "exceptions", [DOMAIN])
    assert texts[f"component.{DOMAIN}.exceptions.migration_failed.message"].startswith("L'aggiornamento")
    texts = await async_get_translations(hass, "en", "config_subentries", [DOMAIN])
    assert texts[f"component.{DOMAIN}.config_subentries.person.initiate_flow.user"] == "Add person"
