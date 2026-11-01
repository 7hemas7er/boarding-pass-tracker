"""Stage B, the fixes of workflow 2's reviews (FIX-repo-w2r; INTEGRATION_PLAN.md 4.2, 4.4, 4.6/C12, 5.2.3, 5.2.4):

  - W2-STAGEB-03: the passengers line of GET /choices reads back unchanged when a stored name ends with a class word
    («Maria Prima»): manual.keep_names, in an edit and in the merge of an add;
  - W2-STAGEB-04: a Wallet pass's arrival is stored with the destination's offset (plan 4.2), so the card's
    arrival_time is the local time before AviationStack answers;
  - W2-STAGEB-05: «orari spostati al nuovo giorno» comes after the seats (plan 5.2.4);
  - W2-PLATFORM-02: a v1 file that fails its quick_check is sqlite3.DatabaseError (no retry), every time; a copy that
    fails while the source passes stays OSError (EIO, retried);
  - W2-PLATFORM-04: a backup that begins while the setup opens (and upgrades) the database waits for the open, then
    holds the new connection (TRUNCATE, wal_autocheckpoint 0).

Fictional passengers only (Alice, Bob, Maria Prima, the tf.bcbp() defaults).

  python3 -I -B -m unittest discover -s tests/core -p 'test_stage_b_fixes.py' -v
"""
import datetime, errno, json, os, sqlite3, sys, threading, unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_editing as ted  # noqa: E402  (test_engine first: the environment, then the engine)
from core_case import CoreCase, settings  # noqa: E402

te, tf, eng = ted.te, ted.tf, ted.eng
manual = eng.manual
ALICE, dmy, days, TRACKED = ted.ALICE, ted.dmy, ted.days, ted.TRACKED
TODAY = datetime.date(2026, 11, 1)
V2_LINES = ("  pass_arrival TEXT,              -- v2: ISO instant with the destination's offset: typed or pass "
            "semantics\n  pass_terminal TEXT,             -- v2: departure terminal: typed or pass semantics "
            "(departureTerminal)\n")


def request(**body):
    return manual.parse_request(json.dumps(body).encode(), TODAY)


def row(i, first_name, passenger=None, seat=None, cabin=None, document_id=None):
    return {"id": i, "first_name": first_name, "passenger": passenger or first_name, "seat": seat, "cabin": cabin,
            "document_id": document_id, "uploaded_by": "alice"}


EDIT = {"action": "edit", "ref": "1-0123456789ab"}


# ---------------------------------------------------------------- W2-STAGEB-03 (manual)
class KeepNames(unittest.TestCase):
    def test_the_request_keeps_the_whole_reading(self):
        r = request(**EDIT, passengers="Alice 12A, Maria Prima, Bob economy 14C, Carla 3B business, Dario")
        self.assertEqual(r["passengers"], [("Alice", "12A"), ("Maria", None), ("Bob", "14C"), ("Carla", "3B"),
                                           ("Dario", None)])
        self.assertEqual(r["cabins"], [None, "F", "Y", "C", None])
        # read whole only where a class word was taken; «Carla 3B business» read whole is no passenger
        self.assertEqual(r["whole"], [None, ("Maria Prima", None), ("Bob economy", "14C"), None, None])
        self.assertEqual(request(**EDIT, passengers="Maria Prima 12A")["whole"], [("Maria Prima", "12A")])
        self.assertEqual(request(**EDIT, passengers="maria prima")["whole"], [("Maria Prima", None)])  # title-cased
        self.assertIsNone(request(**EDIT, date="2026-11-09")["whole"])
        self.assertEqual(manual.parse_passengers_cabins("Maria Prima"), ([("Maria", None)], ["F"]))   # unchanged API

    def test_a_stored_name_ending_with_a_class_word_stays_that_name(self):
        rows = [row(1, "Alice", seat="12A"), row(2, "Maria Prima")]
        line = manual.passengers_text(rows)
        self.assertEqual(line, "Alice 12A, Maria Prima")
        req = request(**EDIT, passengers=line)
        got = manual.keep_names(rows, req)
        self.assertEqual((got["passengers"], got["cabins"]), ([("Alice", "12A"), ("Maria Prima", None)], [None, None]))
        self.assertEqual((req["passengers"], req["cabins"]), ([("Alice", "12A"), ("Maria", None)], [None, "F"]))
        self.assertFalse(manual.plan_passengers(rows, got["passengers"], replace=True))      # the line: no change
        self.assertEqual(manual.plan_cabins(rows, got["passengers"], got["cabins"]), [])

    def test_with_a_seat_and_by_the_full_name(self):
        rows = [row(1, "Alice"), row(2, "Maria Prima", seat="3C"),
                row(3, None, passenger="Anna Business", document_id=7)]      # an unnamed pass of ANNA BUSINESS
        got = manual.keep_names(rows, request(**EDIT, passengers="Alice, Maria Prima 3C, Anna Business"))
        self.assertEqual(got["passengers"], [("Alice", None), ("Maria Prima", "3C"), ("Anna Business", None)])
        self.assertEqual(got["cabins"], [None, None, None])

    def test_a_class_word_on_another_name_is_still_a_class(self):
        rows = [row(1, "Alice"), row(2, "Maria")]
        req = request(**EDIT, passengers="Alice business, Maria Prima")
        self.assertIs(manual.keep_names(rows, req), req)                  # nobody is named «Alice Business»
        rows = [row(1, "Maria Prima")]
        got = manual.keep_names(rows, request(**EDIT, passengers="Maria Prima prima"))
        self.assertEqual((got["passengers"], got["cabins"]), ([("Maria Prima", None)], ["F"]))
        self.assertEqual(manual.plan_cabins(rows, got["passengers"], got["cabins"]), [(1, "F")])
        req = request(**EDIT, passengers="Maria Prima")                   # no rows: the declared 5.2.3 reading
        self.assertIs(manual.keep_names([], req), req)
        plain = request(**EDIT, date="2026-11-09")                        # no passengers: nothing to keep
        self.assertIs(manual.keep_names(rows, plain), plain)


# ---------------------------------------------------------------- W2-STAGEB-03 (through the engine)
class RoundTrip(ted.Base):
    def stage_a_name(self, seat=None):
        """Alice 12A and «Maria Prima» typed before stage B (no class word then): the rows stage A left."""
        self.add(passengers="Alice 12A, Maria Prima")
        with eng._db_lock:
            eng.db().execute("UPDATE boarding_passes SET first_name='Maria Prima', passenger='Maria Prima', "
                             "cabin=NULL, seat=? WHERE first_name LIKE 'Maria%'", (seat,))

    def rows(self):
        return [(p["passenger"], p["first_name"], p["seat"], p["cabin"]) for p in self.passes_of(1)]

    def test_the_line_sent_back_changes_only_what_was_changed(self):
        self.stage_a_name()
        d = self.details(1)
        self.assertEqual((d["passengers"], d["classes"]), ("Alice 12A, Maria Prima", ""))
        code, reply = self.edit(1, passengers=d["passengers"].replace("12A", "14C"))   # only Alice's seat
        self.assertEqual((code, reply), (200, {"id": 1, "result": "edited", "message": ted.edited(
            self.day, "1 posto cambiato", 2)}))
        self.assertEqual(self.rows(), [("Alice", "Alice", "14C", None), ("Maria Prima", "Maria Prima", None, None)])
        code, reply = self.edit(1, passengers=self.line(1))                # the line as shown: nothing changes
        self.assertEqual((code, reply["result"]), (200, "unchanged"))

    def test_with_a_seat_and_a_date_change(self):
        self.stage_a_name(seat="3C")
        line = self.line(1)
        self.assertEqual(line, "Alice 12A, Maria Prima 3C")
        new = self.day + days(1)
        code, reply = self.edit(1, date=new.isoformat(), passengers=line)
        self.assertEqual((code, reply["message"]), (200, ted.edited(new, f"data cambiata (prima il {dmy(self.day)})",
                                                                    2)))
        self.assertEqual(self.rows(), [("Alice", "Alice", "12A", None), ("Maria Prima", "Maria Prima", "3C", None)])

    def test_a_class_for_her_is_a_word_after_her_name(self):
        self.stage_a_name()
        code, reply = self.edit(1, passengers="Alice 12A, Maria Prima business")
        self.assertEqual(reply["message"], ted.edited(self.day, "1 classe cambiata", 2))
        self.assertEqual(self.rows()[1], ("Maria Prima", "Maria Prima", None, "C"))
        self.assertEqual(self.details(1)["classes"], "Alice, Maria Prima business")

    def test_the_merge_of_an_add(self):
        self.stage_a_name()
        reply = self.add(passengers="Maria Prima 5D")                      # «Aggiungi volo» of the same flight
        self.assertEqual(reply["message"], ted.merged(self.day, "1 posto cambiato", 2))
        self.assertEqual(self.rows()[1], ("Maria Prima", "Maria Prima", "5D", None))

    def test_a_new_name_with_a_class_word_is_still_read_as_its_class(self):
        """Plan 5.2.3 (declared, WP8 item 2): on a flight without such a passenger the word is the class."""
        self.add(passengers="Alice 12A, Anna Prima")
        self.assertEqual(self.rows()[1], ("Anna", "Anna", None, "F"))


# ---------------------------------------------------------------- W2-STAGEB-05
class ChangeOrder(ted.Base):
    def test_times_moved_after_the_seats(self):
        self.add(passengers="Alice 12A, Bob", departure_time="07:00", arrival_time="09:00")
        new = self.day + days(1)
        code, reply = self.edit(1, date=new.isoformat(), passengers="Alice 14C, Bob")
        self.assertEqual((code, reply["message"]), (200, ted.edited(
            new, f"data cambiata (prima il {dmy(self.day)}), 1 posto cambiato, orari spostati al nuovo giorno", 2)))


# ---------------------------------------------------------------- W2-STAGEB-04
def z(t):
    """«2026-11-03T16:00:00Z»: the instant as a pass may write it, in UTC with a Z."""
    return t.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class PassArrival(ted.Base):
    def departure(self):
        return datetime.datetime.combine(self.day, datetime.time(7, 0), eng.flights.ZoneInfo("Europe/Rome"))

    def upload_pass(self, destination, arrival):
        """A Wallet pass of FR 1234 BLQ -> `destination`, departing 07:00 Bologna, its semantics' arrival `arrival`."""
        sem = {"airlineCode": "FR", "flightNumber": 1234, "departureAirportCode": "BLQ",
               "destinationAirportCode": destination, "currentDepartureDate": self.departure().isoformat(),
               "currentArrivalDate": arrival}
        self.upload(tf.pkpass(tf.bcbp(day=self.julian, destination=destination), semantics=sem))

    def test_an_arrival_in_utc_is_stored_with_the_destinations_offset(self):
        new_york = eng.flights.ZoneInfo("America/New_York")
        arr = self.departure() + datetime.timedelta(hours=9)
        self.upload_pass("JFK", z(arr))
        v = self.row(1)
        self.assertEqual(v["pass_arrival"], arr.astimezone(new_york).isoformat())       # the same instant
        self.assertEqual(datetime.datetime.fromisoformat(v["pass_arrival"]), arr)
        local = arr.astimezone(new_york).strftime("%H:%M")
        self.assertNotEqual(local, arr.astimezone(datetime.timezone.utc).strftime("%H:%M"))
        card = eng.compute_sensors()["next_flight"][1]
        self.assertEqual((card["arrival_time"], card["departure_time"]), (local, "07:00"))
        self.assertEqual(datetime.datetime.fromisoformat(card["arrival"]), arr)
        self.assertEqual(self.details(1)["arrival_time"], local)

    def test_an_arrival_already_in_the_destinations_offset_is_kept_as_written(self):
        arr = (self.departure() + datetime.timedelta(hours=2, minutes=20)).astimezone(
            eng.flights.ZoneInfo("Europe/London"))
        self.upload_pass("STN", arr.isoformat())
        self.assertEqual(self.row(1)["pass_arrival"], arr.isoformat())

    def test_an_airport_without_a_zone_keeps_the_passs_offset(self):
        self.assertIsNone(eng.flights.zone_of("BKA"))
        arr = self.departure() + datetime.timedelta(hours=3, minutes=40)
        self.upload_pass("BKA", z(arr))
        self.assertEqual(self.row(1)["pass_arrival"], arr.astimezone(datetime.timezone.utc).isoformat())


# ---------------------------------------------------------------- W2-PLATFORM-02 and -04: the v1 -> v2 open
def v1_file(folder, rows=1, damaged=False):
    """A v1 file as the standalone leaves it (the core's SCHEMA without the two v2 columns), `rows` typed flights;
    `damaged`: its last page (a flights leaf) overwritten, page 1 and sqlite_master intact (open_db's read-only
    checks pass)."""
    os.makedirs(folder, mode=0o700, exist_ok=True)
    path = os.path.join(folder, "tracker.db")
    c = sqlite3.connect(path, isolation_level=None)
    c.executescript(eng.SCHEMA.replace(V2_LINES, ""))
    c.execute("PRAGMA user_version = 1")
    c.execute(f"PRAGMA application_id = {eng.APPLICATION_ID}")
    for i in range(rows):
        c.execute("INSERT INTO flights (airline, number, date, origin, destination, snapshot, created_at) VALUES "
                  "('FR', ?, '2026-12-20', 'BLQ', 'STN', ?, '2026-11-01T10:00:00+01:00')",
                  (str(1000 + i), "{}" + " " * 200))
    c.close()
    if damaged:
        size = os.path.getsize(path)
        with open(path, "r+b") as f:
            f.seek(size - 4096 + 8)
            f.write(b"\xff" * 200)
    return path


def user_version(path):
    c = sqlite3.connect(path)
    try:
        return c.execute("PRAGMA user_version").fetchone()[0]
    finally:
        c.close()


def backups(path):
    folder = eng.backups_folder(path)
    return sorted(os.listdir(folder)) if os.path.isdir(folder) else []


class BadCheck(sqlite3.Connection):
    """A connection whose quick_check never says ok: the copy before the upgrade «does not check out»."""

    def execute(self, sql, *args):
        if sql.strip().upper() == "PRAGMA QUICK_CHECK":
            return super().execute("SELECT 'row 1 missing from index flights' AS quick_check")
        return super().execute(sql, *args)


class DamagedSource(CoreCase):
    def test_a_v1_file_that_fails_its_check_is_not_retried(self):
        path = v1_file(self.folder(), rows=400, damaged=True)
        for _ in range(3):                                               # the same on every try
            with self.assertRaises(sqlite3.DatabaseError) as e:
                eng.open_db(path)
            self.assertNotIsInstance(e.exception, (sqlite3.OperationalError, OSError))   # the shell: no retry
            self.assertIn("malformed", str(e.exception))
            self.assertEqual(user_version(path), 1)
            self.assertEqual(backups(path), [])                          # no copy, no partial left

    def test_a_copy_that_fails_while_the_source_passes_is_retried(self):
        path = v1_file(self.folder())
        real = sqlite3.connect

        def connect(target, *a, **k):
            return real(target, *a, **({**k, "factory": BadCheck} if str(target).endswith(".partial") else k))
        with mock.patch.object(eng.sqlite3, "connect", connect):
            with self.assertRaises(OSError) as e:
                eng.open_db(path)
        self.assertEqual(e.exception.errno, errno.EIO)
        self.assertEqual((user_version(path), backups(path)), (1, []))
        eng.open_db(path).close()                                       # the next try: upgraded, one copy
        self.assertEqual((user_version(path), len(backups(path))), (2, 1))


class BackupDuringTheOpen(CoreCase):
    def test_backup_begin_waits_for_the_open_then_holds_the_new_connection(self):
        """C12: a backup_begin while open_and_prepare upgrades a v1 file (the upgrade is held) waits for _db_lock;
        then the new connection is checkpointed and gets wal_autocheckpoint 0."""
        folder = self.folder()
        v1_file(folder)
        arrived, go, errors = threading.Event(), threading.Event(), []
        real = eng._migrate_v1

        def held(c, path):
            arrived.set()
            go.wait(20)
            return real(c, path)

        def run(fn, *args):
            try:
                eng.job(fn, *args)
            except BaseException as e:                                     # reported below
                errors.append(e)
        with mock.patch.object(eng, "_migrate_v1", held):
            opening = threading.Thread(target=run, args=(eng.open_and_prepare, settings(folder), False))
            opening.start()
            self.assertTrue(arrived.wait(20))
            beginning = threading.Thread(target=run, args=(eng.backup_begin,))
            beginning.start()
            for _ in range(500):
                if eng._backup_active:
                    break
                threading.Event().wait(0.01)
            self.assertTrue(eng._backup_active)
            beginning.join(0.3)
            self.assertTrue(beginning.is_alive())                          # it waits for the open
            go.set()
            opening.join(20)
            beginning.join(20)
        self.assertEqual(errors, [])
        self.assertEqual(eng.q("PRAGMA user_version", one=True)["user_version"], 2)
        self.assertEqual(eng.q("PRAGMA wal_autocheckpoint", one=True)["wal_autocheckpoint"], 0)
        self.assertEqual(os.path.getsize(os.path.join(folder, "tracker.db-wal")), 0)    # folded in by backup_begin
        eng.backup_end()
        self.assertEqual(eng.q("PRAGMA wal_autocheckpoint", one=True)["wal_autocheckpoint"], 1000)


if __name__ == "__main__":
    unittest.main()
