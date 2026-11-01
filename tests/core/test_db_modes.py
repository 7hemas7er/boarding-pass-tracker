"""PLATFORM-02 / PARITY-01 / SECURITY-01 (FIX-repo-1): the modes of plan 4.1 under Home Assistant's umask 022 (the
standalone ran with 077): the data folder 0700, tracker.db, tracker.db-wal and tracker.db-shm 0600, the uploads 0600.
A new database file is created 0600 before SQLite opens it (SQLite gives -wal and -shm the main file's mode); an
existing folder or database file with group or other bits is made private; a refused database is left untouched.

  python3 -I -B -m unittest discover -s tests/core -p test_db_modes.py -v
"""
import os, sqlite3, stat, sys, unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from core_case import USER, CoreCase, eng, settings, te  # noqa: E402

DB_FILES = ("tracker.db", "tracker.db-wal", "tracker.db-shm")


def mode(path):
    return stat.S_IMODE(os.lstat(path).st_mode)


class Modes(CoreCase):
    def setUp(self):
        super().setUp()
        old = os.umask(0o022)                                    # Home Assistant's (Core runs as root, umask 022)
        self.addCleanup(os.umask, old)

    def prepare(self, folder):
        return eng.open_and_prepare(settings(folder), False)

    def modes(self, folder):
        return {name: mode(os.path.join(folder, name)) for name in DB_FILES if os.path.exists(os.path.join(folder,
                                                                                                         name))}

    def test_a_new_folder_and_database(self):
        folder = os.path.join(self.folder(), "boarding_pass_tracker")
        self.prepare(folder)
        doc = eng.save_upload(te.tiny_png(), "png", USER)[0]          # a write: the -wal and -shm exist
        self.assertEqual(mode(folder), 0o700)
        self.assertEqual(self.modes(folder), dict.fromkeys(DB_FILES, 0o600))
        rel = eng.q("SELECT file FROM documents WHERE id=?", (doc,), one=True)["file"]
        self.assertEqual(mode(os.path.join(folder, rel)), 0o600)
        self.assertEqual(mode(os.path.dirname(os.path.join(folder, rel))), 0o700)
        eng.shutdown(5)                                                # closed: SQLite removes -wal and -shm
        self.prepare(folder)                                           # and makes them again, 0600
        eng.save_upload(te.tiny_png((9, 9, 9)), "png", USER)
        self.assertEqual(self.modes(folder), dict.fromkeys(DB_FILES, 0o600))

    def test_a_new_file_is_0600_before_sqlite_opens_it(self):
        """No moment with a 0644 tracker.db: the file exists, 0600, when sqlite3.connect opens it."""
        folder = os.path.join(self.folder(), "boarding_pass_tracker")
        seen, real = [], sqlite3.connect

        def connect(path, *a, **k):
            seen.append(mode(path) if os.path.exists(path) else None)
            return real(path, *a, **k)
        with mock.patch.object(eng.sqlite3, "connect", connect):
            self.prepare(folder)
        self.assertEqual(seen, [0o600])

    def test_an_existing_folder_and_files_are_made_private(self):
        """A folder made by hand (or a copy) 0755 with a database written by another process under umask 022:
        tracker.db, a -wal still holding frames and the -shm, all 0644."""
        folder = os.path.join(self.folder(), "boarding_pass_tracker")
        os.mkdir(folder, 0o755)
        os.chmod(folder, 0o755)
        other = sqlite3.connect(os.path.join(folder, "tracker.db"), isolation_level=None)
        self.addCleanup(other.close)
        other.execute("PRAGMA journal_mode=WAL")
        other.execute("PRAGMA wal_autocheckpoint=0")
        other.executescript(eng.SCHEMA)
        other.execute(f"PRAGMA user_version = {eng.SCHEMA_VERSION}")
        other.execute(f"PRAGMA application_id = {eng.APPLICATION_ID}")
        other.execute("INSERT INTO api_usage (month, calls) VALUES ('2026-11', 3)")
        for name in DB_FILES:
            os.chmod(os.path.join(folder, name), 0o644)
        self.assertEqual((mode(folder), self.modes(folder)), (0o755, dict.fromkeys(DB_FILES, 0o644)))
        out = self.prepare(folder)
        self.assertEqual(out["schema"], eng.SCHEMA_VERSION)
        self.assertEqual(mode(folder), 0o700)
        self.assertEqual(self.modes(folder), dict.fromkeys(DB_FILES, 0o600))
        self.assertEqual(eng.q("SELECT calls FROM api_usage", one=True)["calls"], 3)   # the rows are untouched

    def test_owner_bits_are_kept_and_nothing_is_widened(self):
        folder = os.path.join(self.folder(), "boarding_pass_tracker")
        self.prepare(folder)
        eng.shutdown(5)
        db = os.path.join(folder, "tracker.db")
        os.chmod(db, 0o640)
        self.prepare(folder)
        self.assertEqual(mode(db), 0o600)

    def test_a_refused_database_is_left_untouched(self):
        folder = os.path.join(self.folder(), "boarding_pass_tracker")
        os.mkdir(folder, 0o700)
        path = os.path.join(folder, "tracker.db")
        c = sqlite3.connect(path)
        c.execute("PRAGMA application_id = 1212501063")                   # another application's file
        c.execute("CREATE TABLE t (x)")
        c.commit()
        c.close()
        os.chmod(path, 0o644)
        with open(path, "rb") as f:
            before = f.read()
        with self.assertRaises(eng.DatabaseRefused) as e:
            self.prepare(folder)
        self.assertEqual(e.exception.reason, "foreign_database")
        with open(path, "rb") as f:
            self.assertEqual(f.read(), before)
        self.assertEqual(mode(path), 0o644)                               # not ours: not even its mode
        self.assertFalse(os.path.exists(path + "-wal"))

    def test_a_link_or_another_kind_is_left_alone(self):
        target = os.path.join(self.folder(), "elsewhere")
        os.mkdir(target, 0o755)
        os.chmod(target, 0o755)
        link = os.path.join(self.folder(), "link")
        os.symlink(target, link)
        eng._private(link, folder=True)
        self.assertEqual(mode(target), 0o755)
        a_file = os.path.join(self.folder(), "boarding_pass_tracker")      # the data folder's name taken by a file
        with open(a_file, "w") as f:
            f.write("x")
        os.chmod(a_file, 0o644)
        with self.assertRaises(sqlite3.OperationalError):                 # not a folder: as before (not ready)
            self.prepare(a_file)
        self.assertEqual(mode(a_file), 0o644)
        eng._private(target)                                               # a folder is not a regular file
        self.assertEqual(mode(target), 0o755)

    def test_the_database_setting_path_too(self):
        folder = self.folder()
        path = os.path.join(folder, "other.db")
        c = eng.open_db(path)
        self.addCleanup(c.close)
        c.execute("INSERT INTO api_usage (month, calls) VALUES ('2026-11', 1)")
        self.assertEqual({n: mode(os.path.join(folder, n)) for n in os.listdir(folder)},
                         {"other.db": 0o600, "other.db-wal": 0o600, "other.db-shm": 0o600})


if __name__ == "__main__":
    unittest.main()
