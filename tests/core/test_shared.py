"""Tests of the code that household-ledger and boarding-pass-tracker share word for word: the entity id check, the MQTT
identity, the notification group and tags, the application_id guard of open_db, the HTTP reply log. No network, no real
HA or broker: fakes injected in place of them.

The two repositories' copies of this file differ only in ENV_PREFIX.

  cd <package dir> && python3 -I -B -m unittest discover -s . -p test_shared.py -v
"""
import atexit, contextlib, io, json, os, shutil, sqlite3, sys, tempfile, unittest, unittest.mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import support  # noqa: E402,F401  (the ported core on sys.path; its log lines to the current stdout)
ENV_PREFIX = "TRACKER_"               # the only line that differs between the two copies
import ha  # noqa: E402

P = "prova"                           # a test prefix: never the default one
APPLICATION_IDS = {"household-ledger": 0x484C4447, "boarding-pass-tracker": 0x42505454}
FOOTER = ("Le card e le dashboard usano gli id predefiniti: rinomina o elimina l'altra entità e poi rinomina il "
          "sensore di questo servizio, oppure cambia mqtt.prefix nella configurazione.")
CFG = {"users": {"alice@example.com": {"name": "alice", "notify": "mobile_app_alice"}}}
_ENGINE = []


def engine():
    """The engine module: the one another test file of this process already imported, otherwise imported here with
    the dry-run environment (set only in that case)."""
    if not _ENGINE:
        if "engine" not in sys.modules:
            tmp = tempfile.mkdtemp(prefix="shared-test-")
            atexit.register(shutil.rmtree, tmp, True)
            for k, v in {"DATA_DIR": tmp, "CONFIG": os.path.join(tmp, "none.json"), "NO_NOTIFY": "1", "NO_HA": "1",
                         "NO_MQTT": "1", "MQTT_PREFIX": "sharedtest"}.items():
                os.environ.setdefault(ENV_PREFIX + k, v)
        import engine as eng
        _ENGINE.append(eng)
    return _ENGINE[0]


class Quiet(unittest.TestCase):
    """The module logs go to self.output."""

    def setUp(self):
        self.output = io.StringIO()
        r = contextlib.redirect_stdout(self.output)
        r.__enter__()
        self.addCleanup(r.__exit__, None, None, None)

    def lines(self):
        return [line.split(" ", 2)[2] for line in self.output.getvalue().splitlines()]   # without the timestamp


# ---------------------------------------------------------------- ha.py
class Identity(unittest.TestCase):
    def test_constants(self):
        for name in ("PROJECT", "PRODUCT", "VERSION", "DEFAULT_PREFIX"):
            self.assertTrue(isinstance(getattr(ha, name), str) and getattr(ha, name), name)
        self.assertIn(ha.PROJECT, APPLICATION_IDS)
        self.assertRegex(ha.DEFAULT_PREFIX, r"[a-z0-9_]+")
        self.assertEqual(ha.DEFAULT_PREFIX, ha.PROJECT.replace("-", "_"))
        self.assertRegex(ha.VERSION, r"\d+\.\d+\.\d+")


class Group(Quiet):
    def notify(self, h, **kw):
        calls = []
        h.service = lambda domain, name, data, response=False: calls.append((domain, name, data))
        self.assertTrue(h.notify("mobile_app_alice", "Titolo", "Testo", **kw))
        self.assertEqual(len(calls), 1)
        return calls[0]

    def test_default_group_is_the_default_prefix(self):
        h = ha.HA("http://192.0.2.1:8123", lambda: "token")
        self.assertEqual(h.group, ha.DEFAULT_PREFIX)
        self.assertEqual(self.notify(h)[2]["data"], {"group": ha.DEFAULT_PREFIX})

    def test_group_tag_and_actions(self):
        h = ha.HA("http://192.0.2.1:8123", lambda: "token", group=P)
        actions = [{"action": "PROVA_OK_1", "title": "Giusto"}]
        domain, name, data = self.notify(h, actions=actions, tag=f"{P}-d1")
        self.assertEqual((domain, name), ("notify", "mobile_app_alice"))
        self.assertEqual(data, {"title": "Titolo", "message": "Testo",
                                "data": {"group": P, "tag": f"{P}-d1", "actions": actions}})

    def test_failure_returns_false(self):
        h = ha.HA("http://192.0.2.1:8123", lambda: "token", group=P)

        def down(*a, **k):
            raise ha.HAError("HA unreachable on /api/services/notify/mobile_app_alice (OSError: down)")
        h.service = down
        self.assertFalse(h.notify("mobile_app_alice", "Titolo", "Testo"))
        self.assertIn("notification not sent (HAError", self.output.getvalue())


# ---------------------------------------------------------------- engine.py
class EngineCase(Quiet):
    """A new folder for every test; the engine globals a test changes come back afterwards."""
    SAVED = ("DATA_DIR", "DATABASE", "_db", "_ha", "_sensors", "NO_HA", "NO_NOTIFY", "MQTT_PREFIX")

    def setUp(self):
        super().setUp()
        self.eng = eng = engine()
        self.saved = {k: getattr(eng, k) for k in self.SAVED}
        self.addCleanup(lambda: [setattr(eng, k, v) for k, v in self.saved.items()])
        self.dir = tempfile.mkdtemp(prefix="shared-")
        self.addCleanup(shutil.rmtree, self.dir, True)
        eng.DATA_DIR, eng.DATABASE, eng._db, eng._ha, eng._sensors = self.dir, None, None, None, None
        eng.NO_HA, eng.NO_NOTIFY, eng.MQTT_PREFIX = True, True, P


class Tags(EngineCase):
    def test_tags(self):
        for kind in ("d", "s", "p", "f"):
            self.assertEqual(self.eng.tag(kind, 7), f"{P}-{kind}7")
        self.assertEqual(self.eng.tag("f", "12"), f"{P}-f12")
        self.eng.MQTT_PREFIX = "prova_two"
        self.assertEqual(self.eng.tag("d", 1), "prova_two-d1")


class ApplicationId(EngineCase):
    """open_db: the application_id guard, and the checks that read only, before WAL."""

    def setUp(self):
        super().setUp()
        self.path = os.path.join(self.dir, "test.db")

    def make(self, *statements):
        c = sqlite3.connect(self.path, isolation_level=None)
        for s in statements:
            c.execute(s)
        c.close()
        with open(self.path, "rb") as f:
            return f.read()

    def assert_refused(self, message, before):
        with self.assertRaises(SystemExit) as cm:
            self.eng.open_db(self.path)
        self.assertEqual(str(cm.exception), message)
        with open(self.path, "rb") as f:
            self.assertEqual(f.read(), before, "the refused file changed")
        self.assertFalse(os.path.exists(self.path + "-wal"))

    def pragma(self, name):
        c = sqlite3.connect(self.path)
        try:
            return c.execute(f"PRAGMA {name}").fetchone()[0]
        finally:
            c.close()

    def test_the_two_services_have_different_ids(self):
        self.assertEqual(self.eng.APPLICATION_ID, APPLICATION_IDS[ha.PROJECT])
        self.assertEqual(len(set(APPLICATION_IDS.values())), 2)

    def test_fresh_file_is_stamped(self):
        for _ in range(2):                         # opening it again changes nothing
            self.eng.open_db(self.path).close()
            self.assertEqual((self.pragma("user_version"), self.pragma("application_id")),
                             (self.eng.SCHEMA_VERSION, self.eng.APPLICATION_ID))

    def test_another_applications_file_is_refused_untouched(self):
        other = next(v for v in APPLICATION_IDS.values() if v != self.eng.APPLICATION_ID)
        for app_id, shown in ((0x5A5A5A5A, "0x5A5A5A5A"), (other, f"0x{other:08X}"), (-1, "0xFFFFFFFF")):
            with self.subTest(application_id=shown):
                if os.path.exists(self.path):
                    os.remove(self.path)
                before = self.make("CREATE TABLE t (x)", f"PRAGMA application_id = {app_id}", "PRAGMA user_version = 1")
                self.assert_refused(f"{self.path}: this database belongs to another application "
                                    f"(application_id {shown}): check the database setting", before)

    def test_v1_file_without_id_with_foreign_rows_is_refused(self):
        tables = self.eng._FOREIGN_TABLES_V1
        before = self.make(*[f"CREATE TABLE {t} (id INTEGER PRIMARY KEY)" for t in tables],
                           f"INSERT INTO {tables[-1]} DEFAULT VALUES", f"INSERT INTO {tables[-1]} DEFAULT VALUES",
                           "PRAGMA user_version = 1")
        self.assert_refused(f"{self.path}: this database holds another application's data ({tables[-1]} 2 rows): "
                            "check the database setting", before)

    def test_v1_file_without_id_and_without_foreign_rows_is_stamped(self):
        # stand-in names: SCHEMA would add its indexes to real foreign tables that still exist in this schema
        foreign = ("alien_one", "alien_two")
        self.make(*[f"CREATE TABLE {t} (id INTEGER PRIMARY KEY)" for t in foreign], "PRAGMA user_version = 1")
        with unittest.mock.patch.object(self.eng, "_FOREIGN_TABLES_V1", foreign):
            self.eng.open_db(self.path).close()
        self.assertEqual(self.pragma("application_id"), self.eng.APPLICATION_ID)

    def test_newer_schema_is_refused_untouched(self):
        before = self.make("CREATE TABLE t (x)", f"PRAGMA user_version = {self.eng.SCHEMA_VERSION + 1}")
        self.assert_refused(f"{self.path}: database schema version {self.eng.SCHEMA_VERSION + 1} is newer than this "
                            f"code ({self.eng.SCHEMA_VERSION}): update {ha.PROJECT}", before)

    def test_tables_with_rows(self):
        c = sqlite3.connect(":memory:")
        c.executescript("CREATE TABLE a (x); CREATE TABLE b (x); INSERT INTO a VALUES (1); INSERT INTO a VALUES (2);")
        self.assertEqual(self.eng._tables_with_rows(c, ("missing", "b", "a")), [("a", 2)])
        c.close()


class Reply(EngineCase):
    """Handler.reply, log_message and user, on a handler without a socket."""

    def handler(self, command="POST", path="/upload?x=1", headers=None):
        from support import handler        # after engine(): the handler imports the engine
        h = object.__new__(handler.Handler)
        h.command, h.path, h.request_version, h.requestline = command, path, "HTTP/1.1", f"{command} {path} HTTP/1.1"
        h.client_address, h.wfile, h.headers, h.cfg = ("127.0.0.1", 0), io.BytesIO(), headers or {}, CFG
        return h

    def test_reply_logs_the_message(self):
        h = self.handler()
        body = {"id": 1, "status": "in_coda", "message": "Ricevuto: «prova»"}
        h.reply(200, body)
        head, _, raw = h.wfile.getvalue().partition(b"\r\n\r\n")
        self.assertIn(b"Content-Type: application/json; charset=utf-8", head)
        self.assertEqual(json.loads(raw), body)
        self.assertEqual(self.lines(), ["POST /upload 200 Ricevuto: «prova»"])

    def test_old_message_key_is_not_logged(self):
        self.handler("GET", "/x").reply(404, {"messaggio": "vecchio"})
        self.assertEqual(self.lines(), ["GET /x 404 "])

    def test_successful_get_is_not_logged(self):
        h = self.handler("GET", "/health")
        h.reply(200, {"ok": True})
        h.log_message("%s", "nothing")
        self.assertEqual(self.output.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
