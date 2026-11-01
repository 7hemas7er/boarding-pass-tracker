"""Tests for flights.py: synthetic Wallet passes and AviationStack answers, no network.

  python3 -I -m unittest discover -s tracker -p 'test_flights.py' -v
"""
import datetime, io, json, os, re, sys, unittest, urllib.error, urllib.parse, zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import support  # noqa: E402,F401  (the ported core on sys.path; its log lines to the current stdout)
import flights as fl  # noqa: E402

UTC = datetime.timezone.utc
TODAY = datetime.date(2026, 11, 4)          # the test flights leave on 6/11: pass 2 days before
# The example of IATA Resolution 792: AC 834 from Montreal to Frankfurt, day 326 (22/11), seat 1A
IATA = "M1DESMARAIS/LUC       EABC123 YULFRAAC 0834 326J001A0025 100"


def bcbp(name="ROSSI/MARIO MR", pnr="XYZ789", origin="BLQ", destination="STN", airline="FR", number="1234", day=310,
         seat="012A", seq="0042", others=(), cabin="Y"):
    """A BCBP with the first leg and the others (tuples origin, destination, airline, number, day, seat), without
    the variable part."""
    def leg(pnr, origin, destination, airline, number, day, seat, seq):
        return f"{pnr:<7}{origin}{destination}{airline:<3}{number:0>4} {day:03d}{cabin}{seat:>4}{seq:<5}100"
    head = f"M{1 + len(others)}{name:<20}E"
    body = leg(pnr, origin, destination, airline, number, day, seat, seq)
    for t in others:
        body += leg(pnr, *t, "0043")
    return head + body


def pkpass(msg=None, semantics=None, transit="PKTransitTypeAir", extra=None, kind="boardingPass", **pass_):
    # "segreto-del-pass" is the pass token: test_engine checks that it never leaves the service
    p = {"formatVersion": 1, "passTypeIdentifier": "pass.it.prova", "serialNumber": "1", "authenticationToken": "segreto-del-pass",
         "webServiceURL": "https://example.invalid/", kind: {"transitType": transit, "primaryFields": []}, **pass_}
    if msg is not None:
        p["barcodes"] = [{"format": "PKBarcodeFormatAztec", "message": msg, "messageEncoding": "iso-8859-1"}]
    if semantics is not None:
        p["semantics"] = semantics
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("pass.json", json.dumps(p))
        z.writestr("manifest.json", "{}")
        z.writestr("signature", b"\x00")
        for name, data in (extra or {}).items():
            z.writestr(name, data)
    return buf.getvalue()


def api_flight(status="scheduled", date="2026-11-06", iata="FR1234", codeshared=None, **fields):
    """An AviationStack entry as it really arrives: local times with a fake "+00:00". It departs at 06:30 in
    Bologna (05:30Z) and arrives at 07:50 in London (07:50Z). dep_<field> and arr_<field> change departure and
    arrival; an "HH:MM" time becomes that time on day `date`."""
    at = lambda x: f"{date}T{x}:00+00:00" if isinstance(x, str) and re.fullmatch(r"\d\d:\d\d", x) else x
    dep = {"airport": "Bologna International Airport", "timezone": "Europe/Rome", "iata": "BLQ", "icao": "LIPE",
           "terminal": None, "gate": None, "delay": None, "scheduled": at("06:30"), "estimated": at("06:30"),
           "actual": None, "estimated_runway": None, "actual_runway": None}
    arr = {"airport": "London Stansted Airport", "timezone": "Europe/London", "iata": "STN", "icao": "EGSS",
           "terminal": None, "gate": None, "baggage": None, "delay": None, "scheduled": at("07:50"),
           "estimated": None, "actual": None, "estimated_runway": None, "actual_runway": None}
    for k, val in fields.items():
        where, field = k.split("_", 1)
        {"dep": dep, "arr": arr}[where][field] = at(val)
    return {"flight_date": date, "flight_status": status, "departure": dep, "arrival": arr,
            "airline": {"iata": iata[:2]}, "flight": {"number": iata[2:], "iata": iata, "codeshared": codeshared},
            "live": None}


FLIGHT = {"airline": "FR", "number": "1234", "date": "2026-11-06", "origin": "BLQ", "destination": "STN",
          "pass_departure": None, "relevant_date": None}


class BCBP(unittest.TestCase):
    def test_iata_example(self):
        (t,) = fl.parse_bcbp(IATA, datetime.date(2026, 11, 20))
        self.assertEqual({k: t[k] for k in ("passenger", "first_name", "pnr", "origin", "destination", "airline",
                                            "number", "date", "cabin", "seat", "sequence")},
                         {"passenger": "Luc Desmarais", "first_name": "Luc", "pnr": "ABC123", "origin": "YUL",
                          "destination": "FRA", "airline": "AC", "number": "834", "date": "2026-11-22", "cabin": "J",
                          "seat": "1A", "sequence": 25})

    def test_two_legs(self):
        legs = fl.parse_bcbp(bcbp(others=[("STN", "DUB", "FR", "202", 311, "003C")]), TODAY)
        self.assertEqual([(t["origin"], t["destination"], t["number"], t["date"], t["seat"]) for t in legs],
                         [("BLQ", "STN", "1234", "2026-11-06", "12A"), ("STN", "DUB", "202", "2026-11-07", "3C")])
        self.assertEqual({t["passenger"] for t in legs}, {"Mario Rossi"})

    def test_year_from_upload_date(self):
        # in December day 10 is the next January; in January day 360 is the December just gone
        self.assertEqual(fl.julian_date(10, datetime.date(2027, 1, 8)), datetime.date(2027, 1, 10))
        self.assertEqual(fl.julian_date(360, datetime.date(2027, 1, 5)), datetime.date(2026, 12, 26))
        today = datetime.date(2026, 11, 12)                                   # day 316
        # a spring pass uploaded in November is a past flight, not next year's: for day 105 (15/04) next April
        # would even be nearer (154 days against 211)
        self.assertEqual(fl.julian_date(105, today), datetime.date(2026, 4, 15))
        self.assertEqual(fl.julian_date(150, today), datetime.date(2026, 5, 30))
        # at most PASS_LEAD_DAYS (3) ahead: one day later it is last year's flight
        self.assertEqual(fl.julian_date(319, today), datetime.date(2026, 11, 15))   # in 3 days
        self.assertEqual(fl.julian_date(320, today), datetime.date(2025, 11, 16))   # in 4: past
        self.assertEqual(fl.julian_date(321, today), datetime.date(2025, 11, 17))
        self.assertEqual(fl.julian_date(323, today), datetime.date(2025, 11, 19))   # in a week
        # day 366 is only 31/12 of a leap year: within the same window, or it is not a boarding pass
        self.assertEqual(fl.julian_date(366, datetime.date(2028, 12, 28)), datetime.date(2028, 12, 31))   # in 3 days
        self.assertEqual(fl.julian_date(366, datetime.date(2029, 12, 31)), datetime.date(2028, 12, 31))   # 365 back
        for today in (datetime.date(2028, 12, 27), datetime.date(2027, 11, 12), datetime.date(2030, 1, 1)):
            with self.assertRaises(fl.NotABoardingPass, msg=today):          # in 4 days, in 415, 366 back
                fl.julian_date(366, today)

    def test_year_from_issue_date(self):
        today = datetime.date(2026, 11, 12)                                   # day 316
        self.assertEqual(fl.issue_date(6, 149, today), datetime.date(2026, 5, 29))
        self.assertEqual(fl.issue_date(5, 300, today), datetime.date(2025, 10, 27))
        self.assertEqual(fl.issue_date(6, 317, today), datetime.date(2026, 11, 13))  # tomorrow (time zones): this year
        self.assertEqual(fl.issue_date(6, 318, today), datetime.date(2016, 11, 13))  # after tomorrow: 10 years before
        issued = datetime.date(2026, 12, 30)
        # check-in in December, flight in January
        self.assertEqual(fl.julian_date(2, today, issued), datetime.date(2027, 1, 2))
        self.assertEqual(fl.julian_date(364, today, issued), datetime.date(2026, 12, 30))

        def with_conditional(cond, day):
            msg = bcbp(day=day)
            return msg[:-2] + f"{len(cond):02X}" + cond

        # in the BCBP: ">", version "5", length "18", three 1-character fields "0MW", issue date "6149"
        # (29/05/2026) -> flight on day 150 = 30/05/2026
        (t,) = fl.parse_bcbp(with_conditional(">5180MW6149BFR", 150), today)
        self.assertEqual(t["date"], "2026-05-30")
        # the issue date wins over the 3-day rule, which alone would give another year in both cases:
        # a pass issued on 12/11/2025 ("5316") uploaded a year later: the rule alone would say 14/11/2026
        self.assertEqual(fl.julian_date(318, today), datetime.date(2026, 11, 14))
        (t,) = fl.parse_bcbp(with_conditional(">5180MW5316BFR", 318), today)
        self.assertEqual(t["date"], "2025-11-14")
        # no issue date in its place (a malformed section, or blanks): the 3-day rule (PASS_LEAD_DAYS), not 2025
        for cond in (">5180MWW5316BFR", ">5180MW    BFR"):
            (t,) = fl.parse_bcbp(with_conditional(cond, 318), today)
            self.assertEqual(t["date"], "2026-11-14", cond)
        # a check-in done today ("6316", 12/11/2026) for a flight in six days (day 322), uploaded right away: six
        # days is beyond PASS_LEAD_DAYS, so the rule alone would say 18/11/2025
        self.assertEqual(fl.julian_date(322, today), datetime.date(2025, 11, 18))
        (t,) = fl.parse_bcbp(with_conditional(">5180MW6316BFR", 322), today)
        self.assertEqual(t["date"], "2026-11-18")
        for bad in (bcbp(day=366), bcbp(day=310).replace("310Y", "3\u00b20Y")):   # 366 in no nearby leap year, "²"
            with self.assertRaises(fl.NotABoardingPass):
                fl.parse_bcbp(bad, today)

    def test_broken_formats(self):
        for msg in ("", "https://airline.example/bp/123", "M1" + "X" * 30, IATA.replace("YULFRA", "YU1FRA"),
                    IATA.replace(" 326", " 999"), "M5" + IATA[2:], None, IATA[:50]):
            with self.assertRaises(fl.NotABoardingPass, msg=msg):
                fl.parse_bcbp(msg, TODAY)

    def test_malformed_fields(self):
        # flight number and seat in ASCII digits only: "١٢٣٤" (Arabic-Indic) or "１２３４" (full width) are not a
        # flight; a seat like that is no seat, and the pass is still read
        for number in ("\u0661\u0662\u0663\u0664", "\uff11\uff12\uff13\uff14"):
            with self.assertRaises(fl.NotABoardingPass, msg=number):
                fl.parse_bcbp(bcbp(number=number), TODAY)
        (t,) = fl.parse_bcbp(bcbp(seat="\u0660\u0661\u0662A"), TODAY)
        self.assertEqual((t["number"], t["seat"]), ("1234", None))
        # the sequence is up to 5 digits, or 4 and a letter: anything else is no sequence, not an error
        for seq, expected in (("0042", 42), ("00012", 12), ("0012A", 12), ("00000", 0), ("", None), ("A042", None),
                              ("0A42", None), ("0012a", None), ("12 A", None), ("\u0660\u0664\u0662", None)):
            (t,) = fl.parse_bcbp(bcbp(seq=seq), TODAY)
            self.assertEqual(t["sequence"], expected, repr(seq))
        # the size of the variable part is two hex digits: no sign, blank or non-ASCII digit
        self.assertEqual(len(fl.parse_bcbp(bcbp()[:-2] + "0a" + "x" * 10, TODAY)), 1)
        for size in ("-1", "+0", " 0", "0 ", "\u0660\u0660", "0x", "G0"):
            with self.assertRaises(fl.NotABoardingPass, msg=repr(size)):
                fl.parse_bcbp(bcbp()[:-2] + size, TODAY)

    def test_names(self):
        self.assertEqual(fl.passenger_name("ROSSI/MARIA ANNA MRS"), ("Maria Anna Rossi", "Maria"))
        self.assertEqual(fl.passenger_name("BIANCHI/LUCA"), ("Luca Bianchi", "Luca"))
        self.assertEqual(fl.passenger_name("   "), (None, None))
        # without "/" the first word is often the surname: no first name (which would go to HA)
        self.assertEqual(fl.passenger_name("GALLO DAVIDE MR"), ("Gallo Davide", None))


class Pkpass(unittest.TestCase):
    def test_recognized_by_content(self):
        self.assertTrue(fl.is_pkpass(pkpass(bcbp())))
        self.assertFalse(fl.is_pkpass(b"PK\x03\x04broken"))
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("document.xml", "<a/>")
        self.assertFalse(fl.is_pkpass(buf.getvalue()))
        self.assertFalse(fl.is_pkpass(b"%PDF-1.7"))

    def test_pass_with_bcbp_and_semantics(self):
        sem = {"airlineCode": "FR", "flightNumber": 1234, "departureAirportCode": "BLQ", "destinationAirportCode": "STN",
               "originalDepartureDate": "2026-11-06T06:30:00+01:00", "currentBoardingDate": "2026-11-06T05:55:00+01:00",
               "departureGate": "A12", "departureTerminal": "1"}
        (t,) = fl.read_boarding_pass(pkpass(bcbp(), sem, relevantDate="2026-11-06T05:55:00+01:00"), TODAY)
        self.assertEqual((t["airline"], t["number"], t["date"], t["origin"], t["destination"], t["seat"],
                          t["first_name"]),
                         ("FR", "1234", "2026-11-06", "BLQ", "STN", "12A", "Mario"))
        self.assertEqual((t["pass_departure"], t["pass_boarding"], t["pass_gate"], t["terminal"], t["relevant_date"]),
                         ("2026-11-06T06:30:00+01:00", "2026-11-06T05:55:00+01:00", "A12", "1", "2026-11-06T05:55:00+01:00"))

    def test_semantics_of_another_flight_ignored(self):
        sem = {"flightNumber": "999", "departureAirportCode": "BLQ", "departureGate": "B3",
               "originalDepartureDate": "2026-11-06T06:30:00+01:00"}
        (t,) = fl.read_boarding_pass(pkpass(bcbp(), sem), TODAY)
        self.assertEqual((t["pass_gate"], t["pass_departure"]), (None, None))
        sem = {"flightNumber": "1234", "departureAirportCode": "BLQ", "originalDepartureDate": "2026-11-09T06:30:00+01:00"}
        self.assertIsNone(fl.read_boarding_pass(pkpass(bcbp(), sem), TODAY)[0]["pass_departure"])  # another day: BCBP wins

    def test_semantics_suffice_without_bcbp(self):
        sem = {"airlineCode": "AZ", "flightNumber": "2041", "departureAirportCode": "LIN", "destinationAirportCode": "FCO",
               "originalDepartureDate": "2026-11-08T07:00:00+01:00", "passengerName": {"givenName": "Anna", "familyName": "Verdi"}}
        (t,) = fl.read_boarding_pass(pkpass("https://az.example/bp/1", sem), TODAY)
        self.assertEqual((t["airline"], t["number"], t["date"], t["origin"], t["destination"], t["first_name"],
                          t["passenger"]),
                         ("AZ", "2041", "2026-11-08", "LIN", "FCO", "Anna", "Anna Verdi"))
        with self.assertRaises(fl.NotABoardingPass):
            fl.read_boarding_pass(pkpass("https://az.example/bp/1", {"airlineCode": "AZ"}), TODAY)

    def test_not_boarding_passes(self):
        for data in (pkpass(bcbp(), kind="eventTicket"), pkpass(bcbp(), transit="PKTransitTypeTrain"), b"PK\x03\x04xx",
                     pkpass(bcbp(), extra={f"f{i}": "" for i in range(fl.MAX_ZIP_ENTRIES)})):
            with self.assertRaises(fl.NotABoardingPass):
                fl.read_boarding_pass(data, TODAY)

    def test_boarding_pass_from_photo_barcodes(self):
        # a Wallet screenshot: codeshare printed (AZ 7001), but the BCBP holds the operating carrier
        msg = bcbp(name="ROSSI/MARIO", origin="BLQ", destination="STN", airline="FR", number="1234", day=314, seat="012A")
        (t,) = fl.boarding_pass_from_barcodes(["PAGOPA|002|...", "https://example.invalid", msg], datetime.date(2026, 11, 10))
        self.assertEqual((t["airline"], t["number"], t["date"], t["origin"], t["destination"], t["seat"],
                          t["pass_departure"], t["pass_gate"]),
                         ("FR", "1234", "2026-11-10", "BLQ", "STN", "12A", None, None))
        self.assertIsNone(fl.boarding_pass_from_barcodes(["PAGOPA|002|301000001234567864|80012345676|18600"], TODAY))
        self.assertIsNone(fl.boarding_pass_from_barcodes([], TODAY))

    def test_huge_pass_json_rejected_without_reading(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("pass.json", " " * (fl.MAX_PASS_BYTES + 10))
        with self.assertRaisesRegex(fl.NotABoardingPass, "troppo grande"):
            fl.read_pass(buf.getvalue())


class AviationStack(unittest.TestCase):
    NOW = datetime.datetime(2026, 11, 6, 1, 0, tzinfo=UTC)

    def response(self, body, code=200):
        calls = []

        class R(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def opener(url, timeout):
            calls.append(url)
            if code != 200:
                raise urllib.error.HTTPError(url, code, "no", {}, io.BytesIO(json.dumps(body).encode()))
            return R(json.dumps(body).encode())
        return opener, calls

    def test_queryable(self):
        self.assertTrue(fl.queryable("2026-11-06", self.NOW))
        self.assertTrue(fl.queryable("2026-11-07", self.NOW))                       # time zones: tomorrow in UTC
        self.assertFalse(fl.queryable("2026-11-08", self.NOW))
        self.assertFalse(fl.queryable("2026-11-04", self.NOW))

    def test_local_times_with_fake_utc(self):
        # verified on LH 400: "11:00+00:00" is Frankfurt time, and the estimate ignores the delay
        s = fl.normalize(api_flight(dep_delay=25, dep_gate="A12", dep_terminal="1"))
        self.assertEqual((s["departure_scheduled"], s["departure_estimated"], s["arrival_scheduled"]),
                         ("2026-11-06T05:30:00+00:00", "2026-11-06T05:55:00+00:00", "2026-11-06T07:50:00+00:00"))
        self.assertEqual((s["gate"], s["terminal"], s["departure_delay"], s["origin_city"], s["destination_city"],
                          s["destination_tz"]),
                         ("A12", "1", 25, "Bologna", "London Stansted", "Europe/London"))
        # in summer Rome is at +2
        self.assertEqual(fl.normalize(api_flight(date="2026-07-10"))["departure_scheduled"], "2026-07-10T04:30:00+00:00")

    def test_call_and_operating_carrier_choice(self):
        cs = {"airline_iata": "fr", "flight_number": "1234", "flight_iata": "fr1234"}
        opener, calls = self.response({"pagination": {"count": 4}, "data": [
            api_flight(iata="XX7001", codeshared=cs, dep_gate="Z9"),
            api_flight(dep_gate="A12"),
            api_flight(date="2026-11-05", status="landed", dep_gate="B1"),
            api_flight(dep_iata="STN", dep_gate="C3")]})
        s = fl.lookup_flight("fake-key", "FR", "1234", "2026-11-06", "BLQ", self.NOW, opener=opener)
        (url,) = calls
        self.assertTrue(url.startswith("https://api.aviationstack.com/v1/flights?access_key=fake-key&flight_iata=FR1234"))
        self.assertEqual(s["gate"], "A12")
        # only the codeshare (the operating carrier is missing): fine all the same, it is the same data
        opener, _ = self.response({"data": [api_flight(iata="XX7001", codeshared=cs, dep_gate="Z9")]})
        self.assertEqual(fl.lookup_flight("k", "FR", "1234", "2026-11-06", "BLQ", self.NOW, opener=opener)["gate"], "Z9")
        opener, _ = self.response({"data": []})
        self.assertIsNone(fl.lookup_flight("k", "FR", "1234", "2026-11-06", "BLQ", self.NOW, opener=opener))

    def test_errors_without_key_in_message(self):
        cases = [(({"error": {"code": "invalid_access_key"}}, 401), 401, "invalid_access_key"),
                 (({"error": {"code": "usage_limit_reached"}}, 200), 429, "usage_limit_reached"),
                 (({"error": {"code": "invalid_access_key", "message": "secret-key"}}, 200), 401, "invalid_access_key"),
                 (({}, 500), 500, "errore")]
        for (body, http), code, text in cases:
            opener, _ = self.response(body, http)
            with self.assertRaises(fl.APIError) as e:
                fl.lookup_flight("secret-key", "FR", "1234", "2026-11-06", "BLQ", self.NOW, opener=opener)
            self.assertEqual(e.exception.code, code)
            self.assertIn(text, str(e.exception))
            self.assertNotIn("secret-key", str(e.exception))
        with self.assertRaises(ValueError):
            fl.lookup_flight("k", "F/", "1234", "2026-11-06", "BLQ", self.NOW, opener=opener)


class Status(unittest.TestCase):
    def s(self, status="scheduled", **fields):
        return fl.normalize(api_flight(status, **fields))

    def test_texts(self):
        self.assertEqual(fl.status_text(FLIGHT, self.s()), "In orario")
        self.assertEqual(fl.status_text(FLIGHT, self.s(dep_delay=30)), "In ritardo di 30 min")
        self.assertEqual(fl.status_text(FLIGHT, self.s("active", dep_actual="06:40", dep_actual_runway="06:52")), "In volo")
        self.assertEqual(fl.status_text(FLIGHT, self.s("active")), "In volo")      # active without real times
        self.assertEqual(fl.status_text(FLIGHT, self.s("landed", arr_actual_runway="08:10", arr_delay=20)),
                         "Atterrato con 20 min di ritardo")
        self.assertEqual(fl.status_text(FLIGHT, self.s("landed")), "Atterrato")
        self.assertEqual(fl.status_text(FLIGHT, self.s("cancelled")), "Cancellato")
        self.assertEqual(fl.status_text(FLIGHT, self.s("diverted")), "Deviato")
        self.assertEqual(fl.status_text(FLIGHT, self.s("incident")), "Problema segnalato")
        self.assertEqual(fl.status_text(FLIGHT, None), "Stato non disponibile")

    def test_phases(self):
        # English codes, published as next_flight.phase and never stored
        for status, fields, code in (("scheduled", {}, "scheduled"), ("scheduled", {"dep_actual": "06:40"}, "departed"),
                                     ("active", {"dep_actual_runway": "06:52"}, "airborne"), ("landed", {}, "landed"),
                                     ("cancelled", {}, "cancelled"), ("diverted", {}, "diverted"),
                                     ("incident", {}, "diverted")):
            self.assertEqual(fl.phase(self.s(status, **fields)), code, (status, fields))
        self.assertIsNone(fl.phase(None))
        self.assertEqual((fl.PHASE_SCHEDULED, fl.PHASE_DEPARTED, fl.PHASE_AIRBORNE, fl.PHASE_LANDED,
                          fl.PHASE_CANCELLED, fl.PHASE_DIVERTED),
                         ("scheduled", "departed", "airborne", "landed", "cancelled", "diverted"))

    def test_checks_at_60_30_10_minutes(self):
        s = self.s()                                                         # departs at 05:30Z on the 6th
        q = lambda x: datetime.datetime.fromisoformat(x).replace(tzinfo=UTC)
        nxt = lambda st, now, flight=FLIGHT: fl.next_check(flight, st, q(now))
        self.assertEqual(nxt(s, "2026-11-04T09:00"), q("2026-11-06T04:30"))      # pass uploaded days before
        self.assertEqual(nxt(s, "2026-11-06T04:30"), q("2026-11-06T05:00"))
        self.assertEqual(nxt(s, "2026-11-06T05:00"), q("2026-11-06T05:20"))
        self.assertEqual(nxt(s, "2026-11-06T05:20"), q("2026-11-06T05:50"))      # 20 minutes after departure
        self.assertEqual(nxt(s, "2026-11-06T07:00"), q("2026-11-06T08:00"))      # departure passed, no news
        # 40 minutes late (departs at 06:10Z): every 30 minutes, and the points move with the departure
        late = self.s(dep_delay=40)
        self.assertEqual(nxt(late, "2026-11-06T04:30"), q("2026-11-06T05:00"))
        self.assertEqual(nxt(late, "2026-11-06T05:00"), q("2026-11-06T05:10"))   # 60 minutes before 06:10
        self.assertEqual(nxt(late, "2026-11-05T09:00"), q("2026-11-06T05:10"))   # the day before: no half hours
        # airborne: at the scheduled arrival (07:50Z) plus 10 minutes; landed: done
        airborne = self.s("active", dep_actual_runway="06:50")
        self.assertEqual(nxt(airborne, "2026-11-06T06:00"), q("2026-11-06T08:00"))
        self.assertEqual(nxt(airborne, "2026-11-06T07:55"), q("2026-11-06T08:15"))
        self.assertIsNone(nxt(self.s("landed", arr_actual_runway="07:45"), "2026-11-06T08:00"))
        # only the BCBP, time unknown: at 4 am (machine time) on the day of the flight
        at4 = datetime.datetime(2026, 11, 6, 4).astimezone().astimezone(UTC)
        self.assertEqual(nxt(None, "2026-11-04T09:00"), at4)

    def test_calls_for_an_on_time_flight(self):
        q = lambda x: datetime.datetime.fromisoformat(x).replace(tzinfo=UTC)
        phases = [(q("2026-11-06T05:35"), self.s()), (q("2026-11-06T07:55"), self.s("active", dep_actual_runway="06:50")),
                  (q("2026-11-07T00:00"), self.s("landed", dep_actual_runway="06:50", arr_actual_runway="07:45"))]
        now, n = q("2026-11-02T09:00"), 0
        while (p := fl.next_check(FLIGHT, next((x for t, x in phases if now < t), phases[-1][1]), now)):
            now, n = p, n + 1
        self.assertEqual(n, 5)                         # 60, 30 and 10 minutes before, after departure, at arrival

    def test_is_over(self):
        self.assertIsNone(fl.next_check(FLIGHT, self.s("cancelled"), datetime.datetime(2026, 11, 5, tzinfo=UTC)))
        self.assertIsNone(fl.next_check(FLIGHT, self.s("diverted"), datetime.datetime(2026, 11, 6, 6, tzinfo=UTC)))
        # never found: a day after noon of the flight day (machine time) the checks stop, and not before
        noon = datetime.datetime.combine(datetime.date(2026, 11, 6), datetime.time(12)).astimezone()
        self.assertIsNotNone(fl.next_check(FLIGHT, None, noon + datetime.timedelta(hours=23, minutes=59)))
        self.assertIsNone(fl.next_check(FLIGHT, None, noon + datetime.timedelta(hours=24, minutes=1)))
        landed = self.s("landed", arr_actual_runway="07:45", arr_actual="07:52")
        self.assertFalse(fl.is_over(FLIGHT, landed, datetime.datetime(2026, 11, 6, 8, 30, tzinfo=UTC)))  # still in HA
        self.assertTrue(fl.is_over(FLIGHT, landed, datetime.datetime(2026, 11, 6, 9, 0, tzinfo=UTC)))


class Alerts(unittest.TestCase):
    def s(self, status="scheduled", **fields):
        return fl.normalize(api_flight(status, **fields))

    def test_cities_in_italian(self):
        self.assertEqual(fl.route(FLIGHT, self.s()), "Bologna (BLQ) → Londra Stansted (STN)")
        self.assertEqual(fl.route({**FLIGHT, "origin": "MUC", "destination": "CIA"}, {}),
                         "Monaco di Baviera (MUC) → Roma Ciampino (CIA)")
        other = {**FLIGHT, "destination": "XYZ"}
        self.assertEqual(fl.route(other, self.s(arr_iata="XYZ", arr_airport="Nowhere Regional Airport")),
                         "Bologna (BLQ) → Nowhere Regional (XYZ)")        # not in the table: the AviationStack name
        self.assertEqual(fl.route(other, None), "Bologna (BLQ) → XYZ (XYZ)")

    def test_first_gate_is_not_a_change(self):
        e, base = fl.changes({}, self.s(dep_gate="A12", dep_terminal="1"))
        self.assertEqual((e, base["gate"]), ([], "A12"))
        e, _ = fl.changes(base, self.s(dep_gate="A12"))
        self.assertEqual(e, [])
        e, _ = fl.changes(base, self.s(dep_gate="A14", dep_terminal="1"))
        self.assertEqual(e, ["gate"])
        self.assertEqual(fl.message(FLIGHT, self.s(dep_gate="A14", dep_terminal="1"), e, "A12"),
                         ("FR 1234: cambia il gate", "Gate A14 (prima A12), terminal 1. Partenza alle 06:30."))

    def test_gate_different_from_pass(self):
        self.assertEqual(fl.changes({}, self.s(dep_gate="B3"), pass_gate="A1")[0], ["gate"])
        self.assertEqual(fl.changes({}, self.s(dep_gate="A1"), pass_gate="A1")[0], [])

    def test_gate_assigned_after_head_to_gate(self):
        e, _ = fl.changes({"gate_sent": True}, self.s(dep_gate="C7"))
        self.assertEqual(e, ["gate"])
        self.assertEqual(fl.message(FLIGHT, self.s(dep_gate="C7"), e)[0], "FR 1234: gate assegnato")

    def test_delay_above_threshold_and_back(self):
        e, base = fl.changes({}, self.s(dep_delay=10))
        self.assertEqual(e, [])                                              # 10 minutes: nothing
        e, base = fl.changes(base, self.s(dep_delay=25))
        self.assertEqual(e, ["delay"])
        self.assertEqual(fl.message(FLIGHT, self.s(dep_delay=25), e),
                         ("FR 1234: in ritardo di 25 min",
                          "Partenza alle 06:55 invece delle 06:30, imbarco verso le 06:25."))
        e, base = fl.changes(base, self.s(dep_delay=0))
        self.assertEqual(e, ["delay"])
        self.assertEqual(fl.message(FLIGHT, self.s(), e)[0], "FR 1234: in orario")

    def test_no_alert_after_departure(self):
        departed = self.s("active", dep_gate="A14", dep_delay=40, dep_actual_runway="07:15")
        self.assertEqual(fl.changes({"gate": "A12"}, departed)[0], [])
        arrived = self.s("landed", dep_actual_runway="06:48", arr_actual_runway="07:41", arr_baggage="5")
        self.assertEqual(fl.changes({"gate": "A12"}, arrived)[0], [])

    def test_cancelled_overrides_the_rest(self):
        e, _ = fl.changes({"gate": "B1"}, self.s("cancelled", dep_gate="A1", dep_delay=60))
        self.assertEqual(e, ["cancelled"])
        self.assertEqual(fl.message(FLIGHT, self.s("cancelled"), e),
                         ("FR 1234 cancellato", "Bologna (BLQ) → Londra Stansted (STN) del 06/11: la compagnia l'ha cancellato."))

    def test_head_to_gate(self):
        s = self.s(dep_gate="A12", dep_terminal="1")                        # departs at 06:30, boarding estimated 06:00
        q = lambda x: datetime.datetime.fromisoformat(x).replace(tzinfo=UTC)
        self.assertEqual(fl.boarding_time(FLIGHT, s), ("2026-11-06T05:00:00+00:00", True))
        # economy: at the gate 10 minutes before boarding, set off 15 minutes before that
        self.assertEqual((fl.at_gate_time(FLIGHT, s), fl.leave_for_gate_time(FLIGHT, s)),
                         (q("2026-11-06T04:50"), q("2026-11-06T04:35")))
        self.assertFalse(fl.gate_alert_due(FLIGHT, s, {}, q("2026-11-06T04:34")))
        self.assertTrue(fl.gate_alert_due(FLIGHT, s, {}, q("2026-11-06T04:35")))
        self.assertTrue(fl.gate_alert_due(FLIGHT, s, {}, q("2026-11-06T05:20")))      # pass uploaded late: right away
        self.assertFalse(fl.gate_alert_due(FLIGHT, s, {"gate_sent": True}, q("2026-11-06T04:50")))
        self.assertFalse(fl.gate_alert_due(FLIGHT, s, {}, q("2026-11-06T05:26")))      # 5 minutes before departure: no
        self.assertFalse(fl.gate_alert_due(FLIGHT, self.s("cancelled"), {}, q("2026-11-06T04:50")))
        self.assertEqual(fl.gate_message(FLIGHT, s),
                         ("FR 1234: avviati al gate A12", "Al gate per le 05:50 (imbarco alle 06:00 circa), partenza "
                                                          "alle 06:30 per Londra Stansted. Terminal 1."))
        self.assertEqual(fl.gate_message(FLIGHT, self.s())[0], "FR 1234: avviati verso i gate")
        self.assertIn("Il gate non e' ancora indicato", fl.gate_message(FLIGHT, self.s())[1])

    def test_business_at_gate_when_boarding_starts(self):
        s = self.s(dep_gate="B12")
        q = lambda x: datetime.datetime.fromisoformat(x).replace(tzinfo=UTC)
        self.assertTrue(fl.is_priority(["C"]))
        self.assertTrue(fl.is_priority(["J", "F"]))
        self.assertFalse(fl.is_priority(["C", "Y"]))            # one passenger in economy: economy applies
        self.assertFalse(fl.is_priority([None]))
        self.assertEqual((fl.at_gate_time(FLIGHT, s, True), fl.leave_for_gate_time(FLIGHT, s, True)),
                         (q("2026-11-06T05:00"), q("2026-11-06T04:45")))
        self.assertFalse(fl.gate_alert_due(FLIGHT, s, {}, q("2026-11-06T04:44"), True))
        self.assertTrue(fl.gate_alert_due(FLIGHT, s, {}, q("2026-11-06T04:45"), True))
        self.assertEqual(fl.gate_message(FLIGHT, s, True)[1],
                         "Al gate per le 06:00, quando inizia l'imbarco (imbarco alle 06:00 circa), partenza alle 06:30 "
                         "per Londra Stansted.")

    def test_pass_boarding_shifted_by_delay_and_long_flights(self):
        with_pass = {**FLIGHT, "pass_boarding": "2026-11-06T05:55:00+01:00"}
        self.assertEqual(fl.boarding_time(with_pass, self.s(dep_delay=25)), ("2026-11-06T05:20:00+00:00", False))
        self.assertEqual(fl.gate_message(with_pass, self.s(dep_delay=25))[1],
                         "Al gate per le 06:10 (imbarco alle 06:20), partenza alle 06:55 per Londra Stansted. Il gate "
                         "non e' ancora indicato: guarda i monitor.")
        long_haul = self.s(arr_scheduled="2026-11-06T16:00:00+00:00")         # ten and a half hours
        self.assertEqual(fl.boarding_time(FLIGHT, long_haul)[0], "2026-11-06T04:45:00+00:00")
        self.assertEqual(fl.boarding_time(FLIGHT, None), (None, True))

    def test_walk_from_gps(self):
        # invented positions at BLQ: the person 211 m from the airport point, gate B12 farther north-west
        blq = fl.coordinates("BLQ")
        self.assertEqual(blq, [44.5354, 11.2887])
        person = {"lat": 44.5340, "lon": 11.2905, "accuracy": 20}
        gate = (44.5385, 11.2850)
        # 0.0045° north = 500 m, 0.0055° west = 436 m (cos 44.536° = 0.713): 664 m as the crow flies, times 1.4 =
        # 929 m of corridors (rounded to 930); 929 / 75 m a minute = 12.4 -> 12, plus 3 = 15 minutes
        self.assertEqual(fl.walk(person, blq, gate), (15, {"meters": 930, "minutes": 15}))
        self.assertEqual(fl.walk(person, blq, None), (None, {"at_airport": True}))
        # from the centre of Bologna: 4.6 km south and 4.3 km east of the airport, 6.3 km
        self.assertEqual(fl.walk({"lat": 44.4938, "lon": 11.3426, "accuracy": 10}, blq, gate), (None, {"away_km": 6}))
        self.assertEqual(fl.walk({**person, "accuracy": 500}, blq, gate), (None, {}))     # inaccurate GPS
        self.assertEqual(fl.walk(None, blq, gate), (None, {}))
        self.assertEqual(fl.walk(person, blq, (44.53401, 11.29051))[0], 3)               # already there: the minimum
        self.assertIsNone(fl.coordinates("XX1"))

    def test_gate_regex(self):
        self.assertEqual(fl.gate_regex("B12"), "^B[ -]?0*12$")
        self.assertEqual(fl.gate_regex("b 5"), "^B[ -]?0*5$")
        self.assertEqual(fl.gate_regex("Z15A"), "^Z[ -]?0*15A$")
        for bad in ('"]', "", None, "B12;out", "ABCD1"):
            self.assertIsNone(fl.gate_regex(bad), bad)

    def test_find_gate_without_person_position(self):
        requests = []

        class R(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def opener(req, timeout):
            requests.append(req.data.decode())
            return R(json.dumps({"elements": [{"type": "node", "lat": 44.5385, "lon": 11.285}]}).encode())
        self.assertEqual(fl.find_gate(44.5354, 11.2887, "B12", opener=opener), (44.5385, 11.285))   # BLQ, invented node
        (body,) = requests
        (q,) = urllib.parse.parse_qs(body)["data"]                       # the form field Overpass reads
        self.assertIn("nwr(around:4000,44.53540,11.28870)", q)           # only the airport
        self.assertIn('["ref"~"^B[ -]?0*12$",i]', q)
        empty_opener = lambda req, timeout: R(b'{"elements": []}')
        self.assertIsNone(fl.find_gate(44.5, 11.3, "B12", opener=empty_opener))

        def down(req, timeout):
            raise urllib.error.HTTPError(req.full_url, 504, "no", {}, None)
        with self.assertRaises(fl.APIError):
            fl.find_gate(44.5, 11.3, "B12", opener=down)

    def test_overpass_user_agent(self):
        sent = []

        def opener(req, timeout):
            sent.append((req.full_url, req.get_header("User-agent")))
            return io.BytesIO(b'{"elements": []}')
        self.assertIsNone(fl.find_gate(44.5354, 11.2887, "B12", opener=opener))
        self.assertEqual(sent, [("https://overpass-api.de/api/interpreter",
                                 "boarding-pass-tracker/0.1.0 (personal use)")])

    def test_gate_message_with_walk(self):
        s = self.s(dep_gate="A12")
        self.assertTrue(fl.gate_message(FLIGHT, s, False, {"meters": 930, "minutes": 15})[1].endswith(
            "Da dove sei sono circa 930 m, 15 minuti a piedi."))
        self.assertTrue(fl.gate_message(FLIGHT, s, False, {"away_km": 12})[1].endswith(
            "Risulti a 12 km dall'aeroporto: conta anche il viaggio fin li'."))

    def test_hostile_text_cleaned(self):
        s = self.s(dep_gate="A1\u202e<b>", arr_airport="Evil\x00 <script> Airport")
        self.assertEqual((s["gate"], s["destination_city"]), ("A1b", "Evil script"))


if __name__ == "__main__":
    unittest.main()
