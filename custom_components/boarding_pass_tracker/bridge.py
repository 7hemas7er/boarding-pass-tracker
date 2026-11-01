"""The core's way into Home Assistant (INTEGRATION_PLAN.md 3.3, 5.3, 5.8).

HassBridge is the engine's `_ha`: the core's ha.HA facade (notify kept word for word) with its two transport hooks
implemented in process. The hooks run in an executor thread (the core only runs there) and wait for the event loop
at most BRIDGE_TIMEOUT_S (R-C). Called on the loop thread they raise RuntimeError at once (C10): waiting there for
the loop would block it for the whole timeout.

LoopWakeup is the engine's `_wakeup`: set() from any thread wakes the worker task on the loop.

The repair issue of a missing notify service is raised and cleared inside the coroutine the bridge submits to the
loop (_async_call), never from the executor (R-D).
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading

from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ServiceNotFound
from homeassistant.util.async_ import run_callback_threadsafe

from . import issues
from .const import BRIDGE_TIMEOUT_S
from .core import ha

_LOGGER = logging.getLogger(__name__)


def refuse_loop_thread(hass: HomeAssistant, what: str) -> None:
    """C10: RuntimeError when called on the event loop's thread."""
    if threading.get_ident() == hass.loop_thread_id:
        raise RuntimeError(f"{what} called from the event loop")


class HassBridge(ha.HA):
    """ha.HA whose transport is this Home Assistant: hass.states and hass.services, reached from the executor."""

    def __init__(self, hass: HomeAssistant, entry) -> None:
        super().__init__(timeout=BRIDGE_TIMEOUT_S, group=ha.DEFAULT_PREFIX)
        self.hass = hass
        self.entry = entry

    # ------------------------------------------------------------ executor side
    def _get_state(self, entity):
        """The state as GET /api/states/<entity> gave it to the standalone (a JSON round trip), None if missing."""
        refuse_loop_thread(self.hass, "HassBridge")
        try:
            raw = run_callback_threadsafe(self.hass.loop, self._state_json, entity).result(self.timeout)
        except TimeoutError:
            raise ha.HAError(f"HA did not answer for the state of {entity} (timeout)") from None
        except Exception as e:
            raise ha.HAError(f"HA error on the state of {entity} ({type(e).__name__})") from None
        return None if raw is None else json.loads(raw)

    def _call_service(self, domain, name, data):
        """<domain>.<name> with `data`, blocking until HA ran it: HAError if it failed or HA did not answer."""
        refuse_loop_thread(self.hass, "HassBridge")
        if self.hass.is_stopping:
            raise ha.HAError("Home Assistant is stopping")
        future = asyncio.run_coroutine_threadsafe(self._async_call(domain, name, data), self.hass.loop)
        try:
            future.result(self.timeout)
        except TimeoutError:
            future.cancel()
            raise ha.HAError(f"HA did not answer on {domain}.{name} (timeout)") from None
        except Exception as e:
            raise ha.HAError(f"HA error on {domain}.{name} ({type(e).__name__})") from None

    # ------------------------------------------------------------ loop side
    @callback
    def _state_json(self, entity):
        st = self.hass.states.get(entity)
        return None if st is None else st.as_dict_json

    async def _async_call(self, domain, name, data):
        try:
            await self.hass.services.async_call(domain, name, data, blocking=True)
        except ServiceNotFound:
            if domain == "notify":
                issues.async_notify_missing(self.hass, self.entry, name)
            raise
        if domain == "notify":
            issues.async_notify_ok(self.hass, self.entry, name)


class LoopWakeup:
    """engine._wakeup: set() from the executor (an upload saved, a document sent again) wakes the worker task."""

    def __init__(self, loop: asyncio.AbstractEventLoop, event: asyncio.Event) -> None:
        self._loop = loop
        self.event = event

    def set(self) -> None:
        if self._loop.is_closed():
            return
        try:
            self._loop.call_soon_threadsafe(self.event.set)
        except RuntimeError:            # the loop closed in the meantime (HA stopping): nobody to wake
            pass
