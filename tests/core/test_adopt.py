"""adopt.inspect and adopt.names_in_use (INTEGRATION_PLAN.md 4.5, C4, 5.8; the R4 checks of the migration doc): the
report of a data folder, every refusal with its reason, the folder never written (every file's bytes and the
listing compared), the temporary copy removed, no passenger name in the result. Fictional data only (alice, bob,
the tf.bcbp() defaults).

  python3 -I -B -m unittest discover -s tests/core -p test_adopt.py -v
"""
import hashlib, json, os, shutil, sqlite3, sys, tempfile, types, unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from core_case import CoreCase, USER, eng, te  # noqa: E402
import adopt  # noqa: E402

tf = te.tf
PASSENGER_WORDS = ("ROSSI", "MARIO", "Rossi", "Mario", "rossi", "mario")


def tree_state(folder):
    """{relative path: (kind, sha256 or link target)} of everything below `folder` (links not followed)."""
    out = {}
    for d, dirs, files in os.walk(folder):
        for name in dirs + files:
            p = os.path.join(d, name)
            rel = os.path.relpath(p, folder)
            if os.path.islink(p):
                out[rel] = ("link", os.readlink(p))
            elif os.path.isdir(p):
                out[rel] = ("dir", None)
            else:
                with open(p, "rb") as f:
                    out[rel] = ("file", hashlib.sha256(f.read()).hexdigest())
    return out


def append(path, data):
    with open(path, "ab") as f:
        f.write(data)


class Inspect(CoreCase):
    def setUp(self):
        super().setUp()
        self.made = []
        real = tempfile.mkdtemp

        def mkdtemp(*a, **k):                     # the copies inspect makes (removed in `finally`)
            p = real(*a, dir=te.TEST_DIR, **{x: y for x, y in k.items() if x != "dir"})
            self.made.append(p)
            return p
        patcher = mock.patch.object(adopt, "tempfile", types.SimpleNamespace(mkdtemp=mkdtemp))
        patcher.start()
        self.addCleanup(patcher.stop)

    def populate(self):
        """A v1 data folder as the service leaves it: a Wallet pass and a photo read (alice), a photo still queued,
        one document not notified yet, bob in a flight's gate reminder."""
        self.upload_file(tf.pkpass(tf.bcbp()), "pkpass")
        self.upload_photo([tf.bcbp(seat="014C")], color=(9, 8, 7))
        eng.save_upload(te.tiny_png((4, 5, 6)), "png", USER)              # queued
        with eng._db_lock:
            c = eng.db()
            c.execute("UPDATE documents SET notified=1")
            c.execute("UPDATE documents SET notified=0 WHERE id=1")
            c.execute("UPDATE flights SET snapshot=?", (json.dumps({"gate_sent_to": ["bob"],
                                                                     "walk": {"alice": {"margin_min": 5}}}),))

    def snapshot_folder(self):
        """A copy of the engine's folder taken while its connection is open: tracker.db, its -wal (the rows written
        since the last checkpoint) and the files, as a writer killed at that moment leaves them."""
        dst = os.path.join(tempfile.mkdtemp(dir=te.TEST_DIR), "boarding_pass_tracker")
        shutil.copytree(eng.DATA_DIR, dst, symlinks=True)
        return dst

    def inspect(self, folder):
        before = tree_state(folder)
        r = adopt.inspect(folder)
        self.assertEqual(tree_state(folder), before, "the folder was written")
        for p in self.made:
            self.assertFalse(os.path.exists(p), "the temporary copy was left behind")
        text = json.dumps(r, ensure_ascii=False)
        for w in PASSENGER_WORDS:
            self.assertNotIn(w, text)
        self.assertEqual(set(r), {"state", "reason", "schema", "counts", "files", "queued", "unnotified", "users",
                                  "config_users", "mismatched"})
        return r

    # ------------------------------------------------------------ ok
    def test_a_service_folder_with_its_wal(self):
        self.populate()
        folder = self.snapshot_folder()
        self.assertTrue(os.path.getsize(os.path.join(folder, "tracker.db-wal")) > 0)
        r = self.inspect(folder)
        self.assertEqual((r["state"], r["reason"], r["schema"]), ("ok", None, eng.SCHEMA_VERSION))
        self.assertEqual(r["counts"], {"flights": 1, "documents": 3, "boarding_passes": 1})
        self.assertEqual((r["files"], r["queued"], r["unnotified"], r["mismatched"]), (3, 1, 1, 0))
        self.assertEqual((r["users"], r["config_users"]), (["alice", "bob"], []))
        self.assertEqual(len(self.made), 1)

    def test_an_export_with_its_manifest(self):
        self.populate()
        folder = self.snapshot_folder()
        files = {row["file"]: row["sha256"] for row in eng.q("SELECT file, sha256 FROM documents")}
        with open(os.path.join(folder, "manifest.json"), "w", encoding="utf-8") as f:
            json.dump({"format": 1, "schema": 1, "users": ["alice", "bob"], "config_users": ["bob", "alice", "carol"],
                       "files": files}, f)
        r = self.inspect(folder)
        self.assertEqual((r["state"], r["files"], r["mismatched"]), ("ok", 3, 0))
        self.assertEqual(r["config_users"], ["alice", "bob", "carol"])

    def test_a_manifest_that_is_not_readable_is_ignored(self):
        self.populate()
        folder = self.snapshot_folder()
        for content in ("{not json", "[1, 2]", json.dumps({"files": "x", "config_users": "bob"})):
            with self.subTest(manifest=content):
                with open(os.path.join(folder, "manifest.json"), "w", encoding="utf-8") as f:
                    f.write(content)
                r = self.inspect(folder)
                self.assertEqual((r["state"], r["config_users"], r["mismatched"]), ("ok", [], 0))

    def test_no_database_is_a_fresh_install(self):
        folder = tempfile.mkdtemp(dir=te.TEST_DIR)
        self.assertEqual(self.inspect(folder)["state"], "empty")
        self.assertEqual(self.inspect(os.path.join(folder, "missing"))["state"], "empty")
        open(os.path.join(folder, "tracker.db"), "wb").close()               # an empty file: open_db makes it
        r = self.inspect(folder)
        self.assertEqual((r["state"], r["reason"], r["counts"]["flights"]), ("empty", None, 0))

    def test_a_queued_document_is_shown_not_refused(self):
        eng.save_upload(te.tiny_png((1, 1, 1)), "png", USER)
        r = self.inspect(self.snapshot_folder())
        self.assertEqual((r["state"], r["queued"], r["users"]), ("ok", 1, ["alice"]))

    def test_a_heic_stored_by_the_integration_is_accepted(self):
        self.populate()
        eng.save_upload(b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00mif1heic" + b"\x00" * 64, "heic", USER)
        r = self.inspect(self.snapshot_folder())
        self.assertEqual((r["state"], r["counts"]["documents"], r["files"]), ("ok", 4, 4))

    # ------------------------------------------------------------ refusals (R4)
    def refused(self, folder, reason):
        r = self.inspect(folder)
        self.assertEqual((r["state"], r["reason"]), ("refused", reason))
        return r

    def raw_db(self, version=None, app_id=None, script=""):
        folder = tempfile.mkdtemp(dir=te.TEST_DIR)
        c = sqlite3.connect(os.path.join(folder, "tracker.db"))
        if script:
            c.executescript(script)
        if version is not None:
            c.execute(f"PRAGMA user_version = {version}")
        if app_id is not None:
            c.execute(f"PRAGMA application_id = {app_id}")
        c.commit()
        c.close()
        return folder

    def test_a_v0_file_of_app_casa(self):
        self.refused(self.raw_db(0, 0, te.SCHEMA_V0), "old_database_v0")

    def test_another_applications_file(self):
        self.refused(self.raw_db(1, 0x484C4447, eng.SCHEMA), "foreign_database")
        payments = eng.SCHEMA + "CREATE TABLE payments (id INTEGER PRIMARY KEY); INSERT INTO payments VALUES (1);"
        self.refused(self.raw_db(1, 0, payments), "foreign_database")         # household-ledger before the split

    def test_a_newer_schema(self):
        self.refused(self.raw_db(3, eng.APPLICATION_ID, eng.SCHEMA), "newer_database")
        r = self.refused(self.raw_db(eng.SCHEMA_VERSION + 1, eng.APPLICATION_ID, eng.SCHEMA), "newer_database")
        self.assertEqual(r["schema"], eng.SCHEMA_VERSION + 1)

    def test_a_broken_database(self):
        folder = tempfile.mkdtemp(dir=te.TEST_DIR)
        with open(os.path.join(folder, "tracker.db"), "wb") as f:
            f.write(b"this is not a database at all, only some bytes" * 100)
        self.refused(folder, "broken_database")
        self.refused(self.raw_db(1, eng.APPLICATION_ID, "CREATE TABLE flights (id INTEGER);"), "broken_database")
        os.mkdir(os.path.join(folder, "dir"))
        os.mkdir(os.path.join(folder, "dir", "tracker.db"))
        self.refused(os.path.join(folder, "dir"), "broken_database")         # not a file

    def test_a_wal_without_its_database(self):
        self.populate()
        folder = self.snapshot_folder()
        os.remove(os.path.join(folder, "tracker.db"))
        self.refused(folder, "broken_database")

    def test_a_snapshot_that_is_not_json(self):
        self.populate()
        with eng._db_lock:
            eng.db().execute("UPDATE flights SET snapshot='{broken'")
        self.refused(self.snapshot_folder(), "broken_database")

    def test_a_corrupted_page(self):
        self.populate()
        with eng._db_lock:
            eng.db().execute("PRAGMA wal_checkpoint(TRUNCATE)")
        folder = self.snapshot_folder()
        path = os.path.join(folder, "tracker.db")
        with open(path, "r+b") as f:                    # the second page onwards overwritten: integrity_check fails
            f.seek(4096)
            f.write(b"\xff" * (os.path.getsize(path) - 4096))
        self.refused(folder, "broken_database")

    def test_missing_changed_linked_and_misnamed_files(self):
        self.populate()
        rows = eng.q("SELECT id, file FROM documents ORDER BY id")
        cases = {"missing": lambda f, p: os.remove(p),
                 "changed": lambda f, p: append(p, b"x"),
                 "a link outside": lambda f, p: (os.remove(p), os.symlink(te.__file__, p)),
                 "a link inside": lambda f, p: (shutil.copy(p, os.path.join(f, "copy")), os.remove(p),
                                                os.symlink(os.path.join(f, "copy"), p))}
        for what, change in cases.items():
            with self.subTest(file=what):
                folder = self.snapshot_folder()
                change(folder, os.path.join(folder, rows[1]["file"]))
                r = self.refused(folder, "file_mismatch")
                self.assertEqual((r["mismatched"], r["files"]), (1, 2))
        with eng._db_lock:
            eng.db().execute("UPDATE documents SET file='../outside.pkpass' WHERE id=1")
        r = self.refused(self.snapshot_folder(), "file_mismatch")
        self.assertEqual(r["mismatched"], 1)

    def test_a_manifest_that_disagrees_with_the_row(self):
        self.populate()
        folder = self.snapshot_folder()
        files = {row["file"]: row["sha256"] for row in eng.q("SELECT file, sha256 FROM documents")}
        first = sorted(files)[0]
        files[first] = "0" * 64
        with open(os.path.join(folder, "manifest.json"), "w", encoding="utf-8") as f:
            json.dump({"files": files, "config_users": ["alice"]}, f)
        r = self.refused(folder, "file_mismatch")
        self.assertEqual((r["mismatched"], r["config_users"]), (1, ["alice"]))

    def test_the_copy_is_removed_when_the_check_fails(self):
        self.populate()
        folder = self.snapshot_folder()
        with mock.patch.object(adopt, "_check", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                adopt.inspect(folder)
        self.assertEqual(len(self.made), 1)
        self.assertFalse(os.path.exists(self.made[0]))


class NamesInUse(CoreCase):
    def test_the_names_of_the_open_database(self):
        self.assertEqual(adopt.names_in_use(), [])
        self.upload_file(tf.pkpass(tf.bcbp()), "pkpass")
        with eng._db_lock:
            eng.db().execute("UPDATE flights SET snapshot=?", (json.dumps({"gate_sent_to": ["bob"]}),))
        self.assertEqual(adopt.names_in_use(), ["alice", "bob"])

    def test_a_bad_snapshot_does_not_stop_the_others(self):
        self.upload_file(tf.pkpass(tf.bcbp()), "pkpass")
        with eng._db_lock:
            eng.db().execute("UPDATE flights SET snapshot='[1]'")
        self.assertEqual(adopt.names_in_use(), ["alice"])


if __name__ == "__main__":
    unittest.main()
