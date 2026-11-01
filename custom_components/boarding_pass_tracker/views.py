"""The HTTP API of the iOS Shortcuts (INTEGRATION_PLAN.md 5.1): /api/boarding_pass_tracker/<route>, with the HA token
of the person's HA user. Registered once per HA run (there is no unregister), the catch-all last.

Order of the checks, after HA's own (no or bad token: 401 before the view; HA stopping: 503):
  1. no loaded entry, or the runtime stopping -> 503 not_ready;
  2. a POST with a query string -> 404 «percorso sconosciuto» (the standalone's exact paths; a GET ignores it);
  3. no person for the HA user -> 403 unknown_user (/health: any active user that is not a system user);
  4. the length (Content-Length) -> 400 / 413;
  5. the body, streamed with our own cap (HA's 16 MiB client_max_size applies only to read/post/multipart) and one
     deadline for all of it (408 body_timeout: /flight 30 s, /upload upload_deadline), then the core in the
     executor; engine.Closed there -> 503 not_ready (HA stopping).

The replies have the standalone's bytes and Content-Type (application/json; charset=utf-8), and its log line
«<METHOD> <path> <code> <message>» for every POST and every answer that is not 200 (api.log_reply): no name, header
or address; the path as the client sent it, percent-encoded, with anything not printable escaped. On the loop only
the core's constants (texts, limits) and that pure log line are used (R-A).
"""
from __future__ import annotations

import asyncio
import json
import logging
import re

from aiohttp import web

from homeassistant.components.http import KEY_HASS, KEY_HASS_USER, HomeAssistantView
from homeassistant.core import HomeAssistant, callback

from . import const
from .const import API_BASE, DOMAIN
from .core import api, engine, manual

_LOGGER = logging.getLogger(__name__)
NOT_FOUND = {"message": api.TEXTS["not_found"]}
_NOT_PRINTABLE = re.compile(r"[^\x20-\x7e]")


def _short(path: str) -> str:
    """The path as the standalone logged it: /flight, not /api/boarding_pass_tracker/flight."""
    return path[len(API_BASE):] or "/" if path.startswith(API_BASE) else path


def _logged_path(request: web.Request) -> str:
    """The path of the log line: the raw request target, still percent-encoded, as the standalone logged its
    self.path (aiohttp's request.path is decoded: %1B would be an ESC in HA's log, SECURITY-06), short; anything
    that is still not printable ASCII is escaped (\\x1b, \\u2028)."""
    return _NOT_PRINTABLE.sub(lambda m: m.group().encode("unicode_escape").decode("ascii"),
                              _short(request.raw_path))


def reply(request: web.Request, code: int, body: dict) -> web.Response:
    """Handler.reply of the standalone (E:1700-1708): the same JSON bytes, Content-Type and log line."""
    api.log_reply(request.method, _logged_path(request), code, body)
    return web.Response(body=json.dumps(body, ensure_ascii=False).encode(), status=code,
                        content_type="application/json", charset="utf-8")


def upload_deadline(n: int) -> float:
    """Seconds for the whole body of an upload of n bytes (SECURITY-04): at least UPLOAD_TIMEOUT_MIN_S, and the time
    of n bytes at UPLOAD_MIN_BYTES_PER_S, so a slow phone gets through and a stalled body cannot hold a connection
    and its buffer for ever."""
    return max(const.UPLOAD_TIMEOUT_MIN_S, n / const.UPLOAD_MIN_BYTES_PER_S)


async def read_body(request: web.Request, n: int) -> bytes:
    """Up to n bytes of the body from the stream (no client_max_size). A client that closes before n bytes gets no
    reply: Home Assistant runs aiohttp with handler_cancellation, so the view is cancelled and nothing is stored
    (the standalone answered on the partial bytes); another read error gives what came."""
    buf = bytearray()
    try:
        while len(buf) < n:
            chunk = await request.content.read(n - len(buf))
            if not chunk:
                break
            buf += chunk
    except Exception as e:          # the connection went away: what came is used (nobody reads the reply)
        _LOGGER.debug("body cut short: %s", type(e).__name__)
    return bytes(buf)


class _View(HomeAssistantView):
    requires_auth = True
    cors_allowed = False
    needs_person = True

    @staticmethod
    def _runtime(hass: HomeAssistant):
        entries = hass.config_entries.async_loaded_entries(DOMAIN)
        rt = getattr(entries[0], "runtime_data", None) if entries else None
        return None if rt is None or rt.stopping else rt

    @staticmethod
    def _active_user(request: web.Request):
        user = request.get(KEY_HASS_USER)
        if user is None or not user.is_active or user.system_generated:
            return None
        return user

    def _start(self, request: web.Request, post: bool):
        """Steps 1-3: (runtime, person, None) or (None, None, the reply)."""
        rt = self._runtime(request.app[KEY_HASS])
        if rt is None:
            return None, None, reply(request, 503, {"message": api.TEXTS["not_ready"]})
        if post and request.query_string:
            return None, None, reply(request, 404, NOT_FOUND)
        user = self._active_user(request)
        person = (rt.person_for(user.id) if self.needs_person else {}) if user is not None else None
        if person is None:
            return None, None, reply(request, 403, {"message": api.TEXTS["unknown_user"]})
        return rt, person, None

    async def _call(self, request: web.Request, rt, fn, *args) -> web.Response:
        """Step 5: the core in the executor."""
        try:
            code, body = await rt.run(fn, *args)
        except engine.Closed:
            return reply(request, 503, {"message": api.TEXTS["not_ready"]})
        except Exception as e:
            _LOGGER.warning("%s %s failed: %s", request.method, _logged_path(request), type(e).__name__)
            return reply(request, 500, {"message": manual.TEXTS["internal_error"]})
        return reply(request, code, body)


class HealthView(_View):
    url = f"{API_BASE}/health"
    name = f"api:{DOMAIN}:health"
    needs_person = False

    async def get(self, request: web.Request) -> web.Response:
        rt, _, refused = self._start(request, post=False)
        return refused or await self._call(request, rt, api.health)


class DocumentsView(_View):
    url = f"{API_BASE}/documents"
    name = f"api:{DOMAIN}:documents"

    async def get(self, request: web.Request) -> web.Response:
        rt, u, refused = self._start(request, post=False)
        return refused or await self._call(request, rt, api.documents, u)


class FlightsView(_View):
    url = f"{API_BASE}/flights"
    name = f"api:{DOMAIN}:flights"

    async def get(self, request: web.Request) -> web.Response:
        rt, u, refused = self._start(request, post=False)
        return refused or await self._call(request, rt, api.flights, u)


class ChoicesView(_View):
    url = f"{API_BASE}/choices"
    name = f"api:{DOMAIN}:choices"

    async def get(self, request: web.Request) -> web.Response:
        rt, u, refused = self._start(request, post=False)
        return refused or await self._call(request, rt, api.choices, u)


class UploadView(_View):
    url = f"{API_BASE}/upload"
    name = f"api:{DOMAIN}:upload"

    async def post(self, request: web.Request) -> web.Response:
        rt, u, refused = self._start(request, post=True)
        if refused:
            return refused
        n = request.content_length or 0               # chunked (no Content-Length): 400, as the standalone
        if not 0 < n <= engine.MAX_BODY:
            return reply(request, 413 if n else 400, {"message": api.TEXTS["empty_or_too_large"]})
        try:                                           # one deadline for the whole body (SECURITY-04), as /flight
            async with asyncio.timeout(upload_deadline(n)):
                body = await read_body(request, n)
        except TimeoutError:
            _LOGGER.info("POST /upload: body not received (TimeoutError)")
            return reply(request, 408, {"message": api.TEXTS["body_timeout"]})
        return await self._call(request, rt, api.upload, request.headers.get("Content-Type"), body, u)


class FlightView(_View):
    url = f"{API_BASE}/flight"
    name = f"api:{DOMAIN}:flight"

    async def post(self, request: web.Request) -> web.Response:
        rt, u, refused = self._start(request, post=True)
        if refused:
            return refused
        n = request.content_length or 0
        if not 0 < n <= manual.MAX_JSON_BODY:
            return reply(request, 413 if n else 400, {"message": manual.TEXTS["body_size"]})
        try:                                           # one deadline for the whole body, not one per read
            async with asyncio.timeout(manual.BODY_TIMEOUT_S):
                body = await read_body(request, n)
        except TimeoutError:
            _LOGGER.info("POST /flight: body not received (TimeoutError)")
            return reply(request, 408, {"message": api.TEXTS["body_timeout"]})
        return await self._call(request, rt, api.flight, body, u, rt.cfg())


class NotFoundView(HomeAssistantView):
    """Everything else under the prefix (registered last, so the routes above win): 404 «percorso sconosciuto»."""

    url = f"{API_BASE}/{{tail:.*}}"
    name = f"api:{DOMAIN}:not_found"
    requires_auth = True
    cors_allowed = False

    async def get(self, request: web.Request, tail: str = "") -> web.Response:
        return reply(request, 404, NOT_FOUND)

    async def post(self, request: web.Request, tail: str = "") -> web.Response:
        return reply(request, 404, NOT_FOUND)


VIEWS = (HealthView, DocumentsView, FlightsView, ChoicesView, UploadView, FlightView, NotFoundView)


@callback
def async_register(hass: HomeAssistant) -> None:
    """Once per HA run (async_setup): HA has no unregister, and a second registration raises."""
    for view in VIEWS:
        hass.http.register_view(view())
