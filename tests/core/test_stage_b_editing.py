"""Stage B (decision 3, INTEGRATION_PLAN.md 3.7 E17-E21, 5.2.3-5.2.4, 6.3 BC-B2..BC-B9): the times, classes, terminal
and gate typed by hand, through the engine and the test Handler (POST /flight, GET /choices), on test_editing's
fakes and a new database per test. Fictional passengers only.

  python3 -I -B -m unittest discover -s tests/core -p 'test_stage_b_editing.py' -v

The flight day is test_editing's `day` (two days ahead in London); BLQ is Europe/Rome and STN Europe/London, which
change the clocks together, so the local times below are the same in every season, process time zone and fake
clock. AviationStack's fake flight (Base.api) departs 06:30 in Bologna and arrives 07:50 in London; the typed times
here differ from it on purpose (07:00 and 09:00).
"""
import datetime, os, sys, unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_editing as ted  # noqa: E402  (test_engine first: the environment, then the engine)

te, tf, eng = ted.te, ted.tf, ted.eng
CFG, ALICE, BOB, dmy, days = ted.CFG, ted.ALICE, ted.BOB, ted.dmy, ted.days
ROUTE, TRACKED, PAST = ted.ROUTE, ted.TRACKED, ted.PAST
ROME, LONDON = eng.flights.ZoneInfo("Europe/Rome"), eng.flights.ZoneInfo("Europe/London")
EMPTY = {"classes": "", "departure_time": "", "arrival_time": "", "terminal": "", "gate": ""}


def local(day, hhmm, zone):
    """The instant of `hhmm` on `day` in `zone` (an aware datetime), computed here, not with manual."""
    return datetime.datetime.combine(day, datetime.time.fromisoformat(hhmm), zone)


class Typed(ted.Base):
    """test_editing.Base, with the typed flight of these tests: FR 1234 of `day`, BLQ -> STN, Alice in business and
    Bob in economy, 07:00 Bologna -> 09:00 London, terminal 1, gate B12."""
    FIELDS = dict(passengers="Alice 12A business, Bob economy", departure_time="7:00", arrival_time="09.00",
                  terminal="Terminal 1", gate="b12")

    def dep(self, day=None):
        return local(day or self.day, "07:00", ROME)

    def arr(self, day=None):
        return local(day or self.day, "09:00", LONDON)

    def add_typed(self, **over):
        return self.add(**{**self.FIELDS, **over})

    def computed(self, now=None):
        return eng.compute_sensors(now=now or datetime.datetime.now(datetime.timezone.utc))

    def message(self, key, **fill):
        return eng.manual.text(key, **fill)


class Add(Typed):
    def test_every_field(self):
        reply = self.add_typed()
        self.assertEqual(reply, {"id": 1, "result": "added", "message": (
            f"Aggiunto FR 1234 del {dmy(self.day)}, {ROUTE}, partenza alle 07:00, arrivo alle 09:00, 2 passeggeri."
            f"{TRACKED}")})
        v = self.row(1)
        self.assertEqual((v["pass_departure"], v["pass_arrival"], v["pass_terminal"], v["pass_gate"]),
                         (self.dep().isoformat(), self.arr().isoformat(), "1", "B12"))
        self.assertEqual((datetime.datetime.fromisoformat(v["pass_departure"]),
                          datetime.datetime.fromisoformat(v["pass_arrival"])), (self.dep(), self.arr()))
        self.assertIsNone(v["pass_boarding"])
        self.assertEqual([(p["passenger"], p["seat"], p["cabin"], p["document_id"]) for p in self.passes_of(1)],
                         [("Alice", "12A", "C", None), ("Bob", None, "Y", None)])
        self.assertEqual(self.details(1), {"ref": self.ref(1), "date": self.day.isoformat(),
                                           "passengers": "Alice 12A, Bob", "classes": "Alice business, Bob economy",
                                           "departure_time": "07:00", "arrival_time": "09:00", "terminal": "1",
                                           "gate": "B12"})

    def test_the_cards_get_the_typed_values(self):
        """BC-B3: no new attribute, values in the existing ones; status «Inserito a mano», source manual."""
        self.add_typed()
        state, card = self.computed()["next_flight"]
        self.assertEqual(state, "Inserito a mano")
        self.assertEqual({k: card[k] for k in ("departure", "departure_time", "arrival", "arrival_time", "terminal",
                                               "gate", "boarding_time", "boarding_estimated", "at_gate_time",
                                               "leave_for_gate_time", "priority", "status", "source")},
                         {"departure": self.dep().isoformat(), "departure_time": "07:00",
                          "arrival": self.arr().isoformat(), "arrival_time": "09:00", "terminal": "1", "gate": "B12",
                          "boarding_time": "06:30", "boarding_estimated": True, "at_gate_time": "06:20",
                          "leave_for_gate_time": "06:05", "priority": False, "status": "Inserito a mano",
                          "source": "manual"})
        self.assertEqual(set(card), set(eng.flight_card(self.row(1), None)))
        (entry,) = self.computed()["upcoming_flights"][1]["list"]
        self.assertEqual((entry["departure_time"], entry["gate"]), ("07:00", "B12"))
        # all in business: priority, at the gate when boarding starts
        self.assertEqual(self.edit(1, passengers="Alice 12A, Bob business")[1]["result"], "edited")
        card = self.computed()["next_flight"][1]
        self.assertEqual((card["priority"], card["at_gate_time"]), (True, "06:30"))

    def test_without_the_new_fields_as_before(self):
        """BC-B2/BC-B6: the stage-A reply, NULL in the new columns, "" in the new details."""
        self.assertEqual(self.add(), {"id": 1, "result": "added", "message": ted.added(self.day, 2)})
        v = self.row(1)
        self.assertEqual([v[k] for k in ("pass_departure", "pass_arrival", "pass_terminal", "pass_gate")], [None] * 4)
        self.assertEqual([p["cabin"] for p in self.passes_of(1)], [None, None])
        d = self.details(1)
        self.assertEqual({k: d[k] for k in EMPTY}, EMPTY)
        self.assertEqual(d["passengers"], "Alice 12A, Bob")
        card = self.computed()["next_flight"][1]
        self.assertEqual([card[k] for k in ("departure", "arrival", "terminal", "gate", "boarding_time")], [None] * 5)

    def test_a_past_flight(self):
        past = self.today - days(30)
        reply = self.add_typed(date=past.isoformat())
        self.assertEqual(reply["message"], f"Aggiunto FR 1234 del {dmy(past)}, {ROUTE}, partenza alle 07:00, arrivo "
                                           f"alle 09:00, 2 passeggeri.{PAST}")
        self.assertIsNone(self.row(1)["next_check"])

    def test_refusals_write_nothing(self):
        t = eng.manual.TEXTS
        for change, key in (({"departure_time": "24:00"}, "bad_departure_time"),
                            ({"arrival_time": "8"}, "bad_arrival_time"),
                            ({"departure_time": None}, "arrival_needs_departure"),
                            ({"arrival_time": "06:05"}, "bad_duration"),       # London: 5 minutes after 07:00 Bologna
                            ({"terminal": "T12345678"}, "bad_terminal"), ({"gate": "B 12!"}, "bad_gate"),
                            ({"passengers": "Alice 12A business economy x"}, "bad_passenger")):
            with self.subTest(key=key):
                self.refused(400, t[key], **{"action": "add", "flight": "FR 1234", "date": self.day.isoformat(),
                                             "origin": "BLQ", "destination": "STN", **self.FIELDS, **change})
        self.assertEqual(self.flights(), [])

    def test_no_time_zone_refuses_only_the_times(self):
        eng.flights.zone_of("BLQ")
        zones = {k: x for k, x in eng.flights._ZONES_OF.items() if k != "BLQ"}
        with mock.patch.object(eng.flights, "_ZONES_OF", zones):
            self.refused(400, "Non conosco il fuso orario di Bologna (BLQ): lascia vuoto l'orario.",
                         **{"action": "add", "flight": "FR 1234", "date": self.day.isoformat(), "origin": "BLQ",
                            "destination": "STN", **self.FIELDS})
            reply = self.add_typed(departure_time="", arrival_time="")
            self.assertEqual(reply["message"], ted.added(self.day, 2))
            self.assertEqual((self.row(1)["pass_terminal"], self.row(1)["pass_gate"]), ("1", "B12"))
            self.assertEqual(self.details(1)["departure_time"], "")


class Merge(Typed):
    """«Aggiungi volo» on a flight already there: the new fields fill only the empty columns."""

    def test_fills_only_the_empty_columns(self):
        self.add(passengers="Alice 12A")
        reply = self.add_typed(passengers="Alice", gate="")
        self.assertEqual(reply, {"id": 1, "result": "merged", "message": (
            f"FR 1234 del {dmy(self.day)} c'era già: orario di partenza cambiato, orario di arrivo cambiato, terminal "
            f"cambiato. Ora ha 1 passeggero.{TRACKED}")})
        v = self.row(1)
        self.assertEqual((v["pass_departure"], v["pass_arrival"], v["pass_terminal"], v["pass_gate"]),
                         (self.dep().isoformat(), self.arr().isoformat(), "1", None))
        # a different value of a column already filled is kept and not mentioned
        reply = self.add_typed(passengers="Alice 12A", departure_time="08:00", arrival_time="", terminal="2",
                               gate="C3")
        self.assertEqual(reply["message"], f"FR 1234 del {dmy(self.day)} c'era già: gate cambiato. Ora ha 1 "
                                           f"passeggero.{TRACKED}")
        self.assertEqual((self.row(1)["pass_departure"], self.row(1)["pass_terminal"], self.row(1)["pass_gate"]),
                         (self.dep().isoformat(), "1", "C3"))
        self.assertEqual(self.add_typed(passengers="Alice", departure_time="08:00")["result"], "unchanged")

    def test_an_arrival_measured_from_the_stored_departure(self):
        self.add(passengers="Alice", departure_time="07:00")
        reply = self.add(passengers="Alice", arrival_time="09:00")
        self.assertEqual(reply["result"], "merged")
        self.assertEqual(self.row(1)["pass_arrival"], self.arr().isoformat())

    def test_classes(self):
        self.add(passengers="Alice 12A, Bob")
        reply = self.add(passengers="Alice business, Carla prima")
        self.assertEqual(reply["message"], f"FR 1234 del {dmy(self.day)} c'era già: 1 classe cambiata, 1 passeggero "
                                           f"aggiunto. Ora ha 3 passeggeri.{TRACKED}")
        self.assertEqual([(p["first_name"], p["seat"], p["cabin"]) for p in self.passes_of(1)],
                         [("Alice", "12A", "C"), ("Bob", None, None), ("Carla", None, "F")])
        self.assertEqual(self.add(passengers="Alice business")["result"], "unchanged")


class Edit(Typed):
    def edited(self, changes, n=2, day=None):
        return self.message("edited", code="FR 1234", day=dmy(day or self.day), changes=changes,
                            n=eng.manual.count_text(n), suffix=TRACKED)

    def test_each_field_and_clear(self):
        self.add_typed()
        for change, part, column, value in (
                ({"gate": "a3"}, "gate cambiato", "pass_gate", "A3"),
                ({"terminal": "-"}, "terminal cambiato", "pass_terminal", None),
                ({"terminal": "2E"}, "terminal cambiato", "pass_terminal", "2E"),
                ({"arrival_time": "09:30"}, "orario di arrivo cambiato", "pass_arrival",
                 local(self.day, "09:30", LONDON).isoformat()),
                ({"arrival_time": "-"}, "orario di arrivo cambiato", "pass_arrival", None),
                ({"departure_time": "06:15"}, "orario di partenza cambiato", "pass_departure",
                 local(self.day, "06:15", ROME).isoformat()),
                ({"gate": "-", "departure_time": "-"}, "orario di partenza cambiato, gate cambiato", "pass_gate",
                 None)):
            with self.subTest(change=change):
                self.assertEqual(self.edit(1, **change), (200, {"id": 1, "result": "edited",
                                                                "message": self.edited(part)}))
                self.assertEqual(self.row(1)[column], value)
                self.assertEqual(self.edit(1, **change), (200, {"id": 1, "result": "unchanged", "message": (
                    f"Nessuna modifica: FR 1234 del {dmy(self.day)} è già così.")}))
        self.assertEqual([self.row(1)[k] for k in ("pass_departure", "pass_arrival", "pass_gate")], [None] * 3)
        d = self.details(1)
        self.assertEqual((d["departure_time"], d["arrival_time"], d["terminal"], d["gate"]), ("", "", "2E", ""))

    def test_classes_by_word_and_by_class(self):
        self.add_typed()
        self.assertEqual(self.edit(1, passengers="Alice 12A economy, Bob business")[1]["message"],
                         self.edited("2 classi cambiate"))
        self.assertEqual([p["cabin"] for p in self.passes_of(1)], ["Y", "C"])
        # the prefilled line (no class words) and the classes shown: nothing changes, no class is cleared
        self.assertEqual(self.edit(1, passengers=self.line(1))[1]["result"], "unchanged")
        self.assertEqual(self.edit(1, passengers="Alice 12A economy, Bob business")[1]["result"], "unchanged")
        self.assertEqual([p["cabin"] for p in self.passes_of(1)], ["Y", "C"])

    def test_a_pass_in_j_round_trips(self):
        """The cabin J of a boarding pass is «business»: sent back as a word, it is the same class."""
        self.upload(self.boarding_pass(seat="012A", cabin="J"))
        self.assertEqual(self.details(1)["classes"], "Mario business")
        self.assertEqual(self.edit(1, passengers="Mario 12A business")[1]["result"], "unchanged")
        self.assertEqual(self.passes_of(1)[0]["cabin"], "J")
        self.assertEqual(self.edit(1, passengers="Mario 12A prima")[1]["message"], self.edited("1 classe cambiata", 1))
        self.assertEqual(self.passes_of(1)[0]["cabin"], "F")

    def test_an_arrival_needs_a_departure(self):
        self.add()
        self.refused(400, eng.manual.TEXTS["arrival_needs_departure"], action="edit", ref=self.ref(1),
                     arrival_time="09:00")
        self.add_typed(flight="FR 9")
        self.refused(400, eng.manual.TEXTS["arrival_needs_departure"], action="edit", ref=self.ref(2),
                     departure_time="-")                                      # its arrival would stay alone

    def test_a_new_departure_keeps_the_local_arrival(self):
        self.add_typed(departure_time="22:00", arrival_time="06:00")                  # 22:00 -> 06:00 the next day
        self.assertEqual(self.row(1)["pass_arrival"], local(self.day + days(1), "06:00", LONDON).isoformat())
        self.assertEqual(self.edit(1, departure_time="05:00")[1]["message"], self.edited("orario di partenza "
                                                                                          "cambiato"))
        self.assertEqual(self.row(1)["pass_arrival"], local(self.day, "06:00", LONDON).isoformat())

    def test_refs(self):
        """BC-B7: a time edited or a pass uploaded makes a ref stale; the clock's writes do not."""
        self.add_typed()
        ref = self.ref(1)
        self.assertEqual(self.post(action="edit", ref=ref, gate="C1")[1]["result"], "edited")
        self.assertNotEqual(self.ref(1), ref)
        self.refused(409, eng.manual.TEXTS["changed"], action="edit", ref=ref, terminal="9")
        # the same request sent again after a timeout: already so, 200
        self.assertEqual(self.post(action="edit", ref=ref, gate="C1")[1]["result"], "unchanged")
        ref = self.ref(1)
        self.answers = [self.api()]
        eng.check_flights(CFG, self.at(20), lookup=self.lookup)
        self.assertNotEqual(self.row(1)["status"], None)
        self.assertEqual(self.ref(1), ref)                        # status, snapshot, checks: not in the version
        sem = {"airlineCode": "FR", "flightNumber": 1234, "departureAirportCode": "BLQ",
               "currentDepartureDate": local(self.day, "07:10", ROME).isoformat()}
        self.upload(tf.pkpass(tf.bcbp(day=self.julian), sem))
        self.assertNotEqual(self.ref(1), ref)

    def test_a_departure_moved_moves_the_next_check(self):
        """A flight already checked, with no AviationStack data: its checks follow the typed departure."""
        self.add_typed()
        self.answers = [None]
        eng.check_flights(CFG, self.at(20), lookup=self.lookup)
        self.assertEqual(self.row(1)["next_check"], eng.utc(self.dep() - datetime.timedelta(minutes=60)))
        self.edit(1, departure_time="08:00")
        self.assertEqual(self.row(1)["next_check"],
                         eng.utc(local(self.day, "08:00", ROME) - datetime.timedelta(minutes=60)))


class EditDate(Typed):
    """BC-B8: a new date re-bases the typed times to the same local times; the rest of the old day goes."""

    def test_rebased(self):
        self.add_typed(departure_time="22:00", arrival_time="06:00")
        new = self.day + days(3)
        before = datetime.datetime.now(datetime.timezone.utc)
        code, reply = self.edit(1, date=new.isoformat())
        self.assertEqual((code, reply["message"]), (200, self.message(
            "edited", code="FR 1234", day=dmy(new), changes=f"data cambiata (prima il {dmy(self.day)}), orari "
            "spostati al nuovo giorno", n="2 passeggeri", suffix=TRACKED)))
        v = self.row(1)
        self.assertEqual((v["date"], v["pass_departure"], v["pass_arrival"], v["pass_terminal"], v["pass_gate"]),
                         (new.isoformat(), local(new, "22:00", ROME).isoformat(),
                          local(new + days(1), "06:00", LONDON).isoformat(), None, None))
        self.assertTrue(eng.utc(before) <= v["next_check"] <= eng.utc(self.now()))
        d = self.details(1)
        self.assertEqual((d["departure_time"], d["arrival_time"], d["terminal"], d["gate"]), ("22:00", "06:00", "", ""))

    def test_times_sent_with_the_date_win(self):
        self.add_typed()
        new = self.day + days(1)
        code, reply = self.edit(1, date=new.isoformat(), departure_time="06:00", gate="D4")
        self.assertIn("data cambiata (prima il", reply["message"])
        self.assertIn("orari spostati al nuovo giorno, orario di partenza cambiato, gate cambiato", reply["message"])
        v = self.row(1)
        self.assertEqual((v["pass_departure"], v["pass_arrival"], v["pass_gate"]),
                         (local(new, "06:00", ROME).isoformat(), local(new, "09:00", LONDON).isoformat(), "D4"))

    def test_nothing_typed_nothing_moved(self):
        self.add()
        code, reply = self.edit(1, date=(self.day + days(1)).isoformat())
        self.assertNotIn("orari spostati", reply["message"])

    def test_over_a_dst_change(self):
        """The same local time also across a change of the clocks (the next one in Europe/Rome from today)."""
        self.add_typed()
        day = self.day
        while (local(day, "12:00", ROME).utcoffset() == local(day + days(1), "12:00", ROME).utcoffset()
               and day < self.today + days(300)):
            day += days(1)
        new = day + days(1)
        self.edit(1, date=new.isoformat())
        self.assertEqual(self.details(1)["departure_time"], "07:00")
        self.assertEqual(datetime.datetime.fromisoformat(self.row(1)["pass_departure"]).astimezone(ROME).time(),
                         datetime.time(7, 0))


class Precedence(Typed):
    """D3: AviationStack's data wins at read time; GET /choices keeps showing the typed values."""

    def test_aviationstack_wins(self):
        self.add_typed()
        self.answers = [self.api(dep_gate="A5", dep_terminal="3")]
        eng.check_flights(CFG, self.at(20), lookup=self.lookup)
        card = self.computed()["next_flight"][1]
        self.assertEqual({k: card[k] for k in ("departure_time", "arrival_time", "gate", "terminal", "source")},
                         {"departure_time": "06:30", "arrival_time": "07:50", "gate": "A5", "terminal": "3",
                          "source": "aviationstack"})
        d = self.details(1)
        self.assertEqual((d["departure_time"], d["arrival_time"], d["terminal"], d["gate"]), ("07:00", "09:00", "1",
                                                                                              "B12"))
        # without terminal and gate from AviationStack, the typed ones fill in
        self.answers = [self.api()]
        eng.update_flight(1, next_check=eng.utc(self.at(19)))
        eng.check_flights(CFG, self.at(19), lookup=self.lookup)
        card = self.computed()["next_flight"][1]
        self.assertEqual((card["gate"], card["terminal"], card["departure_time"]), ("B12", "1", "06:30"))

    def test_a_typed_gate_is_a_known_gate(self):
        """flights.changes starts from pass_gate: AviationStack's other gate is notified as a change."""
        self.add_typed()
        self.answers = [self.api(dep_gate="A5")]
        eng.check_flights(CFG, self.at(20), lookup=self.lookup)
        texts = [(n["title"], n["message"]) for n in self.notifications()]
        self.assertTrue(texts and all(t == "FR 1234: cambia il gate" for t, _ in texts), texts)
        self.assertTrue(all("Gate A5 (prima B12)" in m for _, m in texts), texts)


class Checks(Typed):
    """BC-B5: the checks at 60/30/10 minutes before a typed departure; the gate reminder without AviationStack."""

    def test_checks_follow_the_typed_departure(self):
        self.add_typed()
        self.answers = [None]                                       # AviationStack does not know it (yet)
        eng.check_flights(CFG, self.at(20), lookup=self.lookup)
        for minutes in (60, 30, 10):
            due = self.dep() - datetime.timedelta(minutes=minutes)
            self.assertEqual(self.row(1)["next_check"], eng.utc(due))
            self.answers = [None]
            self.assertEqual(eng.check_flights(CFG, due.astimezone(datetime.timezone.utc), lookup=self.lookup), 1)
        self.assertEqual(self.row(1)["next_check"], eng.utc(self.dep() + datetime.timedelta(minutes=20)))

    def test_the_gate_reminder_of_a_typed_flight(self):
        self.add_typed()
        leave = self.dep() - datetime.timedelta(minutes=30 + 10 + 15)          # boarding, economy, the walk
        self.assertEqual(eng.notify_gate(CFG, (leave - datetime.timedelta(minutes=1)).astimezone(datetime.timezone.utc)), 0)
        self.assertEqual(eng.notify_gate(CFG, (leave + datetime.timedelta(minutes=1)).astimezone(datetime.timezone.utc)), 2)
        got = sorted((n["to"], n["title"], n["message"]) for n in self.notifications())
        text = ("Al gate per le 06:20 (imbarco alle 06:30 circa), partenza alle 07:00 per Londra Stansted. "
                "Terminal 1.")
        self.assertEqual(got, [("mobile_app_alice", "FR 1234: avviati al gate B12", text),
                               ("mobile_app_bob", "FR 1234: avviati al gate B12", text)])
        self.assertEqual(eng.notify_gate(CFG, (leave + datetime.timedelta(minutes=2)).astimezone(datetime.timezone.utc)), 0)

    def test_no_reminder_without_a_time(self):
        self.add(gate="B12")
        self.assertEqual(eng.notify_gate(CFG, self.at(1)), 0)


class Statistics(Typed):
    """BC-B4: the duration from the typed pair (the 4th source, estimated) and the typed classes."""

    def test_duration_and_classes(self):
        past = self.today - days(30)
        self.add_typed(date=past.isoformat(), passengers="Alice business, Bob business")
        stats = self.computed()["flight_statistics"][1]
        minutes = int((local(past, "09:00", LONDON) - local(past, "07:00", ROME)).total_seconds() // 60)
        self.assertEqual((stats["list"][0]["duration_min"], stats["list"][0]["estimated"], stats["air_time_min"],
                          stats["estimated_durations"]), (minutes, True, minutes, 1))
        self.assertEqual(stats["cabins"], {"business": 1, "economy": 0, "unknown": 0})
        # without the arrival: the distance estimate, as before
        self.edit(1, arrival_time="-")
        stats = self.computed()["flight_statistics"][1]
        self.assertEqual(stats["list"][0]["duration_min"], eng.flight_stats.duration(None, 1155)[0])


class Pkpass(Typed):
    """BC-B9: a Wallet pass's departureTerminal and arrival date fill pass_terminal and pass_arrival (coalesce); a
    photo leaves the typed values."""

    def semantics(self, **over):
        return {"airlineCode": "FR", "flightNumber": 1234, "departureAirportCode": "BLQ",
                "destinationAirportCode": "STN", "currentDepartureDate": self.dep().isoformat(),
                "currentArrivalDate": self.arr().isoformat(), "departureTerminal": "1", "departureGate": "B7", **over}

    def test_terminal_and_arrival_from_the_pass(self):
        self.upload(tf.pkpass(tf.bcbp(day=self.julian), self.semantics()))
        v = self.row(1)
        self.assertEqual((v["pass_departure"], v["pass_arrival"], v["pass_terminal"], v["pass_gate"]),
                         (self.dep().isoformat(), self.arr().isoformat(), "1", "B7"))
        card = self.computed()["next_flight"][1]
        self.assertEqual((card["terminal"], card["arrival_time"], card["source"]), ("1", "09:00", "boarding_pass"))
        title, text = eng.flights.gate_message(v, None)
        self.assertIn("Terminal 1.", text)
        self.assertEqual(self.details(1)["terminal"], "1")

    def test_original_arrival_and_a_not_credible_one(self):
        self.upload(tf.pkpass(tf.bcbp(day=self.julian), self.semantics(
            currentArrivalDate=None, originalArrivalDate=self.arr().isoformat())))
        self.assertEqual(self.row(1)["pass_arrival"], self.arr().isoformat())
        self.upload(tf.pkpass(tf.bcbp(day=self.julian, seat="014C"), self.semantics(
            currentArrivalDate=(self.dep() + datetime.timedelta(minutes=5)).isoformat())))
        self.assertEqual(self.row(1)["pass_arrival"], self.arr().isoformat())      # coalesce: the earlier one stays

    def test_a_pass_after_typed_values_overwrites_a_photo_does_not(self):
        self.add_typed(passengers="Mario", terminal="2", gate="C1")
        self.upload_photo([tf.bcbp(day=self.julian)])
        v = self.row(1)
        self.assertEqual((v["pass_departure"], v["pass_terminal"], v["pass_gate"]), (self.dep().isoformat(), "2",
                                                                                      "C1"))
        self.upload(tf.pkpass(tf.bcbp(day=self.julian), self.semantics(
            currentDepartureDate=local(self.day, "07:20", ROME).isoformat())))
        v = self.row(1)
        self.assertEqual((v["pass_departure"], v["pass_terminal"], v["pass_gate"]),
                         (local(self.day, "07:20", ROME).isoformat(), "1", "B7"))
        self.assertEqual(len(self.passes_of(1)), 1)


if __name__ == "__main__":
    unittest.main()
