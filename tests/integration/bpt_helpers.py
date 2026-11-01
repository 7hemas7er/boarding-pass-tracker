"""Helpers of the integration's tests: fictional HA users with tokens (alice, bob), person subentries, the config
entry, and waiting for the runtime's background work. Nothing here is a real person's data."""
from __future__ import annotations

import asyncio
import json

from homeassistant.auth.const import GROUP_ID_USER
from homeassistant.config_entries import ConfigEntryState, ConfigSubentryData
from homeassistant.core import CoreState, HomeAssistant

from pytest_homeassistant_custom_component.common import MockConfigEntry, MockUser, async_mock_service

from custom_components.boarding_pass_tracker.const import DOMAIN, SUBENTRY_PERSON

API = f"/api/{DOMAIN}"


async def async_user(hass: HomeAssistant, name: str, **kwargs) -> tuple[MockUser, str]:
    """A non-admin HA user (the users group) and a long-lived-like access token of it."""
    group = await hass.auth.async_get_group(GROUP_ID_USER)
    user = MockUser(name=name, groups=[group], **kwargs).add_to_hass(hass)
    refresh = await hass.auth.async_create_refresh_token(user, "https://example.invalid/")
    return user, hass.auth.async_create_access_token(refresh)


def person(name: str, user_id: str | None, notify: str | None = None, entity: str | None = None) -> ConfigSubentryData:
    return ConfigSubentryData(data={"name": name, "ha_user": user_id, "notify": notify or f"mobile_app_{name}",
                                    "person": entity or f"person.{name}"},
                              subentry_type=SUBENTRY_PERSON, title=name, unique_id=name)


def make_entry(people=(), options=None, data=None) -> MockConfigEntry:
    return MockConfigEntry(domain=DOMAIN, title="Boarding Pass Tracker", data=data or {"aviationstack_key": None},
                           options={"monthly_cap": 95, "flights_enabled": True, "notify_enabled": True,
                                    **(options or {})},
                           subentries_data=list(people), version=1, minor_version=1)


async def async_setup(hass: HomeAssistant, entry: MockConfigEntry, start: bool = False) -> MockConfigEntry:
    """Add and set up the entry. start=False: HA looks «starting» during the setup, so the worker and the clock do
    not start (async_at_started waits for an event that never comes); the tests drive the jobs themselves."""
    entry.add_to_hass(hass)
    if not start:
        hass.set_state(CoreState.starting)
    try:
        assert await hass.config_entries.async_setup(entry.entry_id) in (True, False)
        await hass.async_block_till_done()
    finally:
        hass.set_state(CoreState.running)
    return entry


async def async_reload(hass: HomeAssistant, entry, start: bool = False) -> None:
    """Reload the entry; start=False as in async_setup (no worker, no clock: the test drives the jobs)."""
    if not start:
        hass.set_state(CoreState.starting)
    try:
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
    finally:
        hass.set_state(CoreState.running)


class People:
    """alice and bob: HA users with tokens, person subentries, notify mocks, person states."""

    def __init__(self) -> None:
        self.users: dict[str, MockUser] = {}
        self.tokens: dict[str, str] = {}
        self.notify: dict[str, list] = {}

    async def async_create(self, hass: HomeAssistant, names=("alice", "bob"), mock_notify=True) -> "People":
        for name in names:
            self.users[name], self.tokens[name] = await async_user(hass, name)
            if mock_notify:
                self.notify[name] = async_mock_service(hass, "notify", f"mobile_app_{name}")
            hass.states.async_set(f"person.{name}", "home", {"user_id": self.users[name].id})
        return self

    def subentries(self, names=None) -> list[ConfigSubentryData]:
        return [person(n, self.users[n].id) for n in (names or self.users)]


async def wait_round(rt, timeout: float = 30) -> None:
    """The clock round in flight, if any, to its end."""
    task = rt._round_task
    if task is not None:
        await asyncio.wait_for(asyncio.shield(task), timeout)


async def body(response) -> dict:
    return json.loads(await response.read())


def loaded(entry) -> bool:
    return entry.state is ConfigEntryState.LOADED


# ---------------------------------------------------------------- fictional boarding passes (tests/core/test_flights)
def bcbp(name="ROSSI/MARIO MR", pnr="XYZ789", origin="BLQ", destination="STN", airline="FR", number="1234", day=310,
         seat="012A", seq="0042", cabin="Y"):
    """A one-leg BCBP with test_flights.bcbp's fictional defaults."""
    leg = f"{pnr:<7}{origin}{destination}{airline:<3}{number:0>4} {day:03d}{cabin}{seat:>4}{seq:<5}100"
    return f"M1{name:<20}E" + leg


def pkpass(msg=None, **pass_):
    """A Wallet boarding pass (zip with pass.json), as test_flights.pkpass builds it."""
    import io
    import zipfile
    p = {"formatVersion": 1, "passTypeIdentifier": "pass.it.prova", "serialNumber": "1",
         "authenticationToken": "segreto-del-pass", "webServiceURL": "https://example.invalid/",
         "boardingPass": {"transitType": "PKTransitTypeAir", "primaryFields": []}, **pass_}
    if msg is not None:
        p["barcodes"] = [{"format": "PKBarcodeFormatAztec", "message": msg, "messageEncoding": "iso-8859-1"}]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("pass.json", json.dumps(p))
        z.writestr("manifest.json", "{}")
        z.writestr("signature", b"\x00")
    return buf.getvalue()


def julian(day) -> int:
    return day.timetuple().tm_yday
