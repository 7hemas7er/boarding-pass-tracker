"""Engine tests, without network: a fake HA and MQTT, a stubbed barcode reader, a temporary database per test.

  cd tracker && python3 -I -B -m unittest discover -s . -p test_engine.py -v
"""
import atexit, contextlib, datetime, http.client, io, json, os, re, shutil, sqlite3, sys, tempfile, threading
import unittest, unittest.mock, urllib.request, zlib

TEST_DIR = tempfile.mkdtemp(prefix="tracker-test-")
atexit.register(shutil.rmtree, TEST_DIR, True)
os.environ.update(TRACKER_DATA_DIR=TEST_DIR, TRACKER_NO_NOTIFY="1", TRACKER_NO_HA="1", TRACKER_NO_MQTT="1",
                  TRACKER_MQTT_PREFIX="bptprova", TRACKER_CONFIG=os.path.join(TEST_DIR, "none.json"),
                  TRACKER_AVIATIONSTACK_KEY_FILE=os.path.join(TEST_DIR, "none.env"))
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import support  # noqa: E402  (the ported core on sys.path; its log lines to the current stdout)
HERE = support.CORE                    # the ported core: custom_components/boarding_pass_tracker/core
from support import handler  # noqa: E402  (the standalone's HTTP handler, on the ported core)
import engine as eng  # noqa: E402
import test_flights as tf  # noqa: E402  (synthetic Wallet passes and AviationStack flights)
from PIL import Image  # noqa: E402

CFG = {"users": {"alice@example.com": {"name": "alice", "notify": "mobile_app_alice", "person": "person.alice"},
                 "bob@example.com": {"name": "bob", "notify": "mobile_app_bob", "person": "person.bob"}}}
USER = CFG["users"]["alice@example.com"]
P = "bptprova"
NOT_FOUND = "Non trovo il codice a barre di una carta d'imbarco: l'ho archiviato."                       # BC4
NOT_A_PASS = ("Mandami una carta d'imbarco: il pass del Wallet, oppure una foto (JPEG o PNG) o un PDF con il suo "
              "codice a barre.")                                                                       # BC5

# The full v0 schema of the last Italian release of App Casa (its SCHEMA, INDICI and COLONNE, comments dropped),
# expenses and flights together: the migration input, so the names stay Italian
SCHEMA_V0 = """
CREATE TABLE IF NOT EXISTS documenti (
  id INTEGER PRIMARY KEY,
  sha256 TEXT NOT NULL UNIQUE,
  caricato_da TEXT NOT NULL,
  caricato_il TEXT NOT NULL,
  file TEXT NOT NULL,
  tipo_file TEXT NOT NULL,
  stato TEXT NOT NULL,
  tipo TEXT,
  negozio TEXT,
  data_doc TEXT,
  totale_cent INTEGER,
  totale_letto_cent INTEGER,
  somma_cent INTEGER,
  controllo TEXT,
  righe TEXT,
  trascrizione TEXT,
  modello TEXT,
  nodo TEXT,
  durata_s REAL,
  tentativi INTEGER NOT NULL DEFAULT 0,
  errore TEXT,
  notificato INTEGER NOT NULL DEFAULT 0,
  avvisato_coda INTEGER NOT NULL DEFAULT 0,
  risposto_il TEXT,
  categoria TEXT,
  categoria_fonte TEXT,
  esito TEXT
);
CREATE INDEX IF NOT EXISTS documenti_stato ON documenti(stato);
CREATE TABLE IF NOT EXISTS scadenze (
  id INTEGER PRIMARY KEY,
  documento_id INTEGER REFERENCES documenti(id),
  ente TEXT,
  cf_ente TEXT,
  codice_avviso TEXT,
  importo_cent INTEGER,
  scadenza TEXT,
  modalita TEXT,
  fonte TEXT,
  stato TEXT NOT NULL,
  creata_il TEXT NOT NULL,
  pagata_il TEXT,
  pagata_come TEXT,
  movimento_id INTEGER,
  promemoria TEXT NOT NULL DEFAULT '{}',
  notificato INTEGER NOT NULL DEFAULT 0,
  in_ha INTEGER NOT NULL DEFAULT 0,
  note TEXT,
  risposto_il TEXT
);
CREATE TABLE IF NOT EXISTS movimenti (
  id INTEGER PRIMARY KEY,
  documento_id INTEGER REFERENCES documenti(id),
  data TEXT NOT NULL,
  data_valuta TEXT,
  descrizione TEXT,
  importo_cent INTEGER NOT NULL,
  chiave TEXT NOT NULL UNIQUE,
  abbinato_tipo TEXT,
  abbinato_id INTEGER,
  abbinato_come TEXT,
  abbinato_motivo TEXT
);
CREATE TABLE IF NOT EXISTS voli (
  id INTEGER PRIMARY KEY,
  compagnia TEXT NOT NULL,
  numero TEXT NOT NULL,
  data TEXT NOT NULL,
  da TEXT NOT NULL,
  a TEXT NOT NULL,
  partenza_pass TEXT,
  imbarco_pass TEXT,
  gate_pass TEXT,
  rilevante TEXT,
  stato TEXT,
  aggiornato_il TEXT,
  prossimo_controllo TEXT,
  errore TEXT,
  avvisi TEXT NOT NULL DEFAULT '{}',
  creato_il TEXT NOT NULL,
  UNIQUE (compagnia, numero, data, da)
);
CREATE TABLE IF NOT EXISTS carte (
  id INTEGER PRIMARY KEY,
  documento_id INTEGER REFERENCES documenti(id),
  volo_id INTEGER NOT NULL REFERENCES voli(id),
  passeggero TEXT NOT NULL,
  nome TEXT,
  posto TEXT,
  classe TEXT,
  caricato_da TEXT NOT NULL,
  UNIQUE (volo_id, passeggero)
);
CREATE TABLE IF NOT EXISTS uso_api (mese TEXT PRIMARY KEY, chiamate INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS gate_pos (
  aeroporto TEXT NOT NULL, gate TEXT NOT NULL, lat REAL, lon REAL, cercato_il TEXT NOT NULL, errore TEXT,
  PRIMARY KEY (aeroporto, gate)
);
"""
INDEXES_V0 = """
CREATE UNIQUE INDEX IF NOT EXISTS scadenze_avviso ON scadenze(codice_avviso) WHERE codice_avviso IS NOT NULL;
CREATE INDEX IF NOT EXISTS scadenze_stato ON scadenze(stato);
CREATE INDEX IF NOT EXISTS movimenti_data ON movimenti(data);
"""
# The step-1 schema of v0 (before payments and flights existed): only documenti, without the later columns
SCHEMA_STEP_1 = """
CREATE TABLE IF NOT EXISTS documenti (
  id INTEGER PRIMARY KEY, sha256 TEXT NOT NULL UNIQUE, caricato_da TEXT NOT NULL, caricato_il TEXT NOT NULL,
  file TEXT NOT NULL, tipo_file TEXT NOT NULL, stato TEXT NOT NULL, tipo TEXT, negozio TEXT, data_doc TEXT,
  totale_cent INTEGER, totale_letto_cent INTEGER, somma_cent INTEGER, controllo TEXT, righe TEXT, trascrizione TEXT,
  modello TEXT, nodo TEXT, durata_s REAL, tentativi INTEGER NOT NULL DEFAULT 0, errore TEXT,
  notificato INTEGER NOT NULL DEFAULT 0, avvisato_coda INTEGER NOT NULL DEFAULT 0, risposto_il TEXT
);
CREATE INDEX IF NOT EXISTS documenti_stato ON documenti(stato);
"""
V0_TABLES = {"documenti": "documents", "voli": "flights", "carte": "boarding_passes", "uso_api": "api_usage",
             "gate_pos": "gate_positions"}
EXPENSE_TABLES_V0 = ("scadenze", "movimenti")
# The documenti columns of the expenses: household-ledger's, dropped by the tracker's migration
EXPENSE_COLUMNS_V0 = ("totale_cent", "totale_letto_cent", "somma_cent", "controllo", "righe", "trascrizione", "modello",
                      "nodo", "durata_s", "avvisato_coda", "risposto_il", "categoria", "categoria_fonte")
# The two boarding-pass documents of the tracker fixture: a Wallet pass, and a photo whose barcode was read
PASS_DOCUMENTS_V0 = {"tipo_file": ("pkpass", "png"), "tipo": ("carta_imbarco", "carta_imbarco"),
                     "stato": ("letto", "errore")}
# flights.status and flights.snapshot as v0 stored them, and as v1 must read them (TRANSLATION_MAP section 5)
_PAIRS = ("stato_api api_status cancellato cancelled deviato diverted partenza_prevista departure_scheduled "
          "partenza_stimata departure_estimated partenza_effettiva departure_actual decollo takeoff atterraggio landing "
          "arrivo_previsto arrival_scheduled arrivo_stimato arrival_estimated arrivo_effettivo arrival_actual "
          "ritardo_partenza departure_delay ritardo_arrivo arrival_delay gate gate terminal terminal "
          "gate_arrivo arrival_gate terminal_arrivo arrival_terminal nastro baggage_belt citta_da origin_city "
          "citta_a destination_city fuso_da origin_tz fuso_a destination_tz").split()
V0_STATUS = {**{old: f"value {i} è" for i, old in enumerate(_PAIRS[0::2])}, "a_new_key": [1, None]}
V1_STATUS = {**{new: f"value {i} è" for i, new in enumerate(_PAIRS[1::2])}, "a_new_key": [1, None]}
V0_SNAPSHOT = {"cancellato": False, "deviato": None, "gate": "B12", "ritardo": 25, "gate_inviato": True,
               "gate_inviato_a": ["alice"], "tragitto": {"alice": {"metri": 640, "minuti": 9, "margine": 19},
                                                          "bob": {"in_aeroporto": True}, "x": {"lontano_km": 12.5}},
               "unknown": 1}
V1_SNAPSHOT = {"cancelled": False, "diverted": None, "gate": "B12", "delay": 25, "gate_sent": True,
               "gate_sent_to": ["alice"], "walk": {"alice": {"meters": 640, "minutes": 9, "margin_min": 19},
                                                   "bob": {"at_airport": True}, "x": {"away_km": 12.5}},
               "unknown": 1}


def tiny_png(color=(255, 255, 255)):
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), color).save(buf, "PNG")
    return buf.getvalue()


def huge_png(side=9000):
    """A PNG whose header says side x side pixels, with the data of a 16x16 one: decoding it would need GBs."""
    buf = io.BytesIO()
    Image.new("RGB", (16, 16)).save(buf, "PNG")
    p = bytearray(buf.getvalue())
    p[16:24] = side.to_bytes(4, "big") * 2                      # IHDR, then its CRC
    p[29:33] = zlib.crc32(bytes(p[12:29])).to_bytes(4, "big")
    return bytes(p)


INVOICE_XML = (b'<?xml version="1.0" encoding="UTF-8"?>\n<p:FatturaElettronica versione="FPR12" '
               b'xmlns:p="http://ivaservizi.agenziaentrate.gov.it/docs/xsd/fatture/v1.2"><FatturaElettronicaHeader/>'
               b'</p:FatturaElettronica>')
BANK_CSV = b"Data;Descrizione;Importo\n01/10/2026;POS BAR;-1,20\n02/10/2026;POS EDICOLA;-2,50\n"


class FakeHA:
    """The ha.HA calls the engine makes, on in-memory lists. down=True: HA does not answer. registry: the entity
    registry, {entity id: entry} (None: every sensor ours, at its default id)."""

    def __init__(self):
        self.notifications, self.calls, self.states, self.services = [], [], {}, []
        self.down, self.refuse, self.registry = False, set(), None

    def _call(self, *call):
        if self.down:
            raise eng.ha.HAError("HA unreachable (test)")
        self.calls.append(call)

    def notify(self, notify, title, message, actions=None, tag=None):
        if self.down or notify in self.refuse:
            return False
        self.notifications.append({"to": notify, "title": title, "message": message, "actions": actions, "tag": tag})
        return True

    def state(self, entity):
        self._call("state", entity)
        return self.states.get(entity)

    def service(self, domain, name, data, response=False):
        self._call("service", domain, name)
        self.services.append((f"{domain}.{name}", data))

    def _registry(self):
        if self.registry is None:
            return {e: {"entity_id": e, "platform": "mqtt", "unique_id": u}
                    for e, u in eng.ha.sensor_entity_ids(eng.MQTT_PREFIX).items()}
        return self.registry

    def registry_entries(self, entity_ids):
        self._call("registry_entries")
        return {e: self._registry().get(e) for e in entity_ids}

    def registry_list(self):
        self._call("registry_list")
        return list(self._registry().values())


class FakeSensors:
    def __init__(self):
        self.values, self.connected = {}, True

    def publish(self, name, state, attributes=None):
        self.values[name] = (state, attributes)


class WithEngine(unittest.TestCase):
    """A new database in a new folder for every test; fake HA and sensors, a stubbed barcode reader; captured log."""

    def setUp(self):
        self.saved = {k: getattr(eng, k) for k in ("DATA_DIR", "_db", "_ha", "_sensors", "NO_HA", "NO_NOTIFY")}
        self.saved_reader = eng.barcodes.read_barcodes
        eng.DATA_DIR, eng._db = tempfile.mkdtemp(dir=TEST_DIR), None
        self.ha, self.sensors = FakeHA(), FakeSensors()
        eng._ha, eng._sensors, eng.NO_HA, eng.NO_NOTIFY = self.ha, self.sensors, False, False
        self.codes, self.read = [], []                # what the barcode reader finds, and what it was asked
        eng.barcodes.read_barcodes = self.fake_reader
        self.output = io.StringIO()
        r = contextlib.redirect_stdout(self.output)
        r.__enter__()
        self.addCleanup(r.__exit__, None, None, None)

    def tearDown(self):
        if eng._db is not None:
            eng._db.close()
        for k, v in self.saved.items():
            setattr(eng, k, v)
        eng.barcodes.read_barcodes = self.saved_reader
        self.assertNotIn("fake-token", self.output.getvalue())

    def fake_reader(self, path, max_pixels=None):
        self.read.append((os.path.basename(path), max_pixels))
        return list(self.codes)

    # ------------------------------------------------ helpers
    def upload_photo(self, codes, color=(1, 2, 3)):
        """A (fake) photo whose barcodes are `codes`, read by the engine as the worker does."""
        self.codes = codes
        doc_id, status, _ = eng.save_upload(tiny_png(color), "png", USER)
        self.assertEqual(status, "in_coda")
        eng.read_document(self.doc(doc_id))
        return self.doc(doc_id)

    def upload_file(self, data, file_type):
        self.assertEqual(eng.file_type_from_bytes(data), file_type)
        doc_id, status, _ = eng.save_upload(data, file_type, USER)
        eng.read_document(self.doc(doc_id))
        return self.doc(doc_id)

    def doc(self, doc_id):
        return eng.q("SELECT * FROM documents WHERE id=?", (doc_id,), one=True)

    def new_doc(self, **fields):
        row = dict(sha256=os.urandom(8).hex(), uploaded_by="alice", uploaded_at=eng.now_iso(), file="x",
                   file_type="pkpass", status="letto", kind="carta_imbarco", notified=1)
        row.update(fields)
        with eng._db_lock:
            return eng.db().execute(f"INSERT INTO documents ({','.join(row)}) VALUES ({','.join('?' * len(row))})",
                                    tuple(row.values())).lastrowid

    def new_flight(self, **fields):
        row = dict(airline="XY", number="101", date="2026-10-28", origin="BLQ", destination="STN",
                   created_at=eng.now_iso())
        row.update(fields)
        with eng._db_lock:
            return eng.db().execute(f"INSERT INTO flights ({','.join(row)}) VALUES ({','.join('?' * len(row))})",
                                    tuple(row.values())).lastrowid

    def flights(self):
        return eng.q("SELECT * FROM flights ORDER BY id")

    def passes(self):
        return eng.q("SELECT * FROM boarding_passes ORDER BY id")

    def notifications(self, to=None):
        return [n for n in self.ha.notifications if to is None or n["to"] == to]

    def serve(self):
        """The engine's Handler on an ephemeral 127.0.0.1 port, for this test."""
        saved, handler.Handler.cfg = handler.Handler.cfg, CFG
        srv = handler.ThreadingHTTPServer(("127.0.0.1", 0), handler.Handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(setattr, handler.Handler, "cfg", saved)
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        self.port = srv.server_address[1]

    def call(self, method, path, body=None, user="alice@example.com", **headers):
        """(code, JSON body, raw body, Server header) of one request to serve()'s port."""
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        h = {"Host": f"127.0.0.1:{self.port}", **({"Tailscale-User-Login": user} if user else {}), **headers}
        c.request(method, path, body=body, headers=h)
        r = c.getresponse()
        raw, server = r.read(), r.getheader("Server") or ""
        c.close()
        return r.status, json.loads(raw), raw, server.strip()


# ---------------------------------------------------------------- pure functions
class Formats(unittest.TestCase):
    def test_file_type_from_bytes(self):
        self.assertEqual(eng.file_type_from_bytes(tiny_png()), "png")
        self.assertEqual(eng.file_type_from_bytes(b"\xff\xd8\xff\xe0" + b"0" * 20), "jpg")
        self.assertEqual(eng.file_type_from_bytes(b"%PDF-1.7\n"), "pdf")
        self.assertEqual(eng.file_type_from_bytes(b"RIFF\x24\x00\x00\x00WEBPVP8 " + b"0" * 8), "webp")
        self.assertEqual(eng.file_type_from_bytes(b"\x00\x00\x00\x18ftypheic" + b"0" * 8), "heic")
        self.assertEqual(eng.file_type_from_bytes(tf.pkpass(tf.bcbp())), "pkpass")
        # household-ledger's types (an e-invoice, a signed one, a bank statement) and anything else: not a pass
        for data in (INVOICE_XML, b"\xef\xbb\xbf\n" + INVOICE_XML, b"0\x82\x01\x00" + INVOICE_XML, BANK_CSV,
                     b"<html>", b'<?xml version="1.0"?><altro/>', b"Caro Mario,\nci vediamo domani.\n", b""):
            self.assertIsNone(eng.file_type_from_bytes(data), data[:20])

    def test_extract_file(self):
        p = tiny_png()
        self.assertEqual(eng.extract_file("image/png", p), p)
        body = (b"--XX\r\nContent-Disposition: form-data; name=\"nota\"\r\n\r\nciao\r\n"
                b"--XX\r\nContent-Disposition: form-data; name=\"file\"; filename=\"a.png\"\r\n"
                b"Content-Type: image/png\r\n\r\n" + p + b"\r\n--XX--\r\n")
        self.assertEqual(eng.extract_file("multipart/form-data; boundary=XX", body), p)
        csv = (b"--XX\r\nContent-Disposition: form-data; name=\"file\"; filename=\"a.csv\"\r\n\r\n" + BANK_CSV
               + b"\r\n--XX--\r\n")
        self.assertIsNone(eng.extract_file("multipart/form-data; boundary=XX", csv))

    def test_clean(self):
        self.assertEqual(eng.clean("Ada\u202e Prova\x07\n bis"), "Ada Prova bis")
        self.assertEqual(eng.clean("[casa:CASA:s3] Ada"), "[casa:CASA:s3] Ada")      # no markers here: kept as text
        self.assertEqual(len(eng.clean("x" * 500)), 80)
        self.assertEqual(eng.clean("x" * 50, 30), "x" * 30)
        self.assertEqual(eng.clean(None), "")


# ---------------------------------------------------------------- database
class Migration(unittest.TestCase):
    @staticmethod
    def v0_database(folder, owner="tracker", flights_json=True, drop=None):
        """A v0 file of App Casa with every table and index. owner="tracker": two documenti rows that are boarding
        passes (PASS_DOCUMENTS_V0) with the expense columns empty, no scadenze or movimenti rows, two rows (three
        flights) in the other tables, every column set; owner="all": two rows in every table, every column set.
        Returns (path, rows): rows[v0 table] = the inserted rows as tuples, in column order."""
        path = os.path.join(folder, "casa.db")
        c = sqlite3.connect(path)
        c.executescript(SCHEMA_V0 + INDEXES_V0)
        if drop:                                             # an older v0 file, without one of its columns
            c.execute(f"ALTER TABLE {drop[0]} DROP COLUMN {drop[1]}")
        rows = {}
        for (table,) in c.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall():
            info = c.execute(f"PRAGMA table_info({table})").fetchall()
            rows[table] = []
            if owner == "tracker" and table in EXPENSE_TABLES_V0:
                continue                                     # created by the v0 code, empty
            for i in (1, 2, 3) if table == "voli" else (1, 2):
                row = []
                for _, name, kind, *_ in info:
                    if owner == "tracker" and table == "documenti" and name in PASS_DOCUMENTS_V0:
                        row.append(PASS_DOCUMENTS_V0[name][i - 1])
                    elif owner == "tracker" and table == "documenti" and name in EXPENSE_COLUMNS_V0:
                        row.append(0 if name == "avvisato_coda" else None)
                    elif name.endswith("_id") or name == "id":
                        row.append(i if name == "id" else 1)
                    elif kind == "INTEGER":
                        row.append(100 * i + len(name))
                    elif kind == "REAL":
                        row.append(i + 0.5)
                    else:
                        row.append(f"{table}.{name}.{i} àè")      # unique per row, and not ASCII
                rows[table].append(row)
        names = [r[1] for r in c.execute("PRAGMA table_info(voli)")]
        status, snapshot = names.index("stato"), names.index("avvisi")
        for r, (s, a) in zip(rows["voli"], [(json.dumps(V0_STATUS, ensure_ascii=False), json.dumps(V0_SNAPSHOT)),
                                            (None, "{}"), ("not json", "[1, 2]")]):
            r[status], r[snapshot] = (s, a) if flights_json else (None, a)
        for table, table_rows in rows.items():
            for r in table_rows:
                c.execute(f"INSERT INTO {table} VALUES ({','.join('?' * len(r))})", r)
        c.commit()
        c.close()
        return path, {t: [tuple(r) for r in v] for t, v in rows.items()}

    def open_quietly(self, path):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            c = eng.open_db(path)
        return c, out.getvalue()

    def assert_refused(self, path, message):
        with open(path, "rb") as f:
            before = f.read()
        with self.assertRaises(SystemExit) as e:
            self.open_quietly(path)
        self.assertEqual(str(e.exception), message)
        with open(path, "rb") as f:
            self.assertEqual(f.read(), before, "the refused file changed")
        self.assertFalse(os.path.exists(path + "-wal"))

    def test_a_fresh_file_gets_v1_directly(self):
        c, out = self.open_quietly(os.path.join(tempfile.mkdtemp(dir=TEST_DIR), "tracker.db"))
        self.assertEqual((c.execute("PRAGMA user_version").fetchone()[0], c.execute("PRAGMA application_id").fetchone()[0]),
                         (2, eng.APPLICATION_ID))
        self.assertEqual({r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")},
                         set(V0_TABLES.values()))
        self.assertEqual([r[1] for r in c.execute("PRAGMA table_info(documents)")],
                         ["id", "sha256", "uploaded_by", "uploaded_at", "file", "file_type", "status", "kind",
                          "merchant", "doc_date", "attempts", "error", "notified", "outcome"])
        self.assertEqual(out, "")
        c.close()

    def test_a_newer_schema_is_refused_untouched(self):
        path = os.path.join(tempfile.mkdtemp(dir=TEST_DIR), "tracker.db")
        c = sqlite3.connect(path)
        c.executescript("CREATE TABLE documenti (id INTEGER PRIMARY KEY); PRAGMA user_version = 3;")
        c.close()
        self.assert_refused(path, f"{path}: database schema version 3 is newer than this code (2): update "
                                  "boarding-pass-tracker")

    def test_another_applications_file_is_refused_untouched(self):
        path, _ = self.v0_database(tempfile.mkdtemp(dir=TEST_DIR))
        c = sqlite3.connect(path)
        c.execute(f"PRAGMA application_id = {0x484C4447}")          # household-ledger's
        c.close()
        self.assert_refused(path, f"{path}: this database belongs to another application (application_id "
                                  "0x484C4447): check the database setting")

    def test_a_v1_file_of_the_old_engine_with_payments_is_refused(self):
        # made by household-ledger before the split: version 1, no application_id, expenses and flights together
        path = os.path.join(tempfile.mkdtemp(dir=TEST_DIR), "ledger.db")
        c = sqlite3.connect(path)
        c.executescript("CREATE TABLE documents (id INTEGER PRIMARY KEY); CREATE TABLE flights (id INTEGER PRIMARY KEY);"
                        "CREATE TABLE payments (id INTEGER PRIMARY KEY); INSERT INTO payments DEFAULT VALUES;"
                        "PRAGMA user_version = 1;")
        c.close()
        self.assert_refused(path, f"{path}: this database holds another application's data (payments 1 rows): check "
                                  "the database setting")

    def test_database_path(self):
        folder = tempfile.mkdtemp(dir=TEST_DIR)
        saved = eng.DATA_DIR, eng.DATABASE
        try:
            eng.DATA_DIR, eng.DATABASE = folder, None
            self.assertEqual(eng.database_path(), os.path.join(folder, "tracker.db"))
            open(os.path.join(folder, "casa.db"), "w").close()       # App Casa's file is not taken over
            self.assertEqual(eng.database_path(), os.path.join(folder, "tracker.db"))
            eng.DATABASE = "~/elsewhere.db"
            self.assertEqual(eng.database_path(), os.path.expanduser("~/elsewhere.db"))
        finally:
            eng.DATA_DIR, eng.DATABASE = saved


class Rows(WithEngine):
    def test_rows_as_dicts(self):
        fid = self.new_flight(error="tetto di 1 chiamate al mese raggiunto")
        r = eng.q("SELECT * FROM flights WHERE id=?", (fid,), one=True)
        self.assertIsInstance(r, dict)
        self.assertEqual((r.get("airline"), r.get("snapshot")), ("XY", "{}"))
        self.assertTrue(all(isinstance(x, dict) for x in eng.q("SELECT * FROM flights")))
        self.assertIsNone(eng.q("SELECT * FROM flights WHERE id=-1", one=True))


# ---------------------------------------------------------------- reading
class Flights(WithEngine):
    """Wallet passes and AviationStack checks, with a fake AviationStack (lookup) and the key in a test file."""
    LONDON = eng.flights.ZoneInfo("Europe/London")

    def setUp(self):
        super().setUp()
        self.saved.update({k: getattr(eng, k) for k in ("NO_FLIGHTS", "AVIATIONSTACK_KEY_FILE")})
        eng.NO_FLIGHTS, eng.AVIATIONSTACK_KEY_FILE = False, os.path.join(eng.DATA_DIR, "flights.env")
        eng._fr24_attempts.clear()
        # a pass comes at most 3 days before; the London date, as at() is London time (late in its evening, a host
        # far west of UTC is still on the day before, and at(20) would already be past)
        self.day = datetime.datetime.now(self.LONDON).date() + datetime.timedelta(days=2)
        self.julian = self.day.timetuple().tm_yday
        self.answers, self.requests = [], []

    def write_key(self):
        with open(eng.AVIATIONSTACK_KEY_FILE, "w") as f:
            f.write("AVIATIONSTACK_KEY=fake-flights-key\n")

    def boarding_pass(self, **k):
        return tf.pkpass(tf.bcbp(day=self.julian, **k))

    def lookup(self, key, airline, number, date, origin, now):
        self.requests.append((key, airline, number, date, origin))
        r = self.answers.pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    def api(self, status="scheduled", **fields):
        """The AviationStack flight on the pass's day: it departs at 05:30 and arrives at 07:50. «HH:MM» times (here
        and in the comments) are London times, UTC in winter, and become the airport's local time with the fake
        «+00:00», as AviationStack sends them. Rome and London change the clocks together: the expected Italian
        times are the same in every season."""
        def local(hhmm, tz):
            t = datetime.datetime.combine(self.day, datetime.time.fromisoformat(hhmm), self.LONDON)
            return t.astimezone(eng.flights.ZoneInfo(tz)).strftime("%Y-%m-%dT%H:%M:00+00:00")
        times = {"dep_scheduled": "05:30", "dep_estimated": "05:30", "arr_scheduled": "07:50", **fields}
        times = {k: local(x, "Europe/Rome" if k.startswith("dep") else "Europe/London")
                 if isinstance(x, str) and re.fullmatch(r"\d\d:\d\d", x) else x for k, x in times.items()}
        return eng.flights.normalize(tf.api_flight(status, date=self.day.isoformat(), **times))

    def at(self, hours_before):
        departure = datetime.datetime.combine(self.day, datetime.time(5, 30), self.LONDON)
        return departure.astimezone(datetime.timezone.utc) - datetime.timedelta(hours=hours_before)

    def test_wallet_boarding_pass(self):
        d = self.upload_file(self.boarding_pass(), "pkpass")
        self.assertEqual((d["status"], d["kind"], d["merchant"], d["doc_date"]),
                         ("letto", "carta_imbarco", "FR 1234", self.day.isoformat()))
        self.assertEqual(self.read, [])                                      # a pass: no barcode reader
        (v,) = self.flights()
        self.assertEqual((v["airline"], v["number"], v["origin"], v["destination"], v["date"]),
                         ("FR", "1234", "BLQ", "STN", self.day.isoformat()))
        (c,) = self.passes()
        self.assertEqual((c["passenger"], c["first_name"], c["seat"], c["uploaded_by"], c["document_id"]),
                         ("Mario Rossi", "Mario", "12A", "alice", d["id"]))
        with eng._db_lock:
            everything = "\n".join(eng.db().iterdump())
        self.assertNotIn("XYZ789", everything)                               # the PNR is not saved
        self.assertNotIn("segreto-del-pass", everything)                     # the pass's authenticationToken
        eng.send_pending_notifications(CFG)
        (n,) = self.notifications()
        self.assertEqual((n["to"], n["title"], n["tag"], n["actions"]),
                         ("mobile_app_alice", "Carta d'imbarco letta", f"{P}-d{d['id']}", None))
        self.assertIn(f"FR 1234 BLQ → STN, {self.day:%d/%m}, posto 12A (Mario).", n["message"])
        self.assertIn("manca la chiave di AviationStack", n["message"])

    def test_photo_of_the_boarding_pass_read_from_the_barcode(self):
        d = self.upload_photo(["https://example.invalid", tf.bcbp(day=self.julian, seat="014C")])
        self.assertEqual((d["status"], d["kind"], d["file_type"], d["merchant"]), ("letto", "carta_imbarco", "png", "FR 1234"))
        self.assertEqual(self.read, [(os.path.basename(d["file"]), eng.MAX_PIXELS)])   # the pixel limit is passed on
        self.assertEqual(eng.q("SELECT seat FROM boarding_passes", one=True)["seat"], "14C")

    def test_two_passengers_and_a_reloaded_pass(self):
        self.write_key()
        self.upload_file(self.boarding_pass(), "pkpass")
        self.upload_file(self.boarding_pass(name="ROSSI/ANNA MRS", seat="012B", seq="0043"), "pkpass")
        d = self.upload_file(self.boarding_pass(seat="014C"), "pkpass")      # seat changed: a new pass
        self.assertEqual(len(self.flights()), 1)
        self.assertEqual([(c["first_name"], c["seat"]) for c in self.passes()], [("Mario", "14C"), ("Anna", "12B")])
        self.assertTrue(d["outcome"].startswith("Carta aggiornata: "))
        self.assertIn("Ti avviso se cambiano orario o gate.", d["outcome"])

    def test_a_pass_that_is_not_a_boarding_pass(self):
        d = self.upload_file(tf.pkpass(tf.bcbp(), kind="eventTicket"), "pkpass")
        self.assertEqual((d["status"], d["kind"]), ("archiviato", "documento"))
        self.assertEqual(self.flights(), [])
        eng.send_pending_notifications(CFG)
        self.assertEqual([(n["title"], n["tag"]) for n in self.notifications()], [("Documento archiviato", f"{P}-d1")])

    def test_checks_and_notifications(self):
        self.upload_file(self.boarding_pass(), "pkpass")
        self.assertEqual(eng.check_flights(CFG, self.at(20), lookup=self.lookup), 0)     # without a key: nothing
        self.write_key()
        # the first check, just after the pass: the gate is discovered (no notification), the delay is announced
        self.answers = [self.api(dep_gate="A12", dep_delay=25)]
        self.assertEqual(eng.check_flights(CFG, self.at(20), lookup=self.lookup), 1)
        self.assertEqual(self.requests, [("fake-flights-key", "FR", "1234", self.day.isoformat(), "BLQ")])
        self.assertEqual([(n["to"], n["title"], n["tag"]) for n in self.notifications()],
                         [("mobile_app_alice", "FR 1234: in ritardo di 25 min", f"{P}-f1"),
                          ("mobile_app_bob", "FR 1234: in ritardo di 25 min", f"{P}-f1")])
        self.assertNotIn("Gate", self.notifications()[0]["message"])
        v = eng.q("SELECT * FROM flights", one=True)
        one_hour_before = self.at(1) + datetime.timedelta(minutes=25)                   # departs at 05:55
        self.assertEqual(v["next_check"], eng.utc(one_hour_before))
        # before then no call; then, the same data: no new notification
        self.assertEqual(eng.check_flights(CFG, self.at(7), lookup=self.lookup), 0)
        self.answers = [self.api(dep_gate="A12", dep_delay=25)]
        self.assertEqual(eng.check_flights(CFG, one_hour_before, lookup=self.lookup), 1)
        self.assertEqual(len(self.notifications()), 2)
        # delayed and inside the window: in 30 minutes; there the gate changes
        half_hour = one_hour_before + datetime.timedelta(minutes=30)
        self.assertEqual(eng.q("SELECT next_check FROM flights", one=True)["next_check"], eng.utc(half_hour))
        self.answers = [self.api(dep_gate="A14", dep_terminal="1", dep_delay=25)]
        eng.check_flights(CFG, half_hour, lookup=self.lookup)
        self.assertEqual([(n["title"], n["message"]) for n in self.notifications()[2:]],
                         [("FR 1234: cambia il gate", "Gate A14 (prima A12), terminal 1. Partenza alle 06:55.")] * 2)
        self.assertEqual(eng.q("SELECT sum(calls) AS n FROM api_usage", one=True)["n"], 3)   # even across two months
        self.assertNotIn("fake-flights-key", self.output.getvalue())

    def test_go_to_the_gate(self):
        self.write_key()
        self.upload_file(self.boarding_pass(), "pkpass")
        self.answers = [self.api(dep_gate="A12", dep_terminal="1")]
        eng.check_flights(CFG, self.at(1), lookup=self.lookup)
        self.assertEqual(self.notifications(), [])                               # the discovered gate is not announced
        # economy: boarding 05:00, at the gate 04:50, leave at 04:35
        self.assertEqual(eng.notify_gate(CFG, self.at(1) + datetime.timedelta(minutes=4)), 0)
        self.assertEqual(eng.notify_gate(CFG, self.at(1) + datetime.timedelta(minutes=5)), 1)
        self.assertEqual([(n["to"], n["title"], n["message"], n["tag"]) for n in self.notifications()],
                         [("mobile_app_alice", "FR 1234: avviati al gate A12",       # only to who uploaded the pass
                           "Al gate per le 05:50 (imbarco alle 06:00 circa), partenza alle 06:30 per Londra Stansted. "
                           "Terminal 1.", f"{P}-f1")])
        self.assertEqual(eng.notify_gate(CFG, self.at(0.5)), 0)                  # only once
        state, card = eng.compute_sensors(now=self.at(0.5))["next_flight"]
        self.assertEqual((card["boarding_time"], card["boarding_estimated"], card["at_gate_time"],
                          card["leave_for_gate_time"], card["priority"]), ("06:00", True, "05:50", "05:35", False))

    def test_go_to_the_gate_with_gps(self):
        self.write_key()
        self.upload_file(self.boarding_pass(), "pkpass")                     # economy, BLQ, gate A12
        self.answers = [self.api(dep_gate="A12")]
        eng.check_flights(CFG, self.at(1), lookup=self.lookup)
        looked_up = []
        saved = eng.flights.find_gate
        eng.flights.find_gate = lambda lat, lon, gate, **k: looked_up.append((lat, lon, gate)) or (44.5370, 11.2850)
        self.addCleanup(setattr, eng.flights, "find_gate", saved)
        blq = eng.flights.coordinates("BLQ")
        self.ha.states["person.alice"] = {"state": "not_home", "attributes": {
            "latitude": blq[0], "longitude": blq[1], "gps_accuracy": 15}}
        # at the gate at 04:50; 343 m as the crow flies, 480 of corridors: 9 minutes, leave at 04:41 (not 04:35)
        self.assertEqual(eng.notify_gate(CFG, self.at(1) + datetime.timedelta(minutes=5)), 0)
        self.assertEqual(looked_up, [(blq[0], blq[1], "A12")])                # only the airport, never the person
        self.assertEqual(eng.notify_gate(CFG, self.at(0.75)), 1)
        (n,) = self.notifications()
        self.assertIn("minuti a piedi", n["message"])
        self.assertEqual(len(looked_up), 1)                                   # the gate position is cached
        card = eng.compute_sensors(now=self.at(0.7))["next_flight"][1]
        self.assertEqual(card["leave_for_gate_time"], "05:41")

    def test_overpass_down_the_fixed_margin_applies(self):
        self.write_key()
        self.upload_file(self.boarding_pass(), "pkpass")
        self.answers = [self.api(dep_gate="A12")]
        eng.check_flights(CFG, self.at(1), lookup=self.lookup)
        saved = eng.flights.find_gate

        def down(*a, **k):
            raise eng.flights.APIError("Overpass 504", 504)
        eng.flights.find_gate = down
        self.addCleanup(setattr, eng.flights, "find_gate", saved)
        blq = eng.flights.coordinates("BLQ")
        self.ha.states["person.alice"] = {"state": "x", "attributes": {"latitude": blq[0], "longitude": blq[1]}}
        self.assertEqual(eng.notify_gate(CFG, self.at(1) + datetime.timedelta(minutes=4)), 0)
        self.assertEqual(eng.notify_gate(CFG, self.at(1) + datetime.timedelta(minutes=5)), 1)   # 04:35 as before
        self.assertEqual(eng.q("SELECT error FROM gate_positions", one=True)["error"], "Overpass 504")

    def test_business_goes_to_the_gate_later(self):
        self.write_key()
        self.upload_file(self.boarding_pass(cabin="C"), "pkpass")
        self.assertEqual(eng.q("SELECT cabin FROM boarding_passes", one=True)["cabin"], "C")
        self.answers = [self.api(dep_gate="A12")]
        eng.check_flights(CFG, self.at(1), lookup=self.lookup)
        self.assertEqual(eng.notify_gate(CFG, self.at(0.75) - datetime.timedelta(minutes=1)), 0)
        self.assertEqual(eng.notify_gate(CFG, self.at(0.75)), 1)               # at the gate at 05:00, boarding

    def test_checks_computed_again_at_startup(self):
        self.write_key()
        self.upload_file(self.boarding_pass(), "pkpass")
        self.answers = [self.api()]
        eng.check_flights(CFG, self.at(20), lookup=self.lookup)
        self.upload_file(self.boarding_pass(airline="AZ", number="2041"), "pkpass")    # never checked: stays as it is
        before = {v["number"]: v["next_check"] for v in self.flights()}
        eng.update_flight(1, next_check="2000-01-01T00:00:00Z")                 # set with old rules
        eng.reschedule_checks(self.at(20))
        after = {v["number"]: v["next_check"] for v in self.flights()}
        self.assertEqual(after, {"1234": eng.utc(self.at(1)), "2041": before["2041"]})

    def test_flightradar24_tracks_the_flight_and_drops_it(self):
        self.write_key()
        self.upload_file(self.boarding_pass(), "pkpass")
        self.answers = [self.api()]
        eng.check_flights(CFG, self.at(20), lookup=self.lookup)                # known schedule: departs at 05:30
        self.assertEqual(eng.track_fr24(self.at(4)), (0, 0))                   # without the integration: nothing
        self.ha.states[eng.FR24_TRACKED] = {"state": "0", "attributes": {"flights": []}}
        self.assertEqual(eng.track_fr24(self.at(4)), (0, 0))                   # too early
        self.assertEqual(eng.track_fr24(self.at(3)), (1, 0))
        self.assertEqual(self.ha.services, [("text.set_value", {"entity_id": eng.FR24_ADD, "value": "FR1234"})])
        self.assertEqual(eng.track_fr24(self.at(2.9)), (0, 0))                 # FR24 did not find it: in 30 minutes
        self.assertEqual(eng.track_fr24(self.at(2.5)), (1, 0))
        self.ha.states[eng.FR24_TRACKED]["attributes"]["flights"] = [{"flight_number": "FR1234",
                                                                      "tracked_type": "schedule"}]
        self.assertEqual(eng.track_fr24(self.at(1)), (0, 0))                   # already tracked (also after a restart)
        self.answers = [self.api("landed", dep_actual_runway="05:48", arr_actual_runway="07:41", arr_actual="07:47")]
        eng.check_flights(CFG, self.at(-3), lookup=self.lookup)
        self.assertEqual(eng.track_fr24(self.at(-4)), (0, 1))                  # over: removed
        self.assertEqual(self.ha.services[-1], ("text.set_value", {"entity_id": eng.FR24_REMOVE, "value": "FR1234"}))

    def test_ha_down_the_notification_is_retried(self):
        self.write_key()
        self.upload_file(self.boarding_pass(), "pkpass")
        self.answers = [self.api(dep_gate="B3"), self.api(dep_gate="C1"), self.api(dep_gate="C1")]
        eng.check_flights(CFG, self.at(5), lookup=self.lookup)               # B3 discovered
        self.ha.down = True
        eng.check_flights(CFG, self.at(1), lookup=self.lookup)               # C1: the notification is not sent
        self.ha.down = False
        eng.check_flights(CFG, self.at(0.5), lookup=self.lookup)
        self.assertEqual([n["message"] for n in self.notifications()], ["Gate C1 (prima B3). Partenza alle 06:30."] * 2)

    def test_errors_cap_and_flight_over(self):
        self.write_key()
        self.upload_file(self.boarding_pass(), "pkpass")
        self.answers = [eng.flights.APIError("AviationStack 401: invalid_access_key", 401)]
        eng.check_flights(CFG, self.at(10), lookup=self.lookup)
        v = eng.q("SELECT * FROM flights", one=True)
        self.assertEqual((v["error"], v["next_check"]),
                         ("AviationStack 401: invalid_access_key", eng.utc(self.at(4))))
        saved_cap = eng.MAX_API_CALLS_PER_MONTH
        eng.MAX_API_CALLS_PER_MONTH = 1
        with eng._db_lock:                          # the counter is per month: 1 already used in the month of at(4)
            eng.db().execute("INSERT OR REPLACE INTO api_usage (month, calls) VALUES (?, 1)", (self.at(4).strftime("%Y-%m"),))
        try:
            self.assertEqual(eng.check_flights(CFG, self.at(4), lookup=self.lookup), 0)   # cap reached
        finally:
            eng.MAX_API_CALLS_PER_MONTH = saved_cap
        self.assertIn("tetto di 1 chiamate", eng.q("SELECT error FROM flights", one=True)["error"])
        landed = self.api("landed", dep_actual="05:35", dep_actual_runway="05:48", arr_actual_runway="07:41",
                          arr_actual="07:47")
        self.answers = [landed]
        eng.check_flights(CFG, self.at(-6), lookup=self.lookup)                       # 6 hours after departure
        self.assertIsNone(eng.q("SELECT next_check FROM flights", one=True)["next_check"])
        self.assertEqual([n["title"] for n in self.notifications()], [])              # landing: no notification

    def test_a_zero_cap_makes_no_call(self):
        self.write_key()
        self.upload_file(self.boarding_pass(), "pkpass")
        self.saved["MAX_API_CALLS_PER_MONTH"] = eng.MAX_API_CALLS_PER_MONTH
        eng.MAX_API_CALLS_PER_MONTH = 0
        self.assertEqual(eng.check_flights(CFG, self.at(20), lookup=self.lookup), 0)     # the counter is still 0
        self.assertEqual(self.requests, [])
        self.assertIn("tetto di 0 chiamate", eng.q("SELECT error FROM flights", one=True)["error"])

    def test_flight_sensors(self):
        self.write_key()
        self.upload_file(self.boarding_pass(), "pkpass")
        self.answers = [self.api(dep_gate="A12", dep_delay=25)]
        eng.check_flights(CFG, self.at(5), lookup=self.lookup)
        v = eng.compute_sensors(now=self.at(5))
        state, card = v["next_flight"]
        self.assertEqual(state, "In ritardo di 25 min")
        self.assertEqual({k: card[k] for k in ("flight", "route", "departure_time", "departure_scheduled_time", "gate",
                                               "passengers", "delay_min", "boarding_time", "at_gate_time", "source")},
                         {"flight": "FR 1234", "route": "Bologna (BLQ) → Londra Stansted (STN)",
                          "departure_time": "06:55", "departure_scheduled_time": "06:30", "gate": "A12",
                          "passengers": ["Mario (12A)"], "delay_min": 25, "boarding_time": "06:25",
                          "at_gate_time": "06:15", "source": "aviationstack"})
        self.assertEqual({k: card[k] for k in ("origin", "destination", "origin_city", "destination_city", "phase",
                                               "flight_iata", "status")},
                         {"origin": "BLQ", "destination": "STN", "origin_city": "Bologna",
                          "destination_city": "Londra Stansted", "phase": "scheduled", "flight_iata": "FR1234",
                          "status": "In ritardo di 25 min"})
        self.assertEqual(len(card), 30)
        self.assertEqual(v["upcoming_flights"], (1, {"list": [
            {"flight": "FR 1234", "route": "Bologna (BLQ) → Londra Stansted (STN)", "date": self.day.isoformat(),
             "departure_time": "06:55", "status": "In ritardo di 25 min", "gate": "A12", "passengers": ["Mario (12A)"]}]}))
        self.assertEqual((v["flight_statistics"][0], v["flight_statistics"][1]["upcoming"]), (0, 1))
        self.assertEqual(v["flight_statistics"][1]["list"][0]["flight"], "FR 1234")
        everything = json.dumps(v)
        for secret in ("Rossi", "XYZ789", "segreto-del-pass"):
            self.assertNotIn(secret, everything)
        self.assertEqual(eng.compute_sensors(now=self.at(-30))["next_flight"], ("Nessun volo", {}))


class Sensors(WithEngine):
    NOW = datetime.datetime(2026, 10, 28, 8, 0, tzinfo=datetime.timezone.utc)

    def test_no_data(self):
        v = eng.compute_sensors(self.NOW.date(), self.NOW)
        self.assertEqual({k: s for k, (s, _) in v.items()},
                         {"next_flight": "Nessun volo", "upcoming_flights": 0, "flight_statistics": 0})
        self.assertEqual((v["next_flight"][1], v["upcoming_flights"][1]), ({}, {"list": []}))
        eng.publish_sensors()
        self.assertEqual(set(self.sensors.values), set(eng.ha.SENSORS))
        self.assertEqual(set(eng.ha.SENSORS), {"next_flight", "upcoming_flights", "flight_statistics"})

    def test_values(self):
        # twelve flights from the pass only, without a status: the nearest first, at most MAX_LIST in the list
        doc = self.new_doc()
        for i in range(12):
            fid = self.new_flight(number=str(100 + i), date=f"2026-11-{10 + i:02d}", pass_gate="B2" if i == 0 else None)
            with eng._db_lock:
                eng.db().execute("INSERT INTO boarding_passes (document_id, flight_id, passenger, first_name, seat, "
                                 "cabin, uploaded_by) VALUES (?, ?, 'Ada Prova', 'Ada\u202e', ?, 'Y', 'alice')",
                                 (doc, fid, None if i else "3C"))
        v = eng.compute_sensors(self.NOW.date(), self.NOW)
        state, card = v["next_flight"]
        self.assertEqual((state, card["flight"], card["source"], card["phase"], card["gate"], card["passengers"]),
                         ("Dalla carta d'imbarco", "XY 100", "boarding_pass", None, "B2", ["Ada (3C)"]))
        self.assertEqual(v["upcoming_flights"][0], 12)
        self.assertEqual([x["flight"] for x in v["upcoming_flights"][1]["list"]], [f"XY {100 + i}" for i in range(10)])
        self.assertEqual((v["flight_statistics"][0], v["flight_statistics"][1]["upcoming"]), (0, 12))
        self.assertNotIn("Prova", json.dumps(v))                              # no surnames in HA


class SentAgain(WithEngine):
    def test_a_document_in_error_can_be_sent_again(self):
        data = tf.pkpass(tf.bcbp())
        doc_id, _, _ = eng.save_upload(data, "pkpass", USER)
        eng.update_document(doc_id, status="errore", attempts=3, error="BadZipFile: File is not a zip file", notified=1)
        self.assertEqual(eng.notification_text(self.doc(doc_id))[0], "Lettura non riuscita")
        answer = eng.save_upload(data, "pkpass", USER)
        self.assertEqual(answer[:2], (doc_id, "in_coda"))
        self.assertIn("lo rileggo", answer[2])
        d = self.doc(doc_id)
        self.assertEqual((d["status"], d["attempts"], d["error"], d["notified"], d["outcome"]),
                         ("in_coda", 0, None, 0, None))
        eng.update_document(doc_id, status="letto")
        self.assertIn("gia' mandato", eng.save_upload(data, "pkpass", USER)[2])        # read: still a duplicate


class Worker(WithEngine):
    def test_a_pass_produces_its_notification_through_work_one(self):
        # the notification reads the tracker's own columns only: no KeyError in the round before every reading
        doc_id, _, _ = eng.save_upload(tf.pkpass(tf.bcbp()), "pkpass", USER)
        self.assertEqual(eng.work_one(CFG), 0)                                      # read
        self.assertIsNone(eng.work_one(CFG))                                        # notified, the queue is empty
        self.assertEqual([(n["to"], n["title"], n["tag"]) for n in self.notifications()],
                         [("mobile_app_alice", "Carta d'imbarco letta", f"{P}-d{doc_id}")])
        self.assertEqual(self.doc(doc_id)["notified"], 1)
        self.assertEqual(set(self.sensors.values), set(eng.ha.SENSORS))            # published after the reading

    def test_a_document_that_crashes_the_engine_does_not_stay_queued(self):
        doc_id, _, _ = eng.save_upload(tf.pkpass(tf.bcbp()), "pkpass", USER)
        seen, real = [], eng.read_document

        def dies(doc):
            seen.append(self.doc(doc["id"])["attempts"])             # the attempt is already counted
            raise KeyboardInterrupt                                  # OOM, restart: no except sees it
        eng.read_document = dies
        try:
            for _ in range(eng.MAX_ATTEMPTS):
                with self.assertRaises(KeyboardInterrupt):
                    eng.work_one(CFG)
            self.assertEqual(eng.work_one(CFG), 0)                  # at the restart after the third: error, not read
        finally:
            eng.read_document = real
        self.assertEqual((seen, self.doc(doc_id)["status"]), ([1, 2, 3], "errore"))
        self.assertIsNone(eng.work_one(CFG))
        self.assertEqual((self.notifications()[-1]["title"], self.notifications()[-1]["tag"]),
                         ("Lettura non riuscita", f"{P}-d{doc_id}"))


class Http(WithEngine):
    def setUp(self):
        super().setUp()
        self.serve()

    def test_health(self):
        self.new_doc(status="in_coda")
        self.assertEqual(self.call("GET", "/health", user=None)[:2],
                         (200, {"ok": True, "queued": 1, "decoder": "off"}))       # BC10: no websocket, no todo

    def test_paths_of_household_ledger_and_the_italian_ones_are_unknown(self):
        for method, path in (("GET", "/payments"), ("GET", "/salute"), ("GET", "/documenti"), ("GET", "/voli"),
                             ("GET", "/scadenze"), ("POST", "/carica"), ("POST", "/upload?x=1"), ("GET", "/")):
            with self.subTest(path=path):
                self.assertEqual(self.call(method, path, tiny_png() if method == "POST" else None)[:2],
                                 (404, {"message": "percorso sconosciuto"}))

    def test_upload(self):
        data = tf.pkpass(tf.bcbp())
        self.assertEqual(self.call("POST", "/upload", data)[:2],
                         (200, {"id": 1, "status": "queued", "message": "Ricevuto: lo leggo e ti mando una notifica."}))
        code, body, _, _ = self.call("POST", "/upload", data)
        self.assertEqual((code, body["status"]), (200, "queued"))
        self.assertTrue(body["message"].startswith("Questo l'avevi gia' mandato il "))
        eng.update_document(1, status="letto")
        self.assertEqual(self.call("POST", "/upload", data)[1]["status"], "read")
        self.assertEqual(self.call("POST", "/upload", b"\x00\x00\x00\x18ftypheic" + b"0" * 8)[:2],
                         (200, {"id": 2, "status": "queued", "message": "Ricevuto: lo leggo e ti mando una notifica."}))
        self.assertEqual(self.call("POST", "/upload", b"")[:2], (400, {"message": "File vuoto o oltre 25 MB."}))
        self.assertEqual(self.call("POST", "/upload", data, user=None)[:2],
                         (403, {"message": "Non so chi sei: la Scorciatoia deve passare dal tailnet."}))
        self.assertIn("POST /upload 200 Ricevuto", self.output.getvalue())

    def test_documents(self):
        self.new_doc(merchant="XY 101", doc_date="2026-10-28")
        self.new_doc(status="archiviato", kind="documento", file_type="png", outcome=NOT_FOUND)
        self.new_doc(status="in_coda", kind=None)
        self.assertEqual(self.call("GET", "/documents")[1]["documents"],
                         [{"id": 3, "uploaded_by": "alice", "uploaded_at": self.doc(3)["uploaded_at"], "status": "queued",
                           "kind": None, "merchant": None, "doc_date": None},
                          {"id": 2, "uploaded_by": "alice", "uploaded_at": self.doc(2)["uploaded_at"],
                           "status": "archived", "kind": "document", "merchant": None, "doc_date": None},
                          {"id": 1, "uploaded_by": "alice", "uploaded_at": self.doc(1)["uploaded_at"], "status": "read",
                           "kind": "boarding_pass", "merchant": "XY 101", "doc_date": "2026-10-28"}])


class WithoutModel(WithEngine):
    """No model, no expenses: what is not a boarding pass is refused at the door or archived unread."""

    def test_no_model_and_no_expense_code(self):
        for name in ("call_model", "transcribe", "image_b64", "Busy", "MODEL", "LLM_URL", "listen_ha", "ws_session",
                     "connect_ws", "ha_status", "sync_ha", "send_reminders", "compute_payments", "ACTION_PREFIX",
                     "CALENDAR", "TODO_LIST", "websocket", "pagopa", "receipts"):
            self.assertFalse(hasattr(eng, name), name)
        self.assertFalse({"llm", "model"} & eng.SETTINGS_KEYS)
        self.assertFalse({"pagopa", "fatturapa", "bank", "categories", "receipts", "websocket"} & set(vars(eng)))
        self.assertFalse({"pagopa", "fatturapa", "bank", "categories", "receipts"} & set(sys.modules))
        self.assertFalse(os.path.exists(os.path.join(HERE, "prompts")))
        columns = {r["name"] for r in eng.q("PRAGMA table_info(documents)")}
        self.assertFalse(columns & {"transcript", "model", "node", "queue_notified", "category", "total_cents"})

    def test_an_invoice_or_a_bank_statement_is_refused(self):
        self.serve()
        for data in (INVOICE_XML, BANK_CSV, b"just text"):
            with self.subTest(data=data[:12]):
                self.assertEqual(self.call("POST", "/upload", data)[:2], (415, {"message": NOT_A_PASS}))   # BC5
        self.assertEqual(eng.q("SELECT count(*) AS n FROM documents", one=True)["n"], 0)
        self.assertFalse(os.path.exists(os.path.join(eng.DATA_DIR, "file")))      # nothing stored

    def test_a_photo_or_a_pdf_without_a_boarding_pass_is_archived(self):
        def no_network(*a, **k):
            raise AssertionError("no network call")
        with unittest.mock.patch.object(urllib.request, "urlopen", no_network):
            d = self.upload_photo(["https://example.invalid", "PAGOPA|002|301000001234567864|80012345676|18600"])
            pdf = self.upload_file(b"%PDF-1.4\n%%EOF\n", "pdf")
        for x in (d, pdf):
            self.assertEqual((x["status"], x["kind"], x["outcome"], x["error"], x["notified"], x["merchant"]),
                             ("archiviato", "documento", NOT_FOUND, None, 0, None))                         # BC4
        eng.send_pending_notifications(CFG)
        self.assertEqual([(n["title"], n["message"], n["tag"]) for n in self.notifications()],
                         [("Documento archiviato", NOT_FOUND, f"{P}-d1"), ("Documento archiviato", NOT_FOUND, f"{P}-d2")])
        self.assertEqual(self.flights(), [])

    def test_a_huge_photo_ends_in_error_without_decoding(self):
        eng.barcodes.read_barcodes = self.saved_reader                          # the real reader
        decoded = []
        with unittest.mock.patch.object(eng.barcodes, "_decode", lambda *a: decoded.append(a) or []):
            doc_id, _, _ = eng.save_upload(huge_png(), "png", USER)
            self.assertEqual([eng.work_one(CFG) for _ in range(eng.MAX_ATTEMPTS)], [30, 30, 5])
        self.assertEqual(decoded, [])
        text = "ValueError: immagine di 9000x9000 pixel: troppo grande"                                   # BC6
        self.assertEqual((self.doc(doc_id)["status"], self.doc(doc_id)["error"]), ("errore", text))
        self.assertIsNone(eng.work_one(CFG))
        self.assertEqual([(n["title"], n["message"]) for n in self.notifications()],
                         [("Lettura non riuscita", f"Documento #{doc_id}: {text}. Riprova o mandalo di nuovo.")])


class NotificationTags(WithEngine):
    def test_every_tag_and_the_group_follow_the_prefix(self):
        eng.NO_FLIGHTS, self.saved["NO_FLIGHTS"] = False, eng.NO_FLIGHTS
        self.upload_file(tf.pkpass(tf.bcbp(seat="012A")), "pkpass")
        self.upload_photo([])
        eng.send_pending_notifications(CFG)
        fid = self.flights()[0]["id"]
        eng.update_flight(fid, next_check="2000-01-01T00:00:00Z")
        flight = tf.api_flight("cancelled", date=self.flights()[0]["date"])
        key = os.path.join(eng.DATA_DIR, "flights.env")
        with open(key, "w") as f:
            f.write("AVIATIONSTACK_KEY=fake-flights-key\n")
        self.saved["AVIATIONSTACK_KEY_FILE"], eng.AVIATIONSTACK_KEY_FILE = eng.AVIATIONSTACK_KEY_FILE, key
        eng.check_flights(CFG, datetime.datetime.combine(datetime.date.fromisoformat(self.flights()[0]["date"]),
                                                         datetime.time(3), datetime.timezone.utc),
                          lookup=lambda *a: eng.flights.normalize(flight))
        tags = [n["tag"] for n in self.notifications()]
        self.assertEqual(sorted(tags[:2]), [f"{P}-d1", f"{P}-d2"])
        self.assertEqual(set(tags[2:]), {f"{P}-f{fid}"})
        self.assertTrue(all(re.fullmatch(rf"{P}-[df]\d+", t) for t in tags), tags)


if __name__ == "__main__":
    unittest.main()
