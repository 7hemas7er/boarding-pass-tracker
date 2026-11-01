"""PARITY-02 (FIX-repo-1): the noon and the 04:00 of a flight without times (flights._local_at, F1) are fixed-offset
instants, as the standalone's .astimezone() gave, so «a day after departure with no news» (is_over: reference + 24 h)
is 24 real hours also on the daylight saving weekends. With a ZoneInfo datetime Python adds wall-clock time, and the
flight of the Saturday before a change ended an hour early (March) or late (October): the sensors, the /choices
order, next_check and Flightradar24 moved by an hour, with HA's zone equal to the old process zone.

The results do not depend on the process zone (the suite also runs in three other zones): the comparison with the
standalone's own path (LOCAL_TZ None) runs in a child process with TZ=Europe/Rome.

  python3 -I -B -m unittest discover -s tests/core -p test_dst_reference.py -v
"""
import datetime, json, os, subprocess, sys, unittest
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from core_case import CFG, USER, CoreCase, eng  # noqa: E402
import support  # noqa: E402

UTC = datetime.timezone.utc
ROME = ZoneInfo("Europe/Rome")
fl = eng.flights
H = datetime.timedelta(hours=1)
# the Saturdays before the four DST Sundays of 2026-2027 in Rome, and a summer Saturday
SATURDAYS = ("2026-03-28", "2026-10-24", "2027-03-27", "2027-10-30", "2027-06-12")


def flight(day):
    return {"id": 1, "date": day, "relevant_date": None, "pass_departure": None}


def at(text):
    return datetime.datetime.fromisoformat(text)


class DaylightSaving(CoreCase):
    def setUp(self):
        super().setUp()
        eng.set_time_zone(ROME)
        eng.NO_FLIGHTS = True

    def test_noon_is_a_fixed_offset_instant(self):
        for day, offset in (("2027-03-27", 1), ("2027-03-28", 2), ("2027-10-30", 2), ("2027-10-31", 1)):
            with self.subTest(day=day):
                ref = fl._local_at(datetime.date.fromisoformat(day), 12)
                self.assertIsInstance(ref.tzinfo, datetime.timezone)
                self.assertEqual(ref.utcoffset(), offset * H)
                self.assertEqual((ref + 24 * H) - ref, 24 * H)
                self.assertEqual((ref + 24 * H).astimezone(UTC) - ref.astimezone(UTC), 24 * H)   # real hours

    def test_a_day_after_noon_is_24_real_hours(self):
        # March: noon of the 27th is 11:00Z, over at 11:00Z of the 28th (13:00 summer time), not at 10:00Z
        v = flight("2027-03-27")
        self.assertFalse(fl.is_over(v, None, at("2027-03-28T10:30:00+00:00")))
        self.assertFalse(fl.is_over(v, None, at("2027-03-28T10:59:00+00:00")))
        self.assertTrue(fl.is_over(v, None, at("2027-03-28T11:01:00+00:00")))
        # October: noon of the 30th is 10:00Z, over at 10:00Z of the 31st (11:00 winter time), not at 11:00Z
        v = flight("2027-10-30")
        self.assertFalse(fl.is_over(v, None, at("2027-10-31T09:59:00+00:00")))
        self.assertTrue(fl.is_over(v, None, at("2027-10-31T10:01:00+00:00")))
        self.assertTrue(fl.is_over(v, None, at("2027-10-31T10:30:00+00:00")))
        self.assertIsNone(fl.next_check(v, None, at("2027-10-31T10:30:00+00:00")))
        # no change that weekend: the same as every day
        v = flight("2027-06-12")
        self.assertFalse(fl.is_over(v, None, at("2027-06-13T09:59:00+00:00")))
        self.assertTrue(fl.is_over(v, None, at("2027-06-13T10:01:00+00:00")))

    def test_the_sensors_on_the_dst_sundays(self):
        """dst_sensors.py of the parity review: a typed flight without times on each Saturday, the sensors at 10:30Z
        on the Sunday after. The standalone (TZ=Europe/Rome): 2 upcoming in March; «Nessun volo» in October."""
        for day in ("2027-03-27", "2027-10-30"):
            body = json.dumps({"action": "add", "flight": "FR 1234", "date": day, "origin": "BLQ",
                               "destination": "STN", "passengers": "Alice"}).encode()
            code, reply = eng.change_flight(body, USER, CFG, at(day + "T06:00:00+00:00"))
            self.assertEqual(code, 200, reply)
        march = at("2027-03-28T10:30:00+00:00")
        v = eng.compute_sensors(march.astimezone(ROME).date(), march)
        self.assertEqual(v["upcoming_flights"][0], 2)
        self.assertEqual(v["next_flight"][1]["date"], "2027-03-27")
        october = at("2027-10-31T10:30:00+00:00")
        v = eng.compute_sensors(october.astimezone(ROME).date(), october)
        self.assertEqual((v["next_flight"][0], v["upcoming_flights"][0]), ("Nessun volo", 0))
        self.assertEqual([c["date"] for c in eng.flight_choices(march)["details"].values()][:1], ["2027-03-27"])


CHILD = r"""
import datetime, json, sys
from zoneinfo import ZoneInfo
sys.path.insert(0, sys.argv[1])
import flights as fl
UTC = datetime.timezone.utc
def sides(v, now):
    out = {}
    for name, tz in (("process", None), ("ha", ZoneInfo("Europe/Rome"))):
        fl.LOCAL_TZ = tz
        nc = fl.next_check(v, None, now)
        out[name] = (fl.is_over(v, None, now), fl.reference_time(v, None, now).astimezone(UTC).isoformat(),
                     nc and nc.astimezone(UTC).isoformat())
    return out
steps = differences = 0
for day in json.loads(sys.argv[2]):
    v = {"id": 1, "date": day, "relevant_date": None, "pass_departure": None}
    start = datetime.datetime.fromisoformat(day + "T00:00:00+00:00") - datetime.timedelta(days=1)
    for i in range(4 * 24 * 12):                       # four days, every 5 minutes
        now = start + datetime.timedelta(minutes=5 * i)
        s = sides(v, now)
        steps += 1
        differences += s["process"] != s["ha"]
print(json.dumps({"tz": datetime.datetime(2027, 1, 1, 12).astimezone().strftime("%z"), "steps": steps,
                  "differences": differences}))
"""


class SameAsTheStandalone(unittest.TestCase):
    def test_process_zone_and_ha_zone_agree_on_every_step(self):
        """TZ=Europe/Rome: LOCAL_TZ None (the standalone's .astimezone(), F1 keeps it) and LOCAL_TZ Europe/Rome give
        the same is_over, reference_time and next_check every 5 minutes around the four DST Sundays."""
        env = {k: v for k, v in os.environ.items() if not k.startswith("TRACKER_")}
        env["TZ"] = "Europe/Rome"
        p = subprocess.run([sys.executable, "-I", "-B", "-c", CHILD, support.CORE, json.dumps(SATURDAYS)],
                           env=env, capture_output=True, text=True, timeout=300)
        self.assertEqual(p.returncode, 0, p.stderr)
        out = json.loads(p.stdout)
        self.assertEqual(out["tz"], "+0100")                       # the child really is in Rome's zone
        self.assertEqual((out["steps"], out["differences"]), (len(SATURDAYS) * 4 * 24 * 12, 0))


if __name__ == "__main__":
    unittest.main()
