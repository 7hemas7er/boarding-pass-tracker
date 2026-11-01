"""Stage B schema v2 (INTEGRATION_PLAN.md 4.2-4.4, 6.3 BC-B1, CMS-08): the v1 -> v2 upgrade at open_db, its copy in
backups/ (at most one per source state), the refusals that leave the file at v1, idempotence. The v1 file is made
here: the core writes a database (two passes read, a flight typed by hand, a month of calls), and the two columns of
v2 are dropped with user_version 1, the layout of the 0.1.0 service. Fictional data only (alice, bob, the tf.bcbp()
defaults).

  python3 -I -B -m unittest discover -s tests/core -p 'test_stage_b_schema.py' -v
"""
import datetime, errno, hashlib, json, os, shutil, sqlite3, stat, sys, unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from core_case import CFG, CoreCase, eng  # noqa: E402
import test_flights as tf  # noqa: E402

TABLES = ("documents", "flights", "boarding_passes", "api_usage", "gate_positions")
V2_LINES = ("  pass_arrival TEXT,              -- v2: ISO instant with the destination's offset: typed or pass "
            "semantics\n  pass_terminal TEXT,             -- v2: departure terminal: typed or pass semantics "
            "(departureTerminal)\n")
ALICE, BOB = CFG["users"]["alice@example.com"], CFG["users"]["bob@example.com"]


def build_v1(path):
    """A v1 database at `path`: written by the core in its own test folder, copied whole (sqlite3 backup: no -wal),
    then the v2 columns dropped and user_version 1."""
    soon = (datetime.date.today() + datetime.timedelta(days=2)).timetuple().tm_yday
    eng.save_upload(tf.pkpass(tf.bcbp(day=soon)), "pkpass", ALICE)
    eng.save_upload(tf.pkpass(tf.bcbp(number="2345", origin="STN", destination="BLQ", day=soon, seat="003C")),
                    "pkpass", BOB)
    while eng.work_one(CFG) is not None:
        pass
    day = (datetime.date.today() + datetime.timedelta(days=20)).isoformat()
    code, reply = eng.change_flight(json.dumps({"action": "add", "flight": "FR 3456", "date": day, "origin": "BLQ",
                                                "destination": "STN", "passengers": "Alice 4B, Bob"}).encode(),
                                    ALICE, CFG)
    assert code == 200, reply
    with eng.transaction() as c:
        c.execute("INSERT INTO api_usage (month, calls) VALUES ('2026-11', 3)")
    src, dst = sqlite3.connect(eng.database_path()), sqlite3.connect(path)
    try:
        src.backup(dst)
        dst.execute("ALTER TABLE flights DROP COLUMN pass_terminal")
        dst.execute("ALTER TABLE flights DROP COLUMN pass_arrival")
        dst.execute("PRAGMA user_version = 1")
        dst.commit()
    finally:
        src.close()
        dst.close()
    eng._db.close()                               # the tests open their copies themselves
    eng._db = None


def sha(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def files(path):
    """sha256 of the database's main file and -wal (None when missing)."""
    return tuple(sha(path + s) if os.path.exists(path + s) else None for s in ("", "-wal"))


def raw(path):
    """(user_version, flights columns, {table: rows}) read with plain sqlite3 from a copy (the file stays as it is)."""
    folder = os.path.join(os.path.dirname(path), ".raw")
    shutil.rmtree(folder, True)
    os.mkdir(folder)
    copy = os.path.join(folder, "x.db")
    for s in ("", "-wal"):
        if os.path.exists(path + s):
            shutil.copyfile(path + s, copy + s)
    c = sqlite3.connect(copy)
    try:
        return (c.execute("PRAGMA user_version").fetchone()[0], [tuple(r) for r in c.execute("PRAGMA table_info(flights)")],
                {t: [tuple(r) for r in c.execute(f"SELECT * FROM {t} ORDER BY rowid")] for t in TABLES})
    finally:
        c.close()
        shutil.rmtree(folder, True)


class Schema(CoreCase):
    def setUp(self):
        super().setUp()
        self.root = self.folder()
        self.v1 = os.path.join(self.root, "v1", "tracker.db")
        os.makedirs(os.path.dirname(self.v1), mode=0o700)
        build_v1(self.v1)

    def copy(self, name):
        """A data folder holding a copy of the v1 file (without its documents' files)."""
        folder = os.path.join(self.root, name)
        os.mkdir(folder, 0o700)
        for s in ("", "-wal"):
            if os.path.exists(self.v1 + s):
                shutil.copyfile(self.v1 + s, os.path.join(folder, "tracker.db" + s))
        return os.path.join(folder, "tracker.db")

    def open(self, path):
        c = eng.open_db(path)
        c.close()

    def backups(self, path):
        folder = eng.backups_folder(path)
        return sorted(os.listdir(folder)) if os.path.isdir(folder) else []

    def fresh_layout(self):
        path = os.path.join(self.folder(), "fresh.db")
        self.open(path)
        c = sqlite3.connect(path)
        try:
            return c.execute("PRAGMA user_version").fetchone()[0], [tuple(r) for r in
                                                                    c.execute("PRAGMA table_info(flights)")]
        finally:
            c.close()

    # ------------------------------------------------ the upgrade
    def test_v2_is_v1_plus_two_columns(self):
        self.assertEqual(eng.SCHEMA_VERSION, 2)
        self.assertEqual(eng.SCHEMA.count(V2_LINES), 1)
        fresh = self.fresh_layout()[1]
        self.assertEqual(fresh[-2:], [(16, "pass_arrival", "TEXT", 0, None, 0),
                                      (17, "pass_terminal", "TEXT", 0, None, 0)])
        self.assertEqual(raw(self.v1)[:2], (1, fresh[:-2]))  # the v1 file: the same columns but the last two

    def test_upgrade_keeps_every_row_and_makes_one_copy(self):
        path = self.copy("upgrade")
        src12 = sha(path)[:12]                           # a whole copy: everything is in the main file
        before = raw(path)
        self.assertEqual(before[0], 1)
        self.open(path)
        after = raw(path)
        self.assertEqual((after[0], after[1]), self.fresh_layout())       # v2, the column order of a new file
        for t in TABLES:
            want = [r + (None, None) for r in before[2][t]] if t == "flights" else before[2][t]
            self.assertEqual(after[2][t], want, t)                       # every row and value, the two new NULL
        (name,) = self.backups(path)
        self.assertRegex(name, rf"^tracker-v1-\d{{8}}-\d{{6}}-{src12}\.db$")
        copy = os.path.join(eng.backups_folder(path), name)
        self.assertEqual(raw(copy), before)                              # the copy is the v1 file as it was
        self.assertEqual([f for f in os.listdir(eng.backups_folder(path)) if not f.startswith("tracker-v1-")], [])
        self.assertEqual(stat.S_IMODE(os.stat(eng.backups_folder(path)).st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(os.stat(copy).st_mode), 0o600)
        self.assertIn(f"database copied before the upgrade to v2: backups/{name}", self.output.getvalue())
        self.assertIn("database upgraded to v2: api_usage 1, boarding_passes 4, documents 2, flights 3, gate_positions 0",
                      self.output.getvalue())

    def test_idempotent(self):
        path = self.copy("again")
        self.open(path)
        state, listing = files(path), self.backups(path)
        for _ in range(3):
            self.open(path)
        self.assertEqual((files(path), self.backups(path)), (state, listing))
        self.assertEqual(raw(path)[0], 2)

    def test_the_engine_works_on_the_upgraded_file(self):
        path = self.copy("works")
        out = eng.open_and_prepare(self.configure(data_dir=os.path.dirname(path)), False)
        self.assertEqual((out["schema"], out["counts"]["flights"], out["counts"]["boarding_passes"]), (2, 3, 4))
        choices = eng.flight_choices()
        details = choices["details"][choices["choices"][0]]
        self.assertEqual({k: details[k] for k in ("departure_time", "terminal", "gate")},
                         {"departure_time": "", "terminal": "", "gate": ""})
        body = json.dumps({"action": "edit", "ref": details["ref"], "terminal": "1"}).encode()
        code, reply = eng.change_flight(body, te_user(), {"users": {}})
        self.assertEqual((code, reply["result"]), (200, "edited"))
        self.assertEqual(len(self.backups(path)), 1)

    # ------------------------------------------------ failures leave v1 (CMS-08)
    def test_three_failed_opens_leave_one_copy(self):
        path = self.copy("retry")
        src12 = sha(path)[:12]
        start = eng._local_now()
        clock = iter([start + datetime.timedelta(minutes=i) for i in range(10)])           # each try later
        with mock.patch.object(eng, "_migrate_v1", side_effect=OSError(errno.ENOSPC, "No space left on device")), \
                mock.patch.object(eng, "_local_now", lambda: next(clock)):
            for _ in range(3):
                with self.assertRaises(OSError):
                    eng.open_db(path)
                self.assertEqual(raw(path)[0], 1)
        (name,) = self.backups(path)
        self.assertTrue(name.endswith(f"-{src12}.db"), name)
        self.open(path)                                                  # the next try upgrades: still one copy
        self.assertEqual((raw(path)[0], self.backups(path)), (2, [name]))

    def test_a_locked_database_is_not_ready(self):
        path = self.copy("locked")
        other = sqlite3.connect(path, isolation_level=None, timeout=0)
        other.execute("PRAGMA journal_mode=WAL")
        other.execute("BEGIN IMMEDIATE")                                 # another writer holds the database
        try:
            real = sqlite3.connect                                       # a short busy timeout, not 5 s
            with mock.patch.object(eng.sqlite3, "connect", lambda *a, **k: real(*a, **{**k, "timeout": 0.2})):
                with self.assertRaises(sqlite3.OperationalError) as e:
                    eng.open_db(path)
            self.assertIn("locked", str(e.exception))
        finally:
            other.execute("ROLLBACK")
            other.close()
        self.assertEqual(raw(path)[0], 1)
        self.open(path)
        self.assertEqual(raw(path)[0], 2)

    def test_an_unexpected_column_is_migration_failed(self):
        path = self.copy("odd")
        c = sqlite3.connect(path)
        c.execute("ALTER TABLE flights ADD COLUMN pass_seat_map TEXT")
        c.close()
        before = raw(path)
        for _ in range(2):                                               # the same on every try
            with self.assertRaises(eng.MigrationFailed) as e:
                eng.open_db(path)
            self.assertIn("columns a v1 database does not have", str(e.exception))
            self.assertEqual(raw(path), before)                          # rolled back: v1, the columns as they were
        self.assertEqual(len(self.backups(path)), 1)
        self.assertNotIsInstance(e.exception, (sqlite3.Error, OSError))  # the shell's ConfigEntryError, no retry

    def test_a_count_that_changes_is_migration_failed(self):
        path = self.copy("count")
        real = eng._count
        calls = []

        def counting(c, table):
            calls.append(table)
            return real(c, table) + (len(calls) > len(TABLES))           # the second round of counts differs
        with mock.patch.object(eng, "_count", counting):
            with self.assertRaises(eng.MigrationFailed) as e:
                eng.open_db(path)
        self.assertIn("row counts changed", str(e.exception))
        self.assertEqual(raw(path)[0], 1)

    def test_while_a_backup_of_ha_runs_the_upgrade_waits(self):
        path = self.copy("backup")
        before = files(path)
        eng._backup_active = True
        with self.assertRaises(OSError) as e:
            eng.open_db(path)
        self.assertEqual(e.exception.errno, errno.EBUSY)
        self.assertEqual((files(path)[0], self.backups(path)), (before[0], []))
        eng._backup_active = False
        self.open(path)
        self.assertEqual((raw(path)[0], len(self.backups(path))), (2, 1))

    def test_a_new_file_is_v2_without_a_copy(self):
        path = os.path.join(self.folder(), "tracker.db")
        self.open(path)
        self.assertEqual((raw(path)[0], self.backups(path)), (2, []))
        self.assertFalse(os.path.exists(eng.backups_folder(path)))

def te_user():
    return {"name": "alice", "notify": "mobile_app_alice", "person": "person.alice"}


if __name__ == "__main__":
    unittest.main()
