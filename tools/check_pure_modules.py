#!/usr/bin/env python3
"""Checks that the pure modules of the core are the oracle's copies with only the declared hunks (INTEGRATION_PLAN.md
2.2, 3.7 E1 and F1): airports.py and airports.json byte for byte, flight_stats.py and manual.py with only the E1
import header, flights.py with the E1 header, the F1 hunks (LOCAL_TZ, _local_at at its two uses, prepare) and the F2
hunks (FIX-repo-1, SECURITY-05: the ZIP entries counted before zipfile parses the central directory), and the
stage-B hunks of 3.7 (decision 3, schema v2: B:F2-B:F5 in flights.py, B:S1 in flight_stats.py, B:M1-B:M8 in
manual.py, each with its exact text in STAGE_B). Read only.

  python3 -I -B tools/check_pure_modules.py --oracle <the oracle's tracker folder> [--core DIR] [--json OUT]

The oracle's text with the HUNKS applied (each `old` exactly once, in order) must equal the copy byte for byte;
otherwise the undeclared lines are printed as a unified diff. engine.py and ha.py are patched modules (E2-E15,
H1-H3), not checked here: their diff sizes are reported. Exit 0 when only the declared hunks are there.
"""
import argparse, difflib, hashlib, json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CORE = os.path.join(ROOT, "custom_components", "boarding_pass_tracker", "core")

IDENTICAL = ("airports.py", "airports.json")
PATCHED = ("engine.py", "ha.py")                 # not pure: reported only


def e1(names):
    return (f"\nimport {names}\n", f"\nif __package__:\n    from . import {names}\nelse:\n    import {names}\n")


HUNKS = [
    ("E1", "flight_stats.py", "the import header", *e1("flights")),
    ("E1", "manual.py", "the import header", *e1("flight_stats, flights")),
    ("E1", "flights.py", "the import header", *e1("airports")),
    ("F1", "flights.py", "LOCAL_TZ",
     "UTC = datetime.timezone.utc\n",
     "UTC = datetime.timezone.utc\n"
     "# The local zone of «today», noon and 04:00 (reference_time, next_check): None is the process zone (the service);\n"
     "# Home Assistant sets its own configured zone (engine.configure)\n"
     "LOCAL_TZ = None\n"),
    ("F1", "flights.py", "_local_at",
     "def reference_time(v, s, now):\n",
     'def _local_at(day, hour):\n'
     '    """`hour`:00 of `day` in LOCAL_TZ, or in the process zone when LOCAL_TZ'
     ' is None: an aware datetime with that\n'
     '    instant\'s fixed offset, as .astimezone() gives, so that `+'
     ' timedelta(hours=24)` adds 24 real hours also across\n'
     '    a daylight saving change (a ZoneInfo datetime would add wall-clock time: 23 or 25 hours)."""\n'
     '    if LOCAL_TZ is not None:\n'
     '        t = datetime.datetime.combine(day, datetime.time(hour), tzinfo=LOCAL_TZ)\n'
     '        return t.astimezone(datetime.timezone(t.utcoffset()))\n'
     '    return datetime.datetime.combine(day, datetime.time(hour)).astimezone()\n'
     '\n\n'
     'def reference_time(v, s, now):\n'),
    # F2 (FIX-repo-1, SECURITY-05): the ZIP's central-directory entries counted before zipfile lists them
    ("F2", "flights.py", "_too_many_entries, and is_pkpass refuses more than MAX_ZIP_ENTRIES before zipfile",
     'def is_pkpass(b):\n'
     '    """By content: a ZIP with pass.json at the root."""\n'
     '    if b[:4] != b"PK\\x03\\x04":\n',
     'def _too_many_entries(b):\n'
     '    """More than MAX_ZIP_ENTRIES entries in the ZIP\'s central directory?'
     ' Every entry zipfile can list starts with\n'
     '    the signature PK\\\\x01\\\\x02 somewhere in the file, so counting it (at C'
     ' speed, whatever the end record declares)\n'
     '    bounds the work before zipfile builds one object per entry: 300,000 empty'
     ' entries in 25 MB took a second of pure\n'
     '    Python and 50 MB of memory in Home Assistant\'s executor."""\n'
     '    return b.count(b"PK\\x01\\x02") > MAX_ZIP_ENTRIES\n'
     '\n\n'
     'def is_pkpass(b):\n'
     '    """By content: a ZIP with pass.json at the root (and at most MAX_ZIP_ENTRIES entries)."""\n'
     '    if b[:4] != b"PK\\x03\\x04" or _too_many_entries(b):\n'),
    ("F2", "flights.py", "read_pass refuses more than MAX_ZIP_ENTRIES before zipfile",
     '    """pass.json as a dict. NotABoardingPass if the file is broken, too big or not an air boarding pass."""\n'
     '    try:\n',
     '    """pass.json as a dict. NotABoardingPass if the file is broken, too big or not an air boarding pass."""\n'
     '    if _too_many_entries(b):                    # before zipfile lists them all\n'
     '        raise NotABoardingPass("pkpass con troppi file")\n'
     '    try:\n'),
    ("F1", "flights.py", "reference_time: noon of the flight day (F:529)",
     "    return datetime.datetime.combine(d, datetime.time(12)).astimezone()\n",
     "    return _local_at(d, 12)\n"),
    ("F1", "flights.py", "next_check: 04:00 of the flight day (F:557)",
     '        at4 = datetime.datetime.combine(datetime.date.fromisoformat(v["date"]), datetime.time(4)).astimezone()\n',
     '        at4 = _local_at(datetime.date.fromisoformat(v["date"]), 4)\n'),
    ("F1", "flights.py", "prepare: airports.json and the time zones read once",
     "def coordinates(code):\n",
     '_ZONES = {}                 # prepare(): the time zones held, so that no later ZoneInfo() reads a file\n'
     '\n\n'
     'def prepare():\n'
     '    """Reads airports.json and every time zone now (about 0.2 s), so that no later call reads a file: in Home\n'
     '    Assistant it runs in the executor at setup."""\n'
     '    import zoneinfo\n'
     '    _airport("BLQ")\n'
     '    for name in sorted(zoneinfo.available_timezones()):\n'
     '        _ZONES[name] = _zone(name)\n'
     '\n\n'
     'def coordinates(code):\n'),
]
# Stage B (decision 3, INTEGRATION_PLAN.md 3.7: F2-F5 flights, S1 flight_stats, M1-M8 manual), WP8 in workflow 2:
# the exact text of every stage-B hunk, applied after the stage-A ones (ids "B:<plan id>")
STAGE_B = [
    ('B:F5', 'flights.py', 'ARRIVAL_AFTER: the bound of a credible arrival of a pass',
     'PASS_LEAD_DAYS = 3     # days: a boarding pass comes with check-in, at most 3 days before the flight\n\n',
     "PASS_LEAD_DAYS = 3     # days: a boarding pass comes with check-in, at most 3 days before the flight\n# the pass's arrival is kept only this long after its departure (flight_stats.DURATION_RANGE: 10 minutes to a day)\nARRIVAL_AFTER = (datetime.timedelta(minutes=10), datetime.timedelta(hours=24))\n\n"),
    ('B:F5', 'flights.py', '_from_semantics reads currentArrivalDate / originalArrivalDate',
     '        "boarding": _instant(sem.get("currentBoardingDate")) or _instant(sem.get("originalBoardingDate")),\n        "gate": _short(sem.get("departureGate"), 8),\n',
     '        "boarding": _instant(sem.get("currentBoardingDate")) or _instant(sem.get("originalBoardingDate")),\n        "arrival": _instant(sem.get("currentArrivalDate")) or _instant(sem.get("originalArrivalDate")),\n        "gate": _short(sem.get("departureGate"), 8),\n'),
    ('B:F5', 'flights.py', "read_boarding_pass: the leg's pass_arrival, kept only after a credible departure",
     '        for k, sem_key in (("pass_departure", "departure"), ("pass_boarding", "boarding"), ("pass_gate", "gate"),\n                           ("terminal", "terminal")):\n            t[k] = sem.get(sem_key) if same else None\n',
     '        for k, sem_key in (("pass_departure", "departure"), ("pass_boarding", "boarding"), ("pass_gate", "gate"),\n                           ("terminal", "terminal"), ("pass_arrival", "arrival")):\n            t[k] = sem.get(sem_key) if same else None\n'),
    ('B:F5', 'flights.py', 'read_boarding_pass: an arrival without a credible departure is dropped',
     '            t["pass_departure"] = None          # semantics of another date: the BCBP wins\n        t["relevant_date"] = relevant if len(legs) == 1 else None\n',
     '            t["pass_departure"] = None          # semantics of another date: the BCBP wins\n        if t["pass_arrival"] and not (t["pass_departure"] and ARRIVAL_AFTER[0] <= _t(t["pass_arrival"])\n                                      - _t(t["pass_departure"]) <= ARRIVAL_AFTER[1]):\n            t["pass_arrival"] = None            # no departure to measure it from, or not a credible duration\n        t["relevant_date"] = relevant if len(legs) == 1 else None\n'),
    ('B:F5', 'flights.py', 'boarding_pass_from_barcodes: no pass_arrival from a photo',
     '            t["passenger"] = t["passenger"] or "passeggero"\n            t.update(pass_departure=None, pass_boarding=None, pass_gate=None, terminal=None, relevant_date=None)\n        return legs\n',
     '            t["passenger"] = t["passenger"] or "passeggero"\n            t.update(pass_departure=None, pass_boarding=None, pass_gate=None, terminal=None, relevant_date=None,\n                     pass_arrival=None)\n        return legs\n'),
    ('B:F2', 'flights.py', "arrival(s, v=None) falls back to the flight's pass_arrival",
     '\ndef arrival(s):\n    s = s or {}\n    return s.get("arrival_actual") or s.get("arrival_estimated") or s.get("arrival_scheduled")\n\n',
     '\ndef arrival(s, v=None):\n    """The best arrival known: actual, estimated, scheduled from AviationStack, then the flight\'s own (`v`: typed or\n    from the pass). next_check passes no `v`: its arrival is AviationStack\'s."""\n    s = s or {}\n    return (s.get("arrival_actual") or s.get("arrival_estimated") or s.get("arrival_scheduled")\n            or (v or {}).get("pass_arrival"))\n\n'),
    ('B:F3', 'flights.py', 'zone_of(code): airport_zones.json, read on first use',
     '\ndef coordinates(code):\n',
     '\n_ZONES_OF = None\n\n\ndef zone_of(code):\n    """The IANA time zone of the airport ("Europe/Rome"), from airport_zones.json (tools/build_airport_zones.py, read\n    on first use); None if the airport has none or the zone does not load here."""\n    global _ZONES_OF\n    if _ZONES_OF is None:\n        import os\n        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "airport_zones.json"),\n                  encoding="utf-8") as f:\n            _ZONES_OF = json.load(f)\n    name = _ZONES_OF.get(code) if isinstance(code, str) and _AIRPORT.fullmatch(code) else None\n    return name if isinstance(name, str) and _zone(name) is not None else None\n\n\ndef coordinates(code):\n'),
    ('B:F4', 'flights.py', 'gate_message: the terminal from pass_terminal when AviationStack has none',
     '             + f"{city(v[\'destination\'], s.get(\'destination_city\'))}."]\n    if s.get("terminal"):\n        parts.append(f"Terminal {s[\'terminal\']}.")\n    if not gate:\n',
     '             + f"{city(v[\'destination\'], s.get(\'destination_city\'))}."]\n    if s.get("terminal") or v.get("pass_terminal"):\n        parts.append(f"Terminal {s.get(\'terminal\') or v.get(\'pass_terminal\')}.")\n    if not gate:\n'),
    ('B:S1', 'flight_stats.py', 'duration(s, km, v): the signature and its docstring',
     '\ndef duration(s, km=None):\n    """(minutes, estimated): in the air, then between the gates, then from the scheduled times (estimated), then\n    from the distance (estimated: without it, a flight with no times would count zero minutes next to its km).\n    (None, False) if none of them is within DURATION_RANGE."""\n    s = s or {}\n',
     '\ndef duration(s, km=None, v=None):\n    """(minutes, estimated): in the air, then between the gates, then from the scheduled times (estimated), then\n    from the flight\'s own departure and arrival (`v`: typed or from the pass; estimated), then from the distance\n    (estimated: without it, a flight with no times would count zero minutes next to its km). (None, False) if none\n    of them is within DURATION_RANGE."""\n    s = s or {}\n'),
    ('B:S1', 'flight_stats.py', 'duration: the 4th source, pass_departure -> pass_arrival (estimated)',
     '            return _round(m), estimated\n    if isinstance(km, (int, float)) and km > 0:\n',
     '            return _round(m), estimated\n    m = _minutes((v or {}).get("pass_departure"), (v or {}).get("pass_arrival"))\n    if m is not None and DURATION_RANGE[0] <= m <= DURATION_RANGE[1]:\n        return _round(m), True\n    if isinstance(km, (int, float)) and km > 0:\n'),
    ('B:S1', 'flight_stats.py', '_row passes the flight to duration',
     '    km = distance(v.get("origin"), v.get("destination"))\n    minutes, estimated = duration(s, km) if result != "cancelled" else (None, False)\n    arr_delay = arrival_delay(s) if result not in ("cancelled", "diverted") else None   # diverted: it arrived elsewhere\n',
     '    km = distance(v.get("origin"), v.get("destination"))\n    minutes, estimated = duration(s, km, v) if result != "cancelled" else (None, False)\n    arr_delay = arrival_delay(s) if result not in ("cancelled", "diverted") else None   # diverted: it arrived elsewhere\n'),
    ('B:M4', 'manual.py', 'the zoneinfo import (local_instant, arrival_instant, rebase)',
     '"""\nimport collections, datetime, hashlib, json, re, unicodedata\n\n',
     '"""\nimport collections, datetime, hashlib, json, re, unicodedata, zoneinfo\n\n'),
    ('B:M1', 'manual.py', 'KEYS gains departure_time, arrival_time, terminal, gate',
     'TRACK_DAYS_BACK = 1                       # yesterday is still followed: AviationStack knows it (tracked)\nKEYS = ("action", "flight", "date", "origin", "destination", "passengers", "ref")\nACTIONS = ("add", "edit", "delete")\n',
     'TRACK_DAYS_BACK = 1                       # yesterday is still followed: AviationStack knows it (tracked)\nKEYS = ("action", "flight", "date", "origin", "destination", "passengers", "ref", "departure_time", "arrival_time",\n        "terminal", "gate")                 # the last four: stage B (decision 3), each optional\nACTIONS = ("add", "edit", "delete")\n'),
    ('B:M6', 'manual.py', 'TEXTS: the changed no_passengers, bad_passenger and nothing (BC-B2)',
     '    "same_airport": "Partenza e arrivo sono lo stesso aeroporto.",\n    "no_passengers": "Mancano i passeggeri: scrivi i nomi separati da virgole, con il posto dopo il nome, per "\n                     "esempio Alice 12A, Bob.",\n    "bad_passenger": "Non capisco un passeggero: scrivi il nome (solo lettere, spazi, apostrofi e trattini) e poi "\n                     "il posto, per esempio Alice 12A.",\n    "too_many": "Al massimo 9 passeggeri per volo.",\n    "same_name": "Due passeggeri con lo stesso nome: aggiungi il cognome a uno dei due, per esempio Marco Rossi.",\n    "no_ref": "Manca il volo da modificare: sceglilo dall\'elenco di «Modifica volo».",\n    "nothing": "Niente da cambiare: manda la data o i passeggeri.",\n    "not_found": "Questo volo non c\'è più: forse è stato eliminato. Riapri «Modifica volo».",\n',
     '    "same_airport": "Partenza e arrivo sono lo stesso aeroporto.",\n    "no_passengers": "Mancano i passeggeri: scrivi i nomi separati da virgole, con il posto e la classe dopo il "\n                     "nome se li sai, per esempio Alice 12A business, Bob.",\n    "bad_passenger": "Non capisco un passeggero: scrivi il nome (solo lettere, spazi, apostrofi e trattini), poi il "\n                     "posto e la classe se li sai, per esempio Alice 12A business.",\n    "too_many": "Al massimo 9 passeggeri per volo.",\n    "same_name": "Due passeggeri con lo stesso nome: aggiungi il cognome a uno dei due, per esempio Marco Rossi.",\n    "no_ref": "Manca il volo da modificare: sceglilo dall\'elenco di «Modifica volo».",\n    "nothing": "Niente da cambiare: manda la data, i passeggeri, gli orari, il terminal o il gate.",\n    "not_found": "Questo volo non c\'è più: forse è stato eliminato. Riapri «Modifica volo».",\n'),
    ('B:M6', 'manual.py', 'TEXTS: added gains {details} (empty without times)',
     '    # 200 replies of POST /flight\n    "added": "Aggiunto {code} del {day}, {route}, {n}.{suffix}",\n    "merged": "{code} del {day} c\'era già: {changes}. Ora ha {n}.{suffix}",\n',
     '    # 200 replies of POST /flight\n    "added": "Aggiunto {code} del {day}, {route}{details}, {n}.{suffix}",\n    "merged": "{code} del {day} c\'era già: {changes}. Ora ha {n}.{suffix}",\n'),
    ('B:M6', 'manual.py', 'TEXTS: the new texts of 5.2.4',
     '    "status_typed": "Inserito a mano",\n}\n',
     '    "status_typed": "Inserito a mano",\n    # stage B (decision 3): the times, terminal, gate and classes typed by hand. 400 replies of POST /flight\n    "bad_departure_time": "Orario di partenza non valido: scrivi l\'ora locale dell\'aeroporto di partenza, per esempio "\n                          "06:30.",\n    "bad_arrival_time": "Orario di arrivo non valido: scrivi l\'ora locale dell\'aeroporto di arrivo, per esempio 08:45.",\n    "arrival_needs_departure": "Per l\'orario di arrivo serve anche quello di partenza.",\n    "bad_duration": "Orari che non tornano: tra partenza e arrivo ci sarebbero meno di 10 minuti o più di 24 ore.",\n    "no_time_zone": "Non conosco il fuso orario di {city} ({code}): lascia vuoto l\'orario.",\n    "bad_terminal": "Terminal non valido: al massimo 8 tra lettere e cifre, per esempio T1.",\n    "bad_gate": "Gate non valido: al massimo 8 tra lettere e cifre, per esempio B12.",\n    # the parts of {changes} (changes_text) and of {details} (added_details)\n    "departure_changed": "orario di partenza cambiato",\n    "arrival_changed": "orario di arrivo cambiato",\n    "terminal_changed": "terminal cambiato",\n    "gate_changed": "gate cambiato",\n    "one_cabin_changed": "1 classe cambiata",\n    "cabins_changed": "{k} classi cambiate",\n    "times_moved": "orari spostati al nuovo giorno",\n    "added_details": ", partenza alle {departure}{arrival}",\n    "added_arrival": ", arrivo alle {arrival}",\n}\n'),
    ('B:M1', 'manual.py', 'parse_request docstring: the stage-B keys',
     '    null and a blank string mean not given. `today`: the host\'s local date. Returns {"action", "airline",\n    "number", "date", "origin", "destination", "passengers": [(name, seat)] | None, "ref": (id, version) | None};\n    InputError otherwise."""\n    if isinstance(body, (bytes, bytearray)):\n',
     '    null and a blank string mean not given. `today`: the host\'s local date. Returns {"action", "airline",\n    "number", "date", "origin", "destination", "passengers": [(name, seat)] | None, "ref": (id, version) | None,\n    and of stage B "departure_time", "arrival_time": (h, m) | CLEAR | None, "terminal", "gate": the code | CLEAR |\n    None, "cabins": [BCBP letter or None, one per passenger] | None, "whole": [(name, seat) or None, one per\n    passenger] | None (keep_names)}; InputError otherwise. CLEAR («-») only in an edit: in an add it is the same as\n    not given."""\n    if isinstance(body, (bytes, bytearray)):\n'),
    ('B:M1', 'manual.py', 'parse_request: the stage-B keys in its reply',
     '    out = {"action": action, **dict.fromkeys(("airline", "number", "date", "origin", "destination", "passengers",\n                                              "ref"))}\n    if action == "add":\n',
     '    out = {"action": action, **dict.fromkeys(("airline", "number", "date", "origin", "destination", "passengers",\n                                              "ref", *OWN_KEYS, "cabins", "whole"))}\n    if action == "add":\n'),
    ('B:M2', 'manual.py', 'parse_request add: the passengers with their classes (and the whole readings of keep_names)',
     '            raise InputError("same_airport")\n        out["passengers"] = parse_passengers(given.get("passengers"))\n        if not out["passengers"]:\n',
     '            raise InputError("same_airport")\n        out["passengers"], out["cabins"], out["whole"] = _passengers_cabins(given.get("passengers"))\n        if not out["passengers"]:\n'),
    ('B:M1', 'manual.py', 'parse_request add: the four fields',
     '            raise InputError("same_name")\n    elif action == "edit":\n',
     '            raise InputError("same_name")\n        _parse_own(out, given, clear=False)\n    elif action == "edit":\n'),
    ('B:M1', 'manual.py', 'parse_request edit: the classes (and whole readings), the four fields; nothing only when all are absent',
     '        if "passengers" in given:\n            out["passengers"] = parse_passengers(given["passengers"])\n        out["ref"] = parse_ref(given.get("ref"))\n        if out["date"] is None and out["passengers"] is None:\n            raise InputError("nothing")\n',
     '        if "passengers" in given:\n            out["passengers"], out["cabins"], out["whole"] = _passengers_cabins(given["passengers"])\n        _parse_own(out, given, clear=True)\n        out["ref"] = parse_ref(given.get("ref"))\n        if out["date"] is None and out["passengers"] is None and all(out[k] is None for k in OWN_KEYS):\n            raise InputError("nothing")\n'),
    ('B:M2', 'manual.py', 'parse_passengers through parse_passengers_cabins',
     '        raise InputError("bad_type", key="passengers")\n    return [p for p in map(_passenger, items) if p]\n\n',
     '        raise InputError("bad_type", key="passengers")\n    return parse_passengers_cabins(items)[0]\n\n'),
    ('B:M5', 'manual.py', 'version covers pass_departure, pass_arrival, pass_boarding, pass_gate, pass_terminal',
     '    """12 hex digits of a hash of what people edit and what an upload changes (the flight\'s key, destination and\n    created_at; every boarding pass), not of the status, snapshot or checks the clock writes."""\n    data = [v["id"], v["created_at"], v["airline"], v["number"], v["date"], v["origin"], v["destination"],\n            [[p["id"], p["passenger"], p["first_name"], p["seat"], p["cabin"], p["document_id"], p["uploaded_by"]]\n             for p in sorted(passes, key=lambda p: p["id"])]]\n    return hashlib.sha256(json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()[:12]\n',
     '    """12 hex digits of a hash of what people edit and what an upload changes (the flight\'s key, destination and\n    created_at; every boarding pass; the flight\'s own times, gate and terminal, VERSIONED), not of the status,\n    snapshot or checks the clock writes."""\n    data = [v["id"], v["created_at"], v["airline"], v["number"], v["date"], v["origin"], v["destination"],\n            [[p["id"], p["passenger"], p["first_name"], p["seat"], p["cabin"], p["document_id"], p["uploaded_by"]]\n             for p in sorted(passes, key=lambda p: p["id"])], [v.get(k) for k in VERSIONED]]\n    return hashlib.sha256(json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()[:12]\n'),
    ('B:M7', 'manual.py', 'changes_text: the stage-B arguments and their place (after the seats)',
     '\ndef changes_text(old_day, seats, added, removed, renamed=0, kept=0, cards=0):\n    """«data cambiata (prima il 08/11/2026), 1 nome cambiato, la carta d\'imbarco resta, 1 posto cambiato,\n    2 passeggeri aggiunti»: `old_day` (ISO) when the date changed, then the counts; only what changed. `kept`: the\n    renamed passengers with a boarding pass; `cards`: the boarding passes deleted with the removed passengers.\n    Never a name: the reply is logged."""\n    parts = [text("date_changed", old_day=day_text(old_day))] if old_day else []\n',
     '\ndef changes_text(old_day, seats, added, removed, renamed=0, kept=0, cards=0, cabins=0, fields=(), moved=False):\n    """«data cambiata (prima il 08/11/2026), 1 nome cambiato, la carta d\'imbarco resta, 1 posto cambiato,\n    2 passeggeri aggiunti»: `old_day` (ISO) when the date changed, then the counts; only what changed. `kept`: the\n    renamed passengers with a boarding pass; `cards`: the boarding passes deleted with the removed passengers.\n    Stage B, after the seats (plan 5.2.4): `moved` (the typed times re-based to the new day), `cabins` (the\n    classes changed) and `fields` (Fields.parts: departure, arrival, terminal, gate). Never a name: the reply is\n    logged."""\n    parts = [text("date_changed", old_day=day_text(old_day))] if old_day else []\n'),
    ('B:M7', 'manual.py', 'changes_text: times_moved, the classes and the field parts after the seats',
     '        parts.append(text("one_pass_kept") if kept == 1 else text("passes_kept"))\n    for k, one, many in ((seats, "one_seat_changed", "seats_changed"),\n                         (added, "one_passenger_added", "passengers_added"),\n                         (removed, "one_passenger_removed", "passengers_removed")):\n        if k:\n            parts.append(text(one) if k == 1 else text(many, k=k))\n',
     '        parts.append(text("one_pass_kept") if kept == 1 else text("passes_kept"))\n    for k, one, many in ((seats, "one_seat_changed", "seats_changed"), (int(moved), "times_moved", "times_moved"),\n                         (cabins, "one_cabin_changed", "cabins_changed"), (fields, None, None),\n                         (added, "one_passenger_added", "passengers_added"),\n                         (removed, "one_passenger_removed", "passengers_removed")):\n        if one is None:\n            parts += [text(f) for f in k]\n        elif k:\n            parts.append(text(one) if k == 1 else text(many, k=k))\n'),
    ('B:M8', 'manual.py', 'the stage-B block: parsers, classes, keep_names, plan_cabins, choice_fields, added_details, local_instant, arrival_instant, rebase, plan_fields',
     '    return ", ".join(parts)\n',
     '    return ", ".join(parts)\n\n\n# ---------------------------------------------------------------- stage B (decision 3): times, terminal, gate, classes\n# Every field is optional. The times are the local times of each airport (its zone from airport_zones.json:\n# flights.zone_of), stored as instants with that airport\'s offset in pass_departure and pass_arrival; the terminal\n# and the gate in pass_terminal and pass_gate; a class as the BCBP compartment letter in boarding_passes.cabin.\n# AviationStack\'s data wins at read time (flights.departure, flights.arrival, the gate and the terminal of the cards).\nOWN_KEYS = ("departure_time", "arrival_time", "terminal", "gate")           # the request keys of stage B\nOWN = ("pass_departure", "pass_arrival", "pass_terminal", "pass_gate")      # their columns of flights\nVERSIONED = ("pass_departure", "pass_arrival", "pass_boarding", "pass_gate", "pass_terminal")   # in version()\nCLEAR = "-"                               # «-» in an edit clears a field (the dashes iOS may type are read as it)\n_CLEARS = frozenset("-–—")\n# the class words (case-insensitive) and their BCBP letters: no «eco» (a surname) and no single letters\nCABINS = {"economy": "Y", "economica": "Y", "premium": "W", "business": "C", "prima": "F", "first": "F"}\n# a stored letter shown back as a class word (flights.PRIORITY_CABINS are «prima» and «business»; others «economy»)\nCLASS_OF = {**dict.fromkeys("FAP", "prima"), **dict.fromkeys("JCDIZR", "business"), "W": "premium"}\n_TIME = re.compile(r"([01]?\\d|2[0-3])[:.]([0-5]\\d)", re.ASCII)\n_CODE = re.compile(r"[A-Z0-9 -]{1,8}", re.ASCII)\n_CODE_WORD = {"terminal": "TERMINAL", "gate": "GATE"}\n\n\ndef _parse_own(out, given, clear):\n    """The four keys of stage B into `out`: absent stays None; «-» is CLEAR in an edit (`clear`), not given in an\n    add; anything else is parsed (parse_time, parse_code)."""\n    for key, parse in (("departure_time", parse_time), ("arrival_time", parse_time), ("terminal", parse_code),\n                       ("gate", parse_code)):\n        if key in given:\n            x = given[key].strip()\n            out[key] = (CLEAR if clear else None) if x in _CLEARS else parse(x, key)\n\n\ndef parse_time(text, key="departure_time"):\n    """«06:30», «6:30», «06.30» -> (6, 30): a local time from 00:00 to 23:59 (24:00 is refused). `key`:\n    departure_time or arrival_time (the error)."""\n    m = _TIME.fullmatch(text.strip()) if isinstance(text, str) else None\n    if not m:\n        raise InputError("bad_departure_time" if key == "departure_time" else "bad_arrival_time")\n    return int(m.group(1)), int(m.group(2))\n\n\ndef parse_code(text, key="terminal"):\n    """«t1», «Terminal 1», «gate b12» -> "T1", "1", "B12": at most 8 letters, digits, spaces and hyphens after\n    upper-casing, with a leading «terminal» (`key` terminal) or «gate» (`key` gate) word dropped."""\n    s = re.sub(r"\\s+", " ", text.strip().upper()) if isinstance(text, str) else ""\n    s = re.sub(rf"^{_CODE_WORD[key]}(?=[ 0-9]|$) ?", "", s).strip()\n    if not _CODE.fullmatch(s) or not re.search("[A-Z0-9]", s):\n        raise InputError("bad_terminal" if key == "terminal" else "bad_gate")\n    return s\n\n\ndef _cabin(item):\n    """(the item without its class word, the BCBP letter or None). The word is the last of the item or the last\n    before the seat («Alice 12A business», «Bob economy 14C»), and only with a name left before it: «Business 12A»\n    is still a passenger named Business."""\n    words, at = item.split(), None\n    if len(words) >= 2 and words[-1].casefold() in CABINS:\n        at = len(words) - 1\n    elif len(words) >= 3 and words[-2].casefold() in CABINS and flights._SEAT.fullmatch(words[-1].upper()):\n        at = len(words) - 2\n    if at is None:\n        return item, None\n    return " ".join(words[:at] + words[at + 1:]), CABINS[words[at].casefold()]\n\n\ndef parse_passengers_cabins(value):\n    """parse_passengers, and the class typed for each passenger: ([(name, seat or None)], [letter or None]), the two\n    lists aligned. A passenger without a class word has None: its class stays as it is."""\n    return _passengers_cabins(value)[:2]\n\n\ndef _whole(item):\n    """The item read with its class word kept in the name, as stage A read it («Maria Prima 12A» -> ("Maria Prima",\n    "12A")); None when that reading is no passenger («Alice 12A business»)."""\n    try:\n        return _passenger(item)\n    except InputError:\n        return None\n\n\ndef _passengers_cabins(value):\n    """parse_passengers_cabins, and a third aligned list: for a passenger whose class word was taken, the item read\n    whole (_whole), which keep_names uses when it is the name of a passenger of the flight; None for the others.\n    ([], [], []) for None."""\n    if value is None:\n        return [], [], []\n    if isinstance(value, str):\n        items = _SEPARATORS.split(value)\n    elif isinstance(value, list) and all(isinstance(i, str) for i in value):\n        items = value\n    else:\n        raise InputError("bad_type", key="passengers")\n    pairs, cabins, whole = [], [], []\n    for item in items:\n        rest, letter = _cabin(item)\n        p = _passenger(rest)\n        if p:\n            pairs.append(p)\n            cabins.append(letter)\n            whole.append(_whole(item) if letter else None)\n    return pairs, cabins, whole\n\n\ndef keep_names(rows, req):\n    """The request `req` (parse_request\'s reply) on the flight\'s boarding_passes `rows`: a passenger whose last word,\n    or the word before the seat, is a class word, and who read whole (req["whole"]) is the first name or the full\n    name of one of the rows, is that passenger, with no class. So the passengers line of GET /choices\n    (passengers_text) reads back unchanged: «Maria Prima», a name stored before stage B, stays Maria Prima and\n    gets no class «prima»; on a flight without her, «Maria prima» is Maria in prima. Returns `req`\n    itself when nothing changes, else a new dict with new "passengers" and "cabins" lists."""\n    whole = req.get("whole")\n    if not whole or not any(whole) or not req.get("passengers"):\n        return req\n    names = {person_key(r["first_name"]) for r in rows} | {k for r in rows if (k := _full_key(r))}\n    names.discard(PLACEHOLDER)\n    passengers, cabins = list(req["passengers"]), list(req["cabins"])\n    for i, w in enumerate(whole):\n        if w is not None and person_key(w[0]) in names:\n            passengers[i], cabins[i] = w, None\n    if passengers == req["passengers"]:\n        return req\n    return {**req, "passengers": passengers, "cabins": cabins}\n\n\ndef cabin_class(letter):\n    """"C" -> "business": the class of a stored BCBP letter (CLASS_OF, «economy» for any other letter); None\n    without one."""\n    letter = letter.strip().upper()[:1] if isinstance(letter, str) else ""\n    return (CLASS_OF.get(letter, "economy") if letter.isalpha() else None) if letter else None\n\n\ndef match_rows(rows, wanted):\n    """{item index: row} of the typed list `wanted` [(name, seat)] on the flight\'s boarding_passes `rows`, as\n    plan_passengers matches them (a full name first, then the first row not matched yet with the item\'s key), and\n    without its refusals: an item that takes no row is left out."""\n    rows = sorted(rows, key=lambda r: r["id"])\n    keys = [person_key(name) for name, _ in wanted]\n    first = {r["id"]: person_key(r["first_name"]) for r in rows}\n    full = {r["id"]: _full_key(r) for r in rows}\n    first_names, matched, found = set(first.values()), set(), {}\n    for i, key in enumerate(keys):\n        if key not in first_names:\n            row = next((r for r in rows if r["id"] not in matched and full[r["id"]] == key), None)\n            if row is not None:\n                matched.add(row["id"])\n                found[i] = row\n    for i, key in enumerate(keys):\n        if i not in found:\n            row = next((r for r in rows if r["id"] not in matched and first[r["id"]] == key), None)\n            if row is not None:\n                matched.add(row["id"])\n                found[i] = row\n    return found\n\n\ndef plan_cabins(rows, wanted, cabins):\n    """[(row id, letter)]: the class typed for each passenger of `wanted` (aligned `cabins`) on its row of `rows`\n    (match_rows), when it differs from the row\'s by class (a pass\'s «J» and a typed «business» are the same class).\n    A passenger without a class word: unchanged, never cleared. Used on the rows after plan_passengers is applied."""\n    found, out = match_rows(rows, wanted or []), []\n    for i, letter in enumerate(cabins or []):\n        row = found.get(i)\n        if letter and row is not None and cabin_class(row["cabin"]) != cabin_class(letter):\n            out.append((row["id"], letter))\n    return out\n\n\ndef classes_text(passes):\n    """«Alice business, Bob economy»: the class of each passenger in the order of passengers_text (only the name\n    for one without a class); "" when no passenger has a class."""\n    rows = sorted(passes, key=lambda p: p["id"])\n    if not any(cabin_class(p["cabin"]) for p in rows):\n        return ""\n    return ", ".join(name + (f" {cabin_class(p[\'cabin\'])}" if cabin_class(p["cabin"]) else "")\n                     for name, p in zip(_names(rows), rows))\n\n\ndef _hhmm(iso, zone=None):\n    """«06:30» of an instant in the airport\'s zone (else in its own offset); "" without one."""\n    try:\n        return flights.local_time(iso, zone) or ""\n    except (TypeError, ValueError):\n        return ""\n\n\ndef choice_fields(v, passes):\n    """The details of GET /choices added by stage B: the classes, the flight\'s own local times (each in its\n    airport\'s zone) and its own terminal and gate, never AviationStack\'s ("" when none): the values «Modifica volo»\n    offers to edit."""\n    return {"classes": classes_text(passes),\n            "departure_time": _hhmm(v.get("pass_departure"), flights.zone_of(v["origin"])),\n            "arrival_time": _hhmm(v.get("pass_arrival"), flights.zone_of(v["destination"])),\n            "terminal": v.get("pass_terminal") or "", "gate": v.get("pass_gate") or ""}\n\n\ndef added_details(values):\n    """«, partenza alle 06:30, arrivo alle 08:45» for the reply of an add (`values`: Fields.values); "" without a\n    departure, so the replies without times stay as they were."""\n    if not values.get("pass_departure"):\n        return ""\n    arrival = text("added_arrival", arrival=_hhmm(values["pass_arrival"])) if values.get("pass_arrival") else ""\n    return text("added_details", departure=_hhmm(values["pass_departure"]), arrival=arrival)\n\n\n# ---------------------------------------------------------------- local times and instants\ndef _wall(day, t, zone):\n    """`t` (a naive time) on `day` in the IANA `zone`, normalised through UTC: a time in the hour a DST change skips\n    moves forward by it, an ambiguous one is the first of the two (fold 0)."""\n    z = zoneinfo.ZoneInfo(zone)\n    local = datetime.datetime.combine(day, t.replace(tzinfo=None, fold=0), tzinfo=z)\n    return local.astimezone(datetime.timezone.utc).astimezone(z)\n\n\ndef local_instant(day_iso, hm, zone):\n    """"2026-11-08", (6, 30), "Europe/Rome" -> "2026-11-08T06:30:00+01:00": the instant, with the zone\'s offset."""\n    return _wall(datetime.date.fromisoformat(day_iso), datetime.time(*hm), zone).isoformat()\n\n\ndef arrival_instant(day_iso, departure_iso, hm, zone):\n    """The arrival at the local time `hm` of `zone`: the first of the days `day_iso` - 1 .. + 2 whose instant is\n    within flight_stats.DURATION_RANGE after the departure (overnight flights and the date line both ways, without\n    a «+1»). None if no day fits."""\n    day, dep = datetime.date.fromisoformat(day_iso), datetime.datetime.fromisoformat(departure_iso)\n    for k in (-1, 0, 1, 2):\n        t = _wall(day + datetime.timedelta(days=k), datetime.time(*hm), zone)\n        if flight_stats.DURATION_RANGE[0] <= (t - dep).total_seconds() / 60 <= flight_stats.DURATION_RANGE[1]:\n            return t.isoformat()\n    return None\n\n\ndef local_hm(iso, zone=None):\n    """(h, m) of an instant in `zone`, else in its own offset."""\n    t = datetime.datetime.fromisoformat(iso)\n    t = t.astimezone(zoneinfo.ZoneInfo(zone)) if zone else t\n    return t.hour, t.minute\n\n\ndef rebase(iso, old_day_iso, new_day_iso, zone):\n    """The same local time in `zone`, moved by as many days as the flight\'s date: a date change keeps the typed\n    times (plan 5.2.4). None without the instant or the zone."""\n    if not iso or not zone:\n        return None\n    t = datetime.datetime.fromisoformat(iso).astimezone(zoneinfo.ZoneInfo(zone))\n    shift = datetime.date.fromisoformat(new_day_iso) - datetime.date.fromisoformat(old_day_iso)\n    return _wall(t.date() + shift, t.time(), zone).isoformat()\n\n\ndef _same(a, b):\n    """The same value: two instants equal as instants (one written in another offset is the same), else equal."""\n    if a is None or b is None or a == b:\n        return a == b\n    try:\n        return datetime.datetime.fromisoformat(a) == datetime.datetime.fromisoformat(b)\n    except (TypeError, ValueError):\n        return False\n\n\ndef _zone_for(code):\n    zone = flights.zone_of(code)\n    if zone is None:\n        raise InputError("no_time_zone", city=flights.city(code), code=code)\n    return zone\n\n\nclass Fields:\n    """What plan_fields decided for the flight\'s own times, terminal and gate: `values` {column of OWN: its value\n    after the request}, `parts` the change parts (TEXTS keys, in the order of changes_text), `moved` True when typed\n    times were re-based to a new day."""\n    __slots__ = ("values", "parts", "moved")\n\n    def __init__(self, values, parts=(), moved=False):\n        self.values, self.parts, self.moved = dict(values), list(parts), moved\n\n    def __repr__(self):\n        return f"Fields(values={self.values}, parts={self.parts}, moved={self.moved})"\n\n\ndef plan_fields(req, v, day, merge=False):\n    """The request\'s departure_time, arrival_time, terminal and gate on the flight `v` (its row; None for a new\n    flight: origin and destination from `req`), whose day becomes `day` (ISO). Per field: absent = unchanged,\n    CLEAR = NULL, a value = set; `merge` (an add on a flight already there): a value only fills an empty column.\n      - departure: local_instant on `day` in the origin\'s zone (no_time_zone without one);\n      - arrival: arrival_instant after the departure (arrival_needs_departure without one, bad_duration if no day\n        fits); an arrival kept while the departure changes keeps its local time and gets its day chosen again;\n      - a new date (an edit): the typed times re-based to the same local times on the new day (cleared when the\n        airport has no zone), terminal and gate cleared with the rest of the old day (engine._edit), unless sent.\n    An instant that did not change keeps its stored text. Returns Fields; InputError otherwise."""\n    origin, destination = (v or req)["origin"], (v or req)["destination"]\n    stored = {k: (v or {}).get(k) for k in OWN}\n    moved_day = v is not None and not merge and day != v["date"]\n    if moved_day:\n        base = {"pass_departure": rebase(stored["pass_departure"], v["date"], day, flights.zone_of(origin)),\n                "pass_arrival": rebase(stored["pass_arrival"], v["date"], day, flights.zone_of(destination)),\n                "pass_terminal": None, "pass_gate": None}\n    else:\n        base = dict(stored)\n    given = {k: req.get(k) for k in OWN_KEYS}\n    if merge:                                     # only what is empty gets filled; CLEAR is no value in an add\n        given = {k: x if x != CLEAR and base[col] is None else None for (k, x), col in zip(given.items(), OWN)}\n    final = dict(base)\n    dep, arr = given["departure_time"], given["arrival_time"]\n    if dep == CLEAR:\n        final["pass_departure"] = None\n    elif dep is not None:\n        final["pass_departure"] = local_instant(day, dep, _zone_for(origin))\n    if arr == CLEAR:\n        final["pass_arrival"] = None\n    elif arr is not None:\n        if final["pass_departure"] is None:\n            raise InputError("arrival_needs_departure")\n        final["pass_arrival"] = arrival_instant(day, final["pass_departure"], arr, _zone_for(destination))\n        if final["pass_arrival"] is None:\n            raise InputError("bad_duration")\n    elif dep is not None and final["pass_arrival"] is not None \\\n            and not _same(final["pass_departure"], base["pass_departure"]):\n        if final["pass_departure"] is None:       # the departure cleared under an arrival that stays\n            raise InputError("arrival_needs_departure")\n        zone = flights.zone_of(destination)\n        if zone is not None:\n            final["pass_arrival"] = arrival_instant(day, final["pass_departure"],\n                                                    local_hm(final["pass_arrival"], zone), zone)\n            if final["pass_arrival"] is None:\n                raise InputError("bad_duration")\n    for key, col in (("terminal", "pass_terminal"), ("gate", "pass_gate")):\n        if given[key] == CLEAR:\n            final[col] = None\n        elif given[key] is not None:\n            final[col] = given[key]\n    parts = [part for key, col, part in (("departure_time", "pass_departure", "departure_changed"),\n                                         ("arrival_time", "pass_arrival", "arrival_changed"),\n                                         ("terminal", "pass_terminal", "terminal_changed"),\n                                         ("gate", "pass_gate", "gate_changed"))\n             if given[key] is not None and not _same(final[col], base[col])]\n    moved = moved_day and any(base[col] is not None and given[key] is None\n                              for key, col in (("departure_time", "pass_departure"), ("arrival_time", "pass_arrival")))\n    for col in ("pass_departure", "pass_arrival"):\n        if _same(final[col], stored[col]):\n            final[col] = stored[col]\n    return Fields(final, parts, moved)\n'),
]
HUNKS += STAGE_B
FILES = ("flights.py", "flight_stats.py", "manual.py")


def sha(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def expected(oracle_text, file):
    text, applied = oracle_text, []
    for hid, f, what, old, new in HUNKS:
        if f != file:
            continue
        n = text.count(old)
        if n != 1:
            raise SystemExit(f"{file}: hunk {hid} ({what}) found {n} times in the oracle")
        applied.append((hid, what, oracle_text.count("\n", 0, oracle_text.index(old)) + 1))
        text = text.replace(old, new)
    return text, applied


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--oracle", required=True, help="the oracle's tracker folder")
    ap.add_argument("--core", default=CORE)
    ap.add_argument("--json")
    a = ap.parse_args()
    ok, report = True, {"identical": {}, "pure": {}, "patched": {}}
    for f in IDENTICAL:
        same = os.path.exists(os.path.join(a.core, f)) and sha(os.path.join(a.oracle, f)) == sha(os.path.join(a.core, f))
        report["identical"][f] = same
        print(f"{f}: {'byte for byte' if same else 'DIFFERS'}")
        ok &= same
    for f in FILES:
        with open(os.path.join(a.oracle, f), encoding="utf-8") as fh:
            oracle = fh.read()
        try:
            with open(os.path.join(a.core, f), encoding="utf-8") as fh:
                copy = fh.read()
        except OSError:
            print(f"{f}: MISSING")
            ok = False
            continue
        want, applied = expected(oracle, f)
        same = want == copy
        report["pure"][f] = {"equal": same, "hunks": [{"id": h, "what": w, "oracle_line": n} for h, w, n in applied]}
        print(f"{f}: {'only the declared hunks' if same else 'UNDECLARED HUNKS'}")
        for h, w, n in applied:
            print(f"    hunk {h} oracle line {n:>4}: {w}")
        if not same:
            ok = False
            sys.stdout.writelines(difflib.unified_diff(want.splitlines(keepends=True), copy.splitlines(keepends=True),
                                                       f"declared/{f}", f"core/{f}", n=2))
    for f in PATCHED:
        with open(os.path.join(a.oracle, f), encoding="utf-8") as fh:
            old = fh.read().splitlines()
        with open(os.path.join(a.core, f), encoding="utf-8") as fh:
            new = fh.read().splitlines()
        sm = difflib.SequenceMatcher(None, old, new, autojunk=False)
        kept = sum(b.size for b in sm.get_matching_blocks())
        report["patched"][f] = {"oracle_lines": len(old), "core_lines": len(new), "lines_kept": kept}
        print(f"{f}: patched (E/H list, not checked here): {kept} of its {len(old)} oracle lines kept, {len(new)} now")
    report["ok"] = ok
    print("PASS: only the declared hunks in the pure modules" if ok else "FAIL")
    if a.json:
        with open(a.json, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=1)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
