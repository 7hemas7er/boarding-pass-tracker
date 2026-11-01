"""The engine inside Home Assistant (INTEGRATION_PLAN.md 3.6, 3.7 E2 E13 E14 E15, C1 C2 C12): configure() and
open_and_prepare(), the executor jobs and shutdown() with the job generation, the backup hooks, the refused databases
and the diagnostics. Fictional data only.

  python3 -I -B -m unittest discover -s tests/core -p test_lifecycle.py -v
"""
import datetime, hashlib, json, os, sqlite3, sys, threading, time, unittest
from unittest import mock
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from core_case import CFG, USER, CoreCase, eng, settings, te  # noqa: E402

tf = te.tf


def sha(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


class Configure(CoreCase):
    def test_every_setting_from_the_entry(self):
        tz, folder = ZoneInfo("Europe/Rome"), self.folder()
        eng.NO_HA, eng.NO_NOTIFY, eng.MQTT_PREFIX = True, True, "bptprova"
        eng.configure(settings(folder, local_tz=tz, no_notify=True, no_flights=True, max_api_calls_per_month=7,
                               aviationstack_key="fake-key", fr24_add="text.a", fr24_remove="text.r",
                               fr24_tracked="sensor.t", decoder_url="http://local-barcode-decoder:8099",
                               read_photos=True))
        self.assertEqual((eng.DATA_DIR, eng.DATABASE, eng.database_path()), (folder, None,
                                                                            os.path.join(folder, "tracker.db")))
        self.assertEqual((eng.LOCAL_TZ, eng.flights.LOCAL_TZ), (tz, tz))
        self.assertEqual((eng.NO_NOTIFY, eng.NO_HA, eng.NO_FLIGHTS, eng.MAX_API_CALLS_PER_MONTH), (True, False, True, 7))
        self.assertEqual((eng.FR24_ADD, eng.FR24_REMOVE, eng.FR24_TRACKED), ("text.a", "text.r", "sensor.t"))
        self.assertEqual((eng.barcodes.DECODER_URL, eng.READ_PHOTOS), ("http://local-barcode-decoder:8099", True))
        self.assertEqual(eng.MQTT_PREFIX, "boarding_pass_tracker")          # the constant prefix
        self.assertEqual(eng.tag("d", 3), "boarding_pass_tracker-d3")
        self.assertEqual(eng.aviationstack_key(), "fake-key")               # not the TRACKER_* key file
        eng.configure(settings(folder))
        with self.assertRaises(RuntimeError):                               # "" = no key
            eng.aviationstack_key()
        self.assertEqual(eng.tracking_suffix(), eng.manual.text("untracked"))

    def test_unknown_missing_and_invalid_settings_are_refused(self):
        folder = self.folder()
        good = settings(folder)
        bad = [{**good, "mqtt_prefix": "x"}, {k: v for k, v in good.items() if k != "decoder_url"},
               {**good, "data_dir": "relative/dir"}, {**good, "local_tz": "Europe/Rome"},
               {**good, "no_notify": "no"}, {**good, "max_api_calls_per_month": -1},
               {**good, "max_api_calls_per_month": True}, {**good, "aviationstack_key": None},
               {**good, "fr24_add": "not an entity"}, {**good, "decoder_url": 8099},
               {**good, "decoder_url": "ftp://x"}, {**good, "read_photos": 1}, ["data_dir"]]
        before = eng.DATA_DIR
        for s in bad:
            with self.subTest(settings=s), self.assertRaises(ValueError):
                eng.configure(s)
        self.assertEqual(eng.DATA_DIR, before)                              # nothing set by a refused call
        self.assertEqual(eng.SETTINGS_KEYS, frozenset(good))

    def test_the_flightradar24_memory_and_the_backup_flag_survive(self):
        eng._fr24_attempts.clear()                     # (an earlier test may have left some: restored afterwards)
        eng._fr24_forget.clear()
        eng._fr24_attempts["FR1234"] = datetime.datetime.now(datetime.timezone.utc)
        eng._fr24_forget.add("FR4321")
        eng._backup_active = True
        eng._decoder_pause_until, eng._decoder_pause_index = time.monotonic() + 999, 3
        eng._closed = eng._closing = True
        self.configure()
        self.assertIn("FR1234", eng._fr24_attempts)
        self.assertEqual(eng._fr24_forget, {"FR4321"})
        self.assertTrue(eng._backup_active)
        self.assertEqual((eng._decoder_pause_until, eng._decoder_pause_index, eng._closed, eng._closing),
                         (0.0, 0, False, False))

    def test_configure_closes_an_open_database(self):
        c = eng.db()
        self.configure(data_dir=self.folder())
        self.assertIsNone(eng._db)
        with self.assertRaises(sqlite3.ProgrammingError):
            c.execute("SELECT 1")


class OpenAndPrepare(CoreCase):
    def test_folder_database_and_counts(self):
        folder = os.path.join(self.folder(), "config", "boarding_pass_tracker")
        calls = []
        with mock.patch.object(eng, "reschedule_checks", lambda *a: calls.append(a)):
            out = eng.job(eng.open_and_prepare, settings(folder), True)
        self.assertEqual(out, {"schema": eng.SCHEMA_VERSION, "counts": {"flights": 0, "boarding_passes": 0, "documents": 0, "queued": 0,
                                                       "queued_photos": 0}})
        self.assertEqual(calls, [()])
        self.assertEqual(os.stat(folder).st_mode & 0o777, 0o700)
        self.assertEqual(os.stat(os.path.dirname(folder)).st_mode & 0o777, 0o700)
        self.assertTrue(os.path.exists(os.path.join(folder, "tracker.db")))
        self.assertTrue(eng.flights._ZONES)                                # the zones read once
        with mock.patch.object(eng, "reschedule_checks", lambda *a: calls.append(a)):
            eng.open_and_prepare(settings(folder), False)                  # an options reload: no reschedule
        self.assertEqual(calls, [()])

    def test_reschedule_once(self):
        folder = self.folder()
        eng.configure(settings(folder))
        fid = self.new_flight(date="2026-11-10", next_check="2000-01-01T00:00:00Z", updated_at="x")
        eng.open_and_prepare(settings(folder), False)
        self.assertEqual(eng.q("SELECT next_check FROM flights WHERE id=?", (fid,), one=True)["next_check"],
                         "2000-01-01T00:00:00Z")                          # a back-off survives a reload (IBC23)
        eng.open_and_prepare(settings(folder), True)
        self.assertNotEqual(eng.q("SELECT next_check FROM flights WHERE id=?", (fid,), one=True)["next_check"],
                            "2000-01-01T00:00:00Z")


class Refused(CoreCase):
    """open_db refuses with DatabaseRefused(SystemExit) and a reason; the file is untouched (sha256, no -wal)."""

    def make(self, *statements):
        path = os.path.join(self.folder(), "tracker.db")
        c = sqlite3.connect(path, isolation_level=None)
        for s in statements:
            c.execute(s)
        c.close()
        return path

    def assert_refused(self, path, reason, text):
        before = sha(path)
        with self.assertRaises(eng.DatabaseRefused) as e:
            eng.open_db(path)
        self.assertIsInstance(e.exception, SystemExit)                      # the standalone's tests catch it
        self.assertEqual(e.exception.reason, reason)
        self.assertIn(text, str(e.exception))
        self.assertEqual(sha(path), before)
        self.assertFalse(os.path.exists(path + "-wal"))

    def test_newer(self):
        self.assert_refused(self.make("CREATE TABLE t (x)", f"PRAGMA user_version = {eng.SCHEMA_VERSION + 1}"),
                            "newer_database", f"database schema version {eng.SCHEMA_VERSION + 1} is newer than this "
                            f"code ({eng.SCHEMA_VERSION})")

    def test_foreign(self):
        self.assert_refused(self.make("CREATE TABLE t (x)", "PRAGMA application_id = 1212957767",
                                      "PRAGMA user_version = 1"), "foreign_database", "belongs to another application")
        self.assert_refused(self.make("CREATE TABLE payments (id INTEGER PRIMARY KEY)",
                                      "INSERT INTO payments DEFAULT VALUES", "PRAGMA user_version = 1"),
                            "foreign_database", "holds another application's data (payments 1 rows)")

    def test_v0(self):
        path = self.make("CREATE TABLE documenti (id INTEGER PRIMARY KEY, stato TEXT)",
                         "INSERT INTO documenti (stato) VALUES ('letto')")
        self.assert_refused(path, "old_database_v0", "v0 schema of App Casa")
        self.assertFalse(hasattr(eng, "_migrate_v0"))

    def test_refused_through_open_and_prepare_writes_nothing(self):
        folder = self.folder()
        path = os.path.join(folder, "tracker.db")
        c = sqlite3.connect(path, isolation_level=None)
        c.execute("CREATE TABLE documenti (id INTEGER PRIMARY KEY)")
        c.close()
        before = sha(path)
        with self.assertRaises(eng.DatabaseRefused) as e:
            eng.job(eng.open_and_prepare, settings(folder), True)
        self.assertEqual((e.exception.reason, sha(path), sorted(os.listdir(folder))),
                         ("old_database_v0", before, ["tracker.db"]))


class Jobs(CoreCase):
    def setUp(self):
        super().setUp()
        self.configure(data_dir=self.folder())
        eng.db()

    def blocked_job(self, started, release, fn=None):
        """A job in a thread that waits for `release`, then runs fn (default: a write). Returns the thread and
        its outcome list."""
        out = []

        def body():
            started.set()
            release.wait(10)
            return (fn or (lambda: eng.q("INSERT INTO api_usage (month, calls) VALUES ('2099-01', 1)")))()

        def run():
            try:
                out.append(("ok", eng.job(body)))
            except BaseException as e:
                out.append(("raised", type(e).__name__))
        t = threading.Thread(target=run)
        t.start()
        self.assertTrue(started.wait(5))
        return t, out

    def test_job_runs_the_function(self):
        self.assertEqual(eng.job(lambda a, b: a + b, 2, 3), 5)
        self.assertEqual(eng._active, 0)
        with self.assertRaises(ZeroDivisionError):
            eng.job(lambda: 1 / 0)
        self.assertEqual(eng._active, 0)

    def test_after_shutdown_jobs_are_refused_until_the_next_setup(self):
        eng.shutdown(1)
        self.assertIsNone(eng._db)
        with self.assertRaises(eng.Closed):
            eng.job(eng.publish_now)
        with self.assertRaises(eng.Closed):
            eng.db()
        eng.job(eng.backup_begin)                                           # the backup hooks still run
        eng.job(eng.backup_end)
        out = eng.job(eng.open_and_prepare, settings(eng.DATA_DIR), False)  # the next setup opens it again
        self.assertEqual(out["schema"], eng.SCHEMA_VERSION)
        self.assertEqual(eng.job(lambda: eng.q("SELECT 1 AS x", one=True)), {"x": 1})

    def test_shutdown_waits_for_the_running_job(self):
        started, release = threading.Event(), threading.Event()
        t, out = self.blocked_job(started, release)
        timer = threading.Timer(0.3, release.set)
        timer.start()
        t0 = time.monotonic()
        eng.shutdown(5)
        t.join(5)
        self.assertGreaterEqual(time.monotonic() - t0, 0.25)
        self.assertEqual(out, [("ok", [])])                                 # it finished before the close
        self.assertIsNone(eng._db)

    def test_a_job_that_outlives_shutdown_never_writes_into_the_next_database(self):
        started, release = threading.Event(), threading.Event()
        t, out = self.blocked_job(started, release)
        eng.shutdown(0.2)                                                   # gives up waiting
        self.assertIn("1 jobs still running after 0.2 s", self.output.getvalue())
        eng.job(eng.open_and_prepare, settings(eng.DATA_DIR), False)        # the next setup
        release.set()
        t.join(5)
        self.assertEqual(out, [("raised", "Closed")])
        self.assertEqual(eng.q("SELECT count(*) AS n FROM api_usage", one=True)["n"], 0)

    def test_closed_is_not_swallowed(self):
        """work_one, change_flight, api.choices and clock_round let Closed through (the views answer 503)."""
        import api
        st = eng.ClockState()
        body = json.dumps({"action": "add", "flight": "FR 1234", "date": "2026-11-12", "origin": "BLQ",
                           "destination": "STN", "passengers": "Alice"}).encode()
        for name, fn in (("work_one", lambda: eng.work_one(CFG)), ("change_flight", lambda: eng.change_flight(
                body, USER, CFG)), ("choices", lambda: api.choices(USER)), ("clock_round", lambda: eng.clock_round(
                    CFG, st))):
            with self.subTest(job=name):
                started, release = threading.Event(), threading.Event()
                t, out = self.blocked_job(started, release, fn)
                eng.shutdown(0.05)
                release.set()
                t.join(5)
                self.assertEqual(out, [("raised", "Closed")])
                eng.job(eng.open_and_prepare, settings(eng.DATA_DIR), False)
        self.assertNotIn("Errore interno", self.output.getvalue())


class Backup(CoreCase):
    def setUp(self):
        super().setUp()
        self.configure(data_dir=self.folder())
        self.path = eng.database_path()
        eng.db()

    def write(self, n, start=0, pad=0):
        with eng.transaction() as c:
            for i in range(start, start + n):
                c.execute("INSERT INTO gate_positions (airport, gate, lat, lon, searched_at) VALUES (?, ?, 1, 2, 'x')",
                          ("BLQ", f"G{i}" + "x" * pad))

    def pragma(self, name):
        with eng._db_lock:
            return eng._db.execute(f"PRAGMA {name}").fetchone()[0]

    def test_begin_folds_the_wal_and_stops_the_checkpoints(self):
        self.write(50)
        eng.backup_begin()
        self.assertTrue(eng._backup_active)
        self.assertEqual(os.path.getsize(self.path + "-wal"), 0)            # TRUNCATE: everything in tracker.db
        self.assertEqual(self.pragma("wal_autocheckpoint"), 0)
        main = sha(self.path)
        self.write(3000, 50, pad=2000)                                      # 6 MB, past 1000 pages of WAL
        self.assertGreater(os.path.getsize(self.path + "-wal"), 1000 * 4096)
        self.assertEqual(sha(self.path), main)                              # and no checkpoint
        eng.backup_end()
        self.assertEqual((eng._backup_active, self.pragma("wal_autocheckpoint")), (False, 1000))
        self.assertEqual(eng.q("SELECT count(*) AS n FROM gate_positions", one=True)["n"], 3050)

    def test_writes_go_on_during_the_backup(self):
        errors = []

        def writer():
            try:
                for k in range(20):
                    self.write(10, 1000 + 10 * k)
            except Exception as e:
                errors.append(e)
        t = threading.Thread(target=writer)
        t.start()
        for _ in range(5):
            eng.backup_begin()
            eng.backup_end()
        t.join(10)
        self.assertEqual(errors, [])
        self.assertEqual(eng.q("SELECT count(*) AS n FROM gate_positions", one=True)["n"], 200)
        self.assertEqual(self.pragma("quick_check"), "ok")

    def test_a_shutdown_during_the_backup_keeps_the_wal(self):
        """C12: no checkpoint on close while a backup copies the files: tracker.db and -wal stay as they are."""
        self.write(10)
        eng.backup_begin()
        self.write(500, 10)
        main, wal = sha(self.path), sha(self.path + "-wal")
        eng.shutdown(1)
        self.assertEqual((sha(self.path), sha(self.path + "-wal")), (main, wal))
        eng.job(eng.open_and_prepare, settings(eng.DATA_DIR), False)        # a reload during the backup
        self.assertEqual(self.pragma("wal_autocheckpoint"), 0)              # re-applied to the new connection
        self.assertEqual(eng.q("SELECT count(*) AS n FROM gate_positions", one=True)["n"], 510)
        eng.backup_end()
        eng.shutdown(1)
        self.assertFalse(os.path.exists(self.path + "-wal"))                # a normal close folds the WAL

    def test_the_hooks_never_raise(self):
        eng.shutdown(1)
        eng.backup_begin()                                                  # no database open: only the flag
        self.assertTrue(eng._backup_active)
        eng.backup_end()
        broken = sqlite3.connect(":memory:")
        broken.close()
        eng._db = broken
        try:
            eng.backup_begin()
            eng.backup_end()
        finally:
            eng._db = None
        self.assertIn("warning: backup start: ProgrammingError", self.output.getvalue())
        self.assertIn("warning: backup end: ProgrammingError", self.output.getvalue())


class Diagnostics(CoreCase):
    def test_counts_without_names(self):
        self.configure(data_dir=self.folder())
        doc = eng.save_upload(tf.pkpass(tf.bcbp()), "pkpass", USER)[0]
        eng.read_document(self.doc(doc))
        self.new_doc(status="in_coda", file="file/missing.png", file_type="png")
        orphan = os.path.join(eng.DATA_DIR, "file", "2026", "orphan.png")
        os.makedirs(os.path.dirname(orphan), exist_ok=True)
        with open(orphan, "wb") as f:
            f.write(b"x")
        with eng._db_lock:
            eng._db.execute("INSERT INTO api_usage (month, calls) VALUES ('2026-11', 12)")
        d = eng.diagnostics()
        self.assertEqual({k: d[k] for k in ("schema", "application_id", "quick_check", "flights", "boarding_passes",
                                            "gate_positions", "api_usage")},
                         {"schema": eng.SCHEMA_VERSION, "application_id": "0x42505454", "quick_check": "ok", "flights": 1,
                          "boarding_passes": 1, "gate_positions": 0, "api_usage": {"2026-11": 12}})
        self.assertEqual(d["documents"], {"queued": 1, "read": 1, "archived": 0, "error": 0, "unsent": 1})
        self.assertEqual({k: d["files"][k] for k in ("count", "missing", "orphans")}, {"count": 1, "missing": 1,
                                                                                       "orphans": 1})
        self.assertGreater(d["files"]["bytes"], 0)
        text = json.dumps(d)
        for secret in ("Mario", "Rossi", "FR 1234", "BLQ", "alice", ".pkpass", "orphan.png", "file/"):
            self.assertNotIn(secret, text)
        self.assertEqual(eng.queued_counts(), {"queued": 1, "queued_photos": 1})


if __name__ == "__main__":
    unittest.main()
