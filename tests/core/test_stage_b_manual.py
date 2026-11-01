"""Stage B (decision 3, INTEGRATION_PLAN.md 3.7 M1-M8, 5.2.3-5.2.4): the pure rules of the fields typed by hand, in
manual.py. No database, no network; fictional passengers. The zones come from airport_zones.json (flights.zone_of);
the instants are compared as instants, so the results are the same in every process time zone and fake clock.

  python3 -I -B -m unittest discover -s tests/core -p 'test_stage_b_manual.py' -v
"""
import datetime, hashlib, json, os, string, sys, unittest
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import support  # noqa: E402,F401  (the ported core on sys.path)
import flights, manual  # noqa: E402

TODAY = datetime.date(2026, 10, 31)
FLIGHT = {"id": 12, "airline": "FR", "number": "1234", "date": "2026-11-08", "origin": "BLQ", "destination": "STN",
          "created_at": "2026-10-31T10:00:00+01:00", "status": None, "snapshot": "{}", "next_check": None,
          "error": None, "updated_at": None, "pass_departure": None, "pass_boarding": None, "pass_gate": None,
          "relevant_date": None, "pass_arrival": None, "pass_terminal": None}
ADD = dict(action="add", flight="FR 1234", date="2026-11-08", origin="BLQ", destination="STN", passengers="Alice, Bob")
EDIT = dict(action="edit", ref="12-3f9a0c1b2d4e")


def request(**body):
    return manual.parse_request(json.dumps(body).encode(), TODAY)


def at(iso):
    return datetime.datetime.fromisoformat(iso)


def row(i, first_name, seat=None, cabin=None, passenger=None, document_id=7):
    return {"id": i, "flight_id": 12, "passenger": passenger or f"{first_name} Rossi", "first_name": first_name,
            "seat": seat, "cabin": cabin, "document_id": document_id, "uploaded_by": "alice"}


def typed(i, name, seat=None, cabin=None):
    return row(i, name, seat, cabin, passenger=name, document_id=None)


class Refused:
    def refused(self, key, fn, *args, **kwargs):
        with self.assertRaises(manual.InputError) as e:
            fn(*args, **kwargs)
        self.assertEqual((e.exception.key, e.exception.code), (key, 400))
        if "{" not in manual.TEXTS[key]:
            self.assertEqual(str(e.exception), manual.TEXTS[key])
        return e.exception


class Texts(unittest.TestCase):
    """5.2.4: every new and changed text, exactly, with the real accented letters."""
    NEW = {
        "bad_departure_time": "Orario di partenza non valido: scrivi l'ora locale dell'aeroporto di partenza, per "
                              "esempio 06:30.",
        "bad_arrival_time": "Orario di arrivo non valido: scrivi l'ora locale dell'aeroporto di arrivo, per esempio "
                            "08:45.",
        "arrival_needs_departure": "Per l'orario di arrivo serve anche quello di partenza.",
        "bad_duration": "Orari che non tornano: tra partenza e arrivo ci sarebbero meno di 10 minuti o più di 24 ore.",
        "no_time_zone": "Non conosco il fuso orario di {city} ({code}): lascia vuoto l'orario.",
        "bad_terminal": "Terminal non valido: al massimo 8 tra lettere e cifre, per esempio T1.",
        "bad_gate": "Gate non valido: al massimo 8 tra lettere e cifre, per esempio B12.",
        "departure_changed": "orario di partenza cambiato", "arrival_changed": "orario di arrivo cambiato",
        "terminal_changed": "terminal cambiato", "gate_changed": "gate cambiato",
        "one_cabin_changed": "1 classe cambiata", "cabins_changed": "{k} classi cambiate",
        "times_moved": "orari spostati al nuovo giorno",
        "added_details": ", partenza alle {departure}{arrival}", "added_arrival": ", arrivo alle {arrival}",
        # changed (BC-B2)
        "added": "Aggiunto {code} del {day}, {route}{details}, {n}.{suffix}",
        "nothing": "Niente da cambiare: manda la data, i passeggeri, gli orari, il terminal o il gate.",
        "no_passengers": "Mancano i passeggeri: scrivi i nomi separati da virgole, con il posto e la classe dopo il "
                         "nome se li sai, per esempio Alice 12A business, Bob.",
        "bad_passenger": "Non capisco un passeggero: scrivi il nome (solo lettere, spazi, apostrofi e trattini), poi "
                         "il posto e la classe se li sai, per esempio Alice 12A business.",
    }

    def test_exact(self):
        for key, value in self.NEW.items():
            with self.subTest(key=key):
                self.assertEqual(manual.TEXTS[key], value)
        self.assertIn("più", manual.TEXTS["bad_duration"])                  # a real accent, not «piu'»

    def test_no_name_in_a_filler(self):
        for key in self.NEW:
            fillers = {f for _, f, _, _ in string.Formatter().parse(manual.TEXTS[key]) if f}
            self.assertLessEqual(fillers, {"city", "code", "k", "departure", "arrival", "day", "route", "details",
                                           "n", "suffix"}, key)

    def test_the_reply_of_an_add_without_times_is_as_before(self):
        self.assertEqual(manual.added_details({k: None for k in manual.OWN}), "")
        self.assertEqual(manual.added_details({**dict.fromkeys(manual.OWN), "pass_terminal": "1", "pass_gate": "B1"}),
                         "")
        self.assertEqual(manual.added_details({**dict.fromkeys(manual.OWN), "pass_departure": "2026-11-08T06:30:00+01:00"}),
                         ", partenza alle 06:30")
        self.assertEqual(manual.added_details({**dict.fromkeys(manual.OWN), "pass_departure": "2026-11-08T06:30:00+01:00",
                                               "pass_arrival": "2026-11-08T07:45:00+00:00"}),
                         ", partenza alle 06:30, arrivo alle 07:45")

    def test_change_parts_order(self):
        """FP's order with the new parts after the seats (plan 5.2.4): the date, names, passes kept, seats, the times
        moved, classes, the four fields, added, removed (with their passes)."""
        self.assertEqual(manual.changes_text("2026-11-08", 1, 1, 1, 0, 0, 1, cabins=2,
                                             fields=["departure_changed", "gate_changed"], moved=True),
                         "data cambiata (prima il 08/11/2026), 1 posto cambiato, orari spostati al nuovo giorno, "
                         "2 classi cambiate, orario di partenza cambiato, gate cambiato, 1 passeggero aggiunto, "
                         "1 passeggero tolto con la sua carta d'imbarco")
        self.assertEqual(manual.changes_text("2026-11-08", 2, 0, 0, 1, 1, moved=True),
                         "data cambiata (prima il 08/11/2026), 1 nome cambiato, la carta d'imbarco resta, "
                         "2 posti cambiati, orari spostati al nuovo giorno")
        self.assertEqual(manual.changes_text(None, 0, 0, 0, cabins=1, fields=["terminal_changed"]),
                         "1 classe cambiata, terminal cambiato")
        self.assertEqual(manual.changes_text(None, 0, 0, 0, fields=["arrival_changed"]), "orario di arrivo cambiato")


class Times(Refused, unittest.TestCase):
    def test_accepted(self):
        for text, hm in (("06:30", (6, 30)), ("6:30", (6, 30)), ("06.30", (6, 30)), (" 6.05 ", (6, 5)),
                         ("00:00", (0, 0)), ("23:59", (23, 59)), ("9:00", (9, 0))):
            with self.subTest(text=text):
                self.assertEqual(manual.parse_time(text), hm)
                self.assertEqual(request(**ADD, departure_time=text)["departure_time"], hm)

    def test_refused(self):
        for text in ("24:00", "6", "630", "0630", "6:3", "6:60", "25:10", "06:30:00", "6h30", "ore 6", "-6:30",
                     "١٢:٣٠", "06：30"):
            with self.subTest(text=text):
                self.refused("bad_departure_time", request, **ADD, departure_time=text)
                self.refused("bad_arrival_time", request, **ADD, departure_time="06:30", arrival_time=text)

    def test_clear_and_blank(self):
        self.assertEqual(request(**EDIT, departure_time="-")["departure_time"], manual.CLEAR)
        self.assertEqual(request(**EDIT, arrival_time=" – ")["arrival_time"], manual.CLEAR)      # iOS's dash
        r = request(**ADD, departure_time="-", arrival_time="", terminal=None, gate=" ")    # an add: not given
        self.assertEqual([r[k] for k in manual.OWN_KEYS], [None] * 4)
        self.assertEqual(request(**EDIT, gate="-")["gate"], manual.CLEAR)

    def test_types(self):
        for key in manual.OWN_KEYS:
            with self.subTest(key=key):
                self.refused("bad_type", request, **ADD, **{key: 630})
                self.refused("bad_type", request, **ADD, **{key: ["06:30"]})

    def test_nothing_only_when_every_field_is_absent(self):
        self.refused("nothing", request, **EDIT, date=" ", passengers="", departure_time="", arrival_time=None,
                     terminal=" ", gate="")
        for key, value in (("departure_time", "06:30"), ("arrival_time", "08:45"), ("terminal", "1"), ("gate", "-")):
            with self.subTest(key=key):
                self.assertIsNotNone(request(**EDIT, **{key: value})[key])
        self.assertEqual(request(action="delete", ref="3-a1b2c3d4e5f6", departure_time="x", gate="?")["gate"], None)


class Codes(Refused, unittest.TestCase):
    def test_terminal(self):
        for text, code in (("1", "1"), ("t1", "T1"), ("Terminal 1", "1"), ("terminal1", "1"), ("TERMINAL 2E", "2E"),
                           ("  t 2  ", "T 2"), ("A-B", "A-B"), ("12345678", "12345678"), ("Nord", "NORD")):
            with self.subTest(text=text):
                self.assertEqual(manual.parse_code(text, "terminal"), code)
        for text in ("Terminal", "123456789", "T1/A", "T1.", "--", "Ä1", "terminale 1 nord"):
            with self.subTest(text=text):
                self.refused("bad_terminal", manual.parse_code, text, "terminal")

    def test_gate(self):
        for text, code in (("b12", "B12"), ("Gate B12", "B12"), ("gate 7", "7"), ("A 01", "A 01"),
                           ("GATE12", "12"), ("Terminal", "TERMINAL")):
            with self.subTest(text=text):
                self.assertEqual(manual.parse_code(text, "gate"), code)
        for text in ("Gate", "B12!", "porta numero 12", "Terminal 1"):              # only «gate» is dropped here
            with self.subTest(text=text):
                self.refused("bad_gate", manual.parse_code, text, "gate")
        self.assertEqual(request(**EDIT, terminal="terminal 1", gate="gate b12")["terminal"], "1")


class Cabins(Refused, unittest.TestCase):
    def test_both_orders(self):
        pairs, cabins = manual.parse_passengers_cabins("Alice 12A business, Bob economy 14C, Carla prima, "
                                                       "Dario premium 3C, Elena 7F economica, Franco FIRST")
        self.assertEqual(pairs, [("Alice", "12A"), ("Bob", "14C"), ("Carla", None), ("Dario", "3C"), ("Elena", "7F"),
                                 ("Franco", None)])
        self.assertEqual(cabins, ["C", "Y", "F", "W", "Y", "F"])
        self.assertEqual(manual.parse_passengers("Alice 12A business, Bob"), [("Alice", "12A"), ("Bob", None)])

    def test_no_eco_and_no_single_letters(self):
        pairs, cabins = manual.parse_passengers_cabins("Marco Eco, Anna C, Bob Y")
        self.assertEqual(pairs, [("Marco Eco", None), ("Anna C", None), ("Bob Y", None)])
        self.assertEqual(cabins, [None, None, None])

    def test_a_name_must_be_left(self):
        self.assertEqual(manual.parse_passengers_cabins("Business 12A, business"),
                         ([("Business", "12A"), ("Business", None)], [None, None]))
        self.refused("bad_passenger", manual.parse_passengers_cabins, "12A business")
        self.refused("bad_passenger", manual.parse_passengers_cabins, "Alice business 12")

    def test_in_the_request(self):
        r = request(**{**ADD, "passengers": ["Alice 12A Business", " bob "]})
        self.assertEqual((r["passengers"], r["cabins"]), ([("Alice", "12A"), ("Bob", None)], ["C", None]))
        self.assertEqual(request(**EDIT, passengers="Alice economy")["cabins"], ["Y"])
        self.refused("same_name", request, **{**ADD, "passengers": "Alice business, alice economy"})

    def test_classes_from_the_letters(self):
        for letters, word in (("FAP", "prima"), ("JCDIZR", "business"), ("W", "premium"), ("YMBHKLQTVSNOGEX", "economy")):
            for x in letters:
                self.assertEqual(manual.cabin_class(x), word, x)
        for x in (None, "", " ", "1"):
            self.assertIsNone(manual.cabin_class(x))

    def test_classes_text(self):
        self.assertEqual(manual.classes_text([row(2, "Bob", cabin="Y"), row(1, "Alice", cabin="J")]),
                         "Alice business, Bob economy")
        self.assertEqual(manual.classes_text([row(1, "Alice", cabin="F"), typed(2, "Bob")]), "Alice prima, Bob")
        self.assertEqual(manual.classes_text([typed(1, "Alice"), row(2, None)]), "")
        self.assertEqual(manual.classes_text([]), "")

    def test_plan_compares_classes(self):
        rows = [row(1, "Alice", "12A", "J"), row(2, "Bob", None, "Y"), typed(3, "Carla")]
        # the J round trip: a pass's J shown as «business» and sent back changes nothing
        self.assertEqual(manual.plan_cabins(rows, *manual.parse_passengers_cabins(
            "Alice 12A business, Bob economy, Carla")), [])
        self.assertEqual(manual.plan_cabins(rows, *manual.parse_passengers_cabins(
            "Alice 12A economy, Bob premium, Carla prima")), [(1, "Y"), (2, "W"), (3, "F")])
        # a missing word is no change, never a clear
        self.assertEqual(manual.plan_cabins(rows, *manual.parse_passengers_cabins("Alice, Bob, Carla")), [])
        # a full name takes its row, as in plan_passengers
        self.assertEqual(manual.plan_cabins([row(1, "Carla", cabin="Y", passenger="Carla Bianchi")],
                                            [("Carla Bianchi", None)], ["C"]), [(1, "C")])
        self.assertEqual(manual.plan_cabins(rows, None, None), [])


class Instants(unittest.TestCase):
    def test_departure_in_the_origin_zone(self):
        self.assertEqual(manual.local_instant("2026-11-08", (6, 30), "Europe/Rome"), "2026-11-08T06:30:00+01:00")
        self.assertEqual(manual.local_instant("2026-07-08", (6, 30), "Europe/Rome"), "2026-07-08T06:30:00+02:00")
        self.assertEqual(manual.local_instant("2026-11-08", (23, 59), "Pacific/Kiritimati"),
                         "2026-11-08T23:59:00+14:00")

    def test_dst_gap_moves_forward_and_fold_takes_the_first(self):
        # Europe/Rome: 2027-03-28 02:00 -> 03:00 (the gap), 2027-10-31 03:00 -> 02:00 (02:30 twice)
        self.assertEqual(manual.local_instant("2027-03-28", (2, 30), "Europe/Rome"), "2027-03-28T03:30:00+02:00")
        self.assertEqual(manual.local_instant("2027-10-31", (2, 30), "Europe/Rome"), "2027-10-31T02:30:00+02:00")
        self.assertEqual(manual.local_instant("2027-10-31", (3, 30), "Europe/Rome"), "2027-10-31T03:30:00+01:00")
        # America/New_York 2027-03-14 02:30 does not exist
        self.assertEqual(manual.local_instant("2027-03-14", (2, 30), "America/New_York"), "2027-03-14T03:30:00-04:00")

    def test_arrival_day_chosen(self):
        cases = [  # (day, departure, origin zone, arrival hh:mm, destination zone) -> arrival
            (("2026-11-08", (6, 30), "Europe/Rome", (7, 50), "Europe/London"), "2026-11-08T07:50:00+00:00"),
            # overnight: JFK 22:00 -> LHR 10:00 the next day
            (("2026-11-08", (22, 0), "America/New_York", (10, 0), "Europe/London"), "2026-11-09T10:00:00+00:00"),
            # the date line westwards: NRT 17:00 -> HNL 06:00 the same day
            (("2026-11-08", (17, 0), "Asia/Tokyo", (6, 0), "Pacific/Honolulu"), "2026-11-08T06:00:00-10:00"),
            # and eastwards: HNL 14:00 -> NRT 18:00 the next day
            (("2026-11-08", (14, 0), "Pacific/Honolulu", (18, 0), "Asia/Tokyo"), "2026-11-09T18:00:00+09:00"),
            # the day before: AKL 00:30 -> HNL 10:30 of the previous local day
            (("2026-11-08", (0, 30), "Pacific/Auckland", (10, 30), "Pacific/Honolulu"), "2026-11-07T10:30:00-10:00"),
            # exactly 10 minutes, and exactly 24 hours
            (("2026-11-08", (6, 30), "Europe/Rome", (6, 40), "Europe/Rome"), "2026-11-08T06:40:00+01:00"),
        ]
        for (day, dep, oz, arr, dz), want in cases:
            with self.subTest(day=day, dep=dep, oz=oz):
                d = manual.local_instant(day, dep, oz)
                got = manual.arrival_instant(day, d, arr, dz)
                self.assertEqual(got, want)
                minutes = (at(got) - at(d)).total_seconds() / 60
                self.assertTrue(10 <= minutes <= 24 * 60, minutes)

    def test_bad_duration(self):
        d = manual.local_instant("2026-11-08", (6, 30), "Europe/Rome")
        self.assertIsNone(manual.arrival_instant("2026-11-08", d, (6, 35), "Europe/Rome"))     # 5 minutes
        self.assertIsNone(manual.arrival_instant("2026-11-08", d, (6, 31), "Europe/Rome"))     # 24 h 1 min
        # the same time: not 0 minutes but exactly 24 h, the end of the range
        self.assertEqual(manual.arrival_instant("2026-11-08", d, (6, 30), "Europe/Rome"), "2026-11-09T06:30:00+01:00")

    def test_rebase(self):
        self.assertEqual(manual.rebase("2026-11-08T06:30:00+01:00", "2026-11-08", "2026-11-09", "Europe/Rome"),
                         "2026-11-09T06:30:00+01:00")
        # the same local time across a DST change, in the airport's zone (not the stored offset)
        self.assertEqual(manual.rebase("2027-03-27T06:30:00+01:00", "2027-03-27", "2027-03-29", "Europe/Rome"),
                         "2027-03-29T06:30:00+02:00")
        # an instant written in another offset (a pass in UTC): its local time at the airport
        self.assertEqual(manual.rebase("2026-11-08T05:30:00+00:00", "2026-11-08", "2026-11-01", "Europe/Rome"),
                         "2026-11-01T06:30:00+01:00")
        # an arrival the day after keeps its day after
        self.assertEqual(manual.rebase("2026-11-09T10:00:00+00:00", "2026-11-08", "2026-11-12", "Europe/London"),
                         "2026-11-13T10:00:00+00:00")
        self.assertIsNone(manual.rebase(None, "2026-11-08", "2026-11-09", "Europe/Rome"))
        self.assertIsNone(manual.rebase("2026-11-08T06:30:00+01:00", "2026-11-08", "2026-11-09", None))


class Fields(Refused, unittest.TestCase):
    def fields(self, v=None, day=None, merge=False, **body):
        base = EDIT if v is not None and not merge else ADD
        if day and base is EDIT:
            body = {"date": day, **body}                     # an edit of the date (the request needs a field)
        req = request(**base, **body)
        return manual.plan_fields(req, v, day or (v or req)["date"], merge)

    def test_new_flight(self):
        f = self.fields(departure_time="6:30", arrival_time="7.50", terminal="T1", gate="b12")
        self.assertEqual(f.values, {"pass_departure": "2026-11-08T06:30:00+01:00",
                                    "pass_arrival": "2026-11-08T07:50:00+00:00", "pass_terminal": "T1",
                                    "pass_gate": "B12"})
        self.assertEqual(self.fields().values, dict.fromkeys(manual.OWN))
        self.refused("arrival_needs_departure", self.fields, arrival_time="07:50")
        self.refused("bad_duration", self.fields, departure_time="06:30", arrival_time="05:35")

    def test_edit_per_field(self):
        v = {**FLIGHT, "pass_departure": "2026-11-08T06:30:00+01:00", "pass_arrival": "2026-11-08T07:50:00+00:00",
             "pass_terminal": "T1", "pass_gate": "B12"}
        f = self.fields(v, gate="A3")
        self.assertEqual((f.values, f.parts, f.moved), ({**{k: v[k] for k in manual.OWN}, "pass_gate": "A3"},
                                                         ["gate_changed"], False))
        f = self.fields(v, terminal="-", gate="-")
        self.assertEqual((f.values["pass_terminal"], f.values["pass_gate"], f.parts), (None, None,
                                                                                        ["terminal_changed",
                                                                                         "gate_changed"]))
        f = self.fields(v, arrival_time="-")
        self.assertEqual((f.values["pass_arrival"], f.parts), (None, ["arrival_changed"]))
        f = self.fields(v, departure_time="-", arrival_time="-")
        self.assertEqual((f.values["pass_departure"], f.values["pass_arrival"]), (None, None))
        self.refused("arrival_needs_departure", self.fields, v, departure_time="-")          # the arrival stays
        # the same values sent again: no change part
        self.assertEqual(self.fields(v, departure_time="06:30", arrival_time="07:50", terminal="t1", gate="B12").parts,
                         [])

    def test_a_new_departure_keeps_the_arrival_time(self):
        v = {**FLIGHT, "pass_departure": "2026-11-08T22:00:00+01:00", "pass_arrival": "2026-11-09T06:00:00+00:00"}
        f = self.fields(v, departure_time="05:00")                         # 22:00 -> 06:00+1 becomes 05:00 -> 06:00
        self.assertEqual((f.values["pass_departure"], f.values["pass_arrival"], f.parts),
                         ("2026-11-08T05:00:00+01:00", "2026-11-08T06:00:00+00:00", ["departure_changed"]))
        self.refused("bad_duration", self.fields, v, departure_time="06:55")    # 05:55 UTC: 5 min before 06:00

    def test_an_instant_in_another_offset_is_the_same(self):
        v = {**FLIGHT, "pass_departure": "2026-11-08T05:30:00+00:00"}           # from a pass, written in UTC
        f = self.fields(v, departure_time="06:30")
        self.assertEqual((f.values["pass_departure"], f.parts), ("2026-11-08T05:30:00+00:00", []))

    def test_merge_fills_only_empty_columns(self):
        v = {**FLIGHT, "pass_departure": "2026-11-08T06:30:00+01:00", "pass_gate": "B12"}
        f = self.fields(v, merge=True, departure_time="09:00", arrival_time="08:00", terminal="1", gate="C3")
        self.assertEqual(f.values, {"pass_departure": "2026-11-08T06:30:00+01:00",
                                    "pass_arrival": "2026-11-08T08:00:00+00:00", "pass_terminal": "1",
                                    "pass_gate": "B12"})
        self.assertEqual(f.parts, ["arrival_changed", "terminal_changed"])
        self.assertEqual(self.fields(v, merge=True).values, {k: v[k] for k in manual.OWN})

    def test_a_new_date_rebases(self):
        v = {**FLIGHT, "pass_departure": "2026-11-08T22:00:00+01:00", "pass_arrival": "2026-11-09T06:00:00+00:00",
             "pass_terminal": "T1", "pass_gate": "B12"}
        f = self.fields(v, "2026-11-10")
        self.assertEqual((f.values, f.parts, f.moved), ({"pass_departure": "2026-11-10T22:00:00+01:00",
                                                         "pass_arrival": "2026-11-11T06:00:00+00:00",
                                                         "pass_terminal": None, "pass_gate": None}, [], True))
        # times sent with the date win; terminal and gate sent are kept
        f = self.fields(v, "2026-11-10", departure_time="21:00", gate="C1")
        self.assertEqual((f.values, f.parts, f.moved), ({"pass_departure": "2026-11-10T21:00:00+01:00",
                                                         "pass_arrival": "2026-11-11T06:00:00+00:00",
                                                         "pass_terminal": None, "pass_gate": "C1"},
                                                        ["departure_changed", "gate_changed"], True))
        # the same local times sent again with the date: only moved
        f = self.fields(v, "2026-11-10", departure_time="22:00", arrival_time="06:00")
        self.assertEqual((f.parts, f.moved), ([], False))
        # nothing typed: nothing moved
        self.assertFalse(self.fields(FLIGHT, "2026-11-10").moved)

    def test_no_time_zone_refuses_only_the_times(self):
        saved = flights._ZONES_OF
        flights.zone_of("BLQ")
        flights._ZONES_OF = {k: x for k, x in flights._ZONES_OF.items() if k not in ("BLQ", "STN")}
        self.addCleanup(setattr, flights, "_ZONES_OF", saved)
        e = self.refused("no_time_zone", self.fields, departure_time="06:30")
        self.assertEqual(str(e), "Non conosco il fuso orario di Bologna (BLQ): lascia vuoto l'orario.")
        self.assertEqual(self.fields(terminal="1", gate="B2").values["pass_gate"], "B2")
        v = {**FLIGHT, "pass_departure": "2026-11-08T06:30:00+01:00"}
        e = self.refused("no_time_zone", self.fields, v, arrival_time="08:00")
        self.assertEqual(str(e), "Non conosco il fuso orario di Londra Stansted (STN): lascia vuoto l'orario.")
        # a new date: a typed time of an airport without a zone cannot move, so it is cleared
        f = self.fields(v, "2026-11-09")
        self.assertEqual((f.values["pass_departure"], f.moved), (None, False))


class Version(unittest.TestCase):
    PASSES = [row(1, "Alice", "12A", "Y")]

    def test_covers_the_own_columns(self):
        base = manual.version(FLIGHT, self.PASSES)
        for k in ("pass_departure", "pass_arrival", "pass_boarding", "pass_gate", "pass_terminal"):
            with self.subTest(k=k):
                self.assertNotEqual(manual.version({**FLIGHT, k: "x"}, self.PASSES), base)
        for k in ("status", "snapshot", "next_check", "error", "updated_at", "relevant_date"):
            with self.subTest(k=k):
                self.assertEqual(manual.version({**FLIGHT, k: "x"}, self.PASSES), base)

    def test_every_ref_changes_once(self):
        """BC-B7: the stage-A version of a flight (no pass_* column in it) is not stage B's."""
        stage_a = hashlib.sha256(json.dumps(
            [FLIGHT[k] for k in ("id", "created_at", "airline", "number", "date", "origin", "destination")]
            + [[[p[k] for k in ("id", "passenger", "first_name", "seat", "cabin", "document_id", "uploaded_by")]
                for p in self.PASSES]], ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()[:12]
        self.assertNotEqual(manual.version(FLIGHT, self.PASSES), stage_a)
        self.assertRegex(manual.ref(FLIGHT, self.PASSES), r"^12-[0-9a-f]{12}$")


class ChoiceFields(unittest.TestCase):
    def test_own_values_in_each_airport_zone(self):
        v = {**FLIGHT, "pass_departure": "2026-11-08T05:30:00+00:00", "pass_arrival": "2026-11-08T07:50:00+00:00",
             "pass_terminal": "1", "pass_gate": "B12",
             "status": json.dumps({"gate": "A1", "terminal": "2", "departure_scheduled": "2026-11-08T09:00:00+00:00"})}
        self.assertEqual(manual.choice_fields(v, [row(1, "Alice", cabin="C"), typed(2, "Bob")]),
                         {"classes": "Alice business, Bob", "departure_time": "06:30", "arrival_time": "07:50",
                          "terminal": "1", "gate": "B12"})
        self.assertEqual(manual.choice_fields(FLIGHT, [typed(1, "Alice")]),
                         {"classes": "", "departure_time": "", "arrival_time": "", "terminal": "", "gate": ""})

    def test_the_round_trip_of_the_prefilled_values(self):
        """«Modifica volo» sends back what GET /choices offered: nothing changes."""
        v = {**FLIGHT, "pass_departure": "2026-11-08T05:30:00+00:00", "pass_arrival": "2026-11-08T07:50:00+00:00",
             "pass_terminal": "T 1", "pass_gate": "B12"}
        passes = [row(1, "Alice", "12A", "J"), typed(2, "Bob", None, "Y")]
        shown = manual.choice_fields(v, passes)
        req = request(**EDIT, passengers=manual.passengers_text(passes), departure_time=shown["departure_time"],
                      arrival_time=shown["arrival_time"], terminal=shown["terminal"], gate=shown["gate"])
        f = manual.plan_fields(req, v, v["date"])
        self.assertEqual((f.values, f.parts), ({k: v[k] for k in manual.OWN}, []))
        self.assertFalse(manual.plan_passengers(passes, req["passengers"], replace=True))
        # the classes shown, typed into the passengers, change nothing (classes are compared, not letters: J)
        self.assertEqual(shown["classes"], "Alice business, Bob economy")
        self.assertEqual(manual.plan_cabins(passes, *manual.parse_passengers_cabins("Alice 12A business, Bob economy")),
                         [])


class Zones(unittest.TestCase):
    def test_zone_of(self):
        self.assertEqual((flights.zone_of("BLQ"), flights.zone_of("STN"), flights.zone_of("JFK")),
                         ("Europe/Rome", "Europe/London", "America/New_York"))
        for code in (None, "", "blq", "XYZ", "_fonte", 3):
            self.assertIsNone(flights.zone_of(code), code)
        for code in ("BLQ", "NRT", "HNL", "AKL", "SYD", "GRU"):
            ZoneInfo(flights.zone_of(code))


if __name__ == "__main__":
    unittest.main()
