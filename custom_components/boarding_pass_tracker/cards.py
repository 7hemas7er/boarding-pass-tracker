"""The three Lovelace cards (INTEGRATION_PLAN.md 5.11; the manage card, integration only, came after 1.0.0): served from /boarding_pass_tracker/frontend/<file> (a static
path, registered once per HA run, no auth: the files hold no data). This folder is the cards' only source (C1, checked by
tests/integration/test_other.py).

Only the loader (frontend/boarding-pass-tracker-loader.js) is added to every page, through add_extra_js_url with
?v=<manifest version>-<hash of the frontend files>, so a new version, or a changed card under the same version,
busts the 31-day cache. The frontend imports the extra modules at the same
time as its own bundles, and its app bundle then replaces window.customElements with its own registry, which does
not see a card defined earlier (Lovelace shows an error card: PLATFORM-01, measured by WP6 at L3). The loader waits
for that registry, then imports the card files with the same ?v=.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from homeassistant.components.frontend import add_extra_js_url, remove_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant, callback

from .const import FRONTEND_URL, LOADER_FILE

FRONTEND_DIR = Path(__file__).parent / "frontend"


def _frontend_hash() -> str:
    """8 hex digits of sha256 over the frontend files (names and bytes, sorted). Computed once at import, which
    Home Assistant does in the executor: never file I/O in the event loop."""
    h = hashlib.sha256()
    for p in sorted(FRONTEND_DIR.glob("*.js")):
        h.update(p.name.encode() + b"\0" + p.read_bytes() + b"\0")
    return h.hexdigest()[:8]


FRONTEND_HASH = _frontend_hash()


def loader_url(version: str) -> str:
    return f"{FRONTEND_URL}/{LOADER_FILE}?v={version}-{FRONTEND_HASH}"


async def async_register_static(hass: HomeAssistant) -> None:
    """Once per HA run (async_setup): a second registration of the same path raises."""
    await hass.http.async_register_static_paths([StaticPathConfig(FRONTEND_URL, str(FRONTEND_DIR),
                                                                  cache_headers=True)])


@callback
def async_add(hass: HomeAssistant, version: str) -> None:
    add_extra_js_url(hass, loader_url(version))


@callback
def async_remove(hass: HomeAssistant, version: str) -> None:
    try:
        remove_extra_js_url(hass, loader_url(version))
    except KeyError:                  # never added (a setup that failed before step 7)
        pass
