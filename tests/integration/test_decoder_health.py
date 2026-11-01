"""PLATFORM-03 (FIX-repo-1): the flows' decoder check (config_flow.check_decoder) reads GET /health to its end. A
decoder whose JSON arrives in more than one piece (split by the network, or chunked by a reverse proxy) is the
decoder; one StreamReader.read(n) gave only the first piece and the user saw «decoder_wrong_api». A real aiohttp
server on 127.0.0.1 and a real client session (socket_enabled, as hass_client: only 127.0.0.1 is reachable in
these tests); fictional bodies.
"""
import asyncio
import json
from unittest.mock import patch

import aiohttp
from aiohttp import web
import pytest

from custom_components.boarding_pass_tracker import config_flow

BODY = json.dumps({"ok": True, "api": 1, "name": "barcode-decoder", "version": "1.0.0",
                   "types": ["jpeg", "png", "webp", "pdf", "heic"], "pad": "x" * 400}).encode()
BIG = json.dumps({"ok": True, "api": 1, "pad": "x" * (config_flow.HEALTH_MAX_BYTES + 10)}).encode()


def pieces(body, *, chunked, delay=0.05, size=200):
    async def handler(request):
        r = web.StreamResponse(headers={"Content-Type": "application/json"})
        if chunked:
            r.enable_chunked_encoding()
        else:
            r.content_length = len(body)
        await r.prepare(request)
        try:
            for i in range(0, len(body), size):
                await r.write(body[i:i + size])
                await asyncio.sleep(delay)
            await r.write_eof()
        except ConnectionError:                       # the client stopped reading (over the cap): expected
            pass
        return r
    return handler


async def one_write(request):
    return web.Response(body=BODY, content_type="application/json")


@pytest.fixture
async def serve():
    runners, sessions = [], []

    async def start(handler):
        app = web.Application()
        app.router.add_get("/health", handler)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        runners.append(runner)
        session = aiohttp.ClientSession()
        sessions.append(session)
        return f"http://127.0.0.1:{runner.addresses[0][1]}", session

    yield start
    for s in sessions:
        await s.close()
    for r in runners:
        await r.cleanup()


@pytest.mark.parametrize(("name", "handler", "expected"), [
    ("one write", one_write, None),
    ("two writes, Content-Length", pieces(BODY, chunked=False, size=300), None),
    ("chunked, 200 bytes every 50 ms", pieces(BODY, chunked=True), None),
    ("chunked, no delay", pieces(BODY, chunked=True, delay=0), None),
    ("over 64 KiB, in many pieces", pieces(BIG, chunked=True, size=8192, delay=0.01), "decoder_wrong_api"),
    ("over 64 KiB, Content-Length", pieces(BIG, chunked=False, size=8192, delay=0.01), "decoder_wrong_api"),
    ("not api 1, in pieces", pieces(json.dumps({"ok": True, "api": 2, "pad": "y" * 500}).encode(), chunked=True),
     "decoder_wrong_api"),
])
async def test_health_in_pieces(hass, socket_enabled, serve, name, handler, expected):
    url, session = await serve(handler)
    with patch.object(config_flow, "async_get_clientsession", return_value=session):
        for _ in range(3):
            assert await config_flow.check_decoder(hass, url) == expected, name
