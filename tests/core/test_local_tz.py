"""Home Assistant's time zone instead of the process zone (INTEGRATION_PLAN.md 3.7 E3 and F1, IBC14, N06): «today» of
POST /flight and of the sensors, the upload's month and uploaded_at, the noon and the 04:00 of a flight without
times, and _local_at across the daylight saving changes. The results do not depend on the process zone (the suite
also runs with TZ=Pacific/Kiritimati, Etc/GMT+12 and America/New_York).

  python3 -I -B -m unittest discover -s tests/core -p test_local_tz.py -v
"""
import datetime, json, os, sys, unittest
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from core_case import CFG, USER, CoreCase, eng, te  # noqa: E402

UTC = datetime.timezone.utc
EAST, WEST = ZoneInfo("Pacific/Kiritimati"), ZoneInfo("Etc/GMT+12")     # UTC+14 and UTC-12: 26 hours apart
ROME = ZoneInfo("Europe/Rome")
fl = eng.flights


class LocalZone(CoreCase):
    def test_today_of_a_typed_flight(self):
        # at 11:00Z on 15/11 it is the 16th in Kiritimati and the 14th at UTC-12: a flight of the 14th is past
        # (statistics only) in the east and still followed in the west, whatever the process zone
        now = datetime.datetime(2026, 11, 15, 11, 0, tzinfo=UTC)
        body = json.dumps({"action": "add", "flight": "FR 1234", "date": "2026-11-14", "origin": "BLQ",
                           "destination": "STN", "passengers": "Alice"}).encode()
        out = {}
        for name, tz in (("east", EAST), ("west", WEST)):
            eng.set_time_zone(tz)
            with eng.transaction() as c:
                c.execute("DELETE FROM boarding_passes")
                c.execute("DELETE FROM flights")
            code, reply = eng.change_flight(body, USER, CFG, now)
            self.assertEqual(code, 200, reply)
            out[name] = (reply["message"].endswith(eng.manual.text("past")),
                         eng.q("SELECT next_check FROM flights", one=True)["next_check"])
        self.assertEqual(out["east"], (True, None))
        self.assertEqual(out["west"], (False, eng.utc(now)))
        self.assertEqual(eng._since(now), "2026-11-12")                     # west: the 14th minus two days
        eng.set_time_zone(EAST)
        self.assertEqual(eng._since(now), "2026-11-14")

    def test_the_upload_month_and_uploaded_at(self):
        for n, (tz, offset) in enumerate(((EAST, "+14:00"), (WEST, "-12:00"))):
            with self.subTest(tz=str(tz)):
                eng.set_time_zone(tz)
                before = datetime.datetime.now(tz)
                doc = eng.save_upload(te.tiny_png((n, 1, 2)), "png", USER)[0]
                after = datetime.datetime.now(tz)
                d = self.doc(doc)
                self.assertTrue(d["uploaded_at"].endswith(offset), d["uploaded_at"])
                self.assertIn(d["file"][:12], {f"file/{t:%Y}/{t:%m}" for t in (before, after)})
                self.assertEqual(eng.now_iso()[-6:], offset)

    def test_noon_and_four_in_the_morning_of_the_flight_day(self):
        v = {"id": 1, "date": "2026-11-20", "relevant_date": None, "pass_departure": None}
        far = datetime.datetime(2026, 11, 1, tzinfo=UTC)
        for tz in (EAST, WEST, ROME):
            with self.subTest(tz=str(tz)):
                eng.set_time_zone(tz)
                self.assertEqual(fl.reference_time(v, None, far),
                                 datetime.datetime(2026, 11, 20, 12, tzinfo=tz))
                self.assertEqual(fl.next_check(v, None, far),
                                 datetime.datetime(2026, 11, 20, 4, tzinfo=tz).astimezone(UTC))
        eng.set_time_zone(None)                                            # None: the process zone, as before
        self.assertEqual(fl.reference_time(v, None, far),
                         datetime.datetime.combine(datetime.date(2026, 11, 20), datetime.time(12)).astimezone())
        self.assertIsNone(eng.flights.LOCAL_TZ)

    def test_local_at_across_the_daylight_saving_changes(self):
        eng.set_time_zone(ROME)
        gap = fl._local_at(datetime.date(2027, 3, 28), 2)                  # 02:00 does not exist in Rome that day
        # PARITY-02 (FIX-repo-1): a fixed offset (as .astimezone() gives), not the ZoneInfo, so that + 24 h is real time
        self.assertEqual((type(gap.tzinfo), gap.utcoffset()), (datetime.timezone, datetime.timedelta(hours=1)))
        self.assertEqual(gap.astimezone(UTC), datetime.datetime(2027, 3, 28, 1, tzinfo=UTC))   # the offset before
        self.assertEqual(fl._local_at(datetime.date(2027, 3, 28), 3).astimezone(UTC),
                         datetime.datetime(2027, 3, 28, 1, tzinfo=UTC))
        self.assertEqual(fl._local_at(datetime.date(2027, 3, 28), 4).astimezone(UTC),
                         datetime.datetime(2027, 3, 28, 2, tzinfo=UTC))   # the flight day's 04:00, summer time
        fold = fl._local_at(datetime.date(2027, 10, 31), 2)                # 02:00 twice: the first one
        self.assertEqual(fold.astimezone(UTC), datetime.datetime(2027, 10, 31, 0, tzinfo=UTC))
        self.assertEqual(fl._local_at(datetime.date(2027, 10, 31), 12).astimezone(UTC),
                         datetime.datetime(2027, 10, 31, 11, tzinfo=UTC))  # noon, winter time again

    def test_set_time_zone_moves_both(self):
        eng.set_time_zone(EAST)
        self.assertEqual((eng.LOCAL_TZ, eng.flights.LOCAL_TZ), (EAST, EAST))
        self.configure(local_tz=WEST)
        self.assertEqual((eng.LOCAL_TZ, eng.flights.LOCAL_TZ), (WEST, WEST))


if __name__ == "__main__":
    unittest.main()
