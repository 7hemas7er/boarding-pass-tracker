"""Tests for flight_stats.py: synthetic flights and boarding passes, numbers worked out by hand, no network and no
database.

  cd tracker
  python3 -I -m unittest discover -s . -p 'test_flight_stats.py' -v

The distances come from the coordinates in airports.json (OurAirports), rounded to the km:
AMS-FCO 1297, FCO-JFK 6866, FRA-JFK 6189, MXP-CDG 598, BLQ-STN 1155, LIN-FCO 470, BLQ-FCO 314, LHR-FCO 1444,
LHR-ATH 2427, CDG-GRU 9405.
"""
import datetime, json, os, random, sys, unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import support  # noqa: E402,F401  (the ported core on sys.path; its log lines to the current stdout)
import flight_stats as fst  # noqa: E402

TODAY = datetime.date(2026, 11, 6)
SURNAME = "Fontanella"


def iso(day, hhmm):
    return f"{day}T{hhmm}:00+00:00"


def status(day=None, **fields):
    """The normalized status as stored in flights.status; an "HH:MM" time becomes that time on `day`."""
    s = {"cancelled": False, "diverted": False}
    for k, x in fields.items():
        s[k] = iso(day, x) if isinstance(x, str) and len(x) == 5 and x[2] == ":" else x
    return json.dumps(s)


def flight(id_, code, date, route, status=None, pass_departure=None):
    airline, number = code.split()
    origin, destination = route.split("-")
    return {"id": id_, "airline": airline, "number": number, "date": date, "origin": origin,
            "destination": destination, "pass_departure": pass_departure, "status": status}


def boarding_pass(flight_id, first_name, cabin=None, passenger=None):
    return {"flight_id": flight_id, "first_name": first_name, "cabin": cabin, "uploaded_by": "alice",
            "passenger": passenger or f"{first_name} {SURNAME}"}


def outcome(v, today=TODAY):
    return fst.outcome(v, fst._status(v["status"]), today)


# Six flights: every time, only the scheduled ones, cancelled, diverted, future, past with no data
G1, G2, G4, G5 = "2026-10-20", "2025-06-10", "2026-08-01", "2026-11-15"
FLIGHTS = [
    flight(1, "KL 1234", G1, "AMS-FCO", status(G1, departure_scheduled="10:00", departure_actual="10:20",
                                               takeoff="10:35", landing="12:35", arrival_scheduled="12:20",
                                               arrival_actual="12:45", departure_delay=20, arrival_delay=30)),
    flight(2, "AZ 610", G2, "FCO-JFK", status(G2, departure_scheduled="08:00", arrival_scheduled="17:30",
                                              arrival_actual="17:20", departure_delay=-3, arrival_delay=-10)),
    flight(3, "FR 1234", "2026-09-05", "BLQ-STN", status(cancelled=True, departure_delay=90)),
    flight(4, "LH 400", G4, "FRA-JFK", status(G4, diverted=True, takeoff="10:40", landing="16:40",
                                              arrival_scheduled="17:00", departure_delay=40, arrival_delay=50)),
    flight(5, "KL 1235", G5, "FCO-AMS", status(G5, departure_scheduled="13:00", arrival_scheduled="15:30")),
    flight(6, "U2 123", "2026-03-03", "MXP-CDG"),
]
PASSES = [boarding_pass(1, "Paolo", "J"), boarding_pass(1, "Anna", "C"), boarding_pass(2, "Paolo", "Y"),
          boarding_pass(3, "Paolo", "Y"), boarding_pass(5, "Paolo", "Y"), boarding_pass(6, "Paolo", None)]


def entry(date, code, route, cities, duration, km, delay, outcome, estimated=False, passengers=()):
    return {"date": date, "flight": code, "route": route, "cities": cities, "duration_min": duration, "km": km,
            "delay_min": delay, "outcome": outcome, "estimated": estimated, "passengers": list(passengers)}


# passengers: the names on the boarding passes of each flight, in alphabetical order (cancelled or future flights too)
V1 = entry(G1, "KL 1234", "AMS → FCO", "Amsterdam → Roma Fiumicino", 120, 1297, 25, "flown", False,
           ["Anna", "Paolo"])
V2 = entry(G2, "AZ 610", "FCO → JFK", "Roma Fiumicino → New York JFK", 570, 6866, -10, "flown", True, ["Paolo"])
V3 = entry("2026-09-05", "FR 1234", "BLQ → STN", "Bologna → Londra Stansted", None, 1155, None, "cancelled", False,
           ["Paolo"])
V4 = entry(G4, "LH 400", "FRA → JFK", "Francoforte → New York JFK", 360, 6189, None, "diverted")     # no boarding pass
V5 = entry(G5, "KL 1235", "FCO → AMS", "Roma Fiumicino → Amsterdam", 150, 1297, None, "upcoming", True,
           ["Paolo"])
V6 = entry("2026-03-03", "U2 123", "MXP → CDG", "Milano Malpensa → Parigi Charles de Gaulle", 82, 598, None, "flown",
           True, ["Paolo"])                             # no times: 40 + 598 km at 850 km/h = 82.2 minutes


class Totals(unittest.TestCase):
    """The six flights of FLIGHTS, worked out by hand.
      flown: 1 (120 min in the air), 2 (570 from the scheduled times), 4 diverted (360 in the air), 6 (no data: 82
      estimated from the distance) -> 1132 min;
      km 1297 + 6866 + 6189 + 598 = 14950; departure delays 20 + 0 (-3) + 40 = 60;
      at arrival: 1 -> 12:45 - 12:20 = 25 (not AviationStack's 30), 2 -> -10 (counts as 0), 4 diverted -> unknown;
      average (25 + 0) / 2 = 12.5 -> 13; on time: 2 yes, 1 no, 4 no (diverted), 6 unknown -> 1 out of 3."""

    @classmethod
    def setUpClass(cls):
        cls.s = fst.compute(FLIGHTS, PASSES, TODAY)

    def test_counters(self):
        s = self.s
        self.assertEqual({k: s[k] for k in ("flights", "cancelled", "diverted", "upcoming", "air_time_min",
                                            "air_time_h", "estimated_durations", "no_data", "km", "earth_laps",
                                            "departure_delay_min", "arrival_delay_min", "mean_delay_min",
                                            "on_time_pct", "list_total", "updated_on", "countries_total",
                                            "airports_total")},
                         {"flights": 4, "cancelled": 1, "diverted": 1, "upcoming": 1, "air_time_min": 1132,
                          "air_time_h": 18.9, "estimated_durations": 2, "no_data": 1, "km": 14950, "earth_laps": 0.37,
                          "departure_delay_min": 60, "arrival_delay_min": 25, "mean_delay_min": 13,
                          "on_time_pct": 33, "list_total": 6, "updated_on": "2026-11-06", "countries_total": 5,
                          "airports_total": 6})
        self.assertEqual(fst.sensor_state(s), 4)

    def test_countries_airports_airlines_routes(self):
        s = self.s
        self.assertEqual([(p["code"], p["flights"]) for p in s["countries"]],
                         [("IT", 3), ("US", 2), ("DE", 1), ("FR", 1), ("NL", 1)])     # ties: alphabetical
        self.assertEqual(s["airports"], [{"code": c, "name": n, "flights": k} for c, n, k in (
            ("FCO", "Roma Fiumicino", 2), ("JFK", "New York JFK", 2), ("AMS", "Amsterdam", 1),
            ("CDG", "Parigi Charles de Gaulle", 1), ("FRA", "Francoforte", 1), ("MXP", "Milano Malpensa", 1))])
        self.assertEqual(s["airlines"], [{"code": c, "flights": 1} for c in ("AZ", "KL", "LH", "U2")])
        self.assertEqual(s["routes"], [{"route": t, "flights": 1}
                                      for t in ("AMS → FCO", "FCO → JFK", "FRA → JFK", "MXP → CDG")])

    def test_cabins_and_passengers(self):
        # 1: J and C -> business; 2: Y -> economy; 4: no boarding pass, 6: no cabin on its pass -> unknown
        self.assertEqual(self.s["cabins"], {"business": 1, "economy": 1, "unknown": 2})
        # Paolo: 1, 2 and 6 (3 is cancelled, 5 has not departed yet); two passengers on the same flight 1
        self.assertEqual(self.s["passengers"], [
            {"name": "Paolo", "flights": 3, "km": 1297 + 6866 + 598, "air_time_h": 12.9},
            {"name": "Anna", "flights": 1, "km": 1297, "air_time_h": 2.0}])

    def test_records(self):
        self.assertEqual(self.s["records"], {"longest": V2, "shortest": V6, "max_duration": V2, "max_delay": V1})

    def test_per_person(self):
        # Paolo: flown 1, 2 and 6 (3 cancelled and 5 in the future do not count) -> 120 + 570 + 82 = 772 minutes, 2
        # estimated (2 from the scheduled times, 6 from the distance), 1297 + 6866 + 598 km; delays at arrival 25 and
        # -10 (counts as 0), 6 unknown -> 25, average 12.5 -> 13; on time: 2 yes, 1 no, 6 unknown -> 50.
        # Countries: IT 3 (AMS-FCO, FCO-JFK, MXP-CDG), then FR, NL, US 1 in alphabetical order; airports AMS FCO JFK
        # MXP CDG. Cancelled over all of his flights: 3; the diverted 4 has no boarding pass. Anna: only flight 1,
        # with Paolo
        self.assertEqual(self.s["by_person"], [
            {"name": "Paolo", "flights": 3, "cancelled": 1, "diverted": 0, "air_time_min": 772,
             "estimated_durations": 2, "km": 1297 + 6866 + 598, "arrival_delay_min": 25, "mean_delay_min": 13,
             "on_time_pct": 50, "countries": ["IT", "FR", "NL", "US"], "countries_total": 4, "airports_total": 5,
             "airlines": [{"code": "AZ", "flights": 1}, {"code": "KL", "flights": 1}, {"code": "U2", "flights": 1}],
             "longest": V2},
            {"name": "Anna", "flights": 1, "cancelled": 0, "diverted": 0, "air_time_min": 120, "estimated_durations": 0,
             "km": 1297, "arrival_delay_min": 25, "mean_delay_min": 25, "on_time_pct": 0, "countries": ["IT", "NL"],
             "countries_total": 2, "airports_total": 2, "airlines": [{"code": "KL", "flights": 1}], "longest": V1}])
        self.assertEqual([p["name"] for p in self.s["by_person"]], [p["name"] for p in self.s["passengers"]])

    def test_per_year(self):
        self.assertEqual(self.s["by_year"], [
            {"year": 2026, "flights": 3, "air_time_h": 9.4, "km": 1297 + 6189 + 598, "arrival_delay_min": 25,
             "on_time_pct": 0},
            {"year": 2025, "flights": 1, "air_time_h": 9.5, "km": 6866, "arrival_delay_min": 0, "on_time_pct": 100}])

    def test_list_newest_first(self):
        self.assertEqual(self.s["list"], [V5, V1, V3, V4, V6, V2])

    def test_no_surnames_and_json(self):
        text = json.dumps(self.s, ensure_ascii=False)
        self.assertNotIn(SURNAME, text)
        self.assertNotIn('"passenger"', text)                         # the column; "passengers" are first names
        self.assertEqual(json.loads(text), self.s)                      # serializable as it is

    def test_published_keys(self):
        self.assertEqual(list(self.s), [
            "flights", "cancelled", "diverted", "upcoming", "air_time_min", "air_time_h", "estimated_durations",
            "no_data", "km", "earth_laps", "departure_delay_min", "arrival_delay_min", "mean_delay_min", "on_time_pct",
            "countries", "countries_total", "airports", "airports_total", "airlines", "routes", "cabins",
            "passengers", "by_person", "records", "by_year", "list", "list_total", "updated_on"])
        self.assertEqual(list(self.s["list"][0]), ["date", "flight", "route", "cities", "duration_min", "km",
                                                   "delay_min", "outcome", "estimated", "passengers"])
        self.assertEqual({e["outcome"] for e in self.s["list"]}, {"flown", "diverted", "cancelled", "upcoming"})

    def test_input_order_does_not_matter(self):
        flights, passes = list(FLIGHTS), list(PASSES)
        random.Random(7).shuffle(flights)
        random.Random(8).shuffle(passes)
        self.assertEqual(fst.compute(flights, passes, TODAY), self.s)

    def test_no_flights(self):
        s = fst.compute([], [], TODAY)
        self.assertEqual((s["flights"], s["air_time_h"], s["km"], s["mean_delay_min"], s["on_time_pct"], s["list"],
                          s["records"]["longest"], s["by_year"], s["cabins"], s["by_person"]),
                         (0, 0.0, 0, None, None, [], None, [], {"business": 0, "economy": 0, "unknown": 0}, []))


class Classification(unittest.TestCase):
    def test_departed_or_airborne(self):
        today, yesterday = "2026-11-06", "2026-11-05"
        self.assertEqual(outcome(flight(1, "KL 1", today, "AMS-FCO", status(today, takeoff="10:00"))),
                         ("upcoming", False))
        self.assertEqual(outcome(flight(1, "KL 1", yesterday, "AMS-FCO", status(yesterday, takeoff="22:00"))),
                         ("flown", False))
        self.assertEqual(outcome(flight(1, "KL 1", yesterday, "AMS-FCO", status(yesterday, departure_actual="22:00"))),
                         ("flown", False))

    def test_no_data_from_two_days_ago(self):
        yesterday, two_days_ago = "2026-11-05", "2026-11-04"
        self.assertEqual(outcome(flight(1, "KL 1", yesterday, "AMS-FCO")), ("upcoming", False))
        self.assertEqual(outcome(flight(1, "KL 1", yesterday, "AMS-FCO",
                                        status(yesterday, departure_scheduled="10:00"))),
                         ("upcoming", False))
        self.assertEqual(outcome(flight(1, "KL 1", two_days_ago, "AMS-FCO")), ("flown", True))
        self.assertEqual(outcome(flight(1, "KL 1", two_days_ago, "AMS-FCO",
                                        status(two_days_ago, departure_scheduled="10:00"))),
                         ("flown", True))
        self.assertEqual(outcome(flight(1, "KL 1", "2027-01-05", "AMS-FCO")), ("upcoming", False))

    def test_cancelled_and_diverted_even_in_the_future(self):
        self.assertEqual(outcome(flight(1, "KL 1", "2027-01-05", "AMS-FCO", status(cancelled=True))),
                         ("cancelled", False))
        self.assertEqual(outcome(flight(1, "KL 1", "2026-11-06", "AMS-FCO", status(diverted=True))),
                         ("diverted", False))

    def test_broken_json(self):
        for broken in ("{broken", "[1, 2]", "null", "", '"cancelled"', "[" * 100_000, "{}"):
            self.assertIsNone(fst._status(broken) or None, broken[:10])
            self.assertEqual(outcome(flight(1, "KL 1", "2026-01-10", "AMS-FCO", broken)), ("flown", True))
            self.assertEqual(outcome(flight(1, "KL 1", "2026-12-10", "AMS-FCO", broken)), ("upcoming", False))
        s = fst.compute([flight(1, "KL 1", "2026-01-10", "AMS-FCO", "{broken"),
                         flight(2, "KL 2", "2026-12-10", "AMS-FCO", '{"cancelled": tru')], [], TODAY)
        self.assertEqual((s["flights"], s["no_data"], s["cancelled"], s["upcoming"], s["km"], s["air_time_min"]),
                         (1, 1, 0, 1, 1297, 132))            # estimated from the distance: 40 + 1297/850*60
        self.assertEqual(s["list"][1], entry("2026-01-10", "KL 1", "AMS → FCO", "Amsterdam → Roma Fiumicino",
                                               132, 1297, None, "flown", True))


class Durations(unittest.TestCase):
    G = "2026-10-20"

    def d(self, **fields):
        return fst.duration(json.loads(status(self.G, **fields)))

    def test_source_order(self):
        every = dict(departure_scheduled="10:00", arrival_scheduled="12:20", departure_actual="10:20",
                     arrival_actual="12:45", takeoff="10:35", landing="12:35")
        self.assertEqual(self.d(**every), (120, False))
        no_takeoff = {k: x for k, x in every.items() if k != "takeoff"}
        self.assertEqual(self.d(**no_takeoff), (145, False))                         # gate to gate
        self.assertEqual(self.d(departure_scheduled="10:00", arrival_scheduled="12:20", arrival_actual="12:45"),
                         (140, True))                                                # only the scheduled: estimated

    def test_out_of_range_falls_back_to_the_next_source(self):
        # 5 minutes in the air is not believable: the 90 gate to gate counts
        self.assertEqual(self.d(takeoff="10:00", landing="10:05", departure_actual="09:50",
                                arrival_actual="11:20"), (90, False))
        # in the air backwards, 25 hours gate to gate, 9 minutes scheduled: no duration
        self.assertEqual(self.d(takeoff="10:00", landing="09:30", departure_actual="10:00",
                                arrival_actual=iso("2026-10-21", "11:00"), departure_scheduled="10:00",
                                arrival_scheduled="10:09"), (None, False))
        # the bounds are included
        self.assertEqual(self.d(takeoff="10:00", landing="10:10"), (10, False))
        self.assertEqual(self.d(takeoff="10:00", landing=iso("2026-10-21", "10:00")), (1440, False))
        self.assertEqual(self.d(takeoff="10:00", landing=iso("2026-10-21", "10:01")), (None, False))

    def test_times_without_tz_or_broken_do_not_count(self):
        self.assertEqual(self.d(takeoff="2026-10-20T10:00:00", landing="12:00", departure_scheduled="10:00",
                                arrival_scheduled="12:30"), (150, True))
        self.assertEqual(self.d(takeoff="yesterday", landing=7, departure_actual=None), (None, False))
        self.assertEqual(fst.duration(None), (None, False))

    def test_different_time_zones(self):
        # departs at 10:00 Rome time (08:00Z), arrives at 10:30 London time (09:30Z): 90 minutes
        s = {"takeoff": "2026-10-20T10:00:00+02:00", "landing": "2026-10-20T10:30:00+01:00"}
        self.assertEqual(fst.duration(s), (90, False))

    def test_discarded_in_the_totals(self):
        flights = [flight(1, "KL 1", self.G, "AMS-FCO", status(self.G, departure_scheduled="09:50", takeoff="10:00",
                                                               landing="10:05")),
                   flight(2, "KL 2", self.G, "FCO-AMS", status(self.G, departure_scheduled="13:50", takeoff="14:00",
                                                               landing="16:30"))]
        s = fst.compute(flights, [], TODAY)
        # KL 1 has out-of-range times: they are discarded and the estimate from the distance remains (132)
        self.assertEqual((s["flights"], s["air_time_min"], s["air_time_h"], s["estimated_durations"]), (2, 282, 4.7, 1))
        self.assertEqual([e["duration_min"] for e in s["list"]], [150, 132])      # same day: the latest first
        self.assertEqual(s["records"]["max_duration"]["flight"], "KL 2")


class Delays(unittest.TestCase):
    G = "2026-10-20"

    def test_negatives_count_as_zero(self):
        # at arrival -20, +10, +31 -> sum 41, average (0 + 10 + 31) / 3 = 13.67 -> 14; on time 2 out of 3 -> 67
        flights = [flight(i, f"KL {i}", self.G, "AMS-FCO", status(self.G, arrival_scheduled="12:00",
                                                                  arrival_actual=arr, departure_delay=dd))
                   for i, (arr, dd) in enumerate((("11:40", -5), ("12:10", 0), ("12:31", 25)), 1)]
        s = fst.compute(flights, [], TODAY)
        self.assertEqual((s["arrival_delay_min"], s["mean_delay_min"], s["on_time_pct"], s["departure_delay_min"]),
                         (41, 14, 67, 25))
        self.assertEqual(sorted(e["delay_min"] for e in s["list"]), [-20, 10, 31])
        self.assertEqual(s["records"]["max_delay"]["delay_min"], 31)

    def test_no_positive_delay_no_record(self):
        s = fst.compute([flight(1, "KL 1", self.G, "AMS-FCO", status(self.G, arrival_delay=-4))], [], TODAY)
        self.assertEqual((s["arrival_delay_min"], s["mean_delay_min"], s["on_time_pct"], s["records"]["max_delay"]),
                         (0, 0, 100, None))

    def test_sources_of_the_arrival_delay(self):
        r = lambda **f: fst.arrival_delay(json.loads(status(self.G, **f)))
        self.assertEqual(r(arrival_scheduled="12:00", landing="12:20"), 20)                  # no arrival at the gate
        self.assertEqual(r(arrival_scheduled="12:00", arrival_actual="12:30", landing="12:20"), 30)
        self.assertEqual(r(arrival_scheduled="12:00", arrival_actual="broken", landing="12:20"), 20)
        self.assertEqual(r(arrival_actual="12:30", arrival_delay=17), 17)                    # no scheduled time
        self.assertEqual(r(arrival_delay=10.4), 10)
        for bad in (True, "20", 1e9, float("nan"), 10 ** 400, None):
            self.assertIsNone(fst.arrival_delay({"arrival_delay": bad}), bad)
            self.assertIsNone(fst.departure_delay({"departure_delay": bad}), bad)

    def test_on_time_from_the_departure_if_the_arrival_is_unknown(self):
        flights = [flight(1, "KL 1", self.G, "AMS-FCO", status(self.G, landing="12:00", departure_delay=14)),
                   flight(2, "KL 2", self.G, "AMS-FCO", status(self.G, landing="12:00", departure_delay=15)),
                   flight(3, "KL 3", self.G, "AMS-FCO", status(self.G, landing="12:00"))]       # unknown: not counted
        s = fst.compute(flights, [], TODAY)
        self.assertEqual((s["on_time_pct"], s["departure_delay_min"], s["arrival_delay_min"], s["mean_delay_min"]),
                         (50, 29, 0, None))

    def test_cancelled_does_not_count(self):
        s = fst.compute([flight(1, "KL 1", self.G, "AMS-FCO", status(cancelled=True, departure_delay=120,
                                                                     arrival_delay=120))], [], TODAY)
        self.assertEqual((s["flights"], s["departure_delay_min"], s["arrival_delay_min"], s["on_time_pct"]),
                         (0, 0, 0, None))


class Cabins(unittest.TestCase):
    def test_business_economy_unknown(self):
        self.assertEqual(fst.cabin(["J", "C"]), "business")
        self.assertEqual(fst.cabin(["j", None]), "business")         # passes without a cabin do not decide
        self.assertEqual(fst.cabin(["J", "Y"]), "economy")             # one in economy: economy
        self.assertEqual(fst.cabin(["Y"]), "economy")
        self.assertEqual(fst.cabin([None, "", " "]), "unknown")
        self.assertEqual(fst.cabin([]), "unknown")
        g = "2026-10-01"
        flights = [flight(i, f"KL {i}", g, "AMS-FCO", status(g, landing="12:00")) for i in range(1, 5)]
        passes = [boarding_pass(1, "Paolo", "F"), boarding_pass(1, "Anna", "J"), boarding_pass(2, "Paolo", "J"),
                  boarding_pass(2, "Anna", "M"), boarding_pass(3, "Paolo")]
        self.assertEqual(fst.compute(flights, passes, TODAY)["cabins"],
                         {"business": 1, "economy": 1, "unknown": 2})


class Passengers(unittest.TestCase):
    def test_names_cleaned(self):
        self.assertEqual(fst.clean_name("  Anna-Maria  "), "Anna-Maria")
        self.assertEqual(fst.clean_name("D\u2019Angelo"), "D'Angelo")
        self.assertEqual(fst.clean_name("Eve<b>1</b>"), "Evebb")
        self.assertEqual(fst.clean_name("\u202eLuca\u200b"), "Luca")
        self.assertEqual(fst.clean_name("Jose\u0301"), "José")                  # decomposed: the accent stays
        self.assertEqual(fst.clean_name("Maria   Anna"), "Maria Anna")
        self.assertEqual(fst.clean_name("A" * 40), "A" * 30)
        for empty in (None, "", "123", "--", 7):
            self.assertIsNone(fst.clean_name(empty), empty)

    def test_by_first_name(self):
        g = "2026-10-01"
        flights = [flight(1, "KL 1", g, "AMS-FCO", status(g, takeoff="10:00", landing="12:00")),
                   flight(2, "KL 2", g, "BLQ-FCO", status(g, takeoff="14:00", landing="15:00"))]
        passes = [boarding_pass(1, "Paolo", "Y", f"Paolo {SURNAME}"),                                  # two Paolos
                  boarding_pass(1, "Paolo", "Y", "Paolo Bianchi"),
                  boarding_pass(1, "Zoe", "Y"), boarding_pass(2, "Zoe", "Y"), boarding_pass(2, "Ada", "Y"),
                  boarding_pass(2, None, "Y", "Ugo Neri"), boarding_pass(2, "  ", "Y", "Ignoto Neri")]
        s = fst.compute(flights, passes, TODAY)
        self.assertEqual(s["passengers"], [{"name": "Zoe", "flights": 2, "km": 1297 + 314, "air_time_h": 3.0},
                                           {"name": "Ada", "flights": 1, "km": 314, "air_time_h": 1.0},   # tie: by name
                                           {"name": "Paolo", "flights": 1, "km": 1297, "air_time_h": 2.0}])
        text = json.dumps(s, ensure_ascii=False)
        for forbidden in (SURNAME, "Bianchi", "Neri", "Ugo", "Ignoto"):
            self.assertNotIn(forbidden, text)

    def test_at_most_ten(self):
        g = "2026-10-01"
        flights = [flight(1, "KL 1", g, "AMS-FCO", status(g, landing="12:00"))]
        names = [f"Name{c}" for c in "ABCDEFGHIJKL"]
        s = fst.compute(flights, [boarding_pass(1, n) for n in reversed(names)], TODAY)
        self.assertEqual([p["name"] for p in s["passengers"]], names[:10])
        self.assertEqual([p["name"] for p in s["by_person"]], names[:10])
        self.assertEqual(s["list"][0]["passengers"], names)                # the entry has all of them, in order


class PerPerson(unittest.TestCase):
    """by_person: each person's totals over only the flown flights they were on, worked out by hand.
      1 AZ 1  LIN-FCO 470 km, Zoe and Ada: 70 minutes in the air, arrival 11:20 instead of 11:00 -> +20, not on time
      2 AZ 2  FCO-JFK 6866 km, Zoe: 540 in the air, arrival -10 (counts as 0), on time
      3 FR 3  BLQ-STN 1155 km, Zoe: no data, 40 + 1155/850*60 = 121.5 -> 122 estimated, on time unknown
      4 LH 4  FRA-JFK 6189 km, Zoe: 480 in the air, arrival unknown, departed 5 minutes late -> on time
      5 KL 5  AMS-FCO cancelled, Zoe and Bea: counts for nobody (Bea has no other flight: she is not there)
      6 KL 6  FCO-AMS in December, Ada: has not departed yet, does not count
      7 KL 7  MXP-CDG 598 km, Zoe: 90 in the air, no known delay"""

    @classmethod
    def setUpClass(cls):
        g1, g2 = "2026-10-01", "2026-10-05"
        cls.flights = [
            flight(1, "AZ 1", g1, "LIN-FCO", status(g1, takeoff="10:00", landing="11:10", arrival_scheduled="11:00",
                                                    arrival_actual="11:20")),
            flight(2, "AZ 2", g2, "FCO-JFK", status(g2, takeoff="10:00", landing="19:00", arrival_scheduled="19:30",
                                                    arrival_actual="19:20")),
            flight(3, "FR 3", "2026-09-01", "BLQ-STN"),
            flight(4, "LH 4", "2026-08-01", "FRA-JFK", status("2026-08-01", takeoff="10:00", landing="18:00",
                                                              departure_delay=5)),
            flight(5, "KL 5", "2026-07-01", "AMS-FCO", status(cancelled=True, departure_delay=60)),
            flight(6, "KL 6", "2026-12-01", "FCO-AMS"),
            flight(7, "KL 7", "2026-06-01", "MXP-CDG", status("2026-06-01", takeoff="10:00", landing="11:30"))]
        cls.passes = [boarding_pass(1, "Zoe", "Y"), boarding_pass(1, "Ada", "Y"), boarding_pass(2, "Zoe"),
                      boarding_pass(3, "Zoe"), boarding_pass(4, "Zoe"), boarding_pass(5, "Zoe"),
                      boarding_pass(5, "Bea"), boarding_pass(6, "Ada"), boarding_pass(7, "Zoe")]
        cls.s = fst.compute(cls.flights, cls.passes, TODAY)
        cls.shared = entry("2026-10-01", "AZ 1", "LIN → FCO", "Milano Linate → Roma Fiumicino", 70, 470, 20, "flown",
                           False, ["Ada", "Zoe"])
        cls.longest = entry("2026-10-05", "AZ 2", "FCO → JFK", "Roma Fiumicino → New York JFK", 540, 6866, -10,
                            "flown", False, ["Zoe"])

    def test_two_people_on_the_same_flight(self):
        zoe, ada = self.s["by_person"]
        # Zoe: 1, 2, 3, 4, 7. Countries: IT 4 (the domestic LIN-FCO once), US 2, then DE, FR, GB. Airlines: AZ 2 and,
        # of the three with one flight, the first two in alphabetical order (LH is left out). Cancelled: 5 (not among
        # the flown ones, but it counts as in the totals); Ada has 6 still to fly, which does not count
        self.assertEqual(zoe, {
            "name": "Zoe", "flights": 5, "cancelled": 1, "diverted": 0, "air_time_min": 70 + 540 + 122 + 480 + 90,
            "estimated_durations": 1, "km": 470 + 6866 + 1155 + 6189 + 598, "arrival_delay_min": 20,
            "mean_delay_min": 10, "on_time_pct": 67, "countries": ["IT", "US", "DE", "FR", "GB"], "countries_total": 5,
            "airports_total": 8,
            "airlines": [{"code": "AZ", "flights": 2}, {"code": "FR", "flights": 1}, {"code": "KL", "flights": 1}],
            "longest": self.longest})
        self.assertEqual(ada, {
            "name": "Ada", "flights": 1, "cancelled": 0, "diverted": 0, "air_time_min": 70, "estimated_durations": 0,
            "km": 470, "arrival_delay_min": 20, "mean_delay_min": 20, "on_time_pct": 0, "countries": ["IT"],
            "countries_total": 1, "airports_total": 2, "airlines": [{"code": "AZ", "flights": 1}],
            "longest": self.shared})
        # in the totals the shared flight counts once, per person once each
        self.assertEqual((self.s["flights"], self.s["km"]), (5, 470 + 6866 + 1155 + 6189 + 598))
        self.assertEqual(sum(p["flights"] for p in self.s["by_person"]), 6)

    def test_someone_with_only_a_cancelled_flight_is_not_there(self):
        self.assertEqual([p["name"] for p in self.s["by_person"]], ["Zoe", "Ada"])
        self.assertEqual([p["name"] for p in self.s["passengers"]], ["Zoe", "Ada"])
        # but the entries of the cancelled and future flights have their passengers, for the card's filter
        by_code = {e["flight"]: e["passengers"] for e in self.s["list"]}
        self.assertEqual(by_code, {"KL 6": ["Ada"], "AZ 2": ["Zoe"], "AZ 1": ["Ada", "Zoe"], "FR 3": ["Zoe"],
                                   "LH 4": ["Zoe"], "KL 5": ["Bea", "Zoe"], "KL 7": ["Zoe"]})
        self.assertEqual(self.s["records"]["shortest"], self.shared)

    def test_input_order_does_not_matter(self):
        flights, passes = list(self.flights), list(self.passes)
        random.Random(3).shuffle(flights)
        random.Random(4).shuffle(passes)
        self.assertEqual(fst.compute(flights, passes, TODAY), self.s)

    def test_cancelled_and_diverted_even_outside_the_list(self):
        # Bea: a flown and a diverted flight, recent, and a cancelled one from 2023 that stays out of the 30 entries
        # of the list (the others are 30 flights of Ugo's from 2025): the card cannot count it from the list,
        # by_person can
        g = "2026-10-01"
        flights = [flight(1, "KL 1", g, "AMS-FCO", status(g, takeoff="10:00", landing="12:00")),
                   flight(2, "LH 2", "2026-09-01", "FRA-JFK", status("2026-09-01", diverted=True)),
                   flight(3, "FR 3", "2023-01-01", "BLQ-STN", status(cancelled=True))]
        flights += [flight(10 + i, f"AZ {i}", f"2025-01-{1 + i:02d}", "LIN-FCO") for i in range(30)]
        passes = [boarding_pass(1, "Bea"), boarding_pass(2, "Bea"), boarding_pass(3, "Bea")]
        passes += [boarding_pass(10 + i, "Ugo") for i in range(30)]
        s = fst.compute(flights, passes, TODAY)
        self.assertEqual((s["cancelled"], s["diverted"], len(s["list"]), s["list_total"]), (1, 1, 30, 33))
        self.assertNotIn("FR 3", [e["flight"] for e in s["list"]])
        self.assertEqual([(p["name"], p["flights"], p["cancelled"], p["diverted"]) for p in s["by_person"]],
                         [("Ugo", 30, 0, 0), ("Bea", 2, 1, 1)])

    def test_ties_alphabetical_and_namesakes(self):
        g = "2026-10-01"
        flights = [flight(1, "KL 1", g, "AMS-FCO", status(g, takeoff="10:00", landing="12:00"))]
        passes = [boarding_pass(1, "Ugo", "Y", "Ugo Rossi"), boarding_pass(1, "Ugo", "Y", "Ugo Bianchi"),
                  boarding_pass(1, "Ada", "Y")]
        s = fst.compute(flights, passes, TODAY)
        self.assertEqual([(p["name"], p["flights"], p["km"]) for p in s["by_person"]],
                         [("Ada", 1, 1297), ("Ugo", 1, 1297)])
        self.assertEqual(s["list"][0]["passengers"], ["Ada", "Ugo"])         # two Ugos: one name only


class Ordering(unittest.TestCase):
    def test_flags(self):
        self.assertEqual(fst.flag("NL"), "\U0001F1F3\U0001F1F1")
        self.assertEqual(fst.flag("IT"), "\U0001F1EE\U0001F1F9")
        self.assertEqual(fst.flag("US"), "🇺🇸")
        s = fst.compute(FLIGHTS, PASSES, TODAY)
        self.assertEqual({p["code"]: p["flag"] for p in s["countries"]},
                         {"IT": "🇮🇹", "US": "🇺🇸", "DE": "🇩🇪", "FR": "🇫🇷", "NL": "🇳🇱"})

    def test_domestic_flight_counts_once_and_ties(self):
        g = "2026-10-01"
        flights = [flight(1, "AZ 2", g, "LIN-FCO", status(g, landing="09:00")),
                   flight(2, "AZ 1", g, "FCO-LIN", status(g, landing="19:00")),
                   flight(3, "FR 9", g, "BLQ-STN", status(g, landing="12:00")),
                   flight(4, "BA 7", g, "STN-BLQ", status(g, landing="16:00"))]
        s = fst.compute(flights, [], TODAY)
        self.assertEqual([(p["code"], p["flights"]) for p in s["countries"]], [("IT", 4), ("GB", 2)])
        self.assertEqual([(a["code"], a["flights"]) for a in s["airports"]],
                         [("BLQ", 2), ("FCO", 2), ("LIN", 2), ("STN", 2)])
        self.assertEqual(s["airlines"], [{"code": "AZ", "flights": 2}, {"code": "BA", "flights": 1},
                                          {"code": "FR", "flights": 1}])
        self.assertEqual([r["route"] for r in s["routes"]], ["BLQ → STN", "FCO → LIN", "LIN → FCO", "STN → BLQ"])
        # same day, no departure time: flight code in alphabetical order; records with the same km: first in the list
        self.assertEqual([e["flight"] for e in s["list"]], ["AZ 1", "AZ 2", "BA 7", "FR 9"])
        self.assertEqual((s["records"]["longest"]["flight"], s["records"]["shortest"]["flight"]), ("BA 7", "AZ 1"))

    def test_same_day_latest_departure_first(self):
        g = "2026-10-01"
        flights = [flight(1, "AZ 1", g, "LIN-FCO", status(g, departure_actual="07:00", landing="08:10")),
                   flight(2, "AZ 9", g, "FCO-JFK", status(g, departure_actual="10:00", landing="19:00")),
                   flight(3, "ZZ 1", g, "BLQ-FCO", pass_departure="2026-10-01T06:00:00+02:00")]   # pass only: 04:00Z
        self.assertEqual([e["flight"] for e in fst.compute(flights, [], TODAY)["list"]], ["AZ 9", "AZ 1", "ZZ 1"])

    def test_airport_not_in_the_table_uses_the_aviationstack_name(self):
        g = "2026-10-01"
        long_name = "Van Ferit Melen " * 4                                  # 64 characters: 30 are kept
        v = flight(1, "XQ 1", g, "GZT-VAN", status(g, landing="12:00", origin_city="Gaziantep",
                                                   destination_city=long_name))
        s = fst.compute([v], [], TODAY)
        self.assertEqual([a["name"] for a in s["airports"]], ["Gaziantep", long_name[:30]])
        self.assertEqual(s["list"][0]["cities"], "Gaziantep → " + long_name[:30])
        self.assertEqual(s["countries"], [{"code": "TR", "flag": "🇹🇷", "flights": 1}])
        unknown = fst.compute([flight(1, "XQ 1", g, "ZZZ-AMS", status(g, landing="12:00"))], [], TODAY)
        self.assertEqual((unknown["km"], unknown["list"][0]["km"], unknown["list"][0]["cities"]),
                         (0, None, "ZZZ → Amsterdam"))


class FlightList(unittest.TestCase):
    def test_thirty_entries_and_total(self):
        start = datetime.date(2026, 1, 1)
        flights = [flight(i, f"KL {i}", (start + datetime.timedelta(days=i)).isoformat(), "AMS-FCO")
                   for i in range(35)]
        s = fst.compute(flights, [], TODAY)
        self.assertEqual((len(s["list"]), s["list_total"]), (30, 35))
        self.assertEqual([e["date"] for e in s["list"][:2]], ["2026-02-04", "2026-02-03"])
        self.assertEqual(s["list"][-1]["date"], "2026-01-06")              # the 5 oldest are left out
        self.assertEqual(s["flights"], 35)                                        # but they count in the totals


def synthetic(n=500, worst_case=False):
    """n flights, all between different airports, over several years, with every time and three passengers.
    `worst_case`: the AviationStack names are 30 two-byte characters, every flight has a different year and there
    are 12 passengers."""
    with open(os.path.join(support.CORE, "airports.json"), encoding="utf-8") as f:
        codes = sorted(k for k in json.load(f) if len(k) == 3)
    step = len(codes) // (2 * n)
    flights, passes = [], []
    for i in range(n):
        origin, destination = codes[2 * i * step], codes[(2 * i + 1) * step]
        year = 1980 + i % 47 if worst_case else 2018 + i % 9
        g = f"{year}-{1 + i % 12:02d}-{1 + i % 28:02d}"
        extra = {"origin_city": "Ł" * 30, "destination_city": "Ž" * 30} if worst_case else {}
        flights.append(flight(i + 1, f"{'ABCDEFGH'[i % 8]}{'XYZ'[i % 3]} {1000 + i}", g, f"{origin}-{destination}",
                              status(g, departure_scheduled="10:00", departure_actual="10:20", takeoff="10:35",
                                     landing="13:35", arrival_scheduled="13:00", arrival_actual="13:45",
                                     departure_delay=i % 60, gate="A12", **extra)))
        names = [f"{'Ś' * 25}{c}" for c in "ABCDEFGHIJKL"] if worst_case else ["Paolo", "Anna", "Luca"]
        passes += [boarding_pass(i + 1, name, "JY"[i % 2]) for name in names]
    return flights, passes


TEN = ["Ada", "Bea", "Cleo", "Dina", "Ella", "Fede", "Gaia", "Ilia", "Lea", "Mara"]


def ten_people(n, long_cities=False):
    """The flights of synthetic(n) with ten passengers: TEN[k] is on the flights i (from 0) with i % (k + 1) == 0."""
    flights, _ = synthetic(n)
    if long_cities:
        for v in flights:
            v["status"] = json.dumps({**json.loads(v["status"]), "origin_city": "Ł" * 30, "destination_city": "Ž" * 30})
    return flights, [boarding_pass(v["id"], name, "Y") for i, v in enumerate(flights) for k, name in enumerate(TEN)
                     if i % (k + 1) == 0]


class Size(unittest.TestCase):
    def size(self, s):
        return len(json.dumps(s, ensure_ascii=False).encode("utf-8"))

    def test_five_hundred_flights(self):
        flights, passes = synthetic()
        s = fst.compute(flights, passes, TODAY)
        self.assertLess(self.size(s), 12_000)
        # with the passengers in every entry and by_person, the list goes down from 30 to 20 entries (21 with the
        # Italian keys of household-ledger); by_person stays whole
        self.assertEqual((s["flights"], s["list_total"], len(s["list"]), len(s["airports"]), len(s["airlines"]),
                          len(s["routes"]), len(s["passengers"]), len(s["by_year"]), s["airports_total"]),
                         (500, 500, 20, 8, 8, 5, 3, 9, 1000))
        # the list of countries stops at 30, countries_total does not: all three are on every flight
        self.assertEqual([(p["name"], p["flights"], len(p["countries"]), p["countries_total"], len(p["airlines"]))
                          for p in s["by_person"]],
                         [(n, 500, 30, s["countries_total"], 3) for n in ("Anna", "Luca", "Paolo")])
        self.assertEqual(s["air_time_min"], 500 * 180)
        self.assertLessEqual(len(s["countries"]), 30)
        self.assertGreater(s["countries_total"], 30)
        self.assertNotIn(SURNAME, json.dumps(s, ensure_ascii=False))

    def test_worst_case_shortens_the_list(self):
        flights, passes = synthetic(worst_case=True)
        s = fst.compute(flights, passes, TODAY)
        self.assertLess(self.size(s), 12_000)
        self.assertEqual((s["flights"], s["list_total"], len(s["passengers"])), (500, 500, 10))
        # the list is shortened first (down to 10 entries), then the countries, then the oldest years, then
        # by_person until it is empty; only then the list below 10 entries: with 12 names of 51 bytes each entry
        # weighs almost 1 KB
        self.assertEqual((len(s["countries"]), len(s["by_year"]), s["by_person"]), (10, 5, []))
        self.assertTrue(1 <= len(s["list"]) < 10, len(s["list"]))
        self.assertEqual([e["date"][:4] for e in s["list"][:1]], ["2026"])
        self.assertEqual([x["year"] for x in s["by_year"]], [2026, 2025, 2024, 2023, 2022])
        self.assertEqual(len(s["list"][0]["passengers"]), 12)

    def test_cuts_are_published_lists(self):
        # the order and the minimums of household-ledger, with the English paths (the contract)
        self.assertEqual(fst.CUTS, (("list", 10), ("countries", 10), ("by_year", 5), ("by_person.airlines", 0),
                                    ("by_person.countries", 0), ("by_person", 0), ("list", 0), ("countries", 0),
                                    ("by_year", 0), ("passengers", 0), ("airports", 0), ("airlines", 0),
                                    ("routes", 0)))
        s = fst.compute(*ten_people(20), TODAY)
        for key, _ in fst.CUTS:
            parent, _, sub = key.partition(".")
            self.assertIsInstance(s[parent], list, key)
            for x in s[parent] if sub else []:
                self.assertIsInstance(x[sub], list, key)

    def uncut(self, flights, passes):
        """The output with no size limit, to compare it with the shortened one."""
        with mock.patch.object(fst, "MAX_BYTES", 10 ** 9):
            return fst.compute(flights, passes, TODAY)

    def test_ten_people_airlines_first_then_countries(self):
        # 200 flights; person k (0-9) is on the flights i with i % (k + 1) == 0: ceil(200 / (k + 1)) flights of
        # 180 minutes
        flights, passes = ten_people(200)
        s, uncut = fst.compute(flights, passes, TODAY), self.uncut(flights, passes)
        self.assertGreater(self.size(uncut), 12_000)
        self.assertLess(self.size(s), 12_000)
        self.assertEqual((len(s["list"]), len(s["countries"]), len(s["by_year"])), (10, 10, 5))   # the earlier cuts
        flights_k = [-(-200 // (k + 1)) for k in range(10)]
        self.assertEqual(flights_k, [200, 100, 67, 50, 40, 34, 29, 25, 23, 20])
        self.assertEqual([(p["name"], p["flights"], p["air_time_min"]) for p in s["by_person"]],
                         [(n, v, v * 180) for n, v in zip(TEN, flights_k)])
        removed = 0
        for p, q in zip(s["by_person"], uncut["by_person"]):
            self.assertEqual(p["airlines"], [])                                     # the airlines first
            self.assertEqual(p["countries"], q["countries"][:len(p["countries"])])  # then the countries, tail first
            self.assertEqual({k: x for k, x in p.items() if k not in ("airlines", "countries")},
                             {k: x for k, x in q.items() if k not in ("airlines", "countries")})
            removed += len(q["countries"]) - len(p["countries"])
        self.assertGreater(removed, 0)
        # countries_total is not shortened with the list: Ada is on every flight, so she has every country
        self.assertEqual(s["by_person"][0]["countries_total"], s["countries_total"])
        self.assertGreater(s["countries_total"], len(s["by_person"][0]["countries"]))
        self.assertEqual([p["countries_total"] for p in s["by_person"]],
                         [q["countries_total"] for q in uncut["by_person"]])
        # entries come off the longest list: the shortened ones differ by one at most, the shorter at the tail
        short = [len(p["countries"]) for p, q in zip(s["by_person"], uncut["by_person"])
                 if p["countries"] != q["countries"]]
        self.assertLessEqual(max(short) - min(short), 1)
        self.assertEqual(short, sorted(short, reverse=True))
        self.assertTrue(all(len(p["countries"]) <= max(short) for p in s["by_person"]))

    def test_ten_people_then_people_from_the_tail(self):
        # as above, with long AviationStack city names: every entry (and every longest) weighs more
        flights, passes = ten_people(200, long_cities=True)
        s, uncut = fst.compute(flights, passes, TODAY), self.uncut(flights, passes)
        self.assertLess(self.size(s), 12_000)
        self.assertEqual(len(s["list"]), 10)                        # the list stays at 10 entries
        n = len(s["by_person"])
        self.assertTrue(1 <= n < 10, n)
        self.assertEqual([p["name"] for p in s["by_person"]], TEN[:n])               # the first ones stay
        for p, q in zip(s["by_person"], uncut["by_person"]):
            self.assertEqual((p["countries"], p["airlines"]), ([], []))
            self.assertEqual(p["longest"], q["longest"])
            self.assertEqual(p["flights"], q["flights"])
        self.assertEqual(len(s["passengers"]), 10)                    # the passengers ranking is not touched


class DistanceEstimate(unittest.TestCase):
    """A flight with no times does not count zero minutes next to its km."""

    def test_formula_and_comparison_with_typical_block_times(self):
        # (route, km, block): block is an illustrative typical scheduled block time (gate to gate) for the route, in
        # minutes, not a measurement. The estimate stays within 12%: 122 vs 130, 142 vs 150, 211 vs 220, 477 vs 520
        # (westbound against the jet stream: the estimate ignores the wind), 525 vs 520, 704 vs 700
        for route, km, block in (("BLQ-STN", 1155, 130), ("LHR-FCO", 1444, 150), ("LHR-ATH", 2427, 220),
                                 ("FRA-JFK", 6189, 520), ("JFK-FCO", 6866, 520), ("CDG-GRU", 9405, 700)):
            origin, destination = route.split("-")
            self.assertEqual(fst.distance(origin, destination), km)
            m, estimated = fst.duration(None, km)
            self.assertTrue(estimated)
            self.assertEqual(m, round(40 + km / 850 * 60 + 1e-9), route)
            self.assertLess(abs(m - block) / block, 0.12, route)

    def test_only_without_other_sources(self):
        g = "2026-10-01"
        self.assertEqual(fst.duration(json.loads(status(g, takeoff="10:00", landing="11:30")), 1297), (90, False))
        self.assertEqual(fst.duration(None, None), (None, False))
        self.assertEqual(fst.duration(None, 0), (None, False))
        self.assertEqual(fst.duration(None, 5), (40, True))           # the fixed 40 minutes are within DURATION_RANGE
        # hours and km over the same set of flights: seven flights with no times, each with its estimate
        flights = [flight(i, f"KL {i}", "2026-06-0" + str(i), "AMS-FCO") for i in range(1, 8)]
        out = fst.compute(flights, [], TODAY)
        self.assertEqual((out["flights"], out["no_data"], out["estimated_durations"], out["air_time_min"]),
                         (7, 7, 7, 7 * 132))


class Gaps(unittest.TestCase):
    """Cases added after a review: each one makes a specific wrong implementation fail."""

    def stats(self, flights, passes=()):
        return fst.compute(flights, list(passes), TODAY)

    def test_diverted_is_not_on_time_even_if_it_departed_on_time(self):
        g = "2026-10-01"
        dev = flight(1, "LH 400", g, "FRA-JFK", status(g, diverted=True, takeoff="10:00", landing="15:00",
                                                       arrival_scheduled="17:00", departure_delay=0))
        out = self.stats([dev])
        self.assertEqual((out["flights"], out["diverted"], out["on_time_pct"]), (1, 1, 0))
        self.assertIsNone(out["list"][0]["delay_min"])          # arrived elsewhere: no delay at arrival

    def test_the_arrival_delay_counts_before_the_departure_delay(self):
        g = "2026-10-12"
        late = flight(1, "AZ 1", g, "LIN-FCO", status(g, takeoff="10:00", landing="11:00", arrival_scheduled="12:00",
                                                      arrival_actual="12:30", departure_delay=0))
        catches_up = flight(2, "AZ 2", g, "FCO-LIN", status(g, takeoff="14:00", landing="15:00",
                                                            arrival_scheduled="16:00", arrival_actual="15:55",
                                                            departure_delay=40))
        self.assertEqual(self.stats([late])["on_time_pct"], 0)
        self.assertEqual(self.stats([catches_up])["on_time_pct"], 100)
        self.assertEqual(self.stats([late, catches_up])["on_time_pct"], 50)

    def test_fifteen_minute_threshold_at_arrival(self):
        g = "2026-10-13"
        fourteen = flight(1, "AZ 3", g, "LIN-FCO", status(g, takeoff="10:00", landing="11:00",
                                                          arrival_scheduled="12:00", arrival_actual="12:14"))
        fifteen = flight(2, "AZ 4", g, "FCO-LIN", status(g, takeoff="14:00", landing="15:00",
                                                         arrival_scheduled="16:00", arrival_actual="16:15"))
        self.assertEqual(self.stats([fourteen])["on_time_pct"], 100)
        self.assertEqual(self.stats([fifteen])["on_time_pct"], 0)

    def test_rankings_keep_the_most_frequent(self):
        # nine airlines: the most frequent one (ZZ) is only on the oldest flights and last in alphabetical order
        flights, passes, n = [], [], 0
        for i, c in enumerate(["AA", "BB", "CC", "DD", "EE", "FF", "GG", "HH"]):
            n += 1
            flights.append(flight(n, f"{c} 1", f"2026-09-{10 + i:02d}", "LIN-FCO"))
        for i in range(3):
            n += 1
            flights.append(flight(n, "ZZ 9", f"2026-01-{10 + i:02d}", "BLQ-STN"))
        names = ["Ada", "Bea", "Cleo", "Dina", "Ella", "Fede", "Gaia", "Ilia", "Lea", "Mara"]
        for i, name in enumerate(names):
            passes.append(boarding_pass(1 + i % 8, name))
        passes += [boarding_pass(9, "Zoe"), boarding_pass(10, "Zoe"), boarding_pass(11, "Zoe")]
        out = self.stats(flights, passes)
        self.assertEqual(out["airlines"][0], {"code": "ZZ", "flights": 3})
        self.assertEqual([c["code"] for c in out["airlines"]], ["ZZ", "AA", "BB", "CC", "DD", "EE", "FF", "GG"])
        self.assertEqual(out["airports"][:2], [{"code": "FCO", "name": "Roma Fiumicino", "flights": 8},
                                                {"code": "LIN", "name": "Milano Linate", "flights": 8}])
        self.assertEqual(out["routes"][0], {"route": "LIN → FCO", "flights": 8})
        self.assertEqual(out["passengers"][0]["name"], "Zoe")
        self.assertEqual([p["name"] for p in out["passengers"]], ["Zoe"] + names[:9])

    def test_rounding(self):
        g = "2026-10-04"
        hundred = flight(1, "AZ 5", g, "MXP-BLQ", status(g, takeoff="10:00", landing="11:40"))   # 100 minutes, 235 km
        out = self.stats([hundred])
        self.assertEqual((out["air_time_min"], out["air_time_h"]), (100, 1.7))      # truncating would give 1.6
        self.assertEqual((out["km"], out["earth_laps"]), (235, 0.01))           # truncating would give 0.0
        long_haul = flight(2, "AF 1", g, "CDG-GRU", status(g, takeoff="10:00", landing="21:40"))
        self.assertEqual(self.stats([long_haul])["km"], 9405)
        self.assertEqual(self.stats([long_haul])["earth_laps"], 0.23)            # with 40000 km it would be 0.24

    def test_airport_name_from_any_flight(self):
        without = flight(1, "TK 2", "2026-10-20", "GZT-IST")
        with_city = flight(2, "TK 1", "2026-10-10", "IST-GZT", status("2026-10-10", takeoff="10:00", landing="12:00",
                                                                      destination_city="Gaziantep"))
        out = self.stats([without, with_city])
        self.assertIn({"code": "GZT", "name": "Gaziantep", "flights": 2}, out["airports"])


if __name__ == "__main__":
    unittest.main()
