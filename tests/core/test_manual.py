"""Tests for manual.py: the pure rules of the flights added, edited and deleted by hand. No database, no network;
fictional passengers.

  python3 -I -B -m unittest discover -s tracker -p 'test_manual.py' -v
"""
import datetime, json, os, re, string, sys, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import support  # noqa: E402  (the ported core on sys.path; its log lines to the current stdout)
HERE = support.CORE                    # the ported core: custom_components/boarding_pass_tracker/core
import airports, manual  # noqa: E402

TODAY = datetime.date(2026, 10, 31)
FLIGHT = {"id": 12, "airline": "FR", "number": "1234", "date": "2026-11-08", "origin": "BLQ", "destination": "STN",
          "created_at": "2026-10-31T10:00:00+01:00", "status": None, "snapshot": "{}", "next_check": None,
          "error": None, "updated_at": None}
NAMES = ["Alice", "Bob", "Carla", "Dario", "Elena", "Fabio", "Gina", "Ivo", "Luca", "Marta"]


def row(i, first_name, seat=None, passenger=None, document_id=7, cabin="Y", uploaded_by="alice"):
    """A boarding_passes row of an uploaded pass (full name in passenger)."""
    return {"id": i, "flight_id": 12, "passenger": passenger or f"{first_name or 'Nessuno'} Rossi",
            "first_name": first_name, "seat": seat, "cabin": cabin, "document_id": document_id,
            "uploaded_by": uploaded_by}


def typed(i, name, seat=None, uploaded_by="alice"):
    """A boarding_passes row typed by hand."""
    return row(i, name, seat, passenger=name, document_id=None, cabin=None, uploaded_by=uploaded_by)


def request(**body):
    return manual.parse_request(json.dumps(body).encode(), TODAY)


class Refused:
    def refused(self, key, fn, *args, code=400, **kwargs):
        """fn(*args, **kwargs) raises InputError `key` with that HTTP code, and its message is TEXTS[key]."""
        with self.assertRaises(manual.InputError) as e:
            fn(*args, **kwargs)
        self.assertEqual((e.exception.key, e.exception.code), (key, code))
        if "{" not in manual.TEXTS[key]:
            self.assertEqual(str(e.exception), manual.TEXTS[key])
        return e.exception


class ParseFlight(Refused, unittest.TestCase):
    def test_accepted(self):
        for text, code in (("FR 1234", ("FR", "1234")), ("fr1234", ("FR", "1234")), ("W6 0012", ("W6", "12")),
                           ("U2 1234", ("U2", "1234")), ("4U9536", ("4U", "9536")), (" az\t610 ", ("AZ", "610")),
                           ("ZZ 1", ("ZZ", "1")), ("FR 123A", ("FR", "123A"))):
            with self.subTest(text=text):
                self.assertEqual(manual.parse_flight_code(text), code)

    def test_refused(self):
        for text in ("EZY1234", "RYR1234", "1234", "FR", "FR12345", "FR \uff11\uff12\uff13\uff14", "", "   ", None,
                     1234, ["FR 1"]):
            with self.subTest(text=text):
                self.refused("bad_flight", manual.parse_flight_code, text)


class ParseDate(Refused, unittest.TestCase):
    def test_the_four_forms(self):
        for text in ("2026-11-08", "8/11/2026", "08/11/2026", "8.11.2026", "8-11-2026", " 08.11.2026 "):
            with self.subTest(text=text):
                self.assertEqual(manual.parse_day(text, TODAY), "2026-11-08")

    def test_refused(self):
        for text in ("31/02/2026", "2026-13-01", "08/11/26", "2026-11-08T10:00", "2026-11-08 10:00", "8/11-2026",
                     "2026-1-8", "domani", "", None, 20261108):
            with self.subTest(text=text):
                self.refused("bad_date", manual.parse_day, text, TODAY)

    def test_range(self):
        last = TODAY + datetime.timedelta(days=366)
        self.assertEqual(manual.parse_day(last.isoformat(), TODAY), last.isoformat())
        self.refused("date_out_of_range", manual.parse_day, (last + datetime.timedelta(days=1)).isoformat(), TODAY)
        self.assertEqual(manual.parse_day("1/1/1970", TODAY), "1970-01-01")
        self.refused("date_out_of_range", manual.parse_day, "1969-12-31", TODAY)

    def test_tracked_from_yesterday(self):
        self.assertTrue(manual.tracked("2026-10-31", TODAY))
        self.assertTrue(manual.tracked("2026-10-30", TODAY))
        self.assertFalse(manual.tracked("2026-10-29", TODAY))
        self.assertTrue(manual.tracked("2027-10-31", TODAY))


class ParseAirport(Refused, unittest.TestCase):
    def test_accepted(self):
        self.assertEqual(manual.parse_airport(" blq ", "origin"), "BLQ")
        self.assertEqual(manual.parse_airport("AAA", "destination"), "AAA")       # in airports.json, not in the table

    def test_refused(self):
        with open(os.path.join(HERE, "airports.json"), encoding="utf-8") as f:
            self.assertNotIn("KIV", json.load(f))                        # Chișinău is RMO now
        for text in ("BL", "BLQX", "XYZ", "KIV", "B1Q", "", None, 3):
            with self.subTest(text=text):
                self.refused("bad_origin", manual.parse_airport, text, "origin")
                self.refused("bad_destination", manual.parse_airport, text, "destination")

    def test_same_airport(self):
        self.refused("same_airport", request, action="add", flight="FR 1234", date="2026-11-08", origin="BLQ",
                     destination="blq", passengers="Alice")


class ParsePassengers(Refused, unittest.TestCase):
    def test_line(self):
        self.assertEqual(manual.parse_passengers("Alice 12A, Bob"), [("Alice", "12A"), ("Bob", None)])
        self.assertEqual(manual.parse_passengers(["Alice 12A", " Bob "]), [("Alice", "12A"), ("Bob", None)])
        self.assertEqual(manual.parse_passengers("Alice 12A; Bob\nCarla 3c\r\n"),
                         [("Alice", "12A"), ("Bob", None), ("Carla", "3C")])

    def test_every_line_break(self):
        # those of str.splitlines: text pasted from an iOS app can bring U+2028; never two people in one name
        for sep in ("\v", "\f", "\x1c", "\x1d", "\x1e", "\x85", "\u2028", "\u2029"):
            with self.subTest(sep=sep):
                self.assertEqual(manual.parse_passengers(f"Alice{sep}Bob 3C"), [("Alice", None), ("Bob", "3C")])
        self.assertEqual(manual.parse_passengers("Anna\tMaria\u00a03C"), [("Anna Maria", "3C")])   # spaces stay

    def test_names_and_seats(self):
        for text, expected in (("anna maria 03c", ("Anna Maria", "3C")), ("Bob inf", ("Bob", "INF")),
                               ("McDonald", ("McDonald", None)), ("DE LUCA", ("De Luca", None)),
                               ("d\u2019angelo 7f", ("D'Angelo", "7F")), ("José", ("José", None)),
                               ("Jose\u0301", ("José", None)), ("Anna-Maria  14C", ("Anna-Maria", "14C")),
                               ("12A", None)):
            with self.subTest(text=text):
                if expected is None:
                    self.refused("bad_passenger", manual.parse_passengers, text)
                else:
                    self.assertEqual(manual.parse_passengers(text), [expected])

    def test_refused_not_changed(self):
        for text in ("Carla 12", "Ada 12A 3B", "Marco R.", "Mario2", "Ali\u202ece", "Ab" * 15 + "c", "-Bob",
                     "Bob'"):
            with self.subTest(text=text):
                self.refused("bad_passenger", manual.parse_passengers, text)
        self.assertEqual(manual.parse_passengers("Ab" * 15), [("Ab" * 15, None)])        # 30 letters

    def test_blank(self):
        for value in (", ,", "", " \n ", [], [" ", ""], None):
            with self.subTest(value=value):
                self.assertEqual(manual.parse_passengers(value), [])

    def test_any_number_is_read(self):
        # how many a flight may have is the plan's rule (Plan.test_at_most_nine_per_flight): a flight with more
        # uploaded passes has a longer line
        self.assertEqual(len(manual.parse_passengers(", ".join(NAMES))), 10)
        self.assertEqual(len(manual.parse_passengers(NAMES)), 10)

    def test_add_with_the_same_name_twice(self):
        add = dict(action="add", flight="FR 1234", date="2026-11-08", origin="BLQ", destination="STN")
        self.refused("same_name", request, **add, passengers="Alice, alice")
        self.refused("no_passengers", request, **add, passengers=", ,")
        self.refused("no_passengers", request, **add)
        self.assertEqual(request(**add, passengers="Alice, Alice Rossi")["passengers"],
                         [("Alice", None), ("Alice Rossi", None)])


class Request(Refused, unittest.TestCase):
    ADD = dict(action="add", flight="fr 1234", date="8/11/2026", origin="blq", destination="stn",
               passengers="Alice 12A, Bob")

    def test_add(self):
        self.assertEqual(request(**self.ADD, ref="ignored"),
                         {"action": "add", "airline": "FR", "number": "1234", "date": "2026-11-08", "origin": "BLQ",
                          "destination": "STN", "passengers": [("Alice", "12A"), ("Bob", None)], "ref": None,
                          "departure_time": None, "arrival_time": None, "terminal": None, "gate": None,
                          "cabins": [None, None], "whole": [None, None]})

    def test_edit_and_delete(self):
        self.assertEqual(request(action="edit", ref="12-3f9a0c1b2d4e", date="2026-11-09", passengers=""),
                         {"action": "edit", "airline": None, "number": None, "date": "2026-11-09", "origin": None,
                          "destination": None, "passengers": None, "ref": (12, "3f9a0c1b2d4e"),
                          "departure_time": None, "arrival_time": None, "terminal": None, "gate": None,
                          "cabins": None, "whole": None})
        self.assertEqual(request(action="edit", ref="12-3f9a0c1b2d4e", date=None, passengers=", ,")["passengers"], [])
        self.assertEqual(request(action="delete", ref=" 3-a1b2c3d4e5f6 ", date="", passengers="", flight="x"),
                         {"action": "delete", "airline": None, "number": None, "date": None, "origin": None,
                          "destination": None, "passengers": None, "ref": (3, "a1b2c3d4e5f6"),
                          "departure_time": None, "arrival_time": None, "terminal": None, "gate": None,
                          "cabins": None, "whole": None})
        self.refused("nothing", request, action="edit", ref="12-3f9a0c1b2d4e", date=" ", passengers="")
        for ref in (None, "", "12", "0-3f9a0c1b2d4e", "12-3F9A0C1B2D4E", "12-3f9a0c1b2d4", "1234567890-3f9a0c1b2d4e"):
            with self.subTest(ref=ref):
                self.refused("no_ref", request, action="edit", ref=ref, date="2026-11-09")
                self.refused("no_ref", request, action="delete", ref=ref)

    def test_not_json(self):
        for body in (b"\xff\xfe", b"{", b"[" * 16000, b"[]", b'"add"', b"null", "{", 5):
            with self.subTest(body=body[:10] if isinstance(body, bytes) else body):
                self.refused("not_json", manual.parse_request, body, TODAY)
        self.assertEqual(manual.parse_request(dict(self.ADD), TODAY)["airline"], "FR")

    def test_keys_and_types(self):
        e = self.refused("unknown_field", request, **self.ADD, seat="12A")
        self.assertEqual(str(e), "Campo sconosciuto: seat.")
        e = self.refused("unknown_field", request, **{"a\u202eb\n" + "x" * 40: 1})
        self.assertEqual(str(e), "Campo sconosciuto: a b " + "x" * 26 + ".")
        for key, value in (("flight", 1234), ("date", True), ("origin", {"code": "BLQ"}), ("ref", ["12-x"]),
                           ("passengers", ["Alice", 3]), ("passengers", [None]), ("passengers", 2.5),
                           ("action", 0)):
            with self.subTest(key=key, value=value):
                e = self.refused("bad_type", request, **{**self.ADD, key: value})
                self.assertEqual(str(e), f"Il campo {key} ha un tipo sbagliato.")
        self.assertEqual(request(**{**self.ADD, "ref": None})["ref"], None)        # null: not given

    def test_action(self):
        for action in (None, "", "Add", "remove", "aggiungi"):
            with self.subTest(action=action):
                self.refused("bad_action", request, **{**self.ADD, "action": action})

    def test_required_fields_of_add(self):
        for key, error in (("flight", "bad_flight"), ("date", "bad_date"), ("origin", "bad_origin"),
                           ("destination", "bad_destination"), ("passengers", "no_passengers")):
            with self.subTest(key=key):
                self.refused(error, request, **{**self.ADD, key: " "})


class Plan(Refused, unittest.TestCase):
    def test_keep_seat_remove_add(self):
        rows = [row(1, "Alice", "12A"), row(2, "Bob", "14C"), typed(3, "Carla")]
        plan = manual.plan_passengers(rows, [("Alice", "15A"), ("Dario", "2B"), ("Elena", None)], replace=True)
        self.assertEqual((plan.seats, plan.inserts, plan.deletes), ([(1, "15A")], [("Dario", "2B"), ("Elena", None)],
                                                                    [2, 3]))
        self.assertTrue(plan)
        self.assertFalse(manual.plan_passengers(rows, [("Alice", "12A"), ("bob", "14C"), ("CARLA", None)], True))

    def test_one_name_for_one_name_is_a_rename(self):
        rows = [row(1, "Alice", "12A"), row(2, None, "14C", "Rossi Mario"), typed(3, "Carla")]
        plan = manual.plan_passengers(rows, [("Alice", "12A"), ("Dario", "14C"), ("Carla", None)], replace=True)
        self.assertEqual(plan, manual.Plan(renames=[(2, "Dario")]))
        plan = manual.plan_passengers(rows, [("Alice", "12A"), ("Rossi Mario", "14C"), ("Elena", "1A")], True)
        self.assertEqual(plan, manual.Plan(seats=[(3, "1A")], renames=[(2, "Rossi Mario"), (3, "Elena")]))
        self.assertEqual(manual.plan_passengers(rows, [("rossi mario", None)], False),
                         manual.Plan(seats=[], renames=[(2, "rossi mario")]))   # a merge names an unnamed pass
        # two names gone and two new: removed and added; a merge never renames
        plan = manual.plan_passengers(rows, [("Alice", "12A"), ("Dario", None), ("Elena", None)], True)
        self.assertEqual((plan.inserts, plan.deletes, plan.renames), ([("Dario", None), ("Elena", None)], [2, 3], []))
        self.assertEqual(manual.plan_passengers(rows[:1], [("Bob", None)], False), manual.Plan(inserts=[("Bob", None)]))

    def test_seat_cleared_on_edit_kept_on_merge(self):
        rows = [row(1, "Alice", "12A")]
        self.assertEqual(manual.plan_passengers(rows, [("Alice", None)], replace=True).seats, [(1, None)])
        self.assertFalse(manual.plan_passengers(rows, [("Alice", None)], replace=False))
        merge = manual.plan_passengers(rows, [("Alice", "3C"), ("Bob", None)], replace=False)
        self.assertEqual((merge.seats, merge.inserts, merge.deletes), ([(1, "3C")], [("Bob", None)], []))
        self.assertEqual(manual.plan_passengers([row(1, "Alice"), row(2, "Bob")], [("Bob", None)], False),
                         manual.Plan())                                     # a merge removes nobody

    def test_two_marios_in_order(self):
        rows = [row(1, "Mario", "12A", "Mario Rossi"), row(2, "Mario", "14C", "Mario Bianchi")]
        line = manual.passengers_text(rows)
        self.assertEqual(line, "Mario 12A, Mario 14C")
        self.assertFalse(manual.plan_passengers(rows, manual.parse_passengers(line), replace=True))
        swap = manual.plan_passengers(rows, manual.parse_passengers("Mario 14C, Mario 12A"), replace=True)
        self.assertEqual((swap.seats, swap.inserts, swap.deletes), ([(1, "14C"), (2, "12A")], [], []))
        second = manual.plan_passengers(rows, manual.parse_passengers("Mario 12A"), replace=True)
        self.assertEqual((second.seats, second.deletes), ([], [2]))

    def test_unnamed_pass(self):
        rows = [row(1, None, "3C", "passeggero"), row(2, "Alice")]
        self.assertEqual(manual.passengers_text(rows), "passeggero 3C, Alice")
        self.assertFalse(manual.plan_passengers(rows, manual.parse_passengers("passeggero 3C, Alice"), True))
        self.assertEqual(manual.plan_passengers(rows, manual.parse_passengers("Alice"), True).deletes, [1])
        self.refused("bad_passenger", manual.plan_passengers, [row(2, "Alice")],
                     manual.parse_passengers("Alice, Passeggero"), True)
        self.refused("bad_passenger", manual.plan_passengers, [], [("Passeggero", None)], False)

    def test_same_name_unmatched(self):
        self.refused("same_name", manual.plan_passengers, [row(1, "Mario")], [("Mario", None), ("mario", "3C")], True)
        self.refused("same_name", manual.plan_passengers, [], [("Bob", None), ("Bob", None)], False)

    def test_last_passenger(self):
        self.refused("last_passenger", manual.plan_passengers, [row(1, "Alice")], [], True, code=409)

    def test_round_trip_of_every_row_shape(self):
        shapes = [typed(1, "Alice", "12A"), typed(2, "Anna Maria"), row(3, "Bob", "14C"), row(4, "Carla"),
                  row(5, None), row(6, None, "INF", "passeggero"), row(7, "Paolo", None, "Paolo Fontanella", None),
                  row(8, "MARIO", "1A"), row(9, "dario"), row(10, "Anna-Maria", "STBY"), row(11, "D'Angelo"),
                  row(12, "Mario2", "2B", "Mario Due"), row(13, "B" * 35), row(14, "José"), row(15, "Mario", "3C"),
                  row(16, "  Elena ", "GATE"),
                  # a first name that is another row's full name is still read as a first name
                  row(17, "Rossi", None, "Rossi Verdi"), row(18, "Luca", "4D", "Rossi")]
        for i in range(len(shapes)):
            for rows in ([shapes[i]], shapes[i:i + 9]):
                with self.subTest(rows=[r["id"] for r in rows]):
                    line = manual.passengers_text(rows)
                    self.assertEqual(manual.plan_passengers(rows, manual.parse_passengers(line), True), manual.Plan())

    def test_a_full_name_takes_its_row(self):
        carla = row(1, "Carla", "12A", "Carla Bianchi")                         # Carla Bianchi's pass
        # the edit «Carla Bianchi 12A» keeps the pass (it is not a new passenger in place of the pass)
        self.assertFalse(manual.plan_passengers([carla], [("Carla Bianchi", "12A")], replace=True))
        # «Aggiungi volo» «Carla Bianchi 14C, Dario»: her seat, and one passenger added
        merge = manual.plan_passengers([carla], [("Carla Bianchi", "14C"), ("Dario", None)], replace=False)
        self.assertEqual((merge.seats, merge.inserts, merge.deletes), ([(1, "14C")], [("Dario", None)], []))
        # the hint of same_name followed: the surname on the pass's Carla, the other one is new
        hint = manual.plan_passengers([carla], [("Carla Bianchi", "12A"), ("Carla", None)], replace=True)
        self.assertEqual((hint.seats, hint.inserts, hint.deletes), ([], [("Carla", None)], []))
        self.refused("same_name", manual.plan_passengers, [carla], [("Carla", "12A"), ("Carla", None)], True)
        # a row of App Casa without a document (the S11 shape) is matched, never typed onto
        self.assertFalse(manual.plan_passengers([row(1, "Paola", "3C", "Paola Neri", None)], [("Paola Neri", None)],
                                                replace=False))
        # two Marios: the full name picks its row, the first name takes the other one
        marios = [row(1, "Mario", "12A", "Mario Rossi"), row(2, "Mario", "14C", "Mario Bianchi")]
        self.assertFalse(manual.plan_passengers(marios, [("Mario", "14C"), ("mario rossi", "12A")], replace=True))
        self.assertEqual(manual.plan_passengers(marios, [("Mario Bianchi", "15D"), ("Mario", "12A")], True).seats,
                         [(2, "15D")])
        # a typed passenger is matched by the name typed (the same in both columns)
        self.assertFalse(manual.plan_passengers([typed(1, "Anna Maria", "3C")], [("anna maria", "3C")], True))

    def test_an_unnamed_pass_named_by_its_full_name(self):
        # no first name: its full name typed names it (a rename, the seat kept on a merge)
        unnamed = row(1, None, "3C", "Mario Rossi")
        self.assertEqual(manual.plan_passengers([unnamed], [("Mario Rossi", None)], replace=False),
                         manual.Plan(renames=[(1, "Mario Rossi")]))
        self.assertFalse(manual.plan_passengers([unnamed], [("passeggero", "3C")], replace=True))

    def test_at_most_nine_per_flight(self):
        nine = [(n, None) for n in NAMES[:9]]
        self.assertEqual(len(manual.plan_passengers([], nine, replace=False).inserts), 9)
        self.refused("too_many", manual.plan_passengers, [], [(n, None) for n in NAMES], False)
        five = [typed(i, n) for i, n in enumerate(NAMES[:5], 1)]
        self.assertEqual(len(manual.plan_passengers(five, [(n, None) for n in NAMES[5:9]], False).inserts), 4)
        self.refused("too_many", manual.plan_passengers, five, [(n, None) for n in NAMES[5:]], False)
        self.refused("too_many", manual.plan_passengers, [typed(1, "Alice")], [(n, None) for n in NAMES], True)
        # a flight with more uploaded passes keeps them: its line round-trips, changes seats, swaps a passenger,
        # but gets nobody more
        many = [row(i, n, f"{i}A", f"{n} Rossi") for i, n in enumerate(NAMES + ["Nina", "Olga"], 1)]
        line = manual.parse_passengers(manual.passengers_text(many))
        self.assertEqual(len(line), 12)
        self.assertFalse(manual.plan_passengers(many, line, replace=True))
        self.assertEqual(manual.plan_passengers(many, [("Alice", "3C")] + line[1:], True).seats, [(1, "3C")])
        swap = manual.plan_passengers(many, line[:-2] + [("Pia", None)], replace=True)
        self.assertEqual((swap.inserts, swap.deletes, swap.renames), ([("Pia", None)], [11, 12], []))
        rename = manual.plan_passengers(many, line[:-1] + [("Pia", None)], replace=True)
        self.assertEqual((rename.inserts, rename.deletes, rename.renames, rename.seats), ([], [], [(12, "Pia")],
                                                                                          [(12, None)]))
        self.assertFalse(manual.plan_passengers(many, [("Alice", "1A")], replace=False))
        self.refused("too_many", manual.plan_passengers, many, [("Pia", None)], False)
        self.refused("too_many", manual.plan_passengers, many, line + [("Pia", None)], True)


class Markers(unittest.TestCase):
    def test_typed_rows(self):
        self.assertTrue(manual.is_typed(typed(1, "Alice")))
        self.assertFalse(manual.is_typed(row(1, "Alice")))                                   # a pass: a document
        self.assertFalse(manual.is_typed(row(1, "Alice", passenger="Alice")))                # a document, one word
        self.assertFalse(manual.is_typed(row(1, "Paolo", passenger="Paolo Fontanella", document_id=None)))   # S11
        self.assertFalse(manual.is_typed(row(1, None, passenger="passeggero", document_id=None)))

    def test_typed_flights(self):
        self.assertTrue(manual.is_typed_flight([typed(1, "Alice"), typed(2, "Bob")]))
        self.assertFalse(manual.is_typed_flight([typed(1, "Alice"), row(2, "Bob")]))
        self.assertFalse(manual.is_typed_flight([]))

    def test_person_key_and_owner(self):
        self.assertEqual([manual.person_key(x) for x in ("Mario", "MARIO", "mario2", None, "", "  ", "12")],
                         ["mario", "mario", "mario", "passeggero", "passeggero", "passeggero", "passeggero"])
        users = ["alice", "bob", "jose", "anna"]
        self.assertEqual(manual.owner_of("Alice", users, "bob"), "alice")
        self.assertEqual(manual.owner_of("José", users, "bob"), "jose")
        self.assertEqual(manual.owner_of("Anna Maria", users, "bob"), "anna")
        self.assertEqual(manual.owner_of("Carla", users, "bob"), "bob")
        self.assertEqual(manual.owner_of("José", ["alice"], "alice"), "alice")


class Version(unittest.TestCase):
    PASSES = [row(1, "Alice", "12A"), typed(2, "Bob")]

    def test_stable(self):
        a = manual.version(dict(FLIGHT), [dict(p) for p in self.PASSES])
        self.assertRegex(a, r"^[0-9a-f]{12}$")
        self.assertEqual(manual.version(FLIGHT, list(reversed(self.PASSES))), a)              # by id
        self.assertEqual(manual.ref(FLIGHT, self.PASSES), f"12-{a}")
        self.assertEqual(manual.parse_ref(manual.ref(FLIGHT, self.PASSES)), (12, a))

    def test_covered_fields(self):
        base = manual.version(FLIGHT, self.PASSES)
        for k, x in (("id", 13), ("created_at", "2026-10-31T10:00:01+01:00"), ("airline", "W6"), ("number", "1235"),
                     ("date", "2026-11-09"), ("origin", "BGY"), ("destination", "LTN")):
            with self.subTest(k=k):
                self.assertNotEqual(manual.version({**FLIGHT, k: x}, self.PASSES), base)
        for k, x in (("id", 3), ("passenger", "Alice Bianchi"), ("first_name", "Alicia"), ("seat", None),
                     ("cabin", "J"), ("document_id", 8), ("uploaded_by", "bob")):
            with self.subTest(k=k):
                self.assertNotEqual(manual.version(FLIGHT, [{**self.PASSES[0], k: x}, self.PASSES[1]]), base)
        self.assertNotEqual(manual.version(FLIGHT, self.PASSES[:1]), base)
        self.assertNotEqual(manual.version(FLIGHT, []), base)

    def test_not_what_the_clock_writes(self):
        base = manual.version(FLIGHT, self.PASSES)
        for k, x in (("status", '{"api_status": "active"}'), ("snapshot", '{"gate": "A1"}'),
                     ("next_check", "2026-11-08T04:00:00Z"), ("error", "tetto"), ("updated_at", "2026-11-08")):
            with self.subTest(k=k):
                self.assertEqual(manual.version({**FLIGHT, k: x}, self.PASSES), base)

    def test_reused_id(self):
        self.assertNotEqual(manual.ref(FLIGHT, self.PASSES),
                            manual.ref({**FLIGHT, "created_at": "2026-11-01T09:00:00+01:00"}, self.PASSES))

    def test_ref_shape(self):
        self.assertTrue(manual._REF.fullmatch("1-0123456789ab"))
        for text in ("01-0123456789ab", "1-0123456789a", "1-0123456789abc", "-0123456789ab", "1_0123456789ab"):
            self.assertIsNone(manual._REF.fullmatch(text), text)


class Labels(unittest.TestCase):
    def test_exact(self):
        passes = [row(1, "Alice", "12A"), row(2, "Bob"), row(3, None)]
        self.assertEqual(manual.choice_label(FLIGHT, passes),
                         "08/11/2026 · FR 1234 · Bologna → Londra Stansted · Alice, Bob, passeggero")
        self.assertEqual(manual.choice_label({**FLIGHT, "number": "1", "airline": "ZZ", "date": "2026-09-30",
                                              "origin": "FCO", "destination": "AMS"}, []),
                         "30/09/2026 · ZZ 1 · Roma Fiumicino → Amsterdam")
        self.assertEqual(manual.passengers_text(passes), "Alice 12A, Bob, passeggero")
        self.assertEqual(manual.passengers_text([]), "")

    def test_no_aviationstack_city(self):
        status = json.dumps({"origin_city": "Bologna Marconi", "destination_city": "Nowhere"})
        self.assertEqual(manual.choice_label({**FLIGHT, "destination": "AAA", "status": status}, []),
                         "08/11/2026 · FR 1234 · Bologna → AAA")

    def test_unique_and_without_dots(self):
        codes = sorted(airports.CITIES) + ["AAA"]
        self.assertNotIn("AAA", airports.CITIES)
        labels = {manual.choice_label({**FLIGHT, "origin": a, "destination": b}, [row(1, "Alice")])
                  for a in codes for b in ("BLQ", "AAA") if a != b}
        self.assertEqual(len(labels), 2 * len(codes) - 2)
        self.assertEqual([x for x in labels if "." in x], [])
        names = [airports.CITIES[c] for c in airports.CITIES]
        self.assertEqual(len(set(names)), len(names))
        self.assertEqual([x for x in names if re.fullmatch("[A-Z]{3}", x)], [])
        self.assertNotIn(".", manual.choice_label(FLIGHT, [row(1, "St. John"), row(2, "A.B.")]))

    def test_counts(self):
        self.assertEqual((manual.count_text(1), manual.count_text(2), manual.count_text(9)),
                         ("1 passeggero", "2 passeggeri", "9 passeggeri"))
        self.assertEqual(manual.changes_text("2026-11-08", 0, 0, 0), "data cambiata (prima il 08/11/2026)")
        self.assertEqual(manual.changes_text(None, 0, 0, 2, 1, 1, 2), "1 nome cambiato, la carta d'imbarco resta, "
                         "2 passeggeri tolti con 2 carte d'imbarco")
        self.assertEqual(manual.changes_text(None, 0, 0, 1, 2, 2, 1), "2 nomi cambiati, le carte d'imbarco restano, "
                         "1 passeggero tolto con la sua carta d'imbarco")
        self.assertEqual(manual.changes_text(None, 1, 1, 1),
                         "1 posto cambiato, 1 passeggero aggiunto, 1 passeggero tolto")
        self.assertEqual(manual.changes_text("2026-11-08", 2, 3, 4), "data cambiata (prima il 08/11/2026), "
                         "2 posti cambiati, 3 passeggeri aggiunti, 4 passeggeri tolti")
        self.assertEqual(manual.day_text("2026-11-08"), "08/11/2026")


class Texts(unittest.TestCase):
    FILLERS = {"key", "code", "day", "old_day", "route", "origin", "destination", "n", "k", "changes", "suffix",
               "details", "city", "departure", "arrival"}
    SAMPLE = {"key": "seat", "code": "FR 1234", "day": "08/11/2026", "old_day": "07/11/2026",
              "route": "Bologna (BLQ) → Londra Stansted (STN)", "origin": "Bologna", "destination": "Londra Stansted",
              "n": "2 passeggeri", "k": 2, "changes": "1 posto cambiato", "suffix": " Conta solo per le statistiche.",
              "details": ", partenza alle 06:30", "city": "Bologna", "departure": "06:30", "arrival": "08:45"}

    def test_every_text_formats(self):
        for key, value in manual.TEXTS.items():
            with self.subTest(key=key):
                fillers = {f for _, f, _, _ in string.Formatter().parse(value) if f is not None}
                self.assertLessEqual(fillers, self.FILLERS)               # counts and codes, never a name
                out = manual.text(key, **{f: self.SAMPLE[f] for f in fillers})
                self.assertNotRegex(out, r"[{}]")
                self.assertNotRegex(out, r"[aeiouAEIOU]'(?=[\s.,:;»)]|$)")    # real accents, not «gia'»

    def test_replies_of_the_plan(self):
        code, route, suffix = "FR 1234", "Bologna (BLQ) → Londra Stansted (STN)", manual.TEXTS["tracked"]
        self.assertEqual(manual.text("added", code=code, day="08/11/2026", route=route, n=manual.count_text(2),
                                     suffix=suffix, details=""),
                         "Aggiunto FR 1234 del 08/11/2026, Bologna (BLQ) → Londra Stansted (STN), 2 passeggeri. Ti "
                         "avviso se cambiano orario o gate.")
        self.assertEqual(manual.text("edited", code=code, day="09/11/2026", changes=manual.changes_text(
            "2026-11-08", 0, 0, 0), n=manual.count_text(2), suffix=suffix),
            "Modificato FR 1234 del 09/11/2026: data cambiata (prima il 08/11/2026). Ora ha 2 passeggeri. Ti avviso "
            "se cambiano orario o gate.")
        self.assertEqual(manual.text("deleted", code=code, day="09/11/2026", route=route),
                         "Eliminato FR 1234 del 09/11/2026, Bologna (BLQ) → Londra Stansted (STN).")
        self.assertEqual(manual.TEXTS["untracked"],
                         " Lo stato del volo non lo seguo: manca la chiave di AviationStack.")
        self.assertEqual(manual.TEXTS["status_typed"], "Inserito a mano")

    def test_input_error(self):
        e = manual.InputError("date_taken", 409, code="FR 1234", day="09/11/2026", origin="Bologna")
        self.assertEqual((e.key, e.code, str(e)), ("date_taken", 409, "C'è già FR 1234 del 09/11/2026 da Bologna: "
                                                                      "modifica quello, oppure elimina uno dei due."))
        self.assertEqual(str(manual.InputError("unknown_field", key="x")), "Campo sconosciuto: x.")
        self.assertIsInstance(e, ValueError)

    def test_names_never_in_the_builders(self):
        # the builders of the replies take counts and dates: a passenger name has no way in
        self.assertEqual(manual.count_text(3), "3 passeggeri")
        self.assertNotIn("Alice", manual.changes_text("2026-11-08", 1, 2, 3))


if __name__ == "__main__":
    unittest.main()
