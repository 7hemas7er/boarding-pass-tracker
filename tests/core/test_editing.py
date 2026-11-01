"""Engine and HTTP tests of the flights added, edited and deleted by hand (GET /choices, POST /flight): written from
the feature plan as a black box, on test_engine's fakes and a new database per test.

  cd tracker && python3 -I -B -m unittest discover -s . -p test_editing.py -v

Dates are relative to the day the test starts: `today` (the host's local date, which the engine uses for the date
range and for «past») and `day`, two days ahead in London, the flight day of test_engine's Flights (at(), api()).
The results are the same in every time zone (also run with TZ=Pacific/Kiritimati and TZ=Etc/GMT+12).
"""
import contextlib, datetime, hashlib, http.client, json, os, re, socket, sys, tempfile, threading, time, unittest
import unittest.mock, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import test_engine as te  # noqa: E402  (first: it sets the TRACKER_* environment, then imports the engine)
import test_flights as tf  # noqa: E402  (synthetic Wallet passes and AviationStack flights)

eng = te.eng
CFG, P = te.CFG, te.P
ALICE, BOB = "alice@example.com", "bob@example.com"            # Tailscale-User-Login of the two users
BOB_USER = CFG["users"][BOB]
NO_ROUTE = {"message": "percorso sconosciuto"}                 # what 0.1.0 answers on the new paths
ROUTE = "Bologna (BLQ) → Londra Stansted (STN)"
TRACKED = " Ti avviso se cambiano orario o gate."
UNTRACKED = " Lo stato del volo non lo seguo: manca la chiave di AviationStack."
PAST = " Conta solo per le statistiche."
TYPED, FROM_PASS = "Inserito a mano", "Dalla carta d'imbarco"   # the status without AviationStack ([Q3=A])
NAMES = ("Alice", "Bob", "Carla", "Dario", "Elena", "Franco", "Gina", "Ivo", "Lia", "Marco")
# The Italian texts of the plan (section 5.5), as the Shortcuts show them
TEXTS = {
    "body_size": "Richiesta vuota o oltre 16 KB.",
    "not_json": "Richiesta non valida: serve un oggetto JSON con i campi del volo.",
    "unknown_field": "Campo sconosciuto: {key}.",
    "bad_type": "Il campo {key} ha un tipo sbagliato.",
    "bad_action": "Azione sconosciuta: dev'essere add, edit o delete.",
    "bad_flight": "Volo non valido: scrivi la compagnia (2 caratteri) e il numero, per esempio FR 1234.",
    "bad_date": "Data non valida: scrivi il giorno della partenza, per esempio 08/11/2026.",
    "date_out_of_range": "Data fuori intervallo: dal 1970 a un anno da oggi.",
    "bad_origin": "Aeroporto di partenza sconosciuto: scrivi il codice IATA di tre lettere, per esempio BLQ.",
    "bad_destination": "Aeroporto di arrivo sconosciuto: scrivi il codice IATA di tre lettere, per esempio STN.",
    "same_airport": "Partenza e arrivo sono lo stesso aeroporto.",
    "no_passengers": "Mancano i passeggeri: scrivi i nomi separati da virgole, con il posto e la classe dopo il nome "
                     "se li sai, per esempio Alice 12A business, Bob.",
    "bad_passenger": "Non capisco un passeggero: scrivi il nome (solo lettere, spazi, apostrofi e trattini), poi il "
                     "posto e la classe se li sai, per esempio Alice 12A business.",
    "too_many": "Al massimo 9 passeggeri per volo.",
    "same_name": "Due passeggeri con lo stesso nome: aggiungi il cognome a uno dei due, per esempio Marco Rossi.",
    "no_ref": "Manca il volo da modificare: sceglilo dall'elenco di «Modifica volo».",
    "nothing": "Niente da cambiare: manda la data, i passeggeri, gli orari, il terminal o il gate.",
    "not_found": "Questo volo non c'è più: forse è stato eliminato. Riapri «Modifica volo».",
    "changed": "Il volo è cambiato nel frattempo: riapri «Modifica volo» e riprova.",
    "last_passenger": "Serve almeno un passeggero: per togliere il volo scegli «Elimina il volo».",
    "date_taken": "C'è già {code} del {day} da {origin}: modifica quello, oppure elimina uno dei due.",
    "other_destination": "{code} del {day} da {origin} c'è già, ma va a {destination}: controlla l'arrivo.",
    "internal_error": "Errore interno: riprova tra poco.",
    "added": "Aggiunto {code} del {day}, {route}, {n}.{suffix}",
    "merged": "{code} del {day} c'era già: {changes}. Ora ha {n}.{suffix}",
    "edited": "Modificato {code} del {day}: {changes}. Ora ha {n}.{suffix}",
    "unchanged": "Nessuna modifica: {code} del {day} è già così.",
    "deleted": "Eliminato {code} del {day}, {route}.",
    "choose": "Scegli il volo.",
    "no_flights": "Nessun volo: aggiungine uno con «Aggiungi volo» o «Manda volo».",
}


def text(name, /, **fill):
    return TEXTS[name].format(**fill)


def days(n):
    return datetime.timedelta(days=n)


def dmy(day):
    """dd/mm/yyyy of a date or of an ISO date."""
    return (datetime.date.fromisoformat(day) if isinstance(day, str) else day).strftime("%d/%m/%Y")


def people(n):
    return f"{n} passeggero" if n == 1 else f"{n} passeggeri"


def added(day, n, suffix=TRACKED, code="FR 1234", route=ROUTE):
    return text("added", code=code, day=dmy(day), route=route, n=people(n), suffix=suffix)


def merged(day, changes, n, suffix=TRACKED, code="FR 1234"):
    return text("merged", code=code, day=dmy(day), changes=changes, n=people(n), suffix=suffix)


def edited(day, changes, n, suffix=TRACKED, code="FR 1234"):
    return text("edited", code=code, day=dmy(day), changes=changes, n=people(n), suffix=suffix)


def unchanged(day, code="FR 1234"):
    return text("unchanged", code=code, day=dmy(day))


def fr24(entity, ident="FR1234"):
    """The HA service call that adds a flight to Flightradar24's tracked ones, or removes it."""
    return "text.set_value", {"entity_id": entity, "value": ident}


def plan_ref(v, passes):
    """The ref of a flights row and its boarding_passes rows (ordered by id), as the plan defines it (section 4.3):
    computed here, not taken from the engine."""
    data = [v["id"], v["created_at"], v["airline"], v["number"], v["date"], v["origin"], v["destination"],
            [[p["id"], p["passenger"], p["first_name"], p["seat"], p["cabin"], p["document_id"], p["uploaded_by"]]
             for p in passes],
            [v.get(k) for k in ("pass_departure", "pass_arrival", "pass_boarding", "pass_gate", "pass_terminal")]]
    version = hashlib.sha256(json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()[:12]
    return f"{v['id']}-{version}"


class CountingSensors(te.FakeSensors):
    """FakeSensors that also counts the publishing rounds (publish_sensors sends the statistics last)."""

    def __init__(self):
        super().__init__()
        self.rounds = 0

    def publish(self, name, state, attributes=None):
        super().publish(name, state, attributes)
        self.rounds += name == "flight_statistics"


class Base(te.WithEngine):
    """WithEngine with the flights on (an AviationStack key in a test file), the Handler served, no network (the
    gate's position is faked), and the invariants of the plan checked at the end of every test."""
    LONDON = te.Flights.LONDON
    # test_engine's Flights helpers, without its tests: write_key; lookup (pops self.answers, records
    # self.requests); api(status, **fields), the AviationStack flight of self.day, 05:30 -> 07:50 London;
    # at(hours before 05:30 London of self.day); boarding_pass(**bcbp), a Wallet pass of self.day
    write_key, lookup, api, at = te.Flights.write_key, te.Flights.lookup, te.Flights.api, te.Flights.at
    boarding_pass = te.Flights.boarding_pass

    def setUp(self):
        super().setUp()
        self.saved.update({k: getattr(eng, k) for k in ("NO_FLIGHTS", "AVIATIONSTACK_KEY_FILE")})
        eng.NO_FLIGHTS = False
        self.network = []

        def no_network(opener, *a, **k):
            self.network.append(a)
            raise OSError("no network in the tests")
        for target, name, fake in ((urllib.request.OpenerDirector, "open", no_network),
                                   # Overpass: the gate is 343 m from BLQ's point (480 of corridors, 9 minutes)
                                   (eng.flights, "find_gate", lambda lat, lon, gate, **k: (44.5370, 11.2850))):
            patch = unittest.mock.patch.object(target, name, fake)
            patch.start()
            self.addCleanup(patch.stop)
        self.today = datetime.date.today()
        self.day = datetime.datetime.now(self.LONDON).date() + days(2)
        self.julian = self.day.timetuple().tm_yday
        self.fresh()
        self.serve()

    def fresh(self):
        """An empty world: a new database in a new folder, new fakes, the AviationStack key written."""
        if eng._db is not None:
            eng._db.close()
        eng.DATA_DIR, eng._db = tempfile.mkdtemp(dir=te.TEST_DIR), None
        eng.AVIATIONSTACK_KEY_FILE = os.path.join(eng.DATA_DIR, "flights.env")
        self.ha, self.sensors = te.FakeHA(), CountingSensors()
        eng._ha, eng._sensors = self.ha, self.sensors
        eng._fr24_attempts.clear()
        getattr(eng, "_fr24_forget", set()).clear()          # the plan's forget set (section 7.2) is module state
        self.answers, self.requests, self.touched = [], [], set()
        self.write_key()

    def tearDown(self):
        try:
            self.assert_invariants()
            self.assertEqual(self.network, [])
        finally:
            super().tearDown()

    def assert_invariants(self):
        """Plan 12.2: no broken reference, no boarding pass without its flight, every flight added or edited here
        with a passenger at least (a delete's documents are checked by post)."""
        with eng._db_lock:
            c = eng.db()
            self.assertEqual([tuple(r) for r in c.execute("PRAGMA foreign_key_check")], [])
            self.assertEqual([tuple(r) for r in c.execute("SELECT id, flight_id FROM boarding_passes WHERE flight_id "
                                                          "NOT IN (SELECT id FROM flights)")], [])
            for fid in sorted(self.touched):
                if c.execute("SELECT 1 FROM flights WHERE id=?", (fid,)).fetchone():
                    self.assertIsNotNone(c.execute("SELECT 1 FROM boarding_passes WHERE flight_id=?", (fid,))
                                         .fetchone(), f"flight #{fid} without passengers")

    # ------------------------------------------------ requests
    def send(self, data, user=ALICE, **headers):
        """(code, JSON reply, raw reply) of POST /flight with the bytes `data`: no Content-Type unless given."""
        code, body, raw, _ = self.call("POST", "/flight", data, user=user, **headers)
        self.assertNotEqual((code, body), (404, NO_ROUTE), "POST /flight is missing")
        return code, body, raw

    def post(self, user=ALICE, **body):
        """POST /flight with the JSON `body`, as the Shortcuts send it: (code, reply). After a delete, the
        documents that served only that flight are gone ([Q1=A])."""
        docs = []
        if body.get("action") == "delete" and re.fullmatch(r"\d+-[0-9a-f]{12}", str(body.get("ref"))):
            docs = eng.q("SELECT id FROM documents WHERE kind='carta_imbarco' AND status <> 'in_coda' AND id IN "
                         "(SELECT document_id FROM boarding_passes WHERE flight_id=?)",
                         (int(body["ref"].split("-")[0]),))
        code, reply, _ = self.send(json.dumps(body, ensure_ascii=False).encode(), user,
                                   **{"Content-Type": "application/json"})
        if code == 200 and reply.get("result") in ("added", "merged", "edited"):
            self.touched.add(reply["id"])
        if code == 200 and reply.get("result") == "deleted":
            self.touched.discard(reply["id"])
            for d in docs:
                if not eng.q("SELECT 1 FROM boarding_passes WHERE document_id=?", (d["id"],)):
                    self.assertIsNone(self.doc(d["id"]), f"document #{d['id']} served only flight #{reply['id']}")
        return code, reply

    def ok(self, user=ALICE, **body):
        code, reply = self.post(user, **body)
        self.assertEqual(code, 200, reply)
        return reply

    def add(self, user=ALICE, **fields):
        """«Aggiungi volo», by default FR 1234 of self.day BLQ -> STN for Alice (12A) and Bob: the reply."""
        return self.ok(user, **{"action": "add", "flight": "FR 1234", "date": self.day.isoformat(), "origin": "BLQ",
                                "destination": "STN", "passengers": "Alice 12A, Bob", **fields})

    def edit(self, fid, user=ALICE, **fields):
        """«Modifica volo» on flight `fid`, with the ref GET /choices gives: (code, reply)."""
        return self.post(user, action="edit", ref=self.ref(fid), **fields)

    def delete(self, fid, user=ALICE):
        """«Elimina il volo» (the Shortcut sends the empty variables too): (code, reply)."""
        return self.post(user, action="delete", ref=self.ref(fid), date="", passengers="")

    def refused_bytes(self, code, message, data, user=ALICE, **headers):
        """POST /flight with the bytes `data` is refused with `code` and exactly {"message": message}, byte for byte;
        nothing written, nothing published."""
        before, rounds = self.dump(), self.sensors.rounds
        got, reply, raw = self.send(data, user, **headers)
        self.assertEqual((got, reply), (code, {"message": message}), data[:200])
        self.assertEqual(raw, json.dumps({"message": message}, ensure_ascii=False).encode())
        self.assertEqual((self.dump(), self.sensors.rounds), (before, rounds))

    def refused(self, code, message, user=ALICE, **body):
        self.refused_bytes(code, message, json.dumps(body, ensure_ascii=False).encode(), user,
                           **{"Content-Type": "application/json"})

    def choices(self, user=ALICE, path="/choices", **headers):
        """GET /choices: (code, reply)."""
        code, body, _, _ = self.call("GET", path, user=user, **headers)
        self.assertNotEqual((code, body), (404, NO_ROUTE), "GET /choices is missing")
        return code, body

    def details(self, fid):
        """The details GET /choices gives for flight `fid`: ref, date, passengers line."""
        code, body = self.choices()
        self.assertEqual(code, 200, body)
        found = [d for d in body["details"].values() if d["ref"].split("-")[0] == str(fid)]
        self.assertEqual(len(found), 1, body)
        return found[0]

    def ref(self, fid):
        return self.details(fid)["ref"]

    def line(self, fid):
        return self.details(fid)["passengers"]

    # ------------------------------------------------ data
    def upload(self, data, user=te.USER):
        """A Wallet pass sent by `user` and read at once, as the worker does: its documents row."""
        doc_id, status, _ = eng.save_upload(data, "pkpass", user)
        self.assertEqual(status, eng.DOC_QUEUED)
        eng.read_document(self.doc(doc_id))
        return self.doc(doc_id)

    def two_legs(self):
        """Mario Rossi's pass with two legs (FR 1234 BLQ -> STN, FR 202 STN -> DUB): one document, two flights."""
        return self.upload(self.boarding_pass(others=[("STN", "DUB", "FR", "202", self.julian, "003C")]))

    def file(self, doc):
        return os.path.join(eng.DATA_DIR, doc["file"])

    def row(self, fid):
        return eng.q("SELECT * FROM flights WHERE id=?", (fid,), one=True)

    def passes_of(self, fid):
        return eng.q("SELECT * FROM boarding_passes WHERE flight_id=? ORDER BY id", (fid,))

    def dump(self):
        with eng._db_lock:
            return list(eng.db().iterdump())

    def now(self):
        """A moment after the requests made so far: their next_check (utc(now) at the request) is due."""
        return datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=1)

    def tracked_by_flightradar24(self):
        """Mario Rossi's FR 1234 of self.day, with its schedule (05:30 London), among Flightradar24's tracked flights:
        inside its window at at(2)."""
        self.upload(self.boarding_pass())
        self.answers = [self.api()]
        eng.check_flights(CFG, self.at(20), lookup=self.lookup)
        self.ha.states[eng.FR24_TRACKED] = {"state": "1", "attributes": {"flights": [
            {"flight_number": "FR1234", "tracked_type": "schedule"}]}}
        self.assertEqual(eng.track_fr24(self.at(2)), (0, 0))


# ---------------------------------------------------------------- GET /choices
class Choices(Base):
    """The list of «Modifica volo»: the flights not over first, each with its ref and its editable line."""

    def test_who_may_ask(self):
        for headers in ({"user": None}, {"Origin": "http://127.0.0.1"}):
            self.assertEqual(self.choices(**headers), (403, {"message": "Non so chi sei: passa dal tailnet."}), headers)

    def test_an_empty_archive(self):
        self.assertEqual(self.choices(), (200, {"user": "alice", "message": text("no_flights"), "choices": [],
                                                "details": {}}))

    def test_order_labels_lines_and_refs(self):
        later, past = self.today + days(5), self.today - days(30)
        self.add(date=later.isoformat())                                                       # 1
        self.add(flight="ZZ 1", date=past.isoformat(), origin="FCO", destination="AMS", passengers="Alice 14C")  # 2
        self.upload(self.boarding_pass())                                                      # 3: Mario Rossi, 12A
        self.new_flight(date=past.isoformat())                                                 # 4: XY 101, no passes
        code, body = self.choices(BOB)
        labels = [f"{dmy(self.day)} · FR 1234 · Bologna → Londra Stansted · Mario",      # not over: the nearest first
                  f"{dmy(later)} · FR 1234 · Bologna → Londra Stansted · Alice, Bob",
                  f"{dmy(past)} · XY 101 · Bologna → Londra Stansted",                    # then the newest, last id 1st
                  f"{dmy(past)} · ZZ 1 · Roma Fiumicino → Amsterdam · Alice"]
        self.assertEqual((code, body["user"], body["message"], body["choices"]), (200, "bob", text("choose"), labels))
        self.assertEqual({k: (d["date"], d["passengers"]) for k, d in body["details"].items()},
                         {labels[0]: (self.day.isoformat(), "Mario 12A"),
                          labels[1]: (later.isoformat(), "Alice 12A, Bob"),
                          labels[2]: (past.isoformat(), ""), labels[3]: (past.isoformat(), "Alice 14C")})
        for label, fid in zip(labels, (3, 1, 4, 2)):
            self.assertEqual(body["details"][label]["ref"], plan_ref(self.row(fid), self.passes_of(fid)))
            self.assertNotIn(".", label)                      # Shortcuts may read a dotted key as a key path
        # every ref is taken by an edit: the same date, or the same line, changes nothing
        for label in labels:
            d = body["details"][label]
            v = self.row(int(d["ref"].split("-")[0]))
            same = (200, {"id": v["id"], "result": "unchanged", "message": unchanged(v["date"], f"{v['airline']} "
                                                                                                 f"{v['number']}")})
            self.assertEqual(self.post(action="edit", ref=d["ref"], date=d["date"]), same)
            if d["passengers"]:
                self.assertEqual(self.post(action="edit", ref=d["ref"], passengers=d["passengers"]), same)
        self.assertEqual(self.choices(BOB), (200, body))

    def test_every_kind_of_passenger_comes_back_unchanged(self):
        self.upload(self.boarding_pass())                                                      # Mario Rossi, 12A
        self.upload(self.boarding_pass(name="ROSSI", seat="014C", seq="0043"))                 # no first name
        self.upload(self.boarding_pass(name="BIANCHI/ANNA", seat="", seq="0044"))              # no seat
        self.add(passengers="Anna Maria 3C, Lucia D\u2019Amico")
        code, body = self.choices()
        self.assertEqual(body["choices"], [f"{dmy(self.day)} · FR 1234 · Bologna → Londra Stansted · Mario, "
                                           "passeggero, Anna, Anna Maria, Lucia D'Amico"])
        line = self.line(1)
        self.assertEqual(line, "Mario 12A, passeggero 14C, Anna, Anna Maria 3C, Lucia D'Amico")
        self.assertEqual(self.edit(1, passengers=line), (200, {"id": 1, "result": "unchanged",
                                                               "message": unchanged(self.day)}))

    def test_the_query_string_is_ignored(self):
        self.add()
        self.assertEqual(self.choices(path="/choices?x=1&ref=2"), self.choices())

    def test_at_most_200(self):
        for i in range(205):
            self.new_flight(date=(self.today - days(3 + i)).isoformat())
        code, body = self.choices()
        self.assertEqual((code, len(body["choices"]), len(body["details"])), (200, 200, 200))
        self.assertEqual(body["choices"][-1], f"{dmy(self.today - days(202))} · XY 101 · Bologna → Londra Stansted")


# ---------------------------------------------------------------- POST /flight: before the rules
class Requests(Base):
    """Who, size, JSON, keys and types, in this order; the other methods and paths as before; every refusal text."""

    def test_who_may_send(self):
        forbidden = "Non so chi sei: la Scorciatoia deve passare dal tailnet."
        for data in (b'{"action": "add"}', b"", b"x" * 20000):                 # who first, then the size
            self.refused_bytes(403, forbidden, data, user=None)
        self.refused_bytes(403, forbidden, b'{"action": "add"}', Origin="http://127.0.0.1")

    def test_size(self):
        limit = text("body_size")
        self.refused_bytes(400, limit, b"")
        self.refused_bytes(400, limit, b"{}", **{"Content-Length": "abc"})
        self.refused_bytes(413, limit, b"{}", **{"Content-Length": "-1"})
        body = json.dumps({"action": "add", "flight": "FR 1234", "date": self.day.isoformat(), "origin": "BLQ",
                           "destination": "STN", "passengers": "Alice"}).encode()
        exact = body[:-1] + b" " * (16384 - len(body)) + b"}"                   # 16 KB of JSON
        self.refused_bytes(413, limit, exact[:-1] + b" }")
        code, reply, _ = self.send(exact)
        self.assertEqual((code, reply.get("result")), (200, "added"))

    def test_a_body_sent_too_slowly(self):
        # one deadline for the whole body (manual.BODY_TIMEOUT_S, 1 s here), not one for each read: 20 bytes every
        # 0.3 s get no reply, the connection is closed and nothing is written
        body = json.dumps({"action": "add", "flight": "FR 1234", "date": self.day.isoformat(), "origin": "BLQ",
                           "destination": "STN", "passengers": "Alice"}).encode()
        before = self.dump()
        with unittest.mock.patch.object(eng.manual, "BODY_TIMEOUT_S", 1), \
                socket.create_connection(("127.0.0.1", self.port), timeout=10) as s:
            s.sendall(f"POST /flight HTTP/1.1\r\nHost: 127.0.0.1:{self.port}\r\nTailscale-User-Login: {ALICE}\r\n"
                      f"Content-Length: {len(body)}\r\n\r\n".encode())
            with contextlib.suppress(OSError):                   # closed by the engine: the rest is not taken
                for i in range(0, len(body), 20):
                    s.sendall(body[i:i + 20])
                    time.sleep(0.3)
            try:
                reply = s.recv(4096)
            except ConnectionResetError:
                reply = b""
        self.assertEqual(reply, b"")
        self.assertEqual(self.dump(), before)
        self.assertIn("POST /flight: body not received (TimeoutError)", self.output.getvalue())

    def test_not_json(self):
        for data in (b'{"action": "add", "flight": "FR \xff"}', b'{"action": "add",', b"[" * 16000, b'["add"]',
                     b'"add"', b"12", b"null", b"   "):
            with self.subTest(data=data[:24]):
                self.refused_bytes(400, text("not_json"), data)

    def test_keys_and_types(self):
        add = {"action": "add", "flight": "FR 1234", "date": self.day.isoformat(), "origin": "BLQ",
               "destination": "STN", "passengers": "Alice"}
        for key, shown in (("colour", "colour"), ("k" * 40, "k" * 30), ("co\u202elour", "co lour")):
            self.refused(400, text("unknown_field", key=shown), **add, **{key: "x"})
        for key, value in (("flight", 1234), ("date", True), ("origin", {"code": "BLQ"}), ("destination", 1.5),
                           ("passengers", ["Alice", 3]), ("flight", ["FR 1234"])):
            with self.subTest(key=key, value=value):
                self.refused(400, text("bad_type", key=key), **{**add, key: value})
        self.refused(400, text("bad_action"), **{**add, "action": "remove"})
        self.refused(400, text("bad_action"), **{k: x for k, x in add.items() if k != "action"})

    def test_every_field_refusal(self):
        d = self.day.isoformat()
        add = {"action": "add", "flight": "FR 1234", "date": d, "origin": "BLQ", "destination": "STN",
               "passengers": "Alice 12A"}
        cases = [("bad_flight", {"flight": x}) for x in ("EZY1234", "RYR1234", "1234", "FR", "FR12345",
                                                         "FR \uff11\uff12\uff13\uff14", "")]
        cases += [("bad_date", {"date": x}) for x in ("31/02/2026", "2026-13-01", "08/11/26", f"{d}T10:00", "domani")]
        cases += [("date_out_of_range", {"date": x}) for x in ((self.today + days(367)).isoformat(), "1969-12-31")]
        cases += [("bad_origin", {"origin": x}) for x in ("BL", "BLQX", "XYZ", "KIV", "B1Q")]
        cases += [("bad_destination", {"destination": "XYZ"}), ("same_airport", {"destination": " blq "})]
        cases += [("bad_passenger", {"passengers": x}) for x in ("Carla 12", "Ada 12A 3B", "Marco R.", "Mario2",
                                                                 "Ada\u202e", "A" * 31, "passeggero", "Alice, Bob 99")]
        cases += [("no_passengers", {"passengers": x}) for x in ("", ", ,", None)]
        cases += [("too_many", {"passengers": ", ".join(NAMES)}), ("same_name", {"passengers": "Alice, alice"})]
        for key, change in cases:
            with self.subTest(key=key, change=change):
                self.refused(400, text(key), **{**add, **change})
        self.add()
        r = self.ref(1)
        for action in ("edit", "delete"):
            for ref in (None, "", "12", "abc", "0-0123456789ab", "1-0123456789AB", "1-0123456789a",
                        "1234567890-0123456789ab", f"{r}x"):
                with self.subTest(action=action, ref=ref):
                    self.refused(400, text("no_ref"), action=action, ref=ref, date=d)
        self.refused(400, text("nothing"), action="edit", ref=r)
        self.refused(400, text("nothing"), action="edit", ref=r, date="", passengers=None)
        self.refused(400, text("bad_date"), action="edit", ref=r, date="31/02/2026")
        self.refused(400, text("bad_passenger"), action="edit", ref=r, passengers="Mario2")

    def test_no_content_type_and_the_keys_of_other_actions(self):
        data = json.dumps({"action": "add", "flight": "FR 1234", "date": self.day.isoformat(), "origin": "BLQ",
                           "destination": "STN", "passengers": "Alice", "ref": "7-0123456789ab"}).encode()
        code, reply, _ = self.send(data)                     # as curl -d sends it: no Content-Type; add reads no ref
        self.assertEqual((code, reply.get("result")), (200, "added"))
        # «Modifica volo» sends every variable: those of other actions are not read
        reply = self.ok(action="edit", ref=self.ref(1), flight="ZZ 9", origin="FCO", destination="AMS", date="",
                        passengers="Alice 14C")
        self.assertEqual(reply["result"], "edited")
        self.assertEqual([(v["airline"], v["number"], v["origin"], v["date"]) for v in self.flights()],
                         [("FR", "1234", "BLQ", self.day.isoformat())])
        self.assertEqual(self.delete(1)[1]["result"], "deleted")

    def test_other_paths_and_methods_as_before(self):
        # passes on 0.1.0 too: the new paths are exact, and PUT, PATCH and DELETE keep Python's 501 (no JSON)
        for method, path in (("POST", "/flight?x=1"), ("POST", "/flights"), ("POST", "/choices"), ("GET", "/flight"),
                             ("GET", "/choice")):
            with self.subTest(method=method, path=path):
                self.assertEqual(self.call(method, path, b"{}" if method == "POST" else None)[:2], (404, NO_ROUTE))
        for method in ("PUT", "PATCH", "DELETE"):
            c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
            c.request(method, "/flight", body=b"{}", headers={"Host": f"127.0.0.1:{self.port}",
                                                              "Tailscale-User-Login": ALICE})
            r = c.getresponse()
            self.assertEqual((r.status, (r.getheader("Content-Type") or "").split(";")[0]), (501, "text/html"), method)
            r.read()
            c.close()

    def test_an_unexpected_error(self):
        real = eng.transaction

        @contextlib.contextmanager
        def failing():
            with real() as c:
                yield c
                raise RuntimeError("disk I/O error (test)")               # after the writes, before COMMIT
        with unittest.mock.patch.object(eng, "transaction", failing):
            self.refused(500, text("internal_error"), action="add", flight="FR 1234", date=self.day.isoformat(),
                         origin="BLQ", destination="STN", passengers="Carla 3C")
        self.assertEqual((self.flights(), self.passes()), ([], []))
        log = self.output.getvalue()
        self.assertIn("flight request failed: RuntimeError", log)
        self.assertNotIn("disk I/O error", log)                           # the type only: no data
        self.assertNotIn("Carla", log)
        self.assertEqual(self.add(passengers="Carla 3C")["result"], "added")     # and the service carries on


# ---------------------------------------------------------------- add
class AddFuture(Base):
    """«Aggiungi volo» for a flight not flown yet: the row a photo of the pass leaves, the sensors at once, then
    the clock does the rest."""

    def test_rows(self):
        before = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)
        reply = self.add(BOB, passengers="Alice 12A, bob, Jose\u0301 3c, Lucia D\u2019Amico")
        after = datetime.datetime.now(datetime.timezone.utc)
        self.assertEqual(reply, {"id": 1, "result": "added", "message": added(self.day, 4)})
        (v,) = self.flights()
        self.assertTrue(eng.utc(before) <= v["next_check"] <= eng.utc(after), v["next_check"])   # checked at once
        self.assertTrue(before <= datetime.datetime.fromisoformat(v["created_at"]) <= after, v["created_at"])
        self.assertEqual({k: x for k, x in v.items() if k not in ("next_check", "created_at")},
                         {"id": 1, "airline": "FR", "number": "1234", "date": self.day.isoformat(), "origin": "BLQ",
                          "destination": "STN", "pass_departure": None, "pass_boarding": None, "pass_gate": None,
                          "relevant_date": None, "status": None, "updated_at": None, "error": None, "snapshot": "{}",
                          "pass_arrival": None, "pass_terminal": None})
        # the name in both columns, no document, no cabin; the gate reminder to the user of that first name, else
        # to who typed it
        self.assertEqual([(c["document_id"], c["flight_id"], c["passenger"], c["first_name"], c["seat"], c["cabin"],
                           c["uploaded_by"]) for c in self.passes()],
                         [(None, 1, "Alice", "Alice", "12A", None, "alice"), (None, 1, "Bob", "Bob", None, None, "bob"),
                          (None, 1, "José", "José", "3C", None, "bob"),
                          (None, 1, "Lucia D'Amico", "Lucia D'Amico", None, None, "bob")])

    def test_the_forms_people_type(self):
        d = self.day
        base = {"action": "add", "flight": "FR 1234", "date": d.isoformat(), "origin": "BLQ", "destination": "STN",
                "passengers": "Alice"}
        self.ok(**base)
        for change in ({"date": f"{d.day}/{d.month}/{d.year}"}, {"date": f"{d.day}.{d.month}.{d.year}"},
                       {"date": f"{d.day}-{d.month}-{d.year}"}, {"date": dmy(d)}, {"date": f" {d.isoformat()} "},
                       {"flight": "fr1234"}, {"flight": "FR 01234"}, {"flight": " f r 1 2 3 4 "},
                       {"origin": " blq ", "destination": "stn"}, {"passengers": "alice"}, {"passengers": "ALICE"},
                       {"passengers": ["Alice", " "]}):
            with self.subTest(change=change):                 # the same flight and passenger: nothing to change
                self.assertEqual(self.post(**{**base, **change}),
                                 (200, {"id": 1, "result": "unchanged", "message": unchanged(d)}))
        for flight, airline, number in (("W6 0012", "W6", "12"), ("U2 1234", "U2", "1234"), ("4U9536", "4U", "9536"),
                                        ("FR 123A", "FR", "123A")):
            reply = self.ok(**{**base, "flight": flight})
            self.assertEqual(reply["message"], added(d, 1, code=f"{airline} {number}"))
            self.assertEqual((self.row(reply["id"])["airline"], self.row(reply["id"])["number"]), (airline, number))
        last = self.today + days(366)
        self.assertEqual(self.ok(**{**base, "flight": "ZZ 1", "date": last.isoformat(), "passengers": NAMES[:9]}),
                         {"id": 6, "result": "added", "message": added(last, 9, code="ZZ 1")})
        self.assertEqual(self.ok(**{**base, "flight": "ZZ 2", "date": "1970-01-01"})["message"],
                         added(datetime.date(1970, 1, 1), 1, PAST, code="ZZ 2"))

    def test_without_aviationstack(self):
        os.remove(eng.AVIATIONSTACK_KEY_FILE)
        self.assertEqual(self.add()["message"], added(self.day, 2, UNTRACKED))
        self.write_key()
        eng.NO_FLIGHTS = True
        self.assertEqual(self.add(flight="FR 1235")["message"], added(self.day, 2, UNTRACKED, code="FR 1235"))
        self.assertTrue(all(v["next_check"] for v in self.flights()))         # followed once the key is there

    def test_the_sensors_at_once(self):
        self.new_flight(date=(self.today + days(20)).isoformat())             # XY 101, later
        eng.publish_sensors()
        rounds, stats = self.sensors.rounds, self.sensors.values["flight_statistics"][1]
        self.add()
        self.assertEqual(self.sensors.rounds, rounds + 1)                     # before the reply
        state, card = self.sensors.values["next_flight"]
        self.assertEqual((state, card["flight"], card["date"], card["passengers"], card["status"], card["source"]),
                         (TYPED, "FR 1234", self.day.isoformat(), ["Alice (12A)", "Bob"], TYPED, "manual"))
        self.assertEqual(len(card), 30)                                       # no new attribute
        count, attributes = self.sensors.values["upcoming_flights"]
        self.assertEqual((count, [x["flight"] for x in attributes["list"]]), (2, ["FR 1234", "XY 101"]))
        new = self.sensors.values["flight_statistics"][1]
        self.assertEqual((new["upcoming"], new["flights"], new["list_total"]),
                         (stats["upcoming"] + 1, stats["flights"], stats["list_total"] + 1))

    def test_then_the_clock_checks_it(self):
        later = self.today + days(10)
        self.add()                                            # self.day: AviationStack knows it from the day before
        self.add(flight="FR 1235", date=later.isoformat())    # not yet: no call, the check at 04:00 of its day
        self.answers = [self.api()]
        self.assertEqual(eng.check_flights(CFG, self.at(20), lookup=self.lookup), 1)
        self.assertEqual(self.requests, [("fake-flights-key", "FR", "1234", self.day.isoformat(), "BLQ")])
        v = self.row(1)
        self.assertEqual((json.loads(v["status"])["api_status"], v["next_check"]), ("scheduled", eng.utc(self.at(1))))
        at4 = datetime.datetime.combine(later, datetime.time(4)).astimezone()
        self.assertEqual((self.row(2)["status"], self.row(2)["next_check"]), (None, eng.utc(at4)))


class AddPast(Base):
    """A flight already flown: only the statistics, from the day before yesterday back ([Q2=A])."""

    def test_only_the_statistics(self):
        past = self.today - days(30)
        self.ha.states[eng.FR24_TRACKED] = {"state": "0", "attributes": {"flights": []}}
        self.assertEqual(self.add(date=past.isoformat(), passengers="Carla, Dario 7F"),
                         {"id": 1, "result": "added", "message": added(past, 2, PAST)})
        self.assertIsNone(self.row(1)["next_check"])
        now = self.now()
        self.assertEqual((eng.check_flights(CFG, now, lookup=self.lookup), eng.track_fr24(now),
                          eng.notify_gate(CFG, now)), (0, (0, 0), 0))
        self.assertEqual((self.requests, self.ha.services, self.ha.notifications), ([], [], []))
        self.assertEqual(self.sensors.values["next_flight"], ("Nessun volo", {}))
        self.assertEqual(self.sensors.values["upcoming_flights"], (0, {"list": []}))
        km = eng.flight_stats.distance("BLQ", "STN")
        minutes, _ = eng.flight_stats.duration(None, km)                 # estimated from the distance
        state, s = self.sensors.values["flight_statistics"]
        self.assertEqual((state, {k: s[k] for k in ("flights", "upcoming", "no_data", "estimated_durations", "km",
                                                     "air_time_min", "cabins")}),
                         (1, {"flights": 1, "upcoming": 0, "no_data": 1, "estimated_durations": 1, "km": km,
                              "air_time_min": minutes, "cabins": {"business": 0, "economy": 0, "unknown": 1}}))
        self.assertEqual(s["list"], [{"date": past.isoformat(), "flight": "FR 1234", "route": "BLQ → STN",
                                      "cities": "Bologna → Londra Stansted", "duration_min": minutes, "km": km,
                                      "delay_min": None, "outcome": "flown", "estimated": True,
                                      "passengers": ["Carla", "Dario"]}])
        self.assertEqual([x["name"] for x in s["passengers"]], ["Carla", "Dario"])

    def test_yesterday_is_asked_once(self):
        yesterday = self.today - days(1)
        self.assertEqual(self.add(date=yesterday.isoformat(), passengers="Carla")["message"],
                         added(yesterday, 1, TRACKED))
        self.assertIsNotNone(self.row(1)["next_check"])
        landed = eng.flights.normalize(tf.api_flight("landed", date=yesterday.isoformat(), dep_actual="06:40",
                                                     dep_actual_runway="06:52", arr_actual_runway="08:01",
                                                     arr_actual="08:09"))
        self.answers, now = [landed], self.now()
        calls = eng.check_flights(CFG, now, lookup=self.lookup)
        if not eng.flights.queryable(yesterday.isoformat(), now):
            # far west of UTC, in the afternoon, the UTC date is already two days after yesterday: AviationStack's
            # window (flights.queryable) is over, as for an uploaded pass
            self.assertEqual((calls, self.requests), (0, []))
            return
        self.assertEqual(self.requests, [("fake-flights-key", "FR", "1234", yesterday.isoformat(), "BLQ")])
        self.assertEqual(self.ha.notifications, [])                          # landed: nobody is told
        self.assertIsNone(self.row(1)["next_check"])
        (entry,) = self.sensors.values["flight_statistics"][1]["list"]
        self.assertEqual((entry["outcome"], entry["estimated"], entry["duration_min"]),
                         ("flown", False, eng.flight_stats.duration(landed)[0]))

    def test_the_day_before_yesterday_is_past(self):
        day = self.today - days(2)
        self.assertEqual(self.add(date=day.isoformat(), passengers="Carla")["message"], added(day, 1, PAST))
        self.assertIsNone(self.row(1)["next_check"])
        self.assertEqual(self.sensors.values["flight_statistics"][1]["no_data"], 1)


class SameTreatment(Base):
    """A typed future flight is followed as a photo of its pass is (plan 7.1): the same flight, once uploaded and
    once typed, through one AviationStack timeline, the clock's jobs at the instants they are due."""

    def world(self, create):
        """[step] of a new world where `create` made the flight: what each job returned, sent and left."""
        self.fresh()
        create()
        eng.send_pending_notifications(CFG)                                # the upload's «Carta d'imbarco letta»
        blq = eng.flights.coordinates("BLQ")
        self.ha.states["person.alice"] = {"state": "not_home", "attributes": {
            "latitude": blq[0], "longitude": blq[1], "gps_accuracy": 15}}
        self.ha.states[eng.FR24_TRACKED] = {"state": "0", "attributes": {"flights": []}}
        trace, seen = [], [0, 0, 0]

        def step(name, now, job=lambda now: None):
            result = job(now)
            state, card = eng.compute_sensors(now=now)["next_flight"]
            trace.append({"step": name, "at": eng.utc(now), "result": result,
                          "row": eng.q("SELECT status, snapshot, next_check FROM flights", one=True), "state": state,
                          "card": {k: x for k, x in card.items() if k != "updated_at"},     # the wall clock's
                          "notifications": self.ha.notifications[seen[0]:], "services": self.ha.services[seen[1]:],
                          "lookups": self.requests[seen[2]:]})
            seen[:] = len(self.ha.notifications), len(self.ha.services), len(self.requests)

        def check(answer):
            def job(now):
                self.answers.append(answer)
                return eng.check_flights(CFG, now, lookup=self.lookup)
            return job

        def due():
            return datetime.datetime.fromisoformat(
                eng.q("SELECT next_check FROM flights", one=True)["next_check"].replace("Z", "+00:00"))

        def london(hhmm):
            return datetime.datetime.combine(self.day, datetime.time.fromisoformat(hhmm), self.LONDON)

        def gate(now):
            return eng.notify_gate(CFG, now)
        step("created", self.at(21))
        step("scheduled, gate A12", self.at(20), check(self.api(dep_gate="A12")))
        step("Flightradar24 from 3 hours before", self.at(3), eng.track_fr24)
        self.ha.states[eng.FR24_TRACKED]["attributes"]["flights"] = [{"flight_number": "FR1234",
                                                                      "tracked_type": "schedule"}]
        step("delay 25", due(), check(self.api(dep_gate="A12", dep_delay=25)))
        step("go to the gate: not yet", london("04:40"), gate)
        step("gate A14", due(), check(self.api(dep_gate="A14", dep_terminal="1", dep_delay=25)))
        step("go to the gate", london("05:10"), gate)                    # 9 minutes from BLQ to the gate
        step("departed", london("06:10"), check(self.api("active", dep_gate="A14", dep_terminal="1", dep_delay=25,
                                                         dep_actual="05:57")))
        step("landed", london("08:05"), check(self.api("landed", dep_gate="A14", dep_terminal="1", dep_delay=25,
                                                       dep_actual="05:57", dep_actual_runway="06:05",
                                                       arr_actual_runway="07:52", arr_actual="07:58")))
        step("Flightradar24: over", london("10:00"), eng.track_fr24)
        return trace

    def test_a_typed_flight_is_followed_like_a_photo_of_its_pass(self):
        photo = self.world(lambda: self.upload_photo([tf.bcbp(day=self.julian)]))      # Mario Rossi, 12A, by alice
        typed = self.world(lambda: self.add(passengers="Mario 12A"))                    # Mario 12A, typed by alice
        (p0, *p), (t0, *t) = photo, typed
        self.assertEqual(t, p)
        # before the first answer: the upload's own notification, and where the status comes from
        self.assertEqual([(n["to"], n["title"], n["tag"]) for n in p0["notifications"]],
                         [("mobile_app_alice", "Carta d'imbarco letta", f"{P}-d1")])
        self.assertEqual(t0["notifications"], [])
        self.assertEqual((p0["state"], p0["card"]["status"], p0["card"]["source"]), (FROM_PASS, FROM_PASS,
                                                                                     "boarding_pass"))
        self.assertEqual((t0["state"], t0["card"]["status"], t0["card"]["source"]), (TYPED, TYPED, "manual"))

        def rest(x):                             # the first check is due at once in both worlds
            return {**x, "notifications": None, "state": None,
                    "row": {**x["row"], "next_check": bool(x["row"]["next_check"])},
                    "card": {k: v for k, v in x["card"].items() if k not in ("status", "source")}}
        self.assertEqual(rest(t0), rest(p0))
        # and the timeline happened: what each step did
        delay, gate_change = "FR 1234: in ritardo di 25 min", "FR 1234: cambia il gate"
        self.assertEqual([(x["step"], x["result"], [(n["to"], n["title"]) for n in x["notifications"]], x["services"])
                          for x in t],
                         [("scheduled, gate A12", 1, [], []),
                          ("Flightradar24 from 3 hours before", (1, 0), [], [fr24(eng.FR24_ADD)]),
                          ("delay 25", 1, [("mobile_app_alice", delay), ("mobile_app_bob", delay)], []),
                          ("go to the gate: not yet", 0, [], []),
                          ("gate A14", 1, [("mobile_app_alice", gate_change), ("mobile_app_bob", gate_change)], []),
                          ("go to the gate", 1, [("mobile_app_alice", "FR 1234: avviati al gate A14")], []),
                          ("departed", 1, [], []), ("landed", 1, [], []),
                          ("Flightradar24: over", (0, 1), [], [fr24(eng.FR24_REMOVE)])])
        self.assertIsNone(t[-1]["row"]["next_check"])


class Duplicates(Base):
    """«Aggiungi volo» for a flight already there (plan 6.2): passengers merged, the flight row untouched."""

    def test_merged_into_a_typed_flight(self):
        self.add(passengers="Alice 12A")
        flights = self.flights()
        self.assertEqual(self.add(passengers="Bob, Alice"),
                         {"id": 1, "result": "merged", "message": merged(self.day, "1 passeggero aggiunto", 2)})
        self.assertEqual(self.flights(), flights)
        self.assertEqual([(c["passenger"], c["seat"], c["uploaded_by"]) for c in self.passes()],
                         [("Alice", "12A", "alice"), ("Bob", None, "bob")])

    def test_merged_into_an_uploaded_flight(self):
        d = self.upload(self.boarding_pass())                                   # Mario Rossi, 12A, by alice
        flights, rounds = self.flights(), self.sensors.rounds
        again = {"passengers": "Mario 14C, Anna 3C"}
        self.assertEqual(self.add(BOB, **again), {"id": 1, "result": "merged", "message": merged(
            self.day, "1 posto cambiato, 1 passeggero aggiunto", 2)})
        self.assertEqual(self.flights(), flights)                  # pass data beats typed data: next_check, times...
        self.assertEqual([(c["document_id"], c["passenger"], c["first_name"], c["seat"], c["cabin"], c["uploaded_by"])
                          for c in self.passes()],
                         [(d["id"], "Mario Rossi", "Mario", "14C", "Y", "alice"),
                          (None, "Anna", "Anna", "3C", None, "bob")])
        # the same request again, and one without seats (a merge keeps the seats): nothing to do, nothing published
        same = {"id": 1, "result": "unchanged", "message": unchanged(self.day)}
        self.assertEqual(self.add(BOB, **again), same)
        self.assertEqual(self.add(passengers="Mario, anna"), same)
        self.assertEqual(self.sensors.rounds, rounds + 1)
        self.assertEqual(self.passes()[0]["seat"], "14C")

    def test_another_destination_is_refused(self):
        self.add()
        self.refused(409, text("other_destination", code="FR 1234", day=dmy(self.day), origin="Bologna",
                               destination="Londra Stansted"),
                     action="add", flight="FR 1234", date=self.day.isoformat(), origin="BLQ", destination="DUB",
                     passengers="Carla")

    def test_the_full_name_of_an_uploaded_passenger(self):
        d = self.upload(self.boarding_pass(name="BIANCHI/CARLA"))              # Carla Bianchi, 12A
        reply = self.add(passengers="Carla Bianchi 14C, Dario")
        self.assertEqual(reply, {"id": 1, "result": "merged",
                                 "message": merged(self.day, "1 posto cambiato, 1 passeggero aggiunto", 2)})
        self.assertEqual([(c["document_id"], c["passenger"], c["first_name"], c["seat"]) for c in self.passes()],
                         [(d["id"], "Carla Bianchi", "Carla", "14C"), (None, "Dario", "Dario", None)])
        self.assertEqual(self.sensors.values["next_flight"][1]["passengers"], ["Carla (14C)", "Dario"])

    def test_an_old_row_without_a_document_is_not_typed_onto(self):
        # a 0.1.0 row of App Casa without a document (S11's shape): its full name typed changes nothing
        fid = self.new_flight(airline="FR", number="1234", date=self.day.isoformat())
        with eng._db_lock:
            eng.db().execute("INSERT INTO boarding_passes (document_id, flight_id, passenger, first_name, seat, cabin, "
                             "uploaded_by) VALUES (NULL, ?, 'Paola Neri', 'Paola', '3C', NULL, 'alice')", (fid,))
        rows = self.passes()
        self.assertEqual(self.add(passengers="Paola Neri"), {"id": fid, "result": "unchanged",
                                                             "message": unchanged(self.day)})
        self.assertEqual(self.passes(), rows)
        self.assertEqual(self.call("GET", "/flights")[1]["flights"][0]["status"], FROM_PASS)     # not «a mano»

    def test_an_unnamed_pass_named_by_its_full_name(self):
        # plan 6.2: a pass without «/» has no first name; its full name typed names it (document and seat kept)
        d = self.upload(self.boarding_pass(name="ROSSI MARIO"))
        self.assertEqual(self.line(1), "passeggero 12A")
        self.assertEqual(self.add(passengers="Rossi Mario")["result"], "merged")
        self.assertEqual([(c["document_id"], c["passenger"], c["first_name"], c["seat"]) for c in self.passes()],
                         [(d["id"], "Rossi Mario", "Rossi Mario", "12A")])

    def test_a_typed_name_never_renames_a_passenger(self):
        # odd rows of App Casa (no documents) where a full name is another row's first name: «Anna Rossi» takes
        # her row, and the new «Rossi» would be the full name of Luca's row, which is not renamed
        fid = self.new_flight(airline="FR", number="1234", date=self.day.isoformat())
        with eng._db_lock:
            for passenger, first_name in (("Rossi", "Luca"), ("Anna Rossi", "Rossi")):
                eng.db().execute("INSERT INTO boarding_passes (document_id, flight_id, passenger, first_name, seat, "
                                 "cabin, uploaded_by) VALUES (NULL, ?, ?, ?, NULL, NULL, 'alice')",
                                 (fid, passenger, first_name))
        self.refused(400, text("same_name"), action="add", flight="FR 1234", date=self.day.isoformat(), origin="BLQ",
                     destination="STN", passengers="Anna Rossi, Rossi")


class Claim(Base):
    """A pass uploaded after its passenger was typed takes that passenger's place (plan 6.7)."""

    def test_the_pass_takes_the_typed_passenger(self):
        self.add(passengers="Alice 3C")                                         # Alice's, typed by alice
        d = self.upload(self.boarding_pass(name="ROSSI/ALICE"), BOB_USER)       # 12A, sent by bob
        self.assertEqual([(c["id"], c["document_id"], c["passenger"], c["first_name"], c["seat"], c["cabin"],
                           c["uploaded_by"]) for c in self.passes()],
                         [(1, d["id"], "Alice Rossi", "Alice", "12A", "Y", "bob")])
        self.assertEqual(d["outcome"], f"FR 1234 BLQ → STN, {eng.dd_mm(self.day.isoformat())}, posto 12A (Alice)."
                                       f"{TRACKED}")                            # no «Carta aggiornata:»

    def test_a_pass_without_a_seat_keeps_the_typed_one(self):
        self.add(passengers="Alice 3C")
        d = self.upload(self.boarding_pass(name="ROSSI/ALICE", seat=""))
        self.assertEqual([(c["passenger"], c["seat"], c["document_id"]) for c in self.passes()],
                         [("Alice Rossi", "3C", d["id"])])
        self.assertFalse(d["outcome"].startswith("Carta aggiornata"), d["outcome"])

    def test_typed_with_the_full_name(self):
        self.add(passengers="Carla Bianchi 3C")
        d = self.upload(self.boarding_pass(name="BIANCHI/CARLA"), BOB_USER)     # 12A, sent by bob
        self.assertEqual([(c["id"], c["document_id"], c["passenger"], c["first_name"], c["seat"], c["cabin"],
                           c["uploaded_by"]) for c in self.passes()],
                         [(1, d["id"], "Carla Bianchi", "Carla", "12A", "Y", "bob")])
        self.assertEqual(d["outcome"], f"FR 1234 BLQ → STN, {eng.dd_mm(self.day.isoformat())}, posto 12A (Carla)."
                                       f"{TRACKED}")                            # a new pass: no «Carta aggiornata:»
        self.add(flight="FR 77", passengers="Carla Bianchi 3C")
        d = self.upload(self.boarding_pass(name="BIANCHI/CARLA", number="77", seat=""))
        self.assertEqual([(c["passenger"], c["first_name"], c["seat"]) for c in self.passes_of(2)],
                         [("Carla Bianchi", "Carla", "3C")])                     # the typed seat, as in a claim
        self.assertFalse(d["outcome"].startswith("Carta aggiornata"), d["outcome"])

    def test_another_name_is_another_passenger(self):
        self.add(passengers="Alice")
        d = self.upload(self.boarding_pass(name="BIANCHI/BOB"))
        self.assertEqual([(c["document_id"], c["passenger"]) for c in self.passes()],
                         [(None, "Alice"), (d["id"], "Bob Bianchi")])

    def test_an_old_row_without_a_document_is_not_taken(self):
        # a 0.1.0 row without a document (the shape of the split harness's S11): a full name, so not typed
        fid = self.new_flight(airline="FR", number="1234", date=self.day.isoformat())
        with eng._db_lock:
            eng.db().execute("INSERT INTO boarding_passes (document_id, flight_id, passenger, first_name, seat, cabin, "
                             "uploaded_by) VALUES (NULL, ?, 'Alice Verdi', 'Alice', '5F', NULL, 'alice')", (fid,))
        self.assertEqual(self.add(passengers="Bob")["result"], "merged")
        d = self.upload(self.boarding_pass(name="ROSSI/ALICE"))
        self.assertEqual([(c["document_id"], c["passenger"], c["seat"]) for c in self.passes()],
                         [(None, "Alice Verdi", "5F"), (None, "Bob", None), (d["id"], "Alice Rossi", "12A")])

    def test_without_typed_passengers_an_upload_is_as_before(self):
        # passes on 0.1.0 too: rows and outcomes of 0.1.0
        day = eng.dd_mm(self.day.isoformat())
        self.upload(self.boarding_pass())
        d = self.upload(self.boarding_pass(name="ROSSI/ANNA MRS", seat="012B", seq="0043"))
        self.assertEqual(d["outcome"], f"FR 1234 BLQ → STN, {day}, posto 12B (Anna).{TRACKED}")
        d = self.upload(self.boarding_pass(seat="014C"))
        self.assertEqual(d["outcome"], f"Carta aggiornata: FR 1234 BLQ → STN, {day}, posto 14C (Mario).{TRACKED}")
        self.assertEqual([(c["passenger"], c["first_name"], c["seat"], c["document_id"]) for c in self.passes()],
                         [("Mario Rossi", "Mario", "14C", 3), ("Anna Rossi", "Anna", "12B", 2)])


# ---------------------------------------------------------------- edit
class EditDate(Base):
    """A new date (plan 6.3): what belonged to the old day is forgotten, the flight is checked from the start."""

    def uploaded_with_status(self):
        """Mario Rossi's pass with its semantics (departure, boarding, gate, relevantDate), then an AviationStack
        answer that announced a delay, and an error: every column of the old day is set."""
        dep = datetime.datetime.combine(self.day, datetime.time(5, 30), self.LONDON)
        sem = {"airlineCode": "FR", "flightNumber": 1234, "departureAirportCode": "BLQ",
               "destinationAirportCode": "STN", "currentDepartureDate": dep.isoformat(),
               "currentBoardingDate": (dep - datetime.timedelta(minutes=30)).isoformat(), "departureGate": "B7"}
        d = self.upload(tf.pkpass(tf.bcbp(day=self.julian), sem, relevantDate=dep.isoformat()))
        self.answers = [self.api(dep_gate="A12", dep_delay=25)]
        eng.check_flights(CFG, self.at(20), lookup=self.lookup)
        eng.update_flight(1, error="AviationStack 500: prova")
        v = self.row(1)
        self.assertTrue(all(v[k] for k in ("pass_departure", "pass_boarding", "pass_gate", "relevant_date", "status",
                                           "updated_at", "next_check", "error")), v)
        self.assertNotEqual(v["snapshot"], "{}")
        return d, v

    def test_the_old_day_is_forgotten(self):
        d, old = self.uploaded_with_status()
        new = self.day + days(1)
        before = datetime.datetime.now(datetime.timezone.utc)
        self.assertEqual(self.edit(1, date=new.isoformat()), (200, {"id": 1, "result": "edited", "message": edited(
            new, f"data cambiata (prima il {dmy(self.day)}), orari spostati al nuovo giorno", 1)}))
        v = self.row(1)
        self.assertTrue(eng.utc(before) <= v["next_check"] <= eng.utc(self.now()), v["next_check"])
        rome = eng.flights._zone("Europe/Rome")             # BLQ: the same local time on the new day
        moved = datetime.datetime.combine(new, datetime.datetime.fromisoformat(old["pass_departure"]).astimezone(rome)
                                          .time(), rome).isoformat()
        self.assertEqual({**v, "next_check": None},
                         {**old, "date": new.isoformat(), "pass_departure": moved, "pass_boarding": None,
                          "pass_gate": None, "relevant_date": None, "status": None, "updated_at": None,
                          "next_check": None, "error": None, "snapshot": "{}"})     # created_at and the rest kept
        self.assertEqual(self.doc(d["id"])["doc_date"], self.day.isoformat())
        self.assertEqual([(c["document_id"], c["seat"]) for c in self.passes()], [(d["id"], "12A")])

    def test_moved_to_the_past(self):
        self.add()
        past = self.today - days(30)
        self.assertEqual(self.edit(1, date=past.isoformat()), (200, {"id": 1, "result": "edited", "message": edited(
            past, f"data cambiata (prima il {dmy(self.day)})", 2, PAST)}))
        self.assertIsNone(self.row(1)["next_check"])
        self.assertEqual(eng.check_flights(CFG, self.now(), lookup=self.lookup), 0)

    def test_a_date_taken_is_refused(self):
        self.add()
        self.add(date=(self.day + days(1)).isoformat())
        self.refused(409, text("date_taken", code="FR 1234", day=dmy(self.day + days(1)), origin="Bologna"),
                     action="edit", ref=self.ref(1), date=(self.day + days(1)).isoformat())

    def test_the_same_date_changes_nothing(self):
        self.add()
        rounds, d = self.sensors.rounds, self.day
        self.assertEqual(self.edit(1, date=f"{d.day}/{d.month}/{d.year}"),
                         (200, {"id": 1, "result": "unchanged", "message": unchanged(d)}))
        self.assertEqual(self.sensors.rounds, rounds)

    def test_flightradar24_drops_the_old_date(self):
        self.tracked_by_flightradar24()
        eng._fr24_attempts["FR1234"] = self.at(3)
        self.assertEqual(self.edit(1, date=(self.day + days(5)).isoformat())[0], 200)
        self.assertNotIn("FR1234", eng._fr24_attempts)              # the new date is added as soon as it is due
        self.assertEqual(self.ha.services, [])                      # no HA call from the request: the clock does it
        self.assertEqual(eng.track_fr24(self.at(2)), (0, 1))
        self.assertEqual(self.ha.services, [fr24(eng.FR24_REMOVE)])
        self.assertEqual(eng.track_fr24(self.at(1.9)), (0, 0))      # once

    def test_flightradar24_keeps_an_ident_still_in_its_window(self):
        self.tracked_by_flightradar24()                             # FR 1234 of self.day, in its window at at(2)
        self.add(date=(self.day + days(3)).isoformat())             # 2: the same flight three days later
        self.assertEqual(self.edit(2, date=(self.day + days(4)).isoformat())[0], 200)
        self.assertEqual(eng.track_fr24(self.at(2)), (0, 0))
        self.assertEqual(self.ha.services, [])

    def test_flightradar24_not_tracking_it(self):
        self.tracked_by_flightradar24()
        self.ha.states[eng.FR24_TRACKED]["attributes"]["flights"] = []
        self.assertEqual(self.edit(1, date=(self.day + days(5)).isoformat())[0], 200)
        self.assertEqual(eng.track_fr24(self.at(2)), (0, 0))
        self.assertEqual(self.ha.services, [])

    def test_flightradar24_an_edit_while_it_reads_the_rows(self):
        self.tracked_by_flightradar24()
        r, real, fired = self.ref(1), eng.q, []

        def q(sql, args=(), one=False):
            rows = real(sql, args, one)
            if sql == "SELECT * FROM flights WHERE date >= ?" and not fired:     # track_fr24's rows, just read
                fired.append(eng.change_flight(json.dumps({"action": "edit", "ref": r, "date": (
                    self.day + days(7)).isoformat()}).encode(), te.USER, CFG))
            return rows
        with unittest.mock.patch.object(eng, "q", q):
            self.assertEqual(eng.track_fr24(self.at(2)), (0, 0))           # its rows have the old date
        self.assertEqual(fired[0][0], 200)
        self.assertEqual(eng.track_fr24(self.at(2)), (0, 1))               # the next run reads the new one
        self.assertEqual(self.ha.services, [fr24(eng.FR24_REMOVE)])

    def test_flightradar24_told_on_the_next_run_after_an_ha_error(self):
        self.tracked_by_flightradar24()
        self.assertEqual(self.edit(1, date=(self.day + days(5)).isoformat())[0], 200)
        with unittest.mock.patch.object(self.ha, "service", side_effect=eng.ha.HAError("HA unreachable (test)")):
            with self.assertRaises(eng.ha.HAError):                 # as today: the clock logs it
                eng.track_fr24(self.at(2))
        self.assertEqual(eng.track_fr24(self.at(2)), (0, 1))
        self.assertEqual(self.ha.services, [fr24(eng.FR24_REMOVE)])

    def test_the_gate_reminder_again_on_the_new_day(self):
        self.upload(self.boarding_pass())
        self.answers = [self.api(dep_gate="A12")]
        eng.check_flights(CFG, self.at(1), lookup=self.lookup)
        self.assertEqual(eng.notify_gate(CFG, self.at(1) + datetime.timedelta(minutes=5)), 1)
        self.assertEqual(self.edit(1, date=(self.day + days(1)).isoformat())[0], 200)
        self.day += days(1)                                         # at() and api() of the new day
        self.answers = [self.api(dep_gate="A12")]
        self.assertEqual(eng.check_flights(CFG, self.at(1), lookup=self.lookup), 1)
        self.assertEqual(eng.notify_gate(CFG, self.at(1) + datetime.timedelta(minutes=5)), 1)
        self.assertEqual([(n["to"], n["title"]) for n in self.notifications()],
                         [("mobile_app_alice", "FR 1234: avviati al gate A12")] * 2)


class EditPassengers(Base):
    """The passengers line of «Modifica volo» (plan 6.4): seats changed, people added and removed."""

    def test_a_seat_of_an_uploaded_pass(self):
        d = self.upload(self.boarding_pass())                                   # Mario Rossi, 12A, by alice
        flights = self.flights()
        self.assertEqual(self.line(1), "Mario 12A")
        self.assertEqual(self.edit(1, BOB, passengers="Mario 14C"),
                         (200, {"id": 1, "result": "edited", "message": edited(self.day, "1 posto cambiato", 1)}))
        self.assertEqual([(c["id"], c["document_id"], c["passenger"], c["first_name"], c["seat"], c["cabin"],
                           c["uploaded_by"]) for c in self.passes()],
                         [(1, d["id"], "Mario Rossi", "Mario", "14C", "Y", "alice")])
        self.assertEqual(self.flights(), flights)                               # a seat never touches the flight
        # the line came with the seats: one left out is removed
        self.assertEqual(self.edit(1, passengers="Mario")[1]["message"], edited(self.day, "1 posto cambiato", 1))
        self.assertIsNone(self.passes()[0]["seat"])

    def test_people_added_and_removed(self):
        self.add(passengers="Alice 12A, Carla 3C")
        self.assertEqual(self.edit(1, BOB, passengers="Alice 12A, Bob, Dario 5F"), (200, {
            "id": 1, "result": "edited", "message": edited(self.day, "2 passeggeri aggiunti, 1 passeggero tolto", 3)}))
        self.assertEqual([(c["passenger"], c["seat"], c["uploaded_by"]) for c in self.passes()],
                         [("Alice", "12A", "alice"), ("Bob", None, "bob"), ("Dario", "5F", "bob")])

    def test_a_removed_pass_takes_its_document(self):
        old = self.upload(self.boarding_pass())                                 # replaced by the next one
        mario = self.upload(self.boarding_pass(seat="014C"))
        anna = self.upload(self.boarding_pass(name="ROSSI/ANNA MRS", seat="012B", seq="0043"))
        self.assertEqual(self.line(1), "Mario 14C, Anna 12B")
        self.assertEqual(self.edit(1, passengers="Anna 12B")[1]["message"],
                         edited(self.day, "1 passeggero tolto con la sua carta d'imbarco", 1))
        self.assertIsNone(self.doc(mario["id"]))                                # [Q1=A]: its row and its file
        self.assertFalse(os.path.exists(self.file(mario)))
        # kept: Anna's, and the older version of Mario's pass (only a delete sweeps the documents of its flight)
        self.assertEqual([self.doc(x["id"])["id"] for x in (old, anna)], [old["id"], anna["id"]])
        self.assertTrue(os.path.exists(self.file(old)) and os.path.exists(self.file(anna)))

    def test_a_document_of_two_legs_stays(self):
        d = self.two_legs()
        self.assertEqual(self.add(passengers="Bob")["result"], "merged")         # into FR 1234
        self.assertEqual(self.edit(1, passengers="Bob")[1]["message"], edited(self.day, "1 passeggero tolto", 1))
        self.assertEqual(self.doc(d["id"])["id"], d["id"])                      # FR 202 still uses it
        self.assertTrue(os.path.exists(self.file(d)))
        self.assertEqual([(c["flight_id"], c["passenger"]) for c in self.passes()], [(2, "Mario Rossi"), (1, "Bob")])

    def test_the_last_passenger_stays(self):
        self.add()
        self.refused(409, text("last_passenger"), action="edit", ref=self.ref(1), passengers=", ,")

    def test_date_and_passengers_together(self):
        self.add()
        new = self.day + days(1)
        self.assertEqual(self.edit(1, date=new.isoformat(), passengers="Alice 14C, Bob, Carla"), (200, {
            "id": 1, "result": "edited", "message": edited(
                new, f"data cambiata (prima il {dmy(self.day)}), 1 posto cambiato, 1 passeggero aggiunto", 3)}))

    def test_two_marios_through_their_line(self):
        self.upload(self.boarding_pass())                                                   # Mario Rossi, 12A
        self.upload(self.boarding_pass(name="BIANCHI/MARIO", seat="014C", seq="0043"))      # Mario Bianchi, 14C
        line = self.line(1)
        self.assertEqual(line, "Mario 12A, Mario 14C")
        self.assertEqual(self.edit(1, passengers=line)[1]["result"], "unchanged")
        self.assertEqual(self.edit(1, passengers="Mario 12A, Mario 15D")[1]["message"],
                         edited(self.day, "1 posto cambiato", 2))
        self.assertEqual([(c["passenger"], c["seat"]) for c in self.passes()],
                         [("Mario Rossi", "12A"), ("Mario Bianchi", "15D")])

    def test_a_full_name_keeps_the_pass(self):
        d = self.upload(self.boarding_pass(name="BIANCHI/CARLA"))              # Carla Bianchi, 12A
        self.assertEqual(self.edit(1, passengers="Carla Bianchi 14C"),
                         (200, {"id": 1, "result": "edited", "message": edited(self.day, "1 posto cambiato", 1)}))
        self.assertEqual([(c["document_id"], c["passenger"], c["first_name"], c["seat"]) for c in self.passes()],
                         [(d["id"], "Carla Bianchi", "Carla", "14C")])
        self.assertTrue(self.doc(d["id"]) and os.path.exists(self.file(d)))

    def test_the_surname_as_the_hint_says(self):
        d = self.upload(self.boarding_pass(name="BIANCHI/CARLA"))              # Carla Bianchi, 12A
        self.refused(400, text("same_name"), action="edit", ref=self.ref(1), passengers="Carla 12A, Carla")
        self.assertEqual(self.edit(1, passengers="Carla Bianchi 12A, Carla"),
                         (200, {"id": 1, "result": "edited", "message": edited(self.day, "1 passeggero aggiunto", 2)}))
        self.assertEqual([(c["document_id"], c["passenger"], c["first_name"], c["seat"]) for c in self.passes()],
                         [(d["id"], "Carla Bianchi", "Carla", "12A"), (None, "Carla", "Carla", None)])
        self.assertEqual(self.line(1), "Carla 12A, Carla")
        self.assertEqual(self.edit(1, passengers=self.line(1))[1]["result"], "unchanged")

    def test_a_flight_with_more_than_nine(self):
        # nine typed and a tenth uploaded: nobody more by hand, but its line still changes seats and people
        self.add(passengers=", ".join(NAMES[:9]))
        self.upload(self.boarding_pass(name="BIANCHI/MARCO", seat="007C"))
        self.refused(400, text("too_many"), action="add", flight="FR 1234", date=self.day.isoformat(), origin="BLQ",
                     destination="STN", passengers="Nina")
        line = self.line(1)
        self.assertEqual(line, ", ".join(NAMES[:9]) + ", Marco 7C")
        self.assertEqual(self.edit(1, passengers=line.replace("Marco 7C", "Marco 8C")),
                         (200, {"id": 1, "result": "edited", "message": edited(self.day, "1 posto cambiato", 10)}))
        self.assertEqual(self.edit(1, passengers=self.line(1).replace("Bob", "Nina"))[1]["message"],
                         edited(self.day, "1 nome cambiato", 10))
        self.assertEqual(self.edit(1, passengers=self.line(1).replace("Carla, ", "").replace("Dario", "Olga"))[1][
            "message"], edited(self.day, "1 passeggero aggiunto, 2 passeggeri tolti", 9))
        self.refused(400, text("too_many"), action="edit", ref=self.ref(1), passengers=self.line(1) + ", Olga")

    def rows(self):
        return [(c["document_id"], c["passenger"], c["first_name"], c["seat"], c["uploaded_by"]) for c in self.passes()]

    def test_an_unnamed_pass_renamed(self):
        # the user's choice c: one name gone and one new name is a rename, the pass and its file stay
        d = self.upload(self.boarding_pass(name="ROSSI MARIO"))
        self.assertEqual(self.line(1), "passeggero 12A")
        self.assertEqual(self.edit(1, BOB, passengers="Carla 12A"), (200, {
            "id": 1, "result": "edited", "message": edited(self.day, "1 nome cambiato, la carta d'imbarco resta", 1)}))
        self.assertEqual(self.rows(), [(d["id"], "Rossi Mario", "Carla", "12A", "alice")])
        self.assertEqual(self.doc(d["id"])["id"], d["id"])
        self.assertTrue(os.path.exists(self.file(d)))
        self.assertEqual(self.line(1), "Carla 12A")
        self.assertEqual(self.call("GET", "/flights")[1]["flights"][0]["status"], FROM_PASS)     # still a pass

    def test_a_surname_changed_keeps_the_pass(self):
        d = self.upload(self.boarding_pass(name="ROSSI/MARIO MR", seat="014C"))
        self.assertEqual(self.edit(1, passengers="Mario Verdi")[1]["message"],
                         edited(self.day, "1 nome cambiato, la carta d'imbarco resta, 1 posto cambiato", 1))
        self.assertEqual(self.rows(), [(d["id"], "Mario Rossi", "Mario Verdi", None, "alice")])
        self.assertTrue(os.path.exists(self.file(d)))

    def test_a_typed_name_renamed(self):
        self.add(passengers="Alice 12A, Carla 3C")
        r = self.ref(1)
        self.assertEqual(self.edit(1, passengers="Alice 12A, Bob 3C")[1]["message"],
                         edited(self.day, "1 nome cambiato", 2))
        self.assertEqual(self.rows(), [(None, "Alice", "Alice", "12A", "alice"), (None, "Bob", "Bob", "3C", "bob")])
        # sent again after a timeout (the old ref): already so
        self.assertEqual(self.post(ALICE, action="edit", ref=r, date="", passengers="Alice 12A, Bob 3C")[1]["result"],
                         "unchanged")

    def test_two_names_changed_are_removed_with_their_passes(self):
        mario = self.upload(self.boarding_pass())
        anna = self.upload(self.boarding_pass(name="ROSSI/ANNA MRS", seat="012B", seq="0043"))
        self.assertEqual(self.edit(1, passengers="Elena, Franco")[1]["message"], edited(
            self.day, "2 passeggeri aggiunti, 2 passeggeri tolti con 2 carte d'imbarco", 2))
        self.assertEqual([x[0] for x in self.rows()], [None, None])
        self.assertFalse(os.path.exists(self.file(mario)) or os.path.exists(self.file(anna)))

    def test_lines_refused(self):
        self.add(passengers="Alice")
        r = self.ref(1)
        for line, key in (("Alice, Bob, bob", "same_name"), ("Alice, alice", "same_name"),
                          ("Alice, passeggero", "bad_passenger"), (", ".join(NAMES), "too_many")):
            with self.subTest(line=line):
                self.refused(400, text(key), action="edit", ref=r, passengers=line)


class Refs(Base):
    """The ref (plan 4.3, 6.6): a stale list or a reused id never edits the wrong flight, a retried request is
    recognised."""

    def test_a_check_keeps_the_ref(self):
        self.add()
        r = self.ref(1)
        self.answers = [self.api(dep_gate="A12", dep_delay=25)]
        eng.check_flights(CFG, self.at(20), lookup=self.lookup)
        self.assertEqual(self.ref(1), r)
        self.assertEqual(self.ok(action="edit", ref=r, passengers="Alice 14C, Bob")["result"], "edited")

    def test_an_unknown_flight(self):
        self.add()
        for action in ("edit", "delete"):
            self.refused(404, text("not_found"), action=action, ref="999-0123456789ab", date=self.day.isoformat())

    def test_a_stale_ref(self):
        self.add()
        r = self.ref(1)
        self.assertEqual(self.ok(action="edit", ref=r, passengers="Alice 14C, Bob")["result"], "edited")
        for body in ({"action": "edit", "date": (self.day + days(1)).isoformat()},
                     {"action": "edit", "passengers": "Alice 15D, Bob"}, {"action": "delete"}):
            with self.subTest(body=body):
                self.refused(409, text("changed"), ref=r, **body)
        r = self.ref(1)
        self.upload(self.boarding_pass())                           # a pass uploaded meanwhile changes it too
        self.refused(409, text("changed"), action="edit", ref=r, passengers="Alice 16E, Bob")

    def test_a_retried_request(self):
        self.add()
        new = self.day + days(1)
        body = {"action": "edit", "ref": self.ref(1), "date": new.isoformat(), "passengers": "Alice 14C, Bob"}
        self.assertEqual(self.ok(**body)["result"], "edited")
        rounds = self.sensors.rounds
        self.assertEqual(self.post(**body), (200, {"id": 1, "result": "unchanged", "message": unchanged(new)}))
        self.assertEqual(self.sensors.rounds, rounds)
        r = self.ref(1)
        self.assertEqual(self.ok(action="delete", ref=r)["result"], "deleted")
        self.refused(404, text("not_found"), action="delete", ref=r)

    def test_a_reused_id(self):
        self.add()
        r = self.ref(1)
        self.assertEqual(self.ok(action="delete", ref=r)["result"], "deleted")
        self.upload(self.boarding_pass(airline="AZ", number="2041"))     # it gets the free id again
        flights, passes = self.flights(), self.passes()
        self.assertEqual([v["id"] for v in flights], [1])
        self.refused(409, text("changed"), action="delete", ref=r)
        self.refused(409, text("changed"), action="edit", ref=r, date=(self.day + days(1)).isoformat())
        self.assertEqual((self.flights(), self.passes()), (flights, passes))

    def test_two_requests_with_one_ref(self):
        self.add(passengers="Alice 12A")
        for seats, expected in ((("14C", "15D"), [(200, "edited"), (409, text("changed"))]),
                                (("16E", "16E"), [(200, "edited"), (200, "unchanged")])):
            r, start, out = self.ref(1), threading.Barrier(2), []

            def send(seat, r=r, start=start, out=out):
                start.wait(10)
                code, body, _ = self.send(json.dumps({"action": "edit", "ref": r, "passengers": f"Alice {seat}"})
                                          .encode())
                out.append((code, body.get("result") or body.get("message")))
            threads = [threading.Thread(target=send, args=(s,)) for s in seats]
            for x in threads:
                x.start()
            for x in threads:
                x.join(20)
            self.assertEqual(sorted(out), expected, seats)


# ---------------------------------------------------------------- delete
class Delete(Base):
    """«Elimina il volo» (plan 6.5): the flight, its passengers and ([Q1=A]) the documents that served only it."""

    def test_the_flight_and_its_passengers(self):
        self.add()
        self.assertEqual(self.delete(1, BOB), (200, {"id": 1, "result": "deleted",
                                                     "message": text("deleted", code="FR 1234", day=dmy(self.day),
                                                                     route=ROUTE)}))
        self.assertEqual((self.flights(), self.passes()), ([], []))

    def test_documents_and_files(self):
        old = self.upload(self.boarding_pass())                                 # replaced by the next one
        mario = self.upload(self.boarding_pass(seat="014C"))
        anna = self.upload(self.boarding_pass(name="ROSSI/ANNA MRS", seat="012B", seq="0043"))
        queued = self.new_doc(status="in_coda", merchant="FR 1234", doc_date=self.day.isoformat())   # being read
        other = self.upload(self.boarding_pass(airline="AZ", number="2041"))    # another flight's
        self.assertEqual(self.delete(1)[0], 200)
        self.assertEqual([d["id"] for d in eng.q("SELECT id FROM documents ORDER BY id")], [queued, other["id"]])
        self.assertEqual([os.path.exists(self.file(d)) for d in (old, mario, anna, other)], [False, False, False, True])

    def test_a_document_of_another_flight_stays(self):
        d = self.two_legs()
        self.assertEqual(self.delete(1)[0], 200)
        self.assertEqual(self.doc(d["id"])["id"], d["id"])
        self.assertTrue(os.path.exists(self.file(d)))
        self.assertEqual([(c["flight_id"], c["document_id"]) for c in self.passes()], [(2, d["id"])])

    def test_files_outside_the_data_folder_stay(self):
        outside = tempfile.mkdtemp(dir=te.TEST_DIR)
        targets = [os.path.join(outside, f"{n}.pkpass") for n in ("a", "b", "c")]
        for path in targets:
            with open(path, "wb") as f:
                f.write(b"not ours")
        os.makedirs(os.path.join(eng.DATA_DIR, "file"), exist_ok=True)
        os.symlink(targets[2], os.path.join(eng.DATA_DIR, "file", "link.pkpass"))
        fid = self.new_flight(airline="FR", number="1234", date=self.day.isoformat())
        for i, file in enumerate((os.path.relpath(targets[0], eng.DATA_DIR), targets[1], "file/link.pkpass")):
            doc = self.new_doc(file=file)
            with eng._db_lock:
                eng.db().execute("INSERT INTO boarding_passes (document_id, flight_id, passenger, first_name, seat, "
                                 "cabin, uploaded_by) VALUES (?, ?, ?, ?, NULL, 'Y', 'alice')",
                                 (doc, fid, f"{NAMES[i]} Prova", NAMES[i]))
        self.assertEqual(self.delete(fid)[0], 200)
        self.assertEqual(eng.q("SELECT id FROM documents"), [])
        self.assertEqual([os.path.exists(path) for path in targets], [True] * 3)

    def test_a_file_that_is_a_link_stays(self):
        # the service makes no links: one made by hand (a dedupe tool) is left, and what it points to never removed
        d1 = self.upload(self.boarding_pass(name="BIANCHI/CARLA"))
        d2 = self.upload(self.boarding_pass(name="NERI/DARIO", number="202", origin="STN", destination="DUB"))
        os.remove(self.file(d1))
        os.symlink(self.file(d2), self.file(d1))                   # d1's file is now d2's
        self.assertEqual(self.delete(1)[0], 200)
        self.assertIsNone(self.doc(d1["id"]))
        self.assertEqual((self.doc(d2["id"])["id"], os.path.isfile(self.file(d2)), os.path.islink(self.file(d1))),
                         (d2["id"], True, True))
        self.assertIn("file of a deleted document is a link: not removed", self.output.getvalue())

    def test_a_file_named_by_another_document_stays(self):
        d = self.upload(self.boarding_pass())
        twin = self.new_doc(file=d["file"])                         # another row with the same file
        self.assertEqual(self.delete(1)[0], 200)
        self.assertEqual((self.doc(d["id"]), self.doc(twin)["id"]), (None, twin))
        self.assertTrue(os.path.exists(self.file(d)))

    def test_flightradar24_drops_it(self):
        self.tracked_by_flightradar24()
        self.assertEqual(self.delete(1)[0], 200)
        self.assertEqual(self.ha.services, [])                      # from the clock, not from the request
        self.assertEqual(eng.track_fr24(self.at(2)), (0, 1))
        self.assertEqual(self.ha.services, [fr24(eng.FR24_REMOVE)])

    def test_the_sensors_move_on(self):
        self.add(flight="FR 1", date=(self.today + days(3)).isoformat())
        self.add(flight="FR 2", date=(self.today + days(6)).isoformat())
        self.assertEqual(self.sensors.values["next_flight"][1]["flight"], "FR 1")
        self.assertEqual(self.delete(1)[0], 200)
        self.assertEqual((self.sensors.values["next_flight"][1]["flight"], self.sensors.values["upcoming_flights"][0]),
                         ("FR 2", 1))
        self.assertEqual(self.delete(2)[0], 200)
        self.assertEqual(self.sensors.values["next_flight"], ("Nessun volo", {}))
        self.assertEqual(self.sensors.values["upcoming_flights"], (0, {"list": []}))
        self.assertEqual(self.sensors.values["flight_statistics"][1]["list_total"], 0)

    def test_the_same_pass_sent_again_is_read_again(self):
        data = self.boarding_pass()
        received = (200, {"id": 1, "status": "queued", "message": "Ricevuto: lo leggo e ti mando una notifica."})
        self.assertEqual(self.call("POST", "/upload", data)[:2], received)
        eng.read_document(self.doc(1))
        self.assertEqual(self.delete(1)[0], 200)
        self.assertEqual(self.call("POST", "/upload", data)[:2], received)       # [Q1=A]: not «gia' mandato»
        eng.read_document(self.doc(1))
        self.assertEqual([(v["airline"], v["number"], v["date"]) for v in self.flights()],
                         [("FR", "1234", self.day.isoformat())])
        self.assertEqual([c["passenger"] for c in self.passes()], ["Mario Rossi"])


# ---------------------------------------------------------------- concurrency, privacy, schema, source
class Races(Base):
    """A change through POST /flight while the clock is halfway through the same flight (plan 8): the change wins."""

    def test_a_change_while_aviationstack_answers(self):
        self.upload(self.boarding_pass())
        new_day, edited_row = self.day + days(1), {}

        def lookup(key, airline, number, date, origin, now):
            if not edited_row:
                self.assertEqual(self.edit(1, date=new_day.isoformat())[0], 200)
                edited_row.update(self.row(1))
            else:
                self.assertEqual(self.delete(1)[0], 200)
            return self.api(dep_gate="A12", dep_delay=25)               # it would announce a delay
        self.assertEqual(eng.check_flights(CFG, self.at(20), lookup=lookup), 1)
        self.assertEqual(self.notifications(), [])                      # nothing about the old date
        self.assertEqual(self.row(1), edited_row)                       # nothing written over the edit
        self.day = new_day                                              # the new date's first check: deleted meanwhile
        self.assertEqual(eng.check_flights(CFG, self.at(20), lookup=lookup), 1)
        self.assertEqual((self.notifications(), self.flights()), ([], []))
        discarded = "flight #1 changed by hand during the check, result discarded"
        self.assertEqual(self.output.getvalue().count(discarded), 2)
        self.assertEqual(eng.q("SELECT sum(calls) AS n FROM api_usage", one=True)["n"], 2)    # the calls were made

    def test_a_change_while_the_phone_is_located(self):
        self.upload(self.boarding_pass())
        self.answers = [self.api(dep_gate="A12")]
        eng.check_flights(CFG, self.at(1), lookup=self.lookup)
        blq = eng.flights.coordinates("BLQ")
        here = {"state": "not_home", "attributes": {"latitude": blq[0], "longitude": blq[1], "gps_accuracy": 15}}
        edited_row, changes, state = {}, [], self.ha.state

        def located(entity):
            if entity != "person.alice":
                return state(entity)
            if not changes:
                try:
                    changes.append(self.edit(1, date=(self.day + days(1)).isoformat())[0])
                except AssertionError as e:          # notify_gate would take it for HA being down: kept for later
                    changes.append(e)
                edited_row.update(self.row(1))
            return here
        self.ha.state = located
        sent = eng.notify_gate(CFG, self.at(0.75))                     # its time, with the 9-minute walk
        self.assertEqual(changes, [200])
        self.assertEqual((sent, self.notifications()), (0, []))        # no «go to the gate» for the old date
        self.assertEqual(self.row(1), edited_row)

    def test_a_change_before_the_clock_writes_the_next_check(self):
        later, edited_row, started = self.today + days(10), {}, []
        self.add(date=later.isoformat())                                # not queryable: only the next check is written
        real = eng.flights.queryable

        def queryable(date_iso, now):
            if not started:
                started.append(date_iso)
                self.assertEqual(self.edit(1, date=(later + days(1)).isoformat())[0], 200)
                edited_row.update(self.row(1))
            return real(date_iso, now)
        with unittest.mock.patch.object(eng.flights, "queryable", queryable):
            self.assertEqual(eng.check_flights(CFG, self.now(), lookup=self.lookup), 0)
        self.assertEqual((started, self.row(1)), ([later.isoformat()], edited_row))   # the edit's next_check stays

    def test_a_clock_publish_does_not_hide_a_change(self):
        self.add(flight="FR 1", date=(self.today + days(5)).isoformat())
        self.add(flight="FR 2", date=(self.today + days(10)).isoformat())
        body = json.dumps({"action": "edit", "ref": self.ref(1), "date": (self.today + days(20)).isoformat()}).encode()
        real, computed, release, replies = eng.compute_sensors, threading.Event(), threading.Event(), []
        clock = threading.Thread(target=lambda: eng.publish_now(), name="clock (test)")

        def compute(*a, **k):
            values = real(*a, **k)
            if threading.current_thread() is clock and not computed.is_set():
                computed.set()
                release.wait(10)                                        # a slow round: the change commits meanwhile
            return values
        change = threading.Thread(target=lambda: replies.append(self.send(body)[:2]))
        with unittest.mock.patch.object(eng, "compute_sensors", compute):
            clock.start()
            self.assertTrue(computed.wait(10))
            change.start()
            change.join(1)                    # with the plan's publish lock it waits for the clock; without, it is done
            release.set()
            clock.join(10)
            change.join(10)
        self.assertEqual(replies[0][0], 200)
        self.assertEqual(self.sensors.values["next_flight"][1]["flight"], "FR 2")    # the last values are the newest


class Privacy(Base):
    """The journal has user names and flight codes, never a passenger's name (docs/PRIVACY.md)."""

    def test_no_passenger_names_in_the_log(self):
        d, new = self.day.isoformat(), self.day + days(1)
        self.add(passengers="Carla Neri 3C, Dario")
        self.choices()
        self.assertEqual(self.edit(1, BOB, date=new.isoformat(), passengers="Carla Neri 4D, Elena")[0], 200)
        self.refused(400, text("bad_passenger"), action="add", flight="FR 1234", date=d, origin="BLQ",
                     destination="STN", passengers="Carla Neri 12")
        self.assertEqual(self.delete(1)[0], 200)
        log = self.output.getvalue()
        for line in (f"flight #1 added by alice: FR 1234 {d} BLQ-STN, 2 passengers",
                     f"flight #1 edited by bob: FR 1234 {new.isoformat()} BLQ-STN (date from {d}, renamed 1, "
                     "seats 1), 2 passengers",
                     f"flight #1 deleted by alice: FR 1234 {new.isoformat()} BLQ-STN, 2 passengers",
                     "POST /flight 200 Aggiunto FR 1234 del "):
            self.assertIn(line, log)
        for name in ("Carla", "Neri", "Dario", "Elena"):
            self.assertNotIn(name, log)


class Schema(Base):
    """No schema change but v2's (plan 4.2): the file stays the one a new file is."""

    def test_after_add_edit_and_delete(self):
        fresh = eng.open_db(os.path.join(tempfile.mkdtemp(dir=te.TEST_DIR), "fresh.db"))
        shape = "SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY name"
        expected = [tuple(r) for r in fresh.execute(shape)]
        fresh.close()
        self.add()
        self.assertEqual(self.edit(1, date=(self.day + days(1)).isoformat(), passengers="Alice 14C")[0], 200)
        self.add(flight="FR 9")
        self.assertEqual(self.delete(2)[0], 200)
        with eng._db_lock:
            c = eng.db()
            self.assertEqual((c.execute("PRAGMA user_version").fetchone()[0],
                              c.execute("PRAGMA application_id").fetchone()[0]), (2, 0x42505454))
            self.assertEqual([tuple(r) for r in c.execute(shape)], expected)
            self.assertEqual({r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")},
                             {"documents", "flights", "boarding_passes", "api_usage", "gate_positions"})


class Source(Base):
    """Where the card's status comes from ([Q3=A]): «Inserito a mano» until AviationStack knows a typed flight."""

    def test_typed_then_aviationstack(self):
        self.add()
        self.new_flight(date=(self.day + days(1)).isoformat())           # 2: no passengers, as some 0.1.0 rows
        state, card = self.sensors.values["next_flight"]
        self.assertEqual((state, card["status"], card["source"]), (TYPED, TYPED, "manual"))
        upcoming = eng.compute_sensors()["upcoming_flights"][1]["list"]
        self.assertEqual([x["status"] for x in upcoming], [TYPED, FROM_PASS])
        def listed():
            return {v["id"]: v["status"] for v in self.call("GET", "/flights")[1]["flights"]}
        self.assertEqual(listed(), {1: TYPED, 2: FROM_PASS})
        self.answers = [self.api()]
        eng.check_flights(CFG, self.at(20), lookup=self.lookup)
        state, card = eng.compute_sensors(now=self.at(20))["next_flight"]
        self.assertEqual((state, card["source"]), ("In orario", "aviationstack"))
        self.assertEqual(listed(), {1: "In orario", 2: FROM_PASS})

    def test_a_flight_with_an_uploaded_pass(self):
        self.upload(self.boarding_pass())
        self.assertEqual(self.add(passengers="Anna")["result"], "merged")     # one pass uploaded, one typed
        state, card = self.sensors.values["next_flight"]
        self.assertEqual((state, card["source"], card["passengers"]),
                         (FROM_PASS, "boarding_pass", ["Mario (12A)", "Anna"]))
        self.assertEqual(self.call("GET", "/flights")[1]["flights"][0]["status"], FROM_PASS)


if __name__ == "__main__":
    unittest.main()
