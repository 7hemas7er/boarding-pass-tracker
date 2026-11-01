"""PARITY-06 (FIX-repo-1, C12): configure() closes an open connection the way shutdown() does. While a backup of HA
runs, the close must not checkpoint: the backup is copying tracker.db and its -wal, and a checkpoint would rewrite
the main file and drop the -wal under it. A setup that fails after the database opened, retried during a backup,
reached this close (the shell now also shuts the engine down on that failure: T2 test_lifecycle).

  python3 -I -B -m unittest discover -s tests/core -p test_configure_backup.py -v
"""
import hashlib, os, sys, unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from core_case import USER, CoreCase, eng, settings  # noqa: E402


def state(db):
    with open(db, "rb") as f:
        main = hashlib.sha256(f.read()).hexdigest()
    return main, (os.path.getsize(db + "-wal") if os.path.exists(db + "-wal") else None)


class CloseDuringABackup(CoreCase):
    def written_during_a_backup(self):
        folder = self.folder()
        eng.job(eng.open_and_prepare, settings(folder), False)
        eng.backup_begin()                                  # HA's pre-backup hook: TRUNCATE, no autocheckpoint
        self.addCleanup(eng.backup_end)
        eng.save_upload(b"%PDF-1.4 fictional", "pdf", USER)
        db = os.path.join(folder, "tracker.db")
        before = state(db)
        self.assertGreater(before[1], 0)                              # the write is only in the -wal
        return folder, db, before

    def test_configure_keeps_the_wal_as_shutdown_does(self):
        for close in ("shutdown", "configure"):
            with self.subTest(close=close):
                folder, db, before = self.written_during_a_backup()
                if close == "shutdown":
                    eng.shutdown(5)
                else:
                    eng.configure(settings(folder))                   # what open_and_prepare does first on a retry
                self.assertIsNone(eng._db)
                self.assertEqual(state(db), before)                    # tracker.db and its -wal as the backup saw them
                eng.backup_end()

    def test_without_a_backup_the_close_checkpoints_as_before(self):
        folder = self.folder()
        eng.job(eng.open_and_prepare, settings(folder), False)
        eng.save_upload(b"%PDF-1.4 fictional", "pdf", USER)
        db = os.path.join(folder, "tracker.db")
        eng.configure(settings(folder))
        self.assertIsNone(state(db)[1])                               # the last connection closed: WAL folded in


if __name__ == "__main__":
    unittest.main()
