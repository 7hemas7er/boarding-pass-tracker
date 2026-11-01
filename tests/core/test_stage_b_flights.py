"""Stage B in flights.py and flight_stats.py (INTEGRATION_PLAN.md 3.7 F2-F5, S1, 6.3 BC-B4/BC-B9): the arrival and the
terminal of a Wallet pass, arrival() with the flight's own arrival, the terminal of «go to the gate», and the
duration from the flight's own times. Pure, no database; fictional passengers (tf.bcbp() defaults).

  python3 -I -B -m unittest discover -s tests/core -p 'test_stage_b_flights.py' -v
"""
import datetime, os, sys, unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import support  # noqa: E402,F401  (the ported core on sys.path)
import flight_stats as fst, flights as fl  # noqa: E402
import test_flights as tf  # noqa: E402  (synthetic Wallet passes)

TODAY = datetime.date(2026, 11, 5)
SEM = {"airlineCode": "FR", "flightNumber": 1234, "departureAirportCode": "BLQ", "destinationAirportCode": "STN",
       "originalDepartureDate": "2026-11-06T06:30:00+01:00", "currentArrivalDate": "2026-11-06T07:50:00+00:00",
       "departureTerminal": "1", "departureGate": "A12"}
V = {"airline": "FR", "number": "1234", "date": "2026-11-06", "origin": "BLQ", "destination": "STN",
     "pass_departure": "2026-11-06T06:30:00+01:00", "pass_arrival": "2026-11-06T07:50:00+00:00",
     "pass_terminal": "1", "pass_gate": "B12", "pass_boarding": None, "relevant_date": None}


def leg(sem):
    (t,) = fl.read_boarding_pass(tf.pkpass(tf.bcbp(day=310), sem), TODAY)
    return t


class PassSemantics(unittest.TestCase):
    def test_arrival_and_terminal(self):
        t = leg(SEM)
        self.assertEqual((t["pass_departure"], t["pass_arrival"], t["terminal"], t["pass_gate"]),
                         ("2026-11-06T06:30:00+01:00", "2026-11-06T07:50:00+00:00", "1", "A12"))
        t = leg({**SEM, "currentArrivalDate": None, "originalArrivalDate": "2026-11-06T08:05:00+00:00"})
        self.assertEqual(t["pass_arrival"], "2026-11-06T08:05:00+00:00")
        t = leg({**SEM, "currentArrivalDate": "2026-11-06T09:00:00Z", "originalArrivalDate": "2026-11-06T08:05:00Z"})
        self.assertEqual(t["pass_arrival"], "2026-11-06T09:00:00+00:00")            # current first, normalised

    def test_arrival_kept_only_when_credible(self):
        for arrival in ("2026-11-06T05:35:00+00:00",            # 5 minutes after the departure
                        "2026-11-07T06:00:00+00:00",            # more than a day
                        "2026-11-06T05:00:00+00:00",            # before it
                        "2026-11-06T07:50:00",                  # no offset: no known instant
                        "domani"):
            with self.subTest(arrival=arrival):
                self.assertIsNone(leg({**SEM, "currentArrivalDate": arrival})["pass_arrival"])
        self.assertIsNone(leg({**SEM, "originalDepartureDate": None})["pass_arrival"])   # no departure to measure it
        self.assertIsNone(leg({**SEM, "originalDepartureDate": "2026-11-09T06:30:00+01:00"})["pass_arrival"])
        self.assertIsNone(leg({**SEM, "flightNumber": "999"})["pass_arrival"])           # another flight's

    def test_a_photo_has_none(self):
        (t,) = fl.boarding_pass_from_barcodes([tf.bcbp(day=310)], TODAY)
        self.assertEqual((t["pass_arrival"], t["terminal"], t["pass_departure"]), (None, None, None))


class Arrival(unittest.TestCase):
    def test_aviationstack_first(self):
        s = {"arrival_scheduled": "2026-11-06T07:45:00+00:00"}
        self.assertEqual(fl.arrival(s, V), "2026-11-06T07:45:00+00:00")
        self.assertEqual(fl.arrival({**s, "arrival_estimated": "2026-11-06T08:00:00+00:00"}, V),
                         "2026-11-06T08:00:00+00:00")
        self.assertEqual(fl.arrival(None, V), V["pass_arrival"])
        self.assertEqual(fl.arrival({}, V), V["pass_arrival"])
        self.assertIsNone(fl.arrival(None))
        self.assertIsNone(fl.arrival({"gate": "A1"}))

    def test_next_check_keeps_aviationstacks_arrival(self):
        now = datetime.datetime(2026, 11, 6, 6, 0, tzinfo=datetime.timezone.utc)
        s = {"takeoff": "2026-11-06T05:40:00+00:00", "departure_actual": "2026-11-06T05:35:00+00:00"}   # no arrival
        self.assertEqual(fl.next_check(V, s, now), now + datetime.timedelta(minutes=60))


class GateMessage(unittest.TestCase):
    def test_terminal_fallback(self):
        title, text = fl.gate_message(V, None)
        self.assertEqual(title, "FR 1234: avviati al gate B12")
        self.assertTrue(text.endswith(" Terminal 1."), text)
        title, text = fl.gate_message(V, {"terminal": "3", "gate": "A5", "origin_tz": "Europe/Rome",
                                          "departure_scheduled": "2026-11-06T05:30:00+00:00"})
        self.assertIn("Terminal 3.", text)
        self.assertNotIn("Terminal 1.", text)
        title, text = fl.gate_message({**V, "pass_terminal": None}, None)
        self.assertNotIn("Terminal", text)


class Duration(unittest.TestCase):
    def test_the_fourth_source(self):
        self.assertEqual(fst.duration(None, 1155, V), (140, True))                  # 05:30Z -> 07:50Z
        self.assertEqual(fst.duration(None, 1155), fst.duration(None, 1155, None))  # without v: as before
        self.assertEqual(fst.duration({}, 1155, {**V, "pass_arrival": None}), fst.duration(None, 1155))
        # AviationStack's three come first
        s = {"departure_scheduled": "2026-11-06T05:30:00+00:00", "arrival_scheduled": "2026-11-06T07:40:00+00:00"}
        self.assertEqual(fst.duration(s, 1155, V), (130, True))
        s = {"takeoff": "2026-11-06T05:45:00+00:00", "landing": "2026-11-06T07:30:00+00:00"}
        self.assertEqual(fst.duration(s, 1155, V), (105, False))
        # outside DURATION_RANGE: the distance estimate
        for arrival in ("2026-11-06T05:35:00+00:00", "2026-11-07T07:50:00+00:00"):
            self.assertEqual(fst.duration(None, 1155, {**V, "pass_arrival": arrival}), fst.duration(None, 1155))

    def test_in_the_statistics(self):
        today = datetime.date(2026, 12, 1)
        rows = [{**V, "id": 1, "status": None}]
        stats = fst.compute(rows, [{"flight_id": 1, "first_name": "Alice", "cabin": "C"}], today)
        self.assertEqual((stats["air_time_min"], stats["estimated_durations"], stats["cabins"]["business"]),
                         (140, 1, 1))
        self.assertEqual(stats["list"][0]["duration_min"], 140)


if __name__ == "__main__":
    unittest.main()
