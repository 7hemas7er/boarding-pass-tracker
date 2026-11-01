"""T2 other (WP2, INTEGRATION_PLAN.md 4.6, 5.7, 5.11, 7.3): the cards (static files, extra JS with ?v=, removed on
unload) and C1 (the cards and the loader in frontend/); the backup hooks (never raise, safety timer, re-armed after a reload
during a backup, C12); the diagnostics (redacted, no names in the issue ids, CMS-03); system health; the time-zone
event."""
import datetime
import hashlib
import json
import os
import re
import zoneinfo
from pathlib import Path
from unittest.mock import patch

from homeassistant.components.frontend import DATA_EXTRA_MODULE_URL
from homeassistant.helpers import issue_registry as ir
from homeassistant.setup import async_setup_component

from pytest_homeassistant_custom_component.common import async_fire_time_changed, get_system_health_info
from pytest_homeassistant_custom_component.components.diagnostics import _get_diagnostics_for_config_entry

from custom_components.boarding_pass_tracker import backup, issues
from custom_components.boarding_pass_tracker.cards import FRONTEND_HASH
from custom_components.boarding_pass_tracker.const import CARD_FILES, DOMAIN, LOADER_FILE, MANAGE_CARD_FILE
from custom_components.boarding_pass_tracker.core import api, engine, flights

from bpt_helpers import People, async_reload, async_setup, bcbp, julian, make_entry, pkpass

REPO = Path(__file__).resolve().parents[2]
CC = REPO / "custom_components" / "boarding_pass_tracker"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


async def _setup(hass, **kwargs):
    people = await People().async_create(hass)
    entry = await async_setup(hass, make_entry(people.subentries(), **kwargs))
    return people, entry, entry.runtime_data


# ---------------------------------------------------------------- cards
def test_c1_the_frontend_holds_the_cards_and_the_loader():
    """C1: CC/frontend holds the three cards (their only source: dashboards/ has only the dashboard) and the loader
    that imports them."""
    assert sorted(p.name for p in (CC / "frontend").iterdir()) == sorted((*CARD_FILES, MANAGE_CARD_FILE, LOADER_FILE))
    assert not list((REPO / "dashboards").glob("*.js"))


def test_the_loader_imports_the_three_cards_after_the_frontends_registry():
    """PLATFORM-01: the loader imports exactly the three card files, relative to its own URL with its own ?v=, and only
    once window.customElements is the frontend's registry (or <home-assistant> is defined); the cards' own define
    is unchanged."""
    text = (CC / "frontend" / LOADER_FILE).read_text(encoding="utf-8")
    assert text.count("customElements.define") == 0
    assert re.findall(r'"(boarding-pass-tracker-[a-z-]+\.js)"', text) == [*CARD_FILES, MANAGE_CARD_FILE]
    assert "new URL(import.meta.url).search" in text and "new URL(name + query, import.meta.url)" in text
    assert "window.customElements !== first" in text and 'window.customElements.get("home-assistant")' in text
    assert "import(" in text and "setTimeout(wait" in text
    for name in (*CARD_FILES, MANAGE_CARD_FILE):             # the cards define themselves at evaluation
        assert 'customElements.define("' in (CC / "frontend" / name).read_text(encoding="utf-8")


async def test_cards_static_files_and_extra_js(hass, hass_client_no_auth):
    people, entry, rt = await _setup(hass)
    urls = hass.data[DATA_EXTRA_MODULE_URL].urls
    # PLATFORM-01: only the loader is an extra module; the card files are imported by it, never by the page
    ours = [u for u in urls if "boarding_pass_tracker" in u]
    v = f"1.0.0-{FRONTEND_HASH}"                             # the version and a hash of the frontend files
    assert re.fullmatch(r"1\.0\.0-[0-9a-f]{8}", v)
    assert ours == [f"/boarding_pass_tracker/frontend/{LOADER_FILE}?v={v}"]
    client = await hass_client_no_auth()                     # static files: no auth, no data in them
    for name in CARD_FILES:
        r = await client.get(f"/boarding_pass_tracker/frontend/{name}?v={v}")
        assert r.status == 200
        expected = await hass.async_add_executor_job((CC / "frontend" / name).read_bytes)
        assert await r.read() == expected
        assert "javascript" in r.headers["Content-Type"]
        assert r.headers.get("Cache-Control") == "public, max-age=2678400"
    r = await client.get(f"/boarding_pass_tracker/frontend/{MANAGE_CARD_FILE}?v={v}")
    assert r.status == 200 and "javascript" in r.headers["Content-Type"]
    assert await r.read() == await hass.async_add_executor_job((CC / "frontend" / MANAGE_CARD_FILE).read_bytes)
    r = await client.get(f"/boarding_pass_tracker/frontend/{LOADER_FILE}?v={v}")
    assert r.status == 200 and "javascript" in r.headers["Content-Type"]
    assert r.headers.get("Cache-Control") == "public, max-age=2678400"
    assert await r.read() == await hass.async_add_executor_job((CC / "frontend" / LOADER_FILE).read_bytes)
    assert await hass.config_entries.async_unload(entry.entry_id)
    urls = hass.data[DATA_EXTRA_MODULE_URL].urls
    assert not [u for u in urls if "boarding_pass_tracker" in u]
    r = await client.get(f"/boarding_pass_tracker/frontend/{CARD_FILES[0]}")
    assert r.status == 200                                   # the static path stays for the HA run


# ---------------------------------------------------------------- backups (C12)
def _wal_and_autocheckpoint(path):
    import sqlite3  # noqa: PLC0415
    c = sqlite3.connect(path)
    try:
        return os.path.getsize(path + "-wal") if os.path.exists(path + "-wal") else None
    finally:
        c.close()


async def test_backup_hooks_checkpoint_hold_and_release(hass, freezer, caplog):
    people, entry, rt = await _setup(hass)
    alice = rt.person_for(people.users["alice"].id)
    day = datetime.date.today() + datetime.timedelta(days=2)
    assert (await rt.run(api.upload, None, pkpass(bcbp(day=julian(day))), alice))[0] == 200
    db = os.path.join(engine.DATA_DIR, "tracker.db")
    await backup.async_pre_backup(hass)
    assert engine._backup_active is True and rt._backup_unsub is not None
    assert await hass.async_add_executor_job(_wal_and_autocheckpoint, db) == 0      # folded into tracker.db
    assert (await rt.run(engine.q, "PRAGMA wal_autocheckpoint"))[0]["wal_autocheckpoint"] == 0
    assert (await rt.run(api.upload, None, pkpass(bcbp(day=julian(day), number="77")), alice))[0] == 200  # writes go on
    await backup.async_post_backup(hass)
    assert engine._backup_active is False and rt._backup_unsub is None
    assert (await rt.run(engine.q, "PRAGMA wal_autocheckpoint"))[0]["wal_autocheckpoint"] == 1000
    # the safety timer: no post-backup within 30 minutes
    await backup.async_pre_backup(hass)
    freezer.tick(datetime.timedelta(minutes=31))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert engine._backup_active is False and rt._backup_unsub is None
    assert "No end of the backup after 30 minutes" in caplog.text


async def test_backup_hooks_never_raise(hass, caplog):
    people, entry, rt = await _setup(hass)
    with patch.object(engine, "backup_begin", side_effect=RuntimeError("boom")), \
            patch.object(engine, "backup_end", side_effect=OSError("disk")):
        await backup.async_pre_backup(hass)
        await backup.async_post_backup(hass)
    assert "Backup start: RuntimeError" in caplog.text and "Backup end: OSError" in caplog.text
    assert await hass.config_entries.async_unload(entry.entry_id)
    await backup.async_pre_backup(hass)                       # no entry loaded: still fine
    assert engine._backup_active is True
    await backup.async_post_backup(hass)
    assert engine._backup_active is False


async def test_a_reload_during_a_backup_rearms_the_safety_timer_and_keeps_the_wal(hass):
    people, entry, rt = await _setup(hass)
    alice = rt.person_for(people.users["alice"].id)
    day = datetime.date.today() + datetime.timedelta(days=2)
    await backup.async_pre_backup(hass)
    assert (await rt.run(api.upload, None, pkpass(bcbp(day=julian(day))), alice))[0] == 200
    db = os.path.join(engine.DATA_DIR, "tracker.db")
    wal = await hass.async_add_executor_job(os.path.getsize, db + "-wal")
    assert wal > 0
    await async_reload(hass, entry)                           # shutdown with NO_CKPT_ON_CLOSE: -wal stays
    rt2 = entry.runtime_data
    assert rt2 is not rt and engine._backup_active and rt2._backup_unsub is not None
    assert await hass.async_add_executor_job(os.path.exists, db + "-wal")
    assert (await rt2.run(engine.q, "PRAGMA wal_autocheckpoint"))[0]["wal_autocheckpoint"] == 0
    await backup.async_post_backup(hass)
    assert rt2._backup_unsub is None and not engine._backup_active


# ---------------------------------------------------------------- diagnostics and system health
async def test_diagnostics_are_redacted_and_the_issue_ids_hold_no_names(hass, hass_client):
    people, entry, rt = await _setup(hass, data={"aviationstack_key": "secret-key-123"},
                                     options={"decoder_url": "http://decoder.invalid:8099"})
    alice_sub = next(s for s in entry.subentries.values() if s.data["name"] == "alice")
    issues.async_entity_ids_taken(hass, [("sensor.boarding_pass_tracker_next_flight",
                                          "sensor.boarding_pass_tracker_next_flight_2")])
    issues.async_decoder_report(hass, "http://decoder.invalid:8099",
                                {"decoder_status": "down", "decoder_down_since": 0.0, "queued_photos": 2})
    issues.async_notify_missing(hass, entry, "mobile_app_alice")
    issues.async_notify_missing(hass, entry, "mobile_app_bob")
    assert len([i for (d, i) in ir.async_get(hass).issues if d == DOMAIN]) == 4
    full = await _get_diagnostics_for_config_entry(hass, hass_client, entry)
    text = json.dumps(full, ensure_ascii=False)
    for secret in ("alice", "bob", "mobile_app_alice", "person.alice", "person.bob", "secret-key-123",
                   people.users["alice"].id, people.users["bob"].id):
        assert secret not in text, secret
    ids = sorted(i["issue_id"] for i in full["issues"])
    assert ids == sorted(["entity_id_taken", "decoder_unreachable",
                          f"notify_service_missing_{alice_sub.subentry_id}",
                          f"notify_service_missing_{next(s.subentry_id for s in entry.subentries.values() if s.data['name'] == 'bob')}"])
    data = full["data"]
    assert data["entry"]["persons"] == 2 and data["entry"]["key_set"] is True
    assert data["entry"]["options"]["decoder_url"] == "http://decoder.invalid:8099"     # kept: not personal
    assert data["database"]["schema"] == engine.SCHEMA_VERSION and data["database"]["application_id"] == "0x42505454"
    assert data["database"]["documents"] == {"queued": 0, "read": 0, "archived": 0, "error": 0, "unsent": 0}
    assert data["decoder"]["configured"] is True
    assert data["time_zone"] == hass.config.time_zone and data["fr24"] == {"fr24_add": False, "fr24_remove": False,
                                                                           "fr24_tracked": False}


async def test_system_health(hass, aioclient_mock):
    assert await async_setup_component(hass, "system_health", {})
    people, entry, rt = await _setup(hass, options={"decoder_url": "http://decoder.invalid:8099"})
    aioclient_mock.get("http://decoder.invalid:8099/health", json={"ok": True, "api": 1})
    info = await get_system_health_info(hass, DOMAIN)
    assert info["queued"] == 0 and info["aviationstack_calls"] == "0/95"
    assert info["data_folder"] == hass.config.path("boarding_pass_tracker")
    assert await info["decoder"] == "ok"
    aioclient_mock.clear_requests()
    hass.config_entries.async_update_entry(entry, options={**entry.options, "decoder_url": None})
    await async_reload(hass, entry)
    info = await get_system_health_info(hass, DOMAIN)
    assert info["decoder"] == "off"
    assert await hass.config_entries.async_unload(entry.entry_id)
    info = await get_system_health_info(hass, DOMAIN)
    assert info["loaded"] is False


async def test_the_time_zone_event(hass):
    people, entry, rt = await _setup(hass)
    assert engine.LOCAL_TZ is flights.LOCAL_TZ
    assert str(engine.LOCAL_TZ) == hass.config.time_zone          # the test hass: US/Pacific
    await hass.config.async_update(time_zone="Europe/Rome")      # fires EVENT_CORE_CONFIG_UPDATE
    await hass.async_block_till_done()
    assert engine.LOCAL_TZ == zoneinfo.ZoneInfo("Europe/Rome") and flights.LOCAL_TZ == engine.LOCAL_TZ
    assert rt.settings["local_tz"] == engine.LOCAL_TZ


# ---------------------------------------------------------------- settings, registration, packaging
def test_settings_from_entry():
    from types import SimpleNamespace  # noqa: PLC0415
    from custom_components.boarding_pass_tracker.runtime import settings_from_entry  # noqa: PLC0415
    hass = SimpleNamespace(config=SimpleNamespace(path=lambda *p: "/config/" + "/".join(p)))
    tz = zoneinfo.ZoneInfo("Europe/Rome")
    entry = SimpleNamespace(data={"aviationstack_key": None}, options={})
    s = settings_from_entry(hass, entry, tz)
    assert set(s) == engine.SETTINGS_KEYS
    assert s == {"data_dir": "/config/boarding_pass_tracker", "local_tz": tz, "no_notify": False, "no_flights": False,
                 "max_api_calls_per_month": 95, "aviationstack_key": "", "fr24_add": "text.flightradar24_add_to_track",
                 "fr24_remove": "text.flightradar24_remove_from_track",
                 "fr24_tracked": "sensor.flightradar24_additional_tracked", "decoder_url": None, "read_photos": False}
    entry = SimpleNamespace(data={"aviationstack_key": "k"},
                            options={"monthly_cap": 1200.0, "decoder_url": " http://local-barcode-decoder:8099/ ",
                                     "fr24_add": "text.fr24_add", "fr24_remove": "not an id", "fr24_tracked": None,
                                     "flights_enabled": False, "notify_enabled": False})
    s = settings_from_entry(hass, entry, tz)
    assert (s["max_api_calls_per_month"], s["aviationstack_key"], s["decoder_url"], s["read_photos"]) == (
        1000, "k", "http://local-barcode-decoder:8099", True)
    assert (s["fr24_add"], s["fr24_remove"], s["fr24_tracked"]) == ("text.fr24_add", "text.flightradar24_remove_from_track",
                                                                    "sensor.flightradar24_additional_tracked")
    assert (s["no_flights"], s["no_notify"]) == (True, True)


async def test_views_and_static_path_are_registered_once_per_ha_run(hass, hass_client):
    people, entry, rt = await _setup(hass)
    for _ in range(2):
        await async_reload(hass, entry)
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    data = hass.data[DOMAIN]
    assert data.views_registered and data.static_registered and data.version == "1.0.0"
    client = await hass_client(people.tokens["alice"])
    r = await client.get("/api/boarding_pass_tracker/flights")
    assert r.status == 200 and (await r.json())["user"] == "alice"


def test_manifest_and_hacs_json():
    manifest = json.loads((CC / "manifest.json").read_text())
    assert list(manifest) == ["domain", "name", *sorted(k for k in manifest if k not in ("domain", "name"))]
    assert manifest == {"domain": "boarding_pass_tracker", "name": "Boarding Pass Tracker",
                        "after_dependencies": ["hassio", "mobile_app", "person"], "codeowners": ["@7hemas7er"],
                        "config_flow": True, "dependencies": ["frontend", "http"],
                        "documentation": "https://github.com/7hemas7er/boarding-pass-tracker",
                        "integration_type": "service", "iot_class": "cloud_polling",
                        "issue_tracker": "https://github.com/7hemas7er/boarding-pass-tracker/issues", "requirements": [],
                        "single_config_entry": True, "version": "1.0.0"}
    assert json.loads((REPO / "hacs.json").read_text()) == {"name": "Boarding Pass Tracker",
                                                             "homeassistant": "2026.10.0"}
    from custom_components import boarding_pass_tracker as bpt  # noqa: PLC0415
    assert bpt.CONFIG_SCHEMA is not None                        # hassfest: config_entry_only_config_schema


def test_brand_icons():
    from PIL import Image  # noqa: PLC0415
    for name, size in (("icon.png", 256), ("icon@2x.png", 512)):
        with Image.open(CC / "brand" / name) as img:
            assert img.format == "PNG" and img.size == (size, size), name
