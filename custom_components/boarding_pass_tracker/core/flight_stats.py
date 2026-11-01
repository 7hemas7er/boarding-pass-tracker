#!/usr/bin/env python3
"""flight_stats: the numbers of the household's flights (hours in the air, km, delays, countries, airlines, records).

A pure module, no network and no database: it takes the rows of the flights and boarding_passes tables and
returns a dict that the engine publishes as the attributes of the flight_statistics MQTT sensor (the state is
sensor_state()).

A flight counts as flown if it landed or was diverted, if it departed on a day before today, or if it is at least
two days old even with no news: AviationStack (free plan) only knows yesterday and today, so a flight uploaded
late will never get its landing. The duration is the time in the air (takeoff -> landing), then the time
between the gates, then the scheduled times (estimated); outside DURATION_RANGE a time is wrong and the next source
is used; with none of them, the duration is estimated from the distance. On time: less than 15 minutes of delay
at arrival (at departure, if the arrival is unknown); a diverted flight never is.

Only the passengers' first names leave this module: never the `passenger` column (the full name, which together
with the PNR is enough to manage the booking). Every entry of a flight (list and records) has its passengers, so
the card can filter by person; by_person has the same totals restricted to each person's flown flights, plus the
cancelled and diverted ones over all of their flights and countries_total (the countries list may be shortened,
the total is not). HA does not keep attributes over 16 KiB in the recorder: the output stays under MAX_BYTES
bytes, shortening the flight list first (list_total says how many there are), then the by_person lists and the
people at the tail (CUTS).

The output keys and codes are English: the outcome codes ("flown", "diverted", "cancelled", "upcoming") and the
cabin codes ("business", "economy", "unknown") are published as they are, and the stats card shows its own Italian
labels for them. Phases are compared through flights.PHASE_*. City and airport names stay Italian.
Tests: test_flight_stats.py.
"""
import datetime, json, math, unicodedata

if __package__:
    from . import flights
else:
    import flights

MAX_BYTES = 12_000              # bytes of json.dumps(..., ensure_ascii=False) in UTF-8, as ha.py sends them
DURATION_RANGE = (10, 24 * 60)  # minutes: a 3-minute or a two-day flight is a wrong time
MAX_DELAY = 7 * 24 * 60         # minutes: beyond this the data is broken (and a huge int does not fit in the JSON)
ON_TIME_MIN = 15                # minutes of delay at arrival: below, on time (A15, the statistics threshold)
EARTH_CIRCUMFERENCE_KM = 40075  # km at the equator
# Duration estimated from the distance, for flights with no times (uploaded later: the free AviationStack plan does
# not know the past): 40 minutes of taxi, climb and descent plus the distance at 850 km/h. Checked against typical
# scheduled block times from about 1,150 to 9,400 km: within about 10% (the wind is ignored)
ESTIMATE_FIXED_MIN, ESTIMATE_KMH = 40, 850
MAX_LIST, MAX_COUNTRIES, MAX_AIRPORTS, MAX_AIRLINES, MAX_ROUTES, MAX_PASSENGERS = 30, 30, 8, 8, 5, 10
MAX_PEOPLE, MAX_AIRLINES_PER_PERSON = 10, 3     # by_person: the people, and the airlines of each one
MAX_NAME = 30
MAX_CITY = 30                   # the AviationStack name, when the airports.py table does not have the code
FLOWN = ("flown", "diverted")     # the outcomes that count as flown
# If the output is too big, entries are removed from the tail of these lists, in this order, down to the given
# minimum. "by_person.x" is the list x of every person: the entry comes off the longest one (on a tie, the one of
# the person lowest down), so they all shrink together; then "by_person" drops people from the tail. All of this
# before the flight list goes below 10 entries
CUTS = (("list", 10), ("countries", 10), ("by_year", 5),
        ("by_person.airlines", 0), ("by_person.countries", 0), ("by_person", 0),
        ("list", 0), ("countries", 0), ("by_year", 0),
        ("passengers", 0), ("airports", 0), ("airlines", 0), ("routes", 0))
# The _totals() keys that by_year and passengers publish
_YEAR_KEYS = ("flights", "air_time_h", "km", "arrival_delay_min", "on_time_pct")
_PASSENGER_KEYS = ("flights", "km", "air_time_h")
_DURATIONS = (("takeoff", "landing", False), ("departure_actual", "arrival_actual", False),
              ("departure_scheduled", "arrival_scheduled", True))


# ---------------------------------------------------------------- one flight
def _status(raw):
    """The flights.status column as a dict; None if it is missing, if the JSON is broken or if it is not an object."""
    if not isinstance(raw, (str, bytes)) or not raw:
        return None
    try:
        s = json.loads(raw)
    except (ValueError, RecursionError):        # RecursionError: "[[[[..." nested without end
        return None
    return s if isinstance(s, dict) else None


def _day(x):
    try:
        return datetime.date.fromisoformat(x) if isinstance(x, str) else None
    except ValueError:
        return None


def _instant(x):
    """ISO 8601 -> datetime; None if it is missing, invalid or has no time zone (it could not be compared with
    the others)."""
    if not isinstance(x, str):
        return None
    try:
        t = datetime.datetime.fromisoformat(x)
    except ValueError:
        return None
    return t if t.tzinfo else None


def _minutes(start, end):
    a, b = _instant(start), _instant(end)
    return (b - a).total_seconds() / 60 if a is not None and b is not None else None


def _round(x):
    """Half a minute (or half a point) rounds up: Python's round() rounds 12.5 to 12."""
    return math.floor(x + 0.5)


def _delay(x):
    """Minutes of delay (negative too) as an int; None if it is not a believable number. A boolean does not count."""
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        return None
    if isinstance(x, float):
        if not math.isfinite(x):
            return None
        x = _round(x)
    return x if abs(x) <= MAX_DELAY else None


def outcome(v, s, today):
    """(outcome, no_data), outcome being "flown", "diverted", "cancelled" or "upcoming". no_data: flown according to
    the calendar, with no news about the arrival (no status, or only the scheduled times)."""
    phase = flights.phase(s)
    d = _day(v.get("date"))
    if phase == flights.PHASE_CANCELLED:
        return "cancelled", False
    if phase == flights.PHASE_DIVERTED:
        return "diverted", False
    if phase == flights.PHASE_LANDED:
        return "flown", False
    if d is not None and phase in (flights.PHASE_DEPARTED, flights.PHASE_AIRBORNE) and d < today:
        return "flown", False
    if d is not None and phase in (None, flights.PHASE_SCHEDULED) and d < today - datetime.timedelta(days=1):
        return "flown", True
    return "upcoming", False


def duration(s, km=None, v=None):
    """(minutes, estimated): in the air, then between the gates, then from the scheduled times (estimated), then
    from the flight's own departure and arrival (`v`: typed or from the pass; estimated), then from the distance
    (estimated: without it, a flight with no times would count zero minutes next to its km). (None, False) if none
    of them is within DURATION_RANGE."""
    s = s or {}
    for start, end, estimated in _DURATIONS:
        m = _minutes(s.get(start), s.get(end))
        if m is not None and DURATION_RANGE[0] <= m <= DURATION_RANGE[1]:
            return _round(m), estimated
    m = _minutes((v or {}).get("pass_departure"), (v or {}).get("pass_arrival"))
    if m is not None and DURATION_RANGE[0] <= m <= DURATION_RANGE[1]:
        return _round(m), True
    if isinstance(km, (int, float)) and km > 0:
        m = _round(ESTIMATE_FIXED_MIN + km / ESTIMATE_KMH * 60)
        if DURATION_RANGE[0] <= m <= DURATION_RANGE[1]:
            return m, True
    return None, False


def departure_delay(s):
    return _delay((s or {}).get("departure_delay"))


def arrival_delay(s):
    """Minutes between the actual arrival (at the gate, otherwise the landing) and the scheduled one; without one
    of the two, AviationStack's delay. Negative: early."""
    s = s or {}
    end = next((x for x in (s.get("arrival_actual"), s.get("landing")) if _instant(x)), None)
    m = _minutes(s.get("arrival_scheduled"), end)
    computed = _delay(m) if m is not None else None
    return computed if computed is not None else _delay(s.get("arrival_delay"))


def distance(origin, destination):
    """Great-circle km between the two airports, None if the location of either one is unknown."""
    c1, c2 = flights.coordinates(origin), flights.coordinates(destination)
    return _round(flights.meters(*c1, *c2) / 1000) if c1 and c2 else None


def cabin(cabins):
    """"business" if all the passengers are in first or business, "economy" if at least one is not, "unknown" if
    no boarding pass has the cabin."""
    known = [c.strip().upper() for c in cabins if isinstance(c, str) and c.strip()]
    if not known:
        return "unknown"
    return "business" if flights.is_priority(known) else "economy"


def clean_name(s):
    """The first name to show: only letters, spaces, apostrophes and hyphens, at most MAX_NAME characters. None if
    nothing is left."""
    if not isinstance(s, str):
        return None
    s = unicodedata.normalize("NFC", s).replace("\u2019", "'")     # a decomposed "José" would lose its accent
    s = "".join(c for c in s if c.isalpha() or c in " '-")
    s = " ".join(s.split())[:MAX_NAME].strip(" '-")
    return s or None


def flag(code):
    """"NL" -> 🇳🇱: the two letters as Unicode "regional indicator" symbols."""
    return "".join(chr(0x1F1E6 + ord(c) - 65) for c in code)


def _city(code, fallback):
    return flights.city(code, fallback[:MAX_CITY] if isinstance(fallback, str) and fallback else None)


# ---------------------------------------------------------------- all flights
def _row(v, passes, today):
    """What is needed of one flight, computed only once."""
    s = _status(v.get("status"))
    result, no_data = outcome(v, s, today)
    d = _day(v.get("date"))
    km = distance(v.get("origin"), v.get("destination"))
    minutes, estimated = duration(s, km, v) if result != "cancelled" else (None, False)
    arr_delay = arrival_delay(s) if result not in ("cancelled", "diverted") else None   # diverted: it arrived elsewhere
    dep_delay = departure_delay(s) if result != "cancelled" else None
    if result == "diverted":
        on_time = False
    elif arr_delay is not None:
        on_time = arr_delay < ON_TIME_MIN
    elif dep_delay is not None:
        on_time = dep_delay < ON_TIME_MIN
    else:
        on_time = None
    dep = _instant(flights.departure(v, s))
    return {"v": v, "s": s or {}, "outcome": result, "no_data": no_data, "year": d.year if d else None,
            "duration": minutes, "estimated": estimated, "km": km,
            "arrival_delay": arr_delay, "departure_delay": dep_delay, "on_time": on_time,
            "cabin": cabin([c.get("cabin") for c in passes]),
            "names": sorted({n for n in (clean_name(c.get("first_name")) for c in passes) if n}),
            "order": (v.get("date") if isinstance(v.get("date"), str) else "",
                      dep.timestamp() if dep is not None else -math.inf)}


def _entry(r):
    """The flight as an entry of the published lists (list, records, by_person[].longest)."""
    v, s = r["v"], r["s"]
    return {"date": v.get("date"), "flight": flights.flight_code(v), "route": f"{v['origin']} → {v['destination']}",
            "cities": f"{_city(v['origin'], s.get('origin_city'))} → "
                      f"{_city(v['destination'], s.get('destination_city'))}",
            "duration_min": r["duration"], "km": r["km"], "delay_min": r["arrival_delay"],
            "outcome": r["outcome"], "estimated": r["estimated"], "passengers": list(r["names"])}


def _totals(rows):
    """Flights, minutes, km, delays and on-time rate of a group of flown flights, under their published names:
    by_year and passengers publish some of them as they are."""
    known = [max(r["arrival_delay"], 0) for r in rows if r["arrival_delay"] is not None]
    rated = [r["on_time"] for r in rows if r["on_time"] is not None]
    minutes = sum(r["duration"] or 0 for r in rows)
    return {"flights": len(rows), "air_time_min": minutes, "air_time_h": round(minutes / 60, 1),
            "km": sum(r["km"] or 0 for r in rows),
            "departure_delay_min": sum(max(r["departure_delay"], 0) for r in rows if r["departure_delay"] is not None),
            "arrival_delay_min": sum(known),
            "mean_delay_min": _round(sum(known) / len(known)) if known else None,
            "on_time_pct": _round(100 * sum(rated) / len(rated)) if rated else None}


def _top(counts, n=None):
    """[(key, count)] from the most frequent; ties in alphabetical order, so that the output does not change."""
    return sorted(counts.items(), key=lambda x: (-x[1], x[0]))[:n]


def _count(rows):
    """{country: flights}, {airport: flights}, {airline: flights} of a group of flown flights: a domestic flight
    counts once for its country."""
    countries, airports, airlines = {}, {}, {}
    for r in rows:
        v = r["v"]
        for country in {flights.country(v["origin"]), flights.country(v["destination"])} - {None}:
            countries[country] = countries.get(country, 0) + 1
        for code in {v["origin"], v["destination"]}:
            airports[code] = airports.get(code, 0) + 1
        airlines[v["airline"]] = airlines.get(v["airline"], 0) + 1
    return countries, airports, airlines


def _record(rows, key, smallest=False):
    """The entry of the flight with the largest (or smallest) value; on a tie, the most recent one."""
    good = [r for r in rows if r[key] is not None]
    return _entry((min if smallest else max)(good, key=lambda r: r[key])) if good else None


def _person(name, rows, all_rows):
    """The numbers of one passenger: the same definitions as the totals, over only the flown flights they were on
    (rows); cancelled and diverted over all of their flights (all_rows), like the global counters.
    countries_total and airports_total count all of their countries and airports: the countries list stops at
    MAX_COUNTRIES and _within_limit may shorten it further."""
    tot = _totals(rows)
    countries, airports, airlines = _count(rows)
    return {"name": name, "flights": tot["flights"],
            "cancelled": sum(r["outcome"] == "cancelled" for r in all_rows),
            "diverted": sum(r["outcome"] == "diverted" for r in all_rows),
            "air_time_min": tot["air_time_min"],
            "estimated_durations": sum(r["duration"] is not None and r["estimated"] for r in rows),
            "km": tot["km"], "arrival_delay_min": tot["arrival_delay_min"],
            "mean_delay_min": tot["mean_delay_min"], "on_time_pct": tot["on_time_pct"],
            "countries": [c for c, _ in _top(countries, MAX_COUNTRIES)], "countries_total": len(countries),
            "airports_total": len(airports),
            "airlines": [{"code": c, "flights": n} for c, n in _top(airlines, MAX_AIRLINES_PER_PERSON)],
            "longest": _record(rows, "km")}


def _size(d):
    return len(json.dumps(d, ensure_ascii=False).encode("utf-8"))


def _within_limit(out):
    for key, minimum in CUTS:
        if "." in key:                          # the `sub` list of every element of out[parent]
            parent, sub = key.split(".")
            while _size(out) >= MAX_BYTES:
                longer = [x[sub] for x in out[parent] if len(x[sub]) > minimum]
                if not longer:
                    break
                max(reversed(longer), key=len).pop()     # reversed: on a tie, the person lowest down
        else:
            while len(out[key]) > minimum and _size(out) >= MAX_BYTES:
                out[key].pop()
    return out


# compute() takes the flights rows as its `flights` argument, which hides the flights module inside it: this
# helper calls the module on its behalf.
def _code_and_id(r):
    return flights.flight_code(r["v"]), r["v"].get("id") or 0


def compute(flights, passes, today):
    """The flight statistics (rows of flights and boarding_passes, as dicts) as of `today`. See the module
    docstring and test_flight_stats.py for the fields."""
    by_flight = {}
    for c in passes or []:
        c = dict(c)
        by_flight.setdefault(c.get("flight_id"), []).append(c)
    rows = [_row(v, by_flight.get(v.get("id"), []), today) for v in (dict(x) for x in flights or [])]
    # newest first; on the same day the latest departure first; then flight code and id (the sort is stable)
    rows.sort(key=_code_and_id)
    rows.sort(key=lambda r: r["order"], reverse=True)
    flown = [r for r in rows if r["outcome"] in FLOWN]
    tot = _totals(flown)

    countries, airports, airlines = _count(flown)
    airport_names, routes, by_name = {}, {}, {}
    for r in flown:
        v, s = r["v"], r["s"]
        for code, fallback in {v["origin"]: s.get("origin_city"), v["destination"]: s.get("destination_city")}.items():
            if airport_names.get(code, code) == code:   # the first real name, from any flight
                airport_names[code] = _city(code, fallback)
        route = f"{v['origin']} → {v['destination']}"
        routes[route] = routes.get(route, 0) + 1
        for n in r["names"]:
            by_name.setdefault(n, []).append(r)         # newest first, like flown
    people = sorted(by_name.items(), key=lambda x: (-len(x[1]), x[0]))     # most flights first, then alphabetical
    all_by_name = {}                                    # cancelled and future flights too, for _person's counters
    for r in rows:
        for n in r["names"]:
            all_by_name.setdefault(n, []).append(r)
    cabins = {"business": 0, "economy": 0, "unknown": 0}
    for r in flown:
        cabins[r["cabin"]] += 1
    years = {}
    for r in flown:
        if r["year"] is not None:
            years.setdefault(r["year"], []).append(r)

    out = {
        "flights": tot["flights"],
        "cancelled": sum(r["outcome"] == "cancelled" for r in rows),
        "diverted": sum(r["outcome"] == "diverted" for r in rows),
        "upcoming": sum(r["outcome"] == "upcoming" for r in rows),
        "air_time_min": tot["air_time_min"], "air_time_h": tot["air_time_h"],
        "estimated_durations": sum(r["duration"] is not None and r["estimated"] for r in flown),
        "no_data": sum(r["no_data"] for r in flown),
        "km": tot["km"], "earth_laps": round(tot["km"] / EARTH_CIRCUMFERENCE_KM, 2),
        "departure_delay_min": tot["departure_delay_min"], "arrival_delay_min": tot["arrival_delay_min"],
        "mean_delay_min": tot["mean_delay_min"], "on_time_pct": tot["on_time_pct"],
        "countries": [{"code": c, "flag": flag(c), "flights": n} for c, n in _top(countries, MAX_COUNTRIES)],
        "countries_total": len(countries),
        "airports": [{"code": c, "name": airport_names[c], "flights": n} for c, n in _top(airports, MAX_AIRPORTS)],
        "airports_total": len(airports),
        "airlines": [{"code": c, "flights": n} for c, n in _top(airlines, MAX_AIRLINES)],
        "routes": [{"route": t, "flights": n} for t, n in _top(routes, MAX_ROUTES)],
        "cabins": cabins,
        "passengers": [{"name": n, **{k: x[k] for k in _PASSENGER_KEYS}}
                       for n, x in ((n, _totals(rr)) for n, rr in people[:MAX_PASSENGERS])],
        "by_person": [_person(n, rr, all_by_name[n]) for n, rr in people[:MAX_PEOPLE]],
        "records": {"longest": _record(flown, "km"), "shortest": _record(flown, "km", smallest=True),
                    "max_duration": _record(flown, "duration"),
                    "max_delay": _record([r for r in flown if (r["arrival_delay"] or 0) > 0], "arrival_delay")},
        "by_year": [{"year": a, **{k: x[k] for k in _YEAR_KEYS}}
                    for a, x in sorted(((a, _totals(rr)) for a, rr in years.items()), key=lambda y: y[0],
                                       reverse=True)],
        "list": [_entry(r) for r in rows[:MAX_LIST]],
        "list_total": len(rows),
        "updated_on": today.isoformat(),
    }
    return _within_limit(out)


def sensor_state(stats):
    """The sensor state: the flights flown."""
    return stats["flights"]
