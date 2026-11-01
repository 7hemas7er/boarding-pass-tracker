"""T2 views (WP2, INTEGRATION_PLAN.md 5.1, 7.3): identity, readiness, the body caps and timeout, the catch-all, the
exact reply bytes and Content-Type, and test_editing's 9 Requests cases re-expressed against the real views.

The texts the Shortcuts show are written out here where the plan fixes them; the field refusals use manual.text,
whose texts the core tests (T1) pin."""
import asyncio
import datetime
import json
import logging
import os
import re
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from homeassistant.util import dt as dt_util

from custom_components.boarding_pass_tracker import const, views
from custom_components.boarding_pass_tracker.core import engine, manual

from bpt_helpers import API, People, async_reload, async_setup, async_user, bcbp, julian, make_entry, pkpass

NOT_FOUND = {"message": "percorso sconosciuto"}
UNKNOWN_USER = ("Non so chi sei: il tuo utente di Home Assistant non è collegato a nessuna persona di Boarding Pass "
                "Tracker.")
NOT_READY = "Boarding Pass Tracker non è attivo in questo momento: riprova tra poco."
NO_DECODER = ("Per leggere foto e PDF serve il decodificatore, che non è configurato: manda il pass del Wallet, oppure "
              "aggiungi il volo con «Aggiungi volo».")
BODY_TIMEOUT = "Richiesta arrivata troppo lentamente: riprova."
BODY_SIZE = "Richiesta vuota o oltre 16 KB."
EMPTY_OR_LARGE = "File vuoto o oltre 25 MB."
INTERNAL = "Errore interno: riprova tra poco."
NOT_JSON = "Richiesta non valida: serve un oggetto JSON con i campi del volo."
NAMES = ("Alice", "Bob", "Carla", "Dario", "Elena", "Franco", "Gina", "Ivo", "Lia", "Marco")
JSON_TYPE = "application/json; charset=utf-8"


class Api:
    """alice's (and others') HTTP calls to the views, and the database as the core sees it."""

    def __init__(self, hass, entry, people, clients):
        self.hass, self.entry, self.people, self.clients = hass, entry, people, clients
        self.today = dt_util.now().date()                     # HA's zone: the engine's «today»
        self.day = self.today + datetime.timedelta(days=2)

    @property
    def rt(self):
        return self.entry.runtime_data

    async def call(self, method, path, data=None, user="alice", **kwargs):
        r = await self.clients[user].request(method, API + path, data=data, **kwargs)
        raw = await r.read()
        try:
            body = json.loads(raw)
        except ValueError:
            body = raw.decode("utf-8", "replace")
        return r.status, body, r

    async def flight(self, data, user="alice", **kwargs):
        code, body, _ = await self.call("POST", "/flight", data, user, **kwargs)
        return code, body

    async def send(self, user="alice", **req):
        return await self.flight(json.dumps(req).encode(), user)

    async def refused(self, code, message, **req):
        assert await self.send(**req) == (code, {"message": message}), req

    async def ok(self, **req):
        code, body = await self.send(**req)
        assert code == 200, body
        return body

    async def add(self, **fields):
        return await self.ok(**{"action": "add", "flight": "FR 1234", "date": self.day.isoformat(), "origin": "BLQ",
                                "destination": "STN", "passengers": "Alice 12A", **fields})

    async def ref(self, flight_id):
        code, body, _ = await self.call("GET", "/choices")
        assert code == 200, body
        return next(d["ref"] for d in body["details"].values() if d["ref"].startswith(f"{flight_id}-"))

    async def q(self, sql, args=()):
        return await self.rt.run(engine.q, sql, args)

    async def dump(self):
        return {t: await self.q(f"SELECT * FROM {t} ORDER BY id")
                for t in ("flights", "boarding_passes", "documents")}


@pytest.fixture
async def api(hass, hass_client, hass_client_no_auth):
    people = await People().async_create(hass)
    entry = await async_setup(hass, make_entry(people.subentries()))
    carol, carol_token = await async_user(hass, "carol")         # an HA user with no person
    clients = {name: await hass_client(token) for name, token in people.tokens.items()}
    clients["carol"] = await hass_client(carol_token)
    clients[None] = await hass_client_no_auth()
    return Api(hass, entry, people, clients)


# ---------------------------------------------------------------- identity and readiness
async def test_no_or_bad_token_is_401_from_ha(api, hass_client):
    for method, path in (("GET", "/flights"), ("GET", "/health"), ("POST", "/flight"), ("POST", "/upload"),
                         ("GET", "/nothing")):
        code, body, r = await api.call(method, path, b"{}" if method == "POST" else None, user=None)
        assert (code, body) == (401, "401: Unauthorized"), path
        assert r.headers["Content-Type"].startswith("text/plain")
    bad = await hass_client("not-a-token")
    r = await bad.get(API + "/flights")
    assert r.status == 401


async def test_a_user_without_a_person_gets_403_on_every_route(api):
    for method, path, data in (("GET", "/documents", None), ("GET", "/flights", None), ("GET", "/choices", None),
                               ("POST", "/upload", pkpass(bcbp())), ("POST", "/flight", b'{"action": "add"}'),
                               ("POST", "/flight", b""), ("POST", "/flight", b"x" * 20000)):   # who before size
        code, body, r = await api.call(method, path, data, user="carol")
        assert (code, body) == (403, {"message": UNKNOWN_USER}), path
        assert r.headers["Content-Type"] == JSON_TYPE
    # /health holds no personal data: any active user that is not a system user
    code, body, _ = await api.call("GET", "/health", user="carol")
    assert (code, body) == (200, {"ok": True, "queued": 0, "decoder": "off"})
    assert await api.dump() == {"flights": [], "boarding_passes": [], "documents": []}


async def test_503_when_the_entry_is_not_loaded(api, hass):
    assert await hass.config_entries.async_unload(api.entry.entry_id)
    for method, path in (("GET", "/health"), ("GET", "/flights"), ("GET", "/documents"), ("GET", "/choices"),
                         ("POST", "/upload"), ("POST", "/flight")):
        code, body, _ = await api.call(method, path, b"{}" if method == "POST" else None)
        assert (code, body) == (503, {"message": NOT_READY}), path
    code, body, _ = await api.call("GET", "/elsewhere")              # the catch-all needs only the token
    assert (code, body) == (404, NOT_FOUND)


async def test_503_not_ready_during_stop_and_when_the_engine_is_closed(api, hass):
    # the engine shut down under a runtime that does not know yet: rt.run raises Closed -> 503 (step 5)
    await hass.async_add_executor_job(engine.shutdown, 1)
    for method, path, data in (("GET", "/health", None), ("GET", "/flights", None), ("GET", "/choices", None),
                               ("POST", "/upload", pkpass(bcbp())),
                               ("POST", "/flight", json.dumps({"action": "add"}).encode())):
        code, body, _ = await api.call(method, path, data)
        assert (code, body) == (503, {"message": NOT_READY}), path
    # HA stop: the entry stays loaded, the runtime stops: 503 at step 1
    await api.rt.async_stop()
    assert api.entry.state.name == "LOADED" and api.rt.stopping
    code, body, _ = await api.call("GET", "/flights")
    assert (code, body) == (503, {"message": NOT_READY})


async def test_identity_is_the_person_of_the_ha_user(api):
    code, body, r = await api.call("GET", "/flights")
    assert (code, body) == (200, {"user": "alice", "flights": []})
    assert r.headers["Content-Type"] == JSON_TYPE
    code, body, _ = await api.call("GET", "/documents", user="bob")
    assert (code, body) == (200, {"user": "bob", "documents": []})


# ---------------------------------------------------------------- reply bytes, catch-all, methods
async def test_exact_reply_bytes_and_content_type(api):
    r = await api.clients["alice"].get(API + "/choices")
    raw = await r.read()
    expected = {"user": "alice", "message": manual.text("no_flights"), "choices": [], "details": {}}
    assert raw == json.dumps(expected, ensure_ascii=False).encode()          # real accents, not \u escapes
    assert r.headers["Content-Type"] == "application/json; charset=utf-8"
    assert r.status == 200
    assert r.headers.get("Server", "") == ""                         # IBC1: HA sends no server name


async def test_catch_all_query_strings_and_other_methods(api):
    for method, path in (("POST", "/flight?x=1"), ("POST", "/upload?x=1"), ("POST", "/flights"), ("POST", "/choices"),
                         ("GET", "/flight"), ("GET", "/choice"), ("GET", "/upload"), ("GET", "/"), ("POST", "/a/b")):
        code, body, r = await api.call(method, path, b"{}" if method == "POST" else None)
        assert (code, body) == (404, NOT_FOUND), (method, path)
        assert r.headers["Content-Type"] == JSON_TYPE
    code, body, _ = await api.call("GET", "/flights?anything=1")            # a GET ignores the query, as before
    assert (code, body) == (200, {"user": "alice", "flights": []})
    for method in ("PUT", "PATCH", "DELETE"):                               # IBC5: aiohttp's 405
        code, _, _ = await api.call(method, "/flight", b"{}")
        assert code == 405, method


async def test_the_log_line_has_the_raw_path_and_no_control_characters(api, caplog):
    """SECURITY-06: aiohttp's request.path is percent-decoded, so any HA token put an ESC (or U+2028, NUL, ...) in
    HA's log. The line has the raw target, as the standalone logged its self.path, with anything still not printable
    escaped."""
    caplog.set_level(logging.INFO)
    for label, enc in (("ESC", "%1B%5B2K"), ("LSEP", "%E2%80%A8"), ("NUL", "%00"), ("CSI", "%C2%9B"),
                       ("BEL", "%07"), ("DEL", "%7F"), ("EACUTE", "%C3%A9")):
        code, body, _ = await api.call("GET", f"/x{enc}FORGED-{label}?q=%1B")
        assert (code, body) == (404, NOT_FOUND), label
        lines = [r.getMessage() for r in caplog.records if r.name.endswith("core.api") and f"FORGED-{label}" in
                 r.getMessage()]
        assert len(lines) == 1, (label, lines)
        assert re.fullmatch(r"GET /x[\x20-\x7e]*FORGED-" + label + " 404 percorso sconosciuto", lines[0]), lines
        assert "%" in lines[0].split("FORGED")[0] and "?q" not in lines[0], lines   # encoded; no query, as before
    assert not [r for r in caplog.records if re.search(r"[\x00-\x1f\x7f-\x9f\u2028\u2029]", r.getMessage())]
    code, _, _ = await api.call("POST", "/flight?x=1", b"{}")                     # the short path, as before
    assert code == 404 and "POST /flight 404 percorso sconosciuto" in caplog.text


def test_what_is_still_not_printable_in_the_raw_path_is_escaped():
    """SECURITY-06: a client may send bytes the HTTP parser lets through unencoded; the log line escapes them."""
    raw = "/api/boarding_pass_tracker/x\x1b[2K\u2028\x00\x9b\x7f\u00e9 ok?q=\x1b"
    request = SimpleNamespace(raw_path=raw)
    assert views._logged_path(request) == "/x\\x1b[2K\\u2028\\x00\\x9b\\x7f\\xe9 ok?q=\\x1b"
    assert views._logged_path(SimpleNamespace(raw_path="/api/boarding_pass_tracker/flight?x=%1B")) == "/flight?x=%1B"


# ---------------------------------------------------------------- upload
def test_the_upload_deadline():
    """SECURITY-04: at least 2 minutes, and the declared length at 32 KiB/s: 25 MiB in 800 s, never unbounded."""
    assert views.upload_deadline(1) == 120
    assert views.upload_deadline(3 * 1024 * 1024) == 120
    assert views.upload_deadline(25 * 1024 * 1024) == 800


async def test_upload_body_sent_too_slowly(api, caplog):
    """SECURITY-04: a stalled upload body is answered 408 body_timeout (the /flight text, IBC7) at the deadline and
    nothing is stored; one that keeps coming within it is accepted."""
    data = pkpass(bcbp(day=julian(api.day)))

    def slowly(pause, step=200):
        async def gen():
            for i in range(0, len(data), step):
                yield data[i:i + step]
                await asyncio.sleep(pause)
        return gen()
    with patch.object(const, "UPLOAD_TIMEOUT_MIN_S", 1), patch.object(const, "UPLOAD_MIN_BYTES_PER_S", 1 << 40):
        code, body, _ = await api.call("POST", "/upload", slowly(0.4), headers={"Content-Length": str(len(data))})
        assert (code, body) == (408, {"message": BODY_TIMEOUT})
        assert await api.q("SELECT * FROM documents") == []
        assert "POST /upload: body not received (TimeoutError)" in caplog.text
        assert "POST /upload 408 Richiesta arrivata troppo lentamente: riprova." in caplog.text
    with patch.object(const, "UPLOAD_TIMEOUT_MIN_S", 30):
        code, body, _ = await api.call("POST", "/upload", slowly(0.01, 400),
                                       headers={"Content-Length": str(len(data))})
    assert (code, body["status"]) == (200, "queued"), body
async def test_upload_25_mib_accepted_and_one_byte_more_refused(api, hass):
    hass.config_entries.async_update_entry(api.entry, options={**api.entry.options,
                                                               "decoder_url": "http://127.0.0.1:9"})
    await async_reload(hass, api.entry)
    big = b"%PDF-1.4\n" + b"0" * (25 * 1024 * 1024 - 9)                    # exactly 25 MiB, above HA's 16 MiB
    code, body, _ = await api.call("POST", "/upload", big)
    assert code == 200 and body["status"] == "queued", body
    row = (await api.q("SELECT file, file_type FROM documents"))[0]
    assert row["file_type"] == "pdf"
    assert os.path.getsize(os.path.join(engine.DATA_DIR, row["file"])) == 25 * 1024 * 1024
    code, body, _ = await api.call("POST", "/upload", big + b"0")
    assert (code, body) == (413, {"message": EMPTY_OR_LARGE})
    code, body, _ = await api.call("POST", "/upload", b"")
    assert (code, body) == (400, {"message": EMPTY_OR_LARGE})

    async def chunked():                                                    # no Content-Length: 400, as before
        yield b"%PDF-1.4\n"
    code, body, _ = await api.call("POST", "/upload", chunked())
    assert (code, body) == (400, {"message": EMPTY_OR_LARGE})


async def test_upload_types_with_and_without_decoder(api, hass):
    jpeg = b"\xff\xd8\xff\xe0" + b"\x00" * 64
    heic = b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00mif1heic" + b"\x00" * 64
    for data in (jpeg, heic, b"%PDF-1.4\n%%EOF"):                           # no decoder: 415, nothing stored
        code, body, _ = await api.call("POST", "/upload", data)
        assert (code, body) == (415, {"message": NO_DECODER})
    code, body, _ = await api.call("POST", "/upload", b"plain text")
    assert code == 415 and body["message"].startswith("Mandami una carta d'imbarco")
    assert await api.q("SELECT * FROM documents") == []
    code, body, _ = await api.call("POST", "/upload", pkpass(bcbp(day=julian(api.day))))
    assert (code, body) == (200, {"id": 1, "status": "queued",
                                  "message": "Ricevuto: lo leggo e ti mando una notifica."})
    hass.config_entries.async_update_entry(api.entry, options={**api.entry.options,
                                                               "decoder_url": "http://127.0.0.1:9"})
    await async_reload(hass, api.entry)
    code, body, _ = await api.call("POST", "/upload", heic)                # Q4: a HEIC is queued like a JPEG
    assert code == 200 and body["status"] == "queued", body
    row = (await api.q("SELECT file, file_type, uploaded_by FROM documents WHERE id=?", (body["id"],)))[0]
    assert row["file_type"] == "heic" and row["file"].endswith(".heic") and row["uploaded_by"] == "alice"
    form = (b"--XyZ\r\nContent-Disposition: form-data; name=\"file\"; filename=\"x.jpg\"\r\nContent-Type: image/jpeg"
            b"\r\n\r\n" + jpeg + b"\r\n--XyZ--\r\n")                       # a multipart form, as a Shortcut sends it
    code, body, _ = await api.call("POST", "/upload", form,
                                   headers={"Content-Type": "multipart/form-data; boundary=XyZ"})
    assert code == 200 and body["status"] == "queued", body
    code, body, _ = await api.call("POST", "/upload", heic)
    assert code == 200 and body["message"].startswith("Questo l'avevi gia' mandato il ")


# ---------------------------------------------------------------- POST /flight: test_editing.Requests, through the views
async def test_requests_who_may_send(api):
    for data in (b'{"action": "add"}', b"", b"x" * 20000):                   # who first, then the size
        assert await api.flight(data, user="carol") == (403, {"message": UNKNOWN_USER})
    code, body, _ = await api.call("POST", "/flight", b'{"action": "add"}', user=None)
    assert (code, body) == (401, "401: Unauthorized")
    # IBC4: no Origin/Host check any more (a bearer token is not ambient): alice gets a field refusal, not a 403
    code, body = await api.flight(b'{"action": "add"}', headers={"Origin": "http://127.0.0.1"})
    assert code == 400 and body != {"message": UNKNOWN_USER}


async def test_requests_size(api):
    assert await api.flight(b"") == (400, {"message": BODY_SIZE})
    body = json.dumps({"action": "add", "flight": "FR 1234", "date": api.day.isoformat(), "origin": "BLQ",
                       "destination": "STN", "passengers": "Alice"}).encode()
    exact = body[:-1] + b" " * (16384 - len(body)) + b"}"                     # 16 KB of JSON
    assert len(exact) == 16384
    assert await api.flight(exact[:-1] + b" }") == (413, {"message": BODY_SIZE})
    code, reply = await api.flight(exact)
    assert (code, reply.get("result")) == (200, "added")
    # Content-Length "abc" / "-1" never reach the view: aiohttp's parser answers 400 (L3 raw-socket cases)


async def test_requests_a_body_sent_too_slowly(api, caplog):
    body = json.dumps({"action": "add", "flight": "FR 1234", "date": api.day.isoformat(), "origin": "BLQ",
                       "destination": "STN", "passengers": "Alice"}).encode()
    before = await api.dump()

    async def slowly():
        for i in range(0, len(body), 20):
            yield body[i:i + 20]
            await asyncio.sleep(0.3)
    with patch.object(manual, "BODY_TIMEOUT_S", 1):                          # one deadline for the whole body
        code, reply = await api.flight(slowly(), headers={"Content-Length": str(len(body))})
    assert (code, reply) == (408, {"message": BODY_TIMEOUT})                  # IBC7: 408 instead of a closed socket
    assert await api.dump() == before
    assert "POST /flight: body not received (TimeoutError)" in caplog.text


async def test_requests_not_json(api):
    for data in (b'{"action": "add", "flight": "FR \xff"}', b'{"action": "add",', b"[" * 16000, b'["add"]',
                 b'"add"', b"12", b"null", b"   "):
        assert await api.flight(data) == (400, {"message": NOT_JSON}), data[:24]


async def test_requests_keys_and_types(api):
    add = {"action": "add", "flight": "FR 1234", "date": api.day.isoformat(), "origin": "BLQ", "destination": "STN",
           "passengers": "Alice"}
    for key, shown in (("colour", "colour"), ("k" * 40, "k" * 30), ("co\u202elour", "co lour")):
        await api.refused(400, manual.text("unknown_field", key=shown), **add, **{key: "x"})
    for key, value in (("flight", 1234), ("date", True), ("origin", {"code": "BLQ"}), ("destination", 1.5),
                       ("passengers", ["Alice", 3]), ("flight", ["FR 1234"])):
        await api.refused(400, manual.text("bad_type", key=key), **{**add, key: value})
    await api.refused(400, manual.text("bad_action"), **{**add, "action": "remove"})
    await api.refused(400, manual.text("bad_action"), **{k: x for k, x in add.items() if k != "action"})


async def test_requests_every_field_refusal(api):
    d = api.day.isoformat()
    add = {"action": "add", "flight": "FR 1234", "date": d, "origin": "BLQ", "destination": "STN",
           "passengers": "Alice 12A"}
    cases = [("bad_flight", {"flight": x}) for x in ("EZY1234", "RYR1234", "1234", "FR", "FR12345",
                                                     "FR １２３４", "")]
    cases += [("bad_date", {"date": x}) for x in ("31/02/2026", "2026-13-01", "08/11/26", f"{d}T10:00", "domani")]
    cases += [("date_out_of_range", {"date": x}) for x in ((api.today + datetime.timedelta(days=367)).isoformat(),
                                                           "1969-12-31")]
    cases += [("bad_origin", {"origin": x}) for x in ("BL", "BLQX", "XYZ", "KIV", "B1Q")]
    cases += [("bad_destination", {"destination": "XYZ"}), ("same_airport", {"destination": " blq "})]
    cases += [("bad_passenger", {"passengers": x}) for x in ("Carla 12", "Ada 12A 3B", "Marco R.", "Mario2",
                                                             "Ada\u202e", "A" * 31, "passeggero", "Alice, Bob 99")]
    cases += [("no_passengers", {"passengers": x}) for x in ("", ", ,", None)]
    cases += [("too_many", {"passengers": ", ".join(NAMES)}), ("same_name", {"passengers": "Alice, alice"})]
    for key, change in cases:
        await api.refused(400, manual.text(key), **{**add, **change})
    await api.add()
    r = await api.ref(1)
    for action in ("edit", "delete"):
        for ref in (None, "", "12", "abc", "0-0123456789ab", "1-0123456789AB", "1-0123456789a",
                    "1234567890-0123456789ab", f"{r}x"):
            await api.refused(400, manual.text("no_ref"), action=action, ref=ref, date=d)
    await api.refused(400, manual.text("nothing"), action="edit", ref=r)
    await api.refused(400, manual.text("nothing"), action="edit", ref=r, date="", passengers=None)
    await api.refused(400, manual.text("bad_date"), action="edit", ref=r, date="31/02/2026")
    await api.refused(400, manual.text("bad_passenger"), action="edit", ref=r, passengers="Mario2")


async def test_requests_no_content_type_and_the_keys_of_other_actions(api):
    data = json.dumps({"action": "add", "flight": "FR 1234", "date": api.day.isoformat(), "origin": "BLQ",
                       "destination": "STN", "passengers": "Alice", "ref": "7-0123456789ab"}).encode()
    code, reply = await api.flight(data, skip_auto_headers=["Content-Type"])     # as curl -d: no Content-Type
    assert (code, reply.get("result")) == (200, "added")
    reply = await api.ok(action="edit", ref=await api.ref(1), flight="ZZ 9", origin="FCO", destination="AMS",
                         date="", passengers="Alice 14C")
    assert reply["result"] == "edited"
    rows = await api.q("SELECT airline, number, origin, date FROM flights")
    assert [(v["airline"], v["number"], v["origin"], v["date"]) for v in rows] == [("FR", "1234", "BLQ",
                                                                                   api.day.isoformat())]
    reply = await api.ok(action="delete", ref=await api.ref(1))
    assert reply["result"] == "deleted"


async def test_requests_other_paths_and_methods_as_before(api):
    for method, path in (("POST", "/flight?x=1"), ("POST", "/flights"), ("POST", "/choices"), ("GET", "/flight"),
                         ("GET", "/choice")):
        code, body, _ = await api.call(method, path, b"{}" if method == "POST" else None)
        assert (code, body) == (404, NOT_FOUND), (method, path)
    for method in ("PUT", "PATCH", "DELETE"):                                 # 501 page before, 405 now (IBC5)
        code, _, r = await api.call(method, "/flight", b"{}")
        assert code == 405 and not r.headers.get("Content-Type", "").startswith("application/json"), method


async def test_requests_an_unexpected_error(api, caplog):
    real = engine.transaction
    import contextlib

    @contextlib.contextmanager
    def failing():
        with real() as c:
            yield c
            raise RuntimeError("disk I/O error (test)")              # after the writes, before COMMIT
    with patch.object(engine, "transaction", failing):
        await api.refused(500, INTERNAL, action="add", flight="FR 1234", date=api.day.isoformat(), origin="BLQ",
                          destination="STN", passengers="Carla 3C")
    assert await api.q("SELECT * FROM flights") == [] and await api.q("SELECT * FROM boarding_passes") == []
    assert "flight request failed: RuntimeError" in caplog.text
    assert "disk I/O error" not in caplog.text
    assert "Carla" not in caplog.text
    assert (await api.add(passengers="Carla 3C"))["result"] == "added"     # and the service carries on


async def test_the_reply_comes_after_the_publish(api, hass):
    reply = await api.add()
    assert reply["result"] == "added"
    st = hass.states.get("sensor.boarding_pass_tracker_upcoming_flights")
    assert st.state == "1"                                         # published before the reply (P6)
    assert st.attributes["list"][0]["flight"] == "FR 1234"
