#!/usr/bin/env python3
"""flights: Wallet boarding passes (.pkpass) and flight status.

Passes arrive through the «Manda volo» iOS Shortcut and are read by fixed rules, with no model. A Wallet pass (in
Wallet: the pass, "...", Share) is a ZIP with pass.json: flight, date, route, seat and passenger come from the
barcode message, the IATA BCBP (Resolution 792, fixed-length fields); the pass "semantics" (iOS 15 and later),
when present, add the departure time, boarding time and gate printed by the airline. In a photo, screenshot or PDF
of the pass the engine reads the Aztec or PDF417 barcode with barcodes.read_barcodes, and
boarding_pass_from_barcodes takes the BCBP among the texts.

Flight status comes from AviationStack (GET /v1/flights?flight_iata=..., over HTTPS). The key goes in the URL
(access_key), so the URL never ends up in logs or error messages. The free plan has 100 calls a month and only
knows yesterday's and today's flights, so checks start 6 hours before and there are about ten per flight. Two
quirks verified on LH 400: times are the airport's local time with a fake "+00:00" (the airport's time
zone applies), and "estimated" stays equal to "scheduled" even with a delay (the estimate is scheduled + delay).
Every codeshare is an entry of its own.

Only flight, route, times, gate, seat and first name leave this service (that is, go to HA): never the PNR (PNR
and surname are enough to manage the booking), the barcode or the pass token.

The file is untrusted: a ZIP with at most MAX_ZIP_ENTRIES entries, a pass.json over MAX_PASS_BYTES rejected before
reading it, every field validated by a rule. Tests: test_flights.py.
"""
import datetime, io, json, re, urllib.error, urllib.parse, urllib.request, zipfile
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

if __package__:
    from . import airports
else:
    import airports

AVIATIONSTACK_URL = "https://api.aviationstack.com/v1/flights"
MAX_ZIP_ENTRIES = 200   # a pass has pass.json, manifest, signature, logos and translations: a few dozen
MAX_PASS_BYTES = 512 * 1024
MAX_LEGS = 4
DELAY_NOTIFY_MIN = 15   # minutes: below this, a delay is not notified
CHECKS_BEFORE_MIN = (60, 30, 10)                # minutes before the estimated departure; with a delay, every 30
GATE_MARGIN = datetime.timedelta(minutes=15)    # "head to the gate": the walk to the gate
ECONOMY_LEAD = datetime.timedelta(minutes=10)   # in economy, at the gate a little before boarding
PRIORITY_CABINS = frozenset("FAPJCDIZR")        # IATA cabin codes of first and business: they board first
# Walk to the gate from the GPS of whoever uploaded the pass (walk()): straight line times CORRIDOR_FACTOR,
# at WALK_M_PER_MIN
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
USER_AGENT = "boarding-pass-tracker/0.1.0 (personal use)"     # of the Overpass requests
AIRPORT_RADIUS_M = 3000     # meters: farther than this, you are not at the airport
CORRIDOR_FACTOR = 1.4
WALK_M_PER_MIN = 75         # meters per minute, with hand luggage
WALK_MINUTES = (3, 40)      # credible minimum and maximum
MAX_GPS_ACCURACY_M = 200    # meters: beyond this, the position is not used
UTC = datetime.timezone.utc
# The local zone of «today», noon and 04:00 (reference_time, next_check): None is the process zone (the service);
# Home Assistant sets its own configured zone (engine.configure)
LOCAL_TZ = None

# Flight phases returned by phase(): computed from the status, never stored; published as the `phase` attribute
# of the next_flight sensor and read by the flight card
PHASE_SCHEDULED = "scheduled"
PHASE_DEPARTED = "departed"         # left the gate
PHASE_AIRBORNE = "airborne"
PHASE_LANDED = "landed"
PHASE_CANCELLED = "cancelled"
PHASE_DIVERTED = "diverted"

_AIRPORT = re.compile(r"[A-Z]{3}")
_AIRLINE = re.compile(r"[A-Z0-9]{2}[A-Z]?")
_NUMBER = re.compile(r"0*(\d{1,4}[A-Z]?)", re.ASCII)              # re.ASCII: \d would also take "١٢" or "１２"
_SEAT = re.compile(r"0*(\d{1,3}[A-Z])|(INF|GATE|STBY)", re.ASCII)
_SEQUENCE = re.compile(r"(\d+)[A-Z]?", re.ASCII)                  # in a 5-character field
_HEX_SIZE = re.compile(r"[0-9A-Fa-f]{2}")                         # int(x, 16) would also take "-1", " F" or "١٢"
_TITLES = {"MR", "MRS", "MS", "MISS", "MSTR", "DR", "SIG", "SIGRA", "CHD", "INF"}
_TEXT = re.compile(r"[^\w .,'()/+\-]", re.U)


class NotABoardingPass(ValueError):
    """The file is not a boarding pass this module can read. The message is Italian: the engine quotes it in
    the outcome text the user is notified with."""


class APIError(RuntimeError):
    """AviationStack answered with an error, or did not answer. `code`: the HTTP status, None if there is none."""
    def __init__(self, message, code=None):
        super().__init__(message)
        self.code = code


# ---------------------------------------------------------------- pkpass and BCBP
def _too_many_entries(b):
    """More than MAX_ZIP_ENTRIES entries in the ZIP's central directory? Every entry zipfile can list starts with
    the signature PK\\x01\\x02 somewhere in the file, so counting it (at C speed, whatever the end record declares)
    bounds the work before zipfile builds one object per entry: 300,000 empty entries in 25 MB took a second of pure
    Python and 50 MB of memory in Home Assistant's executor."""
    return b.count(b"PK\x01\x02") > MAX_ZIP_ENTRIES


def is_pkpass(b):
    """By content: a ZIP with pass.json at the root (and at most MAX_ZIP_ENTRIES entries)."""
    if b[:4] != b"PK\x03\x04" or _too_many_entries(b):
        return False
    try:
        with zipfile.ZipFile(io.BytesIO(b)) as z:
            return "pass.json" in z.namelist()
    except (zipfile.BadZipFile, ValueError, OSError):
        return False


def read_pass(b):
    """pass.json as a dict. NotABoardingPass if the file is broken, too big or not an air boarding pass."""
    if _too_many_entries(b):                    # before zipfile lists them all
        raise NotABoardingPass("pkpass con troppi file")
    try:
        with zipfile.ZipFile(io.BytesIO(b)) as z:
            entries = z.infolist()
            if len(entries) > MAX_ZIP_ENTRIES:
                raise NotABoardingPass("pkpass con troppi file")
            info = next((i for i in entries if i.filename == "pass.json"), None)
            if info is None:
                raise NotABoardingPass("pkpass senza pass.json")
            if info.file_size > MAX_PASS_BYTES:     # declared in the header: checked again while reading
                raise NotABoardingPass("pass.json troppo grande")
            with z.open(info) as f:
                raw = f.read(MAX_PASS_BYTES + 1)
    except (zipfile.BadZipFile, ValueError, OSError, RuntimeError) as e:  # RuntimeError: encrypted ZIP
        if isinstance(e, NotABoardingPass):
            raise
        raise NotABoardingPass(f"pkpass illeggibile ({type(e).__name__})") from None
    if len(raw) > MAX_PASS_BYTES:
        raise NotABoardingPass("pass.json troppo grande")
    try:
        p = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError):
        raise NotABoardingPass("pass.json non e' JSON") from None
    if not isinstance(p, dict):
        raise NotABoardingPass("pass.json non e' un oggetto")
    bp = p.get("boardingPass")
    if not isinstance(bp, dict):
        raise NotABoardingPass("non e' una carta d'imbarco (biglietto, tessera o coupon del Wallet)")
    if bp.get("transitType") not in (None, "PKTransitTypeAir"):
        raise NotABoardingPass("carta d'imbarco di un treno, un bus o una nave")
    return p


def barcode_message(p):
    """The barcode text: "barcodes" (iOS 9 and later) or the old "barcode"."""
    for b in (p.get("barcodes") or [])[:4] + [p.get("barcode")]:
        if isinstance(b, dict) and isinstance(b.get("message"), str):
            return b["message"]
    return None


PASS_LEAD_DAYS = 3     # days: a boarding pass comes with check-in, at most 3 days before the flight
# the pass's arrival is kept only this long after its departure (flight_stats.DURATION_RANGE: 10 minutes to a day)
ARRIVAL_AFTER = (datetime.timedelta(minutes=10), datetime.timedelta(hours=24))


def _day_of_year(year, n):
    """The date of day n (1-366) of the year, None if it does not exist (day 366 of a non-leap year)."""
    try:
        d = datetime.date(year, 1, 1) + datetime.timedelta(days=n - 1)
    except (OverflowError, ValueError):
        return None
    return d if d.year == year else None


def issue_date(digit, n, today):
    """The BCBP issue date ("6149": a year ending in 6, day 149): the most recent one no later than tomorrow
    (time zones). None if it does not fit."""
    tomorrow = today + datetime.timedelta(days=1)
    for year in range(tomorrow.year, tomorrow.year - 11, -1):
        if year % 10 == digit:
            d = _day_of_year(year, n)
            if d and d <= tomorrow:
                return d
            if d:                                   # same year but after tomorrow: ten years earlier
                continue
    return None


def julian_date(n, today, issued=None):
    """The BCBP day of the year (it has no year) as a date.
      - With the pass issue date: the first day n from that date on (the flight follows the check-in).
      - Without: the date between PASS_LEAD_DAYS days ahead and a year ago. Nobody has a pass months before the
        flight, so a date far in the future is a past flight (uploaded later, for the statistics).
    NotABoardingPass if there is none: day 366 with no leap year in that range."""
    if issued:
        for year in (issued.year, issued.year + 1):
            d = _day_of_year(year, n)
            if d and d >= issued - datetime.timedelta(days=1):
                return d
    good = [d for d in (_day_of_year(y, n) for y in (today.year - 1, today.year, today.year + 1))
            if d and -366 < (d - today).days <= PASS_LEAD_DAYS]                    # a whole year back
    if not good:
        raise NotABoardingPass("BCBP: giorno dell'anno impossibile")
    return min(good, key=lambda d: abs((d - today).days))


def _bcbp_issue_date(variable, today):
    """The issue date from the conditional section of the first leg (IATA 792: ">", version, length of the unique
    section, three one-character fields, then the 4-digit date "YDDD"). None if it is not there."""
    if len(variable) < 11 or variable[0] != ">" or not (variable[7:11].isascii() and variable[7:11].isdigit()):
        return None
    day = int(variable[8:11])
    return issue_date(int(variable[7]), day, today) if 1 <= day <= 366 else None


def passenger_name(s):
    """"ROSSI/MARIO MR" -> ("Mario Rossi", "Mario"). Without "/" there is no telling which word is the first name
    (often the first one is the surname): the full name stays, the one for HA does not."""
    s = re.sub(r"\s+", " ", (s or "").strip())
    if "/" not in s:
        full = " ".join(w for w in s.split() if w.upper() not in _TITLES).title()
        return (full or None), None
    surname, given = s.split("/", 1)
    words = [w for w in given.split() if w.upper() not in _TITLES] or given.split()
    given = " ".join(words).title()
    surname = surname.strip().title()
    full = f"{given} {surname}".strip()
    return (full or None), (words[0].title() if words else None)


def parse_bcbp(msg, today):
    """The legs of a BCBP: list of dicts with passenger, pnr, origin, destination, airline, number, date
    (YYYY-MM-DD, local date of departure), cabin, seat, sequence. NotABoardingPass if the format does not match."""
    if not isinstance(msg, str) or len(msg) < 60 or msg[0] != "M" or not (msg[1].isascii() and msg[1].isdigit()):
        raise NotABoardingPass("il codice a barre non e' un BCBP")
    n = int(msg[1])
    if not 1 <= n <= MAX_LEGS:
        raise NotABoardingPass("BCBP con un numero di tratte impossibile")
    passenger, first_name = passenger_name(msg[2:22])
    legs, pos, issued = [], 23, None
    for i in range(n):
        t = msg[pos:pos + 37]
        if len(t) < 37:
            raise NotABoardingPass("BCBP troncato")
        if not _HEX_SIZE.fullmatch(t[35:37]):
            raise NotABoardingPass("BCBP: lunghezza della parte variabile illeggibile")
        variable = int(t[35:37], 16)
        if i == 0:                                  # the unique section (with the issue date) is in the first leg
            issued = _bcbp_issue_date(msg[pos + 37:pos + 37 + variable], today)
        origin, destination, airline = t[7:10], t[10:13], t[13:16].strip()
        number, day = _NUMBER.fullmatch(t[16:21].strip()), t[21:24]
        if not (_AIRPORT.fullmatch(origin) and _AIRPORT.fullmatch(destination) and _AIRLINE.fullmatch(airline)
                and number and day.isascii() and day.isdigit() and 1 <= int(day) <= 366):
            raise NotABoardingPass("BCBP: volo, tratta o data non validi")
        seat = _SEAT.fullmatch(t[25:29].strip())
        seq = _SEQUENCE.fullmatch(t[29:34].strip())   # unreadable: no sequence, as for the seat, the pass is still read
        legs.append({"passenger": passenger, "first_name": first_name, "pnr": t[0:7].strip() or None,
                     "origin": origin, "destination": destination, "airline": airline, "number": number.group(1),
                     "date": julian_date(int(day), today, issued).isoformat(),
                     "cabin": t[24].strip() or None, "seat": (seat.group(1) or seat.group(2)) if seat else None,
                     "sequence": int(seq.group(1)) if seq else None})
        pos += 37 + variable
    return legs


def _instant(s):
    """ISO 8601 with an offset -> normalized ISO string; None if missing or invalid (an instant without an offset
    has no known time)."""
    if not isinstance(s, str):
        return None
    try:
        t = datetime.datetime.fromisoformat(s.strip())
    except ValueError:
        return None
    return t.isoformat() if t.tzinfo else None


def _short(s, n=12):
    if not isinstance(s, (str, int)):
        return None
    s = _TEXT.sub("", str(s)).strip()[:n]
    return s or None


def _from_semantics(sem):
    """The pass semantics: only the fields needed, validated. {} if there are none."""
    if not isinstance(sem, dict):
        return {}
    num = sem.get("flightNumber")
    num = _NUMBER.fullmatch(str(num)) if isinstance(num, (int, str)) else None
    code = sem.get("airlineCode")
    return {k: v for k, v in {
        "airline": code if isinstance(code, str) and _AIRLINE.fullmatch(code) else None,
        "number": num.group(1) if num else None,
        "origin": sem.get("departureAirportCode") if _AIRPORT.fullmatch(str(sem.get("departureAirportCode"))) else None,
        "destination": (sem.get("destinationAirportCode")
                        if _AIRPORT.fullmatch(str(sem.get("destinationAirportCode"))) else None),
        "departure": _instant(sem.get("currentDepartureDate")) or _instant(sem.get("originalDepartureDate")),
        "boarding": _instant(sem.get("currentBoardingDate")) or _instant(sem.get("originalBoardingDate")),
        "arrival": _instant(sem.get("currentArrivalDate")) or _instant(sem.get("originalArrivalDate")),
        "gate": _short(sem.get("departureGate"), 8),
        "terminal": _short(sem.get("departureTerminal"), 8),
    }.items() if v is not None}


def read_boarding_pass(b, today):
    """The legs of a Wallet pass. The BCBP decides flight, date and route; the semantics add departure, boarding,
    gate and terminal to the leg that departs from their airport (or, without a BCBP, they are the only source, if
    they have flight, route and departure). `relevant_date`: the relevantDate, which Wallet uses to show the pass."""
    p = read_pass(b)
    sem = _from_semantics(p.get("semantics"))
    relevant = _instant(p.get("relevantDate"))
    msg = barcode_message(p)
    try:
        legs = parse_bcbp(msg, today)
    except NotABoardingPass:
        if not all(k in sem for k in ("airline", "number", "origin", "destination", "departure")):
            raise
        name = (p.get("semantics") or {}).get("passengerName") or {}
        full, given = passenger_name(f"{name.get('familyName', '')}/{name.get('givenName', '')}"
                                     if isinstance(name, dict) else "")
        # "passeggero" (passenger) is the placeholder name: it is stored in boarding_passes.passenger, part of
        # its UNIQUE key, so the Italian value stays
        legs = [{"passenger": full or "passeggero", "first_name": given, "pnr": None, "origin": sem["origin"],
                 "destination": sem["destination"], "airline": sem["airline"], "number": sem["number"],
                 "date": datetime.datetime.fromisoformat(sem["departure"]).date().isoformat(),
                 "cabin": None, "seat": None, "sequence": None}]
    for t in legs:
        t["passenger"] = t["passenger"] or "passeggero"
        same = sem.get("origin", t["origin"]) == t["origin"] and sem.get("number", t["number"]) == t["number"]
        for k, sem_key in (("pass_departure", "departure"), ("pass_boarding", "boarding"), ("pass_gate", "gate"),
                           ("terminal", "terminal"), ("pass_arrival", "arrival")):
            t[k] = sem.get(sem_key) if same else None
        if t["pass_departure"] and \
                datetime.datetime.fromisoformat(t["pass_departure"]).date().isoformat() != t["date"]:
            t["pass_departure"] = None          # semantics of another date: the BCBP wins
        if t["pass_arrival"] and not (t["pass_departure"] and ARRIVAL_AFTER[0] <= _t(t["pass_arrival"])
                                      - _t(t["pass_departure"]) <= ARRIVAL_AFTER[1]):
            t["pass_arrival"] = None            # no departure to measure it from, or not a credible duration
        t["relevant_date"] = relevant if len(legs) == 1 else None
    return legs


def boarding_pass_from_barcodes(texts, today):
    """The legs of the first barcode that is a BCBP (photo or screenshot of the pass, the airline's PDF), None if
    none is. Without a pass there are no semantics: departure, boarding and gate come from the status."""
    for text in texts or []:
        try:
            legs = parse_bcbp(text, today)
        except NotABoardingPass:
            continue
        for t in legs:
            t["passenger"] = t["passenger"] or "passeggero"
            t.update(pass_departure=None, pass_boarding=None, pass_gate=None, terminal=None, relevant_date=None,
                     pass_arrival=None)
        return legs
    return None


# ---------------------------------------------------------------- AviationStack
def queryable(date_iso, now):
    """AviationStack (free plan) knows yesterday's and today's flights: outside that, the call is wasted."""
    d = datetime.date.fromisoformat(date_iso)
    today = now.astimezone(UTC).date()
    return today - datetime.timedelta(days=1) <= d <= today + datetime.timedelta(days=1)


def _zone(name):
    try:
        return ZoneInfo(name) if isinstance(name, str) and name else None
    except (ZoneInfoNotFoundError, ValueError):
        return None


def _local(s, zone):
    """AviationStack's "2026-11-06T06:30:00+00:00": the airport's local time, fake offset. ISO in UTC."""
    if not isinstance(s, str) or zone is None:
        return None
    try:
        t = datetime.datetime.fromisoformat(s.strip()[:19])
    except ValueError:
        return None
    return t.replace(tzinfo=zone).astimezone(UTC).isoformat()


def _city(name):
    """"Frankfurt International Airport" -> "Frankfurt": the airport name without the customary words."""
    if not isinstance(name, str):
        return None
    name = re.sub(r"\b(international|intl|airport|aeroporto|aeropuerto|flughafen|a[eé]roport)\b", "", name, flags=re.I)
    return _short(re.sub(r"\s+", " ", name), 30)


def normalize(f):
    """The AviationStack flight reduced to the fields the engine uses (the flights.status JSON). ISO times in
    UTC."""
    o, d = f.get("departure") or {}, f.get("arrival") or {}
    zo, zd = _zone(o.get("timezone")), _zone(d.get("timezone"))
    status = f.get("flight_status") if isinstance(f.get("flight_status"), str) else None
    minutes = lambda x: int(x) if isinstance(x, (int, float)) else None
    scheduled, estimated = _local(o.get("scheduled"), zo), _local(o.get("estimated"), zo)
    delay = minutes(o.get("delay"))
    if scheduled and delay and estimated in (None, scheduled):
        estimated = (_t(scheduled) + datetime.timedelta(minutes=delay)).isoformat()
    actual = _local(o.get("actual"), zo)
    takeoff = _local(o.get("actual_runway"), zo)
    if status == "active" and not takeoff:      # airborne, but the real time is sometimes missing: the best estimate
        takeoff = _local(o.get("estimated_runway"), zo) or actual or estimated or scheduled
    landing = _local(d.get("actual_runway"), zd) or _local(d.get("actual"), zd)
    if status == "landed" and not landing:
        landing = _local(d.get("estimated_runway"), zd) or _local(d.get("estimated"), zd) \
            or _local(d.get("scheduled"), zd)
    return {
        "api_status": _short(status, 20),
        "cancelled": status == "cancelled",
        "diverted": status in ("diverted", "incident"),
        "departure_scheduled": scheduled,
        "departure_estimated": estimated,
        "departure_actual": actual,
        "takeoff": takeoff,
        "landing": landing,
        "arrival_scheduled": _local(d.get("scheduled"), zd),
        "arrival_estimated": _local(d.get("estimated"), zd),
        "arrival_actual": _local(d.get("actual"), zd),
        "departure_delay": delay,
        "arrival_delay": minutes(d.get("delay")),
        "gate": _short(o.get("gate"), 8),
        "terminal": _short(o.get("terminal"), 8),
        "arrival_gate": _short(d.get("gate"), 8),
        "arrival_terminal": _short(d.get("terminal"), 8),
        "baggage_belt": _short(d.get("baggage"), 8),
        "origin_city": _city(o.get("airport")),
        "destination_city": _city(d.get("airport")),
        "origin_tz": o.get("timezone") if zo else None,
        "destination_tz": d.get("timezone") if zd else None,
    }


def pick(flights, ident, origin, date_iso):
    """Among the AviationStack entries, flight `ident` departing from `origin` on the local day `date_iso`. Every
    codeshare is an entry of its own, with the same data: the operating carrier's one is preferred."""
    good = []
    for f in flights if isinstance(flights, list) else []:
        if not isinstance(f, dict) or (f.get("departure") or {}).get("iata") != origin \
                or f.get("flight_date") != date_iso:
            continue
        fl = f.get("flight") or {}
        cs = fl.get("codeshared") or {}
        if str(fl.get("iata") or "").upper() == ident:
            good.append((0 if not cs else 1, f))
        elif str(cs.get("flight_iata") or "").upper() == ident:
            good.append((2, f))
    return min(good, key=lambda x: x[0])[1] if good else None


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None                             # the key is in the URL: a redirect is not followed


def lookup_flight(key, airline, number, date_iso, origin, now, timeout=20, opener=None):
    """The normalized flight, None if AviationStack does not know it. APIError (with the HTTP code, or the one of
    the error in the body, which also arrives with a 200) if something goes wrong; never the URL, which holds the
    key. `opener` (for tests): function (url, timeout) -> response with read()."""
    ident = f"{airline}{number}"
    if not re.fullmatch(r"[A-Z0-9]{2,3}\d{1,4}[A-Z]?", ident):
        raise ValueError(f"invalid flight: {ident!r}")
    url = AVIATIONSTACK_URL + "?" + urllib.parse.urlencode({"access_key": key, "flight_iata": ident})
    if opener is None:
        opener = urllib.request.build_opener(_NoRedirect).open
    # The APIError messages stay Italian: the engine stores them in flights.error, which GET /flights shows to people
    try:
        with opener(url, timeout=timeout) as response:
            body = json.loads(response.read(2 * 1024 * 1024).decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            code = (json.loads(e.read(65536) or b"{}").get("error") or {}).get("code")
        except ValueError:
            code = None
        raise APIError(f"AviationStack {e.code}: {_short(code, 40) or 'errore'}", e.code) from None
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise APIError(f"AviationStack non raggiungibile ({type(e).__name__})") from None
    if not isinstance(body, dict):
        raise APIError("AviationStack: risposta inattesa")
    if body.get("error"):
        code = _short((body["error"] or {}).get("code"), 40) or "errore"
        raise APIError(f"AviationStack: {code}", 401 if "access_key" in code else 429 if "limit" in code else None)
    chosen = pick(body.get("data"), ident, origin, date_iso)
    return normalize(chosen) if chosen else None


# ---------------------------------------------------------------- status, checks, alerts
def _t(iso):
    return datetime.datetime.fromisoformat(iso) if iso else None


def local_time(iso, tz=None):
    """"06:30" in the airport's time (the AviationStack time zone), or in the offset written in the instant."""
    t = _t(iso)
    if t is None:
        return None
    z = _zone(tz)
    return (t.astimezone(z) if z else t).strftime("%H:%M")


def departure(v, s):
    """The best departure known: actual, estimated, scheduled from AviationStack, then the one of the pass."""
    s = s or {}
    return (s.get("departure_actual") or s.get("departure_estimated") or s.get("departure_scheduled")
            or v.get("pass_departure"))


def arrival(s, v=None):
    """The best arrival known: actual, estimated, scheduled from AviationStack, then the flight's own (`v`: typed or
    from the pass). next_check passes no `v`: its arrival is AviationStack's."""
    s = s or {}
    return (s.get("arrival_actual") or s.get("arrival_estimated") or s.get("arrival_scheduled")
            or (v or {}).get("pass_arrival"))


def phase(s):
    """PHASE_SCHEDULED, PHASE_DEPARTED (left the gate), PHASE_AIRBORNE, PHASE_LANDED, PHASE_CANCELLED,
    PHASE_DIVERTED; None without data."""
    if not s:
        return None
    if s.get("cancelled"):
        return PHASE_CANCELLED
    if s.get("diverted"):
        return PHASE_DIVERTED
    if s.get("landing") or s.get("arrival_actual"):
        return PHASE_LANDED
    if s.get("takeoff"):
        return PHASE_AIRBORNE
    if s.get("departure_actual"):
        return PHASE_DEPARTED
    return PHASE_SCHEDULED


def status_text(v, s):
    """The sensor state, short and in Italian."""
    f = phase(s)
    if f is None:
        return "Stato non disponibile"
    if f == PHASE_CANCELLED:
        return "Cancellato"
    if f == PHASE_DIVERTED:
        return "Problema segnalato" if s.get("api_status") == "incident" else "Deviato"
    if f == PHASE_LANDED:
        r = s.get("arrival_delay") or 0
        return f"Atterrato con {r} min di ritardo" if r >= DELAY_NOTIFY_MIN else "Atterrato"
    if f == PHASE_AIRBORNE:
        return "In volo"
    r = s.get("departure_delay") or 0
    if f == PHASE_DEPARTED:
        return "Partito dal gate"
    return f"In ritardo di {r} min" if r >= DELAY_NOTIFY_MIN else "In orario"


def _local_at(day, hour):
    """`hour`:00 of `day` in LOCAL_TZ, or in the process zone when LOCAL_TZ is None: an aware datetime with that
    instant's fixed offset, as .astimezone() gives, so that `+ timedelta(hours=24)` adds 24 real hours also across
    a daylight saving change (a ZoneInfo datetime would add wall-clock time: 23 or 25 hours)."""
    if LOCAL_TZ is not None:
        t = datetime.datetime.combine(day, datetime.time(hour), tzinfo=LOCAL_TZ)
        return t.astimezone(datetime.timezone(t.utcoffset()))
    return datetime.datetime.combine(day, datetime.time(hour)).astimezone()


def reference_time(v, s, now):
    """The instant the checks are scheduled around: the known departure, then the pass relevantDate, then noon of
    the flight day in the machine's time zone."""
    p = departure(v, s) or v.get("relevant_date")
    if p:
        return _t(p)
    d = datetime.date.fromisoformat(v["date"])
    return _local_at(d, 12)


def is_over(v, s, now):
    """Nothing more to follow: landed an hour ago, cancelled, or a day after departure with no news."""
    f = phase(s)
    ref = reference_time(v, s, now)
    if f == PHASE_CANCELLED or f == PHASE_DIVERTED and now > ref + datetime.timedelta(hours=12):
        return True
    if f == PHASE_LANDED:
        end = _t(s.get("arrival_actual") or s.get("landing"))
        return now > end + datetime.timedelta(hours=1)
    return now > ref + datetime.timedelta(hours=24)


def next_check(v, s, now):
    """When to ask again (datetime in UTC), None once it is no longer needed. The free plan has 100 calls a month:
    besides the one right after the pass is uploaded, at 60, 30 and 10 minutes before the estimated departure
    (every 30 with a delay), 20 minutes after departure and at arrival. About half a dozen per flight."""
    f = phase(s)
    if is_over(v, s, now) or f in (PHASE_LANDED, PHASE_DIVERTED):
        return None
    mins = lambda n: datetime.timedelta(minutes=n)
    if f in (PHASE_AIRBORNE, PHASE_DEPARTED):
        a = _t(arrival(s))
        return max(a + mins(10) if a else now + mins(60), now + mins(20)).astimezone(UTC)
    p = departure(v, s) or v.get("relevant_date")
    if not p:                                   # only the BCBP and no answer: at 4 am on the day of the flight
        at4 = _local_at(datetime.date.fromisoformat(v["date"]), 4)
        return (at4 if at4 > now else now + mins(60)).astimezone(UTC)
    dep = _t(p)
    points = [t for t in [dep - mins(n) for n in CHECKS_BEFORE_MIN] + [dep + mins(20)] if t > now + mins(1)]
    nxt = min(points) if points else now + mins(60)     # departure passed with no news
    # delayed: every 30 minutes, but only once the check window of the scheduled time opens
    # (a delay known since the morning must not spend the plan half an hour at a time)
    scheduled = _t((s or {}).get("departure_scheduled"))
    if s and (s.get("departure_delay") or 0) >= DELAY_NOTIFY_MIN and scheduled \
            and now >= scheduled - mins(max(CHECKS_BEFORE_MIN)):
        nxt = min(nxt, now + mins(30))
    return nxt.astimezone(UTC)


def boarding_time(v, s):
    """(ISO instant in UTC, estimated). The pass one, shifted by the delay; otherwise 30 minutes before the
    estimated departure (45 for flights over 4 hours). (None, True) if the departure is unknown."""
    s = s or {}
    if v.get("pass_boarding"):
        t = _t(v["pass_boarding"])
        if s.get("departure_scheduled") and s.get("departure_estimated"):
            t += max(_t(s["departure_estimated"]) - _t(s["departure_scheduled"]), datetime.timedelta(0))
        return t.astimezone(UTC).isoformat(), False
    p = departure(v, s)
    if not p:
        return None, True
    duration = (_t(s["arrival_scheduled"]) - _t(s["departure_scheduled"])
                if s.get("arrival_scheduled") and s.get("departure_scheduled") else datetime.timedelta(0))
    lead = 45 if duration > datetime.timedelta(hours=4) else 30
    return (_t(p) - datetime.timedelta(minutes=lead)).isoformat(), True


def is_priority(cabins):
    """True if every passenger of the flight travels in first or business (BCBP cabin code)."""
    cabins = [c for c in cabins if c]
    return bool(cabins) and all(c in PRIORITY_CABINS for c in cabins)


def at_gate_time(v, s, priority=False):
    """When to be at the gate: when boarding starts in first and business, ECONOMY_LEAD earlier in economy.
    None if boarding is unknown."""
    t, _ = boarding_time(v, s)
    return _t(t) - (datetime.timedelta(0) if priority else ECONOMY_LEAD) if t else None


def leave_for_gate_time(v, s, priority=False, margin=GATE_MARGIN):
    """When to set off: `margin` (the walk) before at_gate_time. None if unknown."""
    t = at_gate_time(v, s, priority)
    return t - margin if t else None


def gate_alert_due(v, s, snapshot, now, priority=False, margin=GATE_MARGIN, who=None):
    """It is time for "head to the gate" for `who`: only once per person, before the flight departs, not if it is
    cancelled or diverted. Without `who`, only once for the flight."""
    snapshot = snapshot or {}
    sent = who in (snapshot.get("gate_sent_to") or []) if who else snapshot.get("gate_sent")
    if sent or phase(s) not in (None, PHASE_SCHEDULED):
        return False
    when, p = leave_for_gate_time(v, s, priority, margin), departure(v, s)
    return bool(when and p and when <= now < _t(p) - datetime.timedelta(minutes=5))


# ---------------------------------------------------------------- GPS and gate
_COORDINATES = None


def _airport(code):
    """[lat, lon, country] from airports.json (OurAirports), None if missing."""
    global _COORDINATES
    if _COORDINATES is None:
        import os
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "airports.json"),
                  encoding="utf-8") as f:
            _COORDINATES = json.load(f)
    c = _COORDINATES.get(code) if isinstance(code, str) and _AIRPORT.fullmatch(code) else None
    return c if isinstance(c, list) and len(c) >= 2 else None


_ZONES = {}                 # prepare(): the time zones held, so that no later ZoneInfo() reads a file


def prepare():
    """Reads airports.json and every time zone now (about 0.2 s), so that no later call reads a file: in Home
    Assistant it runs in the executor at setup."""
    import zoneinfo
    _airport("BLQ")
    for name in sorted(zoneinfo.available_timezones()):
        _ZONES[name] = _zone(name)


_ZONES_OF = None


def zone_of(code):
    """The IANA time zone of the airport ("Europe/Rome"), from airport_zones.json (tools/build_airport_zones.py, read
    on first use); None if the airport has none or the zone does not load here."""
    global _ZONES_OF
    if _ZONES_OF is None:
        import os
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "airport_zones.json"),
                  encoding="utf-8") as f:
            _ZONES_OF = json.load(f)
    name = _ZONES_OF.get(code) if isinstance(code, str) and _AIRPORT.fullmatch(code) else None
    return name if isinstance(name, str) and _zone(name) is not None else None


def coordinates(code):
    """[lat, lon] of the airport, None if missing."""
    c = _airport(code)
    return c[:2] if c else None


def country(code):
    """ISO 3166-1 alpha-2 code of the airport's country ("NL"), None if unknown."""
    c = _airport(code)
    return c[2] if c and len(c) > 2 and isinstance(c[2], str) and re.fullmatch(r"[A-Z]{2}", c[2]) else None


def meters(lat1, lon1, lat2, lon2):
    """Great-circle distance, in meters."""
    import math
    r = math.radians
    h = math.sin(r(lat2 - lat1) / 2) ** 2 + math.cos(r(lat1)) * math.cos(r(lat2)) * math.sin(r(lon2 - lon1) / 2) ** 2
    return 12_742_000 * math.asin(min(1.0, math.sqrt(h)))


def gate_regex(gate):
    """"B12" -> "^B[ -]?0*12$": how gates appear in the OpenStreetMap ref tag. None if the gate has no usable
    shape."""
    m = re.fullmatch(r"\s*([A-Za-z]{0,3})[\s-]*0*(\d{1,4})([A-Za-z]?)\s*", gate or "")
    if not m:
        return None
    letters, number, suffix = (x.upper() for x in m.groups())
    return f"^{letters}[ -]?0*{number}{suffix}$"


def find_gate(lat, lon, gate, timeout=40, opener=None):
    """(lat, lon) of the gate from OpenStreetMap (aeroway=gate, ref) within 4 km of the airport; None if it is not
    there. Only the airport coordinates and the gate code go to Overpass. APIError if it does not answer."""
    rx = gate_regex(gate)
    if rx is None:
        return None
    q = (f'[out:json][timeout:25];nwr(around:4000,{float(lat):.5f},{float(lon):.5f})'
         f'["aeroway"="gate"]["ref"~"{rx}",i];out center 5;')
    r = urllib.request.Request(OVERPASS_URL, data=urllib.parse.urlencode({"data": q}).encode(),
                               headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    try:
        with (opener or urllib.request.urlopen)(r, timeout=timeout) as response:
            body = json.loads(response.read(1024 * 1024).decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise APIError(f"Overpass {e.code}", e.code) from None
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise APIError(f"Overpass unreachable ({type(e).__name__})") from None
    for e in (body.get("elements") or []) if isinstance(body, dict) else []:
        c = e.get("center") or e
        if isinstance(c.get("lat"), (int, float)) and isinstance(c.get("lon"), (int, float)):
            return float(c["lat"]), float(c["lon"])
    return None


def walk(person, airport, gate_pos):
    """(walking minutes or None, details) from the person's position to the gate.
      - person: {"lat", "lon", "accuracy"} from the phone's GPS (None if missing; ignored if too inaccurate);
      - airport: [lat, lon] of the departure airport; gate_pos: (lat, lon) of the gate, or None.
    Away from the airport or without a gate: minutes None (GATE_MARGIN applies), with the details for the
    message."""
    if not person or not airport or (person.get("accuracy") or 0) > MAX_GPS_ACCURACY_M:
        return None, {}
    from_airport = meters(person["lat"], person["lon"], *airport)
    if from_airport > AIRPORT_RADIUS_M:
        return None, {"away_km": round(from_airport / 1000)}
    if not gate_pos:
        return None, {"at_airport": True}
    path = meters(person["lat"], person["lon"], *gate_pos) * CORRIDOR_FACTOR
    minutes = min(max(round(path / WALK_M_PER_MIN) + 3, WALK_MINUTES[0]), WALK_MINUTES[1])
    return minutes, {"meters": int(round(path, -1)), "minutes": minutes}


def base_snapshot(pass_gate=None):
    """What the user knows before any answer: the time and gate of the pass, no delay."""
    return {"cancelled": False, "diverted": False, "gate": pass_gate, "delay": 0, "gate_sent": False}


def changes(before, s, pass_gate=None):
    """(events, new snapshot). Only what changes compared with what the user knows:
      - cancelled, diverted;
      - gate: different from one already known (from the pass or an earlier answer), or assigned after "head to
        the gate" went out without one. The first gate discovered is not a change: it comes with "head to the
        gate";
      - delay: changed by at least 15 minutes, also when it goes back down.
    After departure only cancellation and diversion count."""
    before = {**base_snapshot(pass_gate), **(before or {})}
    s = s or {}
    departed = phase(s) in (PHASE_DEPARTED, PHASE_AIRBORNE, PHASE_LANDED)
    after, events = dict(before), []
    for k in ("cancelled", "diverted"):         # the same word is the status key, the snapshot key and the event
        if s.get(k) and not before[k]:
            events.append(k)
            after[k] = True
    if s.get("gate") and not departed:
        if s["gate"] != before["gate"] and (before["gate"] or before["gate_sent"]):
            events.append("gate")
        after["gate"] = s["gate"]
    delay = s.get("departure_delay") or 0
    if not departed and abs(delay - before["delay"]) >= DELAY_NOTIFY_MIN \
            and max(delay, before["delay"]) >= DELAY_NOTIFY_MIN:
        events.append("delay")
        after["delay"] = delay
    return (["cancelled"] if "cancelled" in events else events), after


def flight_code(v):
    return f"{v['airline']} {v['number']}"


def city(code, fallback=None):
    """"Roma Fiumicino" from the table, then the AviationStack name, then the code."""
    return airports.CITIES.get(code) or fallback or code


def route(v, s=None):
    s = s or {}
    return (f"{city(v['origin'], s.get('origin_city'))} ({v['origin']}) → "
            f"{city(v['destination'], s.get('destination_city'))} ({v['destination']})")


def message(v, s, events, gate_before=None):
    """(title, text) of the notification for the events of one check. `gate_before`: the gate that was known."""
    s = s or {}
    name, otz = flight_code(v), s.get("origin_tz")
    hhmm = lambda iso: local_time(iso, otz) or "?"
    if "cancelled" in events:
        return f"{name} cancellato", f"{route(v, s)} del {dd_mm(v['date'])}: la compagnia l'ha cancellato."
    if "diverted" in events:
        if s.get("api_status") == "incident":
            return (f"{name}: problema segnalato",
                    f"{route(v, s)}: AviationStack segnala un incidente o un problema. Controlla con la compagnia.")
        return f"{name} deviato", f"{route(v, s)}: il volo e' stato dirottato su un altro aeroporto."
    parts = []
    if "delay" in events:
        title = f"{name}: {status_text(v, s).lower()}"
        parts.append(f"Partenza alle {hhmm(departure(v, s))}"
                     + (f" invece delle {hhmm(s['departure_scheduled'])}" if s.get("departure_scheduled")
                        and s.get("departure_delay") else "") + f", imbarco verso le {hhmm(boarding_time(v, s)[0])}.")
    else:
        title = f"{name}: cambia il gate" if gate_before else f"{name}: gate assegnato"
    if "gate" in events and s.get("gate"):
        parts.append(f"Gate {s['gate']}" + (f" (prima {gate_before})" if gate_before else "")
                     + (f", terminal {s['terminal']}" if s.get("terminal") else "") + ".")
    if "delay" not in events:
        parts.append(f"Partenza alle {hhmm(departure(v, s))}.")
    return title, " ".join(parts)


def gate_message(v, s, priority=False, details=None):
    """(title, text) of "head to the gate". `details`: those of walk()."""
    s = s or {}
    otz, gate = s.get("origin_tz"), s.get("gate") or v.get("pass_gate")
    t, estimated = boarding_time(v, s)
    by = local_time(at_gate_time(v, s, priority).isoformat(), otz) if t else "?"
    title = f"{flight_code(v)}: avviati al gate {gate}" if gate else f"{flight_code(v)}: avviati verso i gate"
    parts = [f"Al gate per le {by}" + (", quando inizia l'imbarco" if priority else "")
             + f" (imbarco alle {local_time(t, otz) or '?'}" + (" circa" if estimated else "")
             + f"), partenza alle {local_time(departure(v, s), otz) or '?'} per "
             + f"{city(v['destination'], s.get('destination_city'))}."]
    if s.get("terminal") or v.get("pass_terminal"):
        parts.append(f"Terminal {s.get('terminal') or v.get('pass_terminal')}.")
    if not gate:
        parts.append("Il gate non e' ancora indicato: guarda i monitor.")
    d = details or {}
    if d.get("meters"):
        parts.append(f"Da dove sei sono circa {d['meters']} m, {d['minutes']} minuti a piedi.")
    elif d.get("away_km"):
        parts.append(f"Risulti a {d['away_km']} km dall'aeroporto: conta anche il viaggio fin li'.")
    return title, " ".join(parts)


def dd_mm(iso):
    return f"{iso[8:10]}/{iso[5:7]}" if iso else "?"
