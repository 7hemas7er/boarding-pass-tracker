#!/usr/bin/env python3
"""manual: flights added, edited and deleted by hand, with the iOS Shortcuts «Aggiungi volo» and «Modifica volo»
(POST /flight and GET /choices in engine.py). Pure rules, no database and no network: parsing and validation of
the request, the passengers plan of an edit, the `ref` of a flight, the pick-list labels and the Italian texts
(TEXTS is the only place they are written; the upload's tracked/untracked sentences come from here too).

A passenger typed by hand is stored like one of a boarding pass, without a document: it is recognised by
`document_id IS NULL AND passenger = first_name` (is_typed), never by a stored marker, so the schema does not
change. A flight is typed when it has boarding passes and all of them are typed (is_typed_flight).

The `ref` of a flight («12-3f9a0c1b2d4e») is its id and a version: a hash of what people edit and of what an
upload changes, not of the status the clock writes. An edit or a delete with a stale `ref` is refused, so a list
read before another change, or an id reused after a delete, never changes the wrong flight.

This module never imports engine. Tests: test_manual.py.
"""
import collections, datetime, hashlib, json, re, unicodedata, zoneinfo

if __package__:
    from . import flight_stats, flights
else:
    import flight_stats, flights

MAX_JSON_BODY = 16 * 1024
BODY_TIMEOUT_S = 30                       # seconds to receive the whole JSON body
MAX_PASSENGERS = 9                        # on a flight, by hand (a flight with more uploaded passes keeps them)
MAX_CHOICES = 200                         # entries of GET /choices
MIN_DATE = datetime.date(1970, 1, 1)
MAX_AHEAD_DAYS = 366
TRACK_DAYS_BACK = 1                       # yesterday is still followed: AviationStack knows it (tracked)
KEYS = ("action", "flight", "date", "origin", "destination", "passengers", "ref", "departure_time", "arrival_time",
        "terminal", "gate")                 # the last four: stage B (decision 3), each optional
ACTIONS = ("add", "edit", "delete")
PLACEHOLDER = "passeggero"                # the stored name of an unnamed pass (flights.read_boarding_pass)

_FLIGHT = re.compile(r"([A-Z0-9]{2})0*(\d{1,4}[A-Z]?)", re.ASCII)      # leading zeros dropped as flights._NUMBER
_ISO_DAY = re.compile(r"(\d{4})-(\d{2})-(\d{2})", re.ASCII)
_DAY = re.compile(r"(\d{1,2})([/.-])(\d{1,2})\2(\d{4})", re.ASCII)      # D/M/YYYY, D.M.YYYY, D-M-YYYY
_AIRPORT = re.compile(r"[A-Z]{3}")
_REF = re.compile(r"([1-9]\d{0,8})-([0-9a-f]{12})", re.ASCII)
_SEPARATORS = re.compile(r"[,;\r\n\v\f\x1c-\x1e\x85\u2028\u2029]")   # , ; and every line break of str.splitlines

# Italian texts for people (the «message» of the replies, a sensor state, the upload's suffixes): exact, with the
# real accented letters. The 403 texts and the 404 «percorso sconosciuto» are the engine's existing ones.
TEXTS = {
    # 400, 404 and 409 replies of POST /flight
    "body_size": "Richiesta vuota o oltre 16 KB.",
    "not_json": "Richiesta non valida: serve un oggetto JSON con i campi del volo.",
    "unknown_field": "Campo sconosciuto: {key}.",
    "bad_type": "Il campo {key} ha un tipo sbagliato.",
    "bad_action": "Azione sconosciuta: dev'essere add, edit o delete.",
    "bad_flight": "Volo non valido: scrivi la compagnia (2 caratteri) e il numero, per esempio FR 1234.",
    "bad_date": "Data non valida: scrivi il giorno della partenza, per esempio 08/11/2026.",
    "date_out_of_range": "Data fuori intervallo: dal 1970 a un anno da oggi.",
    "bad_origin": "Aeroporto di partenza sconosciuto: scrivi il codice IATA di tre lettere, per esempio BLQ.",
    "bad_destination": "Aeroporto di arrivo sconosciuto: scrivi il codice IATA di tre lettere, per esempio STN.",
    "same_airport": "Partenza e arrivo sono lo stesso aeroporto.",
    "no_passengers": "Mancano i passeggeri: scrivi i nomi separati da virgole, con il posto e la classe dopo il "
                     "nome se li sai, per esempio Alice 12A business, Bob.",
    "bad_passenger": "Non capisco un passeggero: scrivi il nome (solo lettere, spazi, apostrofi e trattini), poi il "
                     "posto e la classe se li sai, per esempio Alice 12A business.",
    "too_many": "Al massimo 9 passeggeri per volo.",
    "same_name": "Due passeggeri con lo stesso nome: aggiungi il cognome a uno dei due, per esempio Marco Rossi.",
    "no_ref": "Manca il volo da modificare: sceglilo dall'elenco di «Modifica volo».",
    "nothing": "Niente da cambiare: manda la data, i passeggeri, gli orari, il terminal o il gate.",
    "not_found": "Questo volo non c'è più: forse è stato eliminato. Riapri «Modifica volo».",
    "changed": "Il volo è cambiato nel frattempo: riapri «Modifica volo» e riprova.",
    "last_passenger": "Serve almeno un passeggero: per togliere il volo scegli «Elimina il volo».",
    "date_taken": "C'è già {code} del {day} da {origin}: modifica quello, oppure elimina uno dei due.",
    "other_destination": "{code} del {day} da {origin} c'è già, ma va a {destination}: controlla l'arrivo.",
    # 500
    "internal_error": "Errore interno: riprova tra poco.",
    # 200 replies of POST /flight
    "added": "Aggiunto {code} del {day}, {route}{details}, {n}.{suffix}",
    "merged": "{code} del {day} c'era già: {changes}. Ora ha {n}.{suffix}",
    "edited": "Modificato {code} del {day}: {changes}. Ora ha {n}.{suffix}",
    "unchanged": "Nessuna modifica: {code} del {day} è già così.",
    "deleted": "Eliminato {code} del {day}, {route}.",
    # GET /choices
    "choose": "Scegli il volo.",
    "no_flights": "Nessun volo: aggiungine uno con «Aggiungi volo» o «Manda volo».",
    # {suffix}: tracked and untracked end the upload's notification too (engine.tracking_suffix)
    "tracked": " Ti avviso se cambiano orario o gate.",
    "untracked": " Lo stato del volo non lo seguo: manca la chiave di AviationStack.",
    "past": " Conta solo per le statistiche.",
    # {n} (count_text) and the parts of {changes} (changes_text)
    "one_passenger": "1 passeggero",
    "many_passengers": "{k} passeggeri",
    "date_changed": "data cambiata (prima il {old_day})",
    "one_seat_changed": "1 posto cambiato",
    "seats_changed": "{k} posti cambiati",
    "one_passenger_added": "1 passeggero aggiunto",
    "passengers_added": "{k} passeggeri aggiunti",
    "one_passenger_removed": "1 passeggero tolto",
    "passengers_removed": "{k} passeggeri tolti",
    "one_pass_removed": "con la sua carta d'imbarco",
    "passes_removed": "con {k} carte d'imbarco",
    "one_renamed": "1 nome cambiato",
    "renamed": "{k} nomi cambiati",
    "one_pass_kept": "la carta d'imbarco resta",
    "passes_kept": "le carte d'imbarco restano",
    # the state of a typed flight without an AviationStack status (sensor and GET /flights)
    "status_typed": "Inserito a mano",
    # stage B (decision 3): the times, terminal, gate and classes typed by hand. 400 replies of POST /flight
    "bad_departure_time": "Orario di partenza non valido: scrivi l'ora locale dell'aeroporto di partenza, per esempio "
                          "06:30.",
    "bad_arrival_time": "Orario di arrivo non valido: scrivi l'ora locale dell'aeroporto di arrivo, per esempio 08:45.",
    "arrival_needs_departure": "Per l'orario di arrivo serve anche quello di partenza.",
    "bad_duration": "Orari che non tornano: tra partenza e arrivo ci sarebbero meno di 10 minuti o più di 24 ore.",
    "no_time_zone": "Non conosco il fuso orario di {city} ({code}): lascia vuoto l'orario.",
    "bad_terminal": "Terminal non valido: al massimo 8 tra lettere e cifre, per esempio T1.",
    "bad_gate": "Gate non valido: al massimo 8 tra lettere e cifre, per esempio B12.",
    # the parts of {changes} (changes_text) and of {details} (added_details)
    "departure_changed": "orario di partenza cambiato",
    "arrival_changed": "orario di arrivo cambiato",
    "terminal_changed": "terminal cambiato",
    "gate_changed": "gate cambiato",
    "one_cabin_changed": "1 classe cambiata",
    "cabins_changed": "{k} classi cambiate",
    "times_moved": "orari spostati al nuovo giorno",
    "added_details": ", partenza alle {departure}{arrival}",
    "added_arrival": ", arrivo alle {arrival}",
}


def text(key, /, **fmt):
    """TEXTS[key] with its fillers."""
    return TEXTS[key].format(**fmt)


class InputError(ValueError):
    """A request refused: str(e) is the Italian message (TEXTS[key] with its fillers), e.code the HTTP status (400,
    404 or 409), e.key the TEXTS key. `key` and `code` are positional only: the fillers are keywords, and {code} is
    a flight code («FR 1234»)."""

    def __init__(self, key, code=400, /, **fmt):
        super().__init__(text(key, **fmt))
        self.key, self.code = key, code


def _clean(s, n):
    """As engine.clean (this module never imports engine): no control or invisible characters, at most n."""
    s = "".join(" " if unicodedata.category(c)[0] == "C" else c for c in str(s or ""))
    return re.sub(r"\s+", " ", s).strip()[:n].rstrip()


# ---------------------------------------------------------------- request
def parse_request(body, today):
    """The JSON body of POST /flight (bytes, str or an already decoded dict), checked: keys, types, then the fields
    the action uses (the others are ignored: «Modifica volo» sends date and passengers with a delete too). JSON
    null and a blank string mean not given. `today`: the host's local date. Returns {"action", "airline",
    "number", "date", "origin", "destination", "passengers": [(name, seat)] | None, "ref": (id, version) | None,
    and of stage B "departure_time", "arrival_time": (h, m) | CLEAR | None, "terminal", "gate": the code | CLEAR |
    None, "cabins": [BCBP letter or None, one per passenger] | None, "whole": [(name, seat) or None, one per
    passenger] | None (keep_names)}; InputError otherwise. CLEAR («-») only in an edit: in an add it is the same as
    not given."""
    if isinstance(body, (bytes, bytearray)):
        try:
            body = bytes(body).decode("utf-8")
        except UnicodeDecodeError:
            raise InputError("not_json") from None
    if isinstance(body, str):
        try:
            body = json.loads(body)
        except (ValueError, RecursionError):          # RecursionError: '[' * 16000
            raise InputError("not_json") from None
    if not isinstance(body, dict):
        raise InputError("not_json")
    for k in body:
        if k not in KEYS:
            raise InputError("unknown_field", key=_clean(k, 30))
    given = {}
    for k, x in body.items():
        if x is None:
            continue
        if k == "passengers" and isinstance(x, list):
            if not all(isinstance(i, str) for i in x):
                raise InputError("bad_type", key=k)
            given[k] = x
        elif isinstance(x, str):
            if x.strip():
                given[k] = x
        else:                                         # a number, a bool, an object or a list
            raise InputError("bad_type", key=k)
    action = (given.get("action") or "").strip()
    if action not in ACTIONS:
        raise InputError("bad_action")
    out = {"action": action, **dict.fromkeys(("airline", "number", "date", "origin", "destination", "passengers",
                                              "ref", *OWN_KEYS, "cabins", "whole"))}
    if action == "add":
        out["airline"], out["number"] = parse_flight_code(given.get("flight"))
        out["date"] = parse_day(given.get("date"), today)
        out["origin"] = parse_airport(given.get("origin"), "origin")
        out["destination"] = parse_airport(given.get("destination"), "destination")
        if out["origin"] == out["destination"]:
            raise InputError("same_airport")
        out["passengers"], out["cabins"], out["whole"] = _passengers_cabins(given.get("passengers"))
        if not out["passengers"]:
            raise InputError("no_passengers")
        keys = [person_key(name) for name, _ in out["passengers"]]
        if len(set(keys)) < len(keys):
            raise InputError("same_name")
        _parse_own(out, given, clear=False)
    elif action == "edit":
        if "date" in given:
            out["date"] = parse_day(given["date"], today)
        if "passengers" in given:
            out["passengers"], out["cabins"], out["whole"] = _passengers_cabins(given["passengers"])
        _parse_own(out, given, clear=True)
        out["ref"] = parse_ref(given.get("ref"))
        if out["date"] is None and out["passengers"] is None and all(out[k] is None for k in OWN_KEYS):
            raise InputError("nothing")
    else:
        out["ref"] = parse_ref(given.get("ref"))
    return out


def parse_flight_code(text):
    """«FR 1234», «fr1234» -> ("FR", "1234"); «W6 0012» -> ("W6", "12"). The airline is the 2-character IATA code
    with a letter (an ICAO code such as EZY would never match AviationStack's flight_iata)."""
    m = _FLIGHT.fullmatch(re.sub(r"\s+", "", text).upper()) if isinstance(text, str) else None
    if not m or not re.search("[A-Z]", m.group(1)):
        raise InputError("bad_flight")
    return m.group(1), m.group(2)


def parse_day(text, today):
    """«2026-11-08», «8/11/2026», «8.11.2026» or «8-11-2026» -> "2026-11-08": a real calendar day from MIN_DATE to
    MAX_AHEAD_DAYS after `today`. No times: a date-time in UTC could shift the day."""
    s = text.strip() if isinstance(text, str) else ""
    if m := _ISO_DAY.fullmatch(s):
        y, mo, d = m.groups()
    elif m := _DAY.fullmatch(s):
        d, _, mo, y = m.groups()
    else:
        raise InputError("bad_date")
    try:
        day = datetime.date(int(y), int(mo), int(d))
    except ValueError:
        raise InputError("bad_date") from None
    if not MIN_DATE <= day <= today + datetime.timedelta(days=MAX_AHEAD_DAYS):
        raise InputError("date_out_of_range")
    return day.isoformat()


def parse_airport(text, which):
    """« blq » -> "BLQ", an airport of airports.json. `which`: "origin" or "destination" (the error)."""
    code = text.strip().upper() if isinstance(text, str) else ""
    if not _AIRPORT.fullmatch(code) or flights.coordinates(code) is None:
        raise InputError("bad_origin" if which == "origin" else "bad_destination")
    return code


def _passenger(item):
    """One passenger «Anna Maria 3C» -> ("Anna Maria", "3C"), None for a blank item. The seat is the last word when
    it has the shape of a seat; the name must be what flight_stats.clean_name keeps (it is refused, not changed),
    and it is title-cased only when written all in lower or all in upper case («McDonald» stays)."""
    item = unicodedata.normalize("NFC", item).replace("\u2019", "'").strip()
    if not item:
        return None
    words, seat = item.split(), None
    m = flights._SEAT.fullmatch(words[-1].upper()) if len(words) >= 2 else None
    if m:
        seat, words = m.group(1) or m.group(2), words[:-1]
    name = " ".join(words)
    if flight_stats.clean_name(name) != name:
        raise InputError("bad_passenger")
    if name.islower() or name.isupper():
        name = name.title()
        if flight_stats.clean_name(name) != name:
            raise InputError("bad_passenger")
    return name, seat


def parse_passengers(value):
    """«Alice 12A, Bob» (also «;» and every kind of line break, as in text pasted from another app), or a list with
    one passenger per item -> [(name, seat or None)]; [] when blank. How many a flight may have is decided by
    plan_passengers: the line of a flight with more uploaded passes than MAX_PASSENGERS is read too."""
    if value is None:
        return []
    if isinstance(value, str):
        items = _SEPARATORS.split(value)
    elif isinstance(value, list) and all(isinstance(i, str) for i in value):
        items = value
    else:
        raise InputError("bad_type", key="passengers")
    return parse_passengers_cabins(items)[0]


def parse_ref(text):
    """«12-3f9a0c1b2d4e» -> (12, "3f9a0c1b2d4e")."""
    m = _REF.fullmatch(text.strip()) if isinstance(text, str) else None
    if not m:
        raise InputError("no_ref")
    return int(m.group(1)), m.group(2)


# ---------------------------------------------------------------- passengers
def person_key(first_name):
    """The matching key of a boarding pass row (its first_name) or of a typed name: case-folded, PLACEHOLDER for a
    row without a usable first name."""
    name = flight_stats.clean_name(first_name)
    return name.casefold() if name else PLACEHOLDER


def is_typed(p):
    """A boarding_passes row typed by hand: no document, and the name in both columns (a pass row always has a
    document; rows of App Casa without one have the full name in passenger)."""
    return p["document_id"] is None and p["passenger"] == p["first_name"]


def is_typed_flight(passes):
    return bool(passes) and all(is_typed(p) for p in passes)


def _fold(word):
    """«José» -> "jose": NFKD, combining marks dropped, lower case."""
    return "".join(c for c in unicodedata.normalize("NFKD", word) if not unicodedata.combining(c)).lower()


def owner_of(name, user_names, caller):
    """Who gets the gate reminder of a typed passenger: the configured user whose name is the first word of the
    typed name («Anna Maria» -> anna), otherwise `caller`, the user who typed it."""
    first = _fold(name.split()[0]) if name and name.split() else ""
    return first if first in set(user_names) else caller


class Plan:
    """What plan_passengers decided: seats [(row id, seat)] to set, inserts [(name, seat)] of new typed rows,
    deletes [row id], renames [(row id, name)] of rows that keep their boarding pass under a new name. False when
    nothing changes."""
    __slots__ = ("seats", "inserts", "deletes", "renames")

    def __init__(self, seats=(), inserts=(), deletes=(), renames=()):
        self.seats, self.inserts, self.deletes = list(seats), list(inserts), list(deletes)
        self.renames = list(renames)

    def __bool__(self):
        return bool(self.seats or self.inserts or self.deletes or self.renames)

    def __eq__(self, other):
        return isinstance(other, Plan) and (self.seats, self.inserts, self.deletes, self.renames) == \
            (other.seats, other.inserts, other.deletes, other.renames)

    def __repr__(self):
        return (f"Plan(seats={self.seats}, inserts={self.inserts}, deletes={self.deletes}, "
                f"renames={self.renames})")


def _full_key(p):
    """The matching key of a boarding pass row's full name (passenger): «Carla Bianchi» is Carla's pass, and
    «Rossi Mario» names the unnamed pass of ROSSI MARIO (plan_passengers)."""
    name = flight_stats.clean_name(p["passenger"])
    return name.casefold() if name else None


def plan_passengers(rows, wanted, replace):
    """The flight's boarding_passes `rows` against the typed list `wanted` [(name, seat)]. An item that is no row's
    first name but the full name of a row takes that row: «Carla Bianchi», the pass of Carla, and «Rossi Mario»,
    the unnamed pass of ROSSI MARIO, which gets that name (a rename). Every other
    item takes the first row not matched yet with its key (person_key), rows in id order: two «Mario» rows take
    the two «Mario» items in order, and the line of passengers_text (first names) matches its own rows.
    replace=True (an edit: `wanted` is the whole new list): a matched row gets the typed seat, none if none was
    typed; rows left unmatched are deleted; an empty list is 409 last_passenger. replace=False (an add onto an
    existing flight): the seat only if one was typed, nobody removed. In an edit, exactly one row left unmatched
    and exactly one new name is a rename (the user's choice c): the row keeps its boarding pass, document and
    cabin under the new name, with the typed seat. An unmatched item becomes a new typed row,
    except PLACEHOLDER (bad_passenger) and a name another item has too (same_name). too_many: a flight would end
    with more than MAX_PASSENGERS, or more than it has (uploads have no limit: its line still changes seats)."""
    if replace and not wanted:
        raise InputError("last_passenger", 409)
    rows = sorted(rows, key=lambda r: r["id"])
    limit = max(MAX_PASSENGERS, len(rows))
    if replace and len(wanted) > limit:               # an edit leaves one row per item: checked before the work
        raise InputError("too_many")
    keys = [person_key(name) for name, _ in wanted]
    first = {r["id"]: person_key(r["first_name"]) for r in rows}
    full = {r["id"]: _full_key(r) for r in rows}
    first_names, repeated = set(first.values()), collections.Counter(keys)
    matched, found, plan = set(), {}, Plan()          # row ids; item index -> the row of its full name
    for i, key in enumerate(keys):
        if key not in first_names:
            row = next((r for r in rows if r["id"] not in matched and full[r["id"]] == key), None)
            if row is not None:
                matched.add(row["id"])
                found[i] = row
                if first[row["id"]] == PLACEHOLDER:          # an unnamed pass gets the name typed
                    plan.renames.append((row["id"], wanted[i][0]))
    for i, (name, seat) in enumerate(wanted):
        row = found.get(i) or next((r for r in rows if r["id"] not in matched and first[r["id"]] == keys[i]), None)
        if row is not None:
            matched.add(row["id"])
            new = seat if replace else seat or row["seat"]
            if new != row["seat"]:
                plan.seats.append((row["id"], new))
        elif keys[i] == PLACEHOLDER:
            raise InputError("bad_passenger")
        elif repeated[keys[i]] > 1:
            raise InputError("same_name")
        else:
            plan.inserts.append((name, seat))
    if replace:
        left = [r for r in rows if r["id"] not in matched]
        if len(left) == 1 and len(plan.inserts) == 1:
            (name, seat), row = plan.inserts.pop(), left.pop()
            plan.renames.append((row["id"], name))
            if seat != row["seat"]:
                plan.seats.append((row["id"], seat))
        plan.deletes = [r["id"] for r in left]
    if len(rows) - len(plan.deletes) + len(plan.inserts) > limit:
        raise InputError("too_many")
    return plan


# ---------------------------------------------------------------- ref
def version(v, passes):
    """12 hex digits of a hash of what people edit and what an upload changes (the flight's key, destination and
    created_at; every boarding pass; the flight's own times, gate and terminal, VERSIONED), not of the status,
    snapshot or checks the clock writes."""
    data = [v["id"], v["created_at"], v["airline"], v["number"], v["date"], v["origin"], v["destination"],
            [[p["id"], p["passenger"], p["first_name"], p["seat"], p["cabin"], p["document_id"], p["uploaded_by"]]
             for p in sorted(passes, key=lambda p: p["id"])], [v.get(k) for k in VERSIONED]]
    return hashlib.sha256(json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()[:12]


def ref(v, passes):
    return f"{v['id']}-{version(v, passes)}"


def tracked(day_iso, today):
    """Is a flight of that day followed (AviationStack, gate, Flightradar24), or does it only count for the
    statistics? From TRACK_DAYS_BACK days before `today` (the host's local date) on."""
    return datetime.date.fromisoformat(day_iso) >= today - datetime.timedelta(days=TRACK_DAYS_BACK)


# ---------------------------------------------------------------- labels and texts
def _names(passes):
    return [flight_stats.clean_name(p["first_name"]) or PLACEHOLDER for p in sorted(passes, key=lambda p: p["id"])]


def choice_label(v, passes):
    """«08/11/2026 · FR 1234 · Bologna → Londra Stansted · Alice, Bob»: an entry of GET /choices. Cities from the
    table only (else the code), so the label never depends on a status; no seats; never a dot (Shortcuts may read
    a dotted dictionary key as a key path)."""
    label = (f"{day_text(v['date'])} · {flights.flight_code(v)} · {flights.city(v['origin'])} → "
             f"{flights.city(v['destination'])}")
    return label + (f" · {', '.join(_names(passes))}" if passes else "")


def passengers_text(passes):
    """«Alice 12A, Bob»: the editable line of a flight, which parse_passengers and plan_passengers read back with no
    change."""
    return ", ".join(name + (f" {p['seat']}" if p["seat"] else "")
                     for name, p in zip(_names(passes), sorted(passes, key=lambda p: p["id"])))


def day_text(iso):
    """"2026-11-08" -> «08/11/2026»."""
    return f"{iso[8:10]}/{iso[5:7]}/{iso[0:4]}"


def count_text(n):
    return text("one_passenger") if n == 1 else text("many_passengers", k=n)


def changes_text(old_day, seats, added, removed, renamed=0, kept=0, cards=0, cabins=0, fields=(), moved=False):
    """«data cambiata (prima il 08/11/2026), 1 nome cambiato, la carta d'imbarco resta, 1 posto cambiato,
    2 passeggeri aggiunti»: `old_day` (ISO) when the date changed, then the counts; only what changed. `kept`: the
    renamed passengers with a boarding pass; `cards`: the boarding passes deleted with the removed passengers.
    Stage B, after the seats (plan 5.2.4): `moved` (the typed times re-based to the new day), `cabins` (the
    classes changed) and `fields` (Fields.parts: departure, arrival, terminal, gate). Never a name: the reply is
    logged."""
    parts = [text("date_changed", old_day=day_text(old_day))] if old_day else []
    if renamed:
        parts.append(text("one_renamed") if renamed == 1 else text("renamed", k=renamed))
    if kept:
        parts.append(text("one_pass_kept") if kept == 1 else text("passes_kept"))
    for k, one, many in ((seats, "one_seat_changed", "seats_changed"), (int(moved), "times_moved", "times_moved"),
                         (cabins, "one_cabin_changed", "cabins_changed"), (fields, None, None),
                         (added, "one_passenger_added", "passengers_added"),
                         (removed, "one_passenger_removed", "passengers_removed")):
        if one is None:
            parts += [text(f) for f in k]
        elif k:
            parts.append(text(one) if k == 1 else text(many, k=k))
    if cards:
        parts[-1] += " " + (text("one_pass_removed") if cards == 1 else text("passes_removed", k=cards))
    return ", ".join(parts)


# ---------------------------------------------------------------- stage B (decision 3): times, terminal, gate, classes
# Every field is optional. The times are the local times of each airport (its zone from airport_zones.json:
# flights.zone_of), stored as instants with that airport's offset in pass_departure and pass_arrival; the terminal
# and the gate in pass_terminal and pass_gate; a class as the BCBP compartment letter in boarding_passes.cabin.
# AviationStack's data wins at read time (flights.departure, flights.arrival, the gate and the terminal of the cards).
OWN_KEYS = ("departure_time", "arrival_time", "terminal", "gate")           # the request keys of stage B
OWN = ("pass_departure", "pass_arrival", "pass_terminal", "pass_gate")      # their columns of flights
VERSIONED = ("pass_departure", "pass_arrival", "pass_boarding", "pass_gate", "pass_terminal")   # in version()
CLEAR = "-"                               # «-» in an edit clears a field (the dashes iOS may type are read as it)
_CLEARS = frozenset("-–—")
# the class words (case-insensitive) and their BCBP letters: no «eco» (a surname) and no single letters
CABINS = {"economy": "Y", "economica": "Y", "premium": "W", "business": "C", "prima": "F", "first": "F"}
# a stored letter shown back as a class word (flights.PRIORITY_CABINS are «prima» and «business»; others «economy»)
CLASS_OF = {**dict.fromkeys("FAP", "prima"), **dict.fromkeys("JCDIZR", "business"), "W": "premium"}
_TIME = re.compile(r"([01]?\d|2[0-3])[:.]([0-5]\d)", re.ASCII)
_CODE = re.compile(r"[A-Z0-9 -]{1,8}", re.ASCII)
_CODE_WORD = {"terminal": "TERMINAL", "gate": "GATE"}


def _parse_own(out, given, clear):
    """The four keys of stage B into `out`: absent stays None; «-» is CLEAR in an edit (`clear`), not given in an
    add; anything else is parsed (parse_time, parse_code)."""
    for key, parse in (("departure_time", parse_time), ("arrival_time", parse_time), ("terminal", parse_code),
                       ("gate", parse_code)):
        if key in given:
            x = given[key].strip()
            out[key] = (CLEAR if clear else None) if x in _CLEARS else parse(x, key)


def parse_time(text, key="departure_time"):
    """«06:30», «6:30», «06.30» -> (6, 30): a local time from 00:00 to 23:59 (24:00 is refused). `key`:
    departure_time or arrival_time (the error)."""
    m = _TIME.fullmatch(text.strip()) if isinstance(text, str) else None
    if not m:
        raise InputError("bad_departure_time" if key == "departure_time" else "bad_arrival_time")
    return int(m.group(1)), int(m.group(2))


def parse_code(text, key="terminal"):
    """«t1», «Terminal 1», «gate b12» -> "T1", "1", "B12": at most 8 letters, digits, spaces and hyphens after
    upper-casing, with a leading «terminal» (`key` terminal) or «gate» (`key` gate) word dropped."""
    s = re.sub(r"\s+", " ", text.strip().upper()) if isinstance(text, str) else ""
    s = re.sub(rf"^{_CODE_WORD[key]}(?=[ 0-9]|$) ?", "", s).strip()
    if not _CODE.fullmatch(s) or not re.search("[A-Z0-9]", s):
        raise InputError("bad_terminal" if key == "terminal" else "bad_gate")
    return s


def _cabin(item):
    """(the item without its class word, the BCBP letter or None). The word is the last of the item or the last
    before the seat («Alice 12A business», «Bob economy 14C»), and only with a name left before it: «Business 12A»
    is still a passenger named Business."""
    words, at = item.split(), None
    if len(words) >= 2 and words[-1].casefold() in CABINS:
        at = len(words) - 1
    elif len(words) >= 3 and words[-2].casefold() in CABINS and flights._SEAT.fullmatch(words[-1].upper()):
        at = len(words) - 2
    if at is None:
        return item, None
    return " ".join(words[:at] + words[at + 1:]), CABINS[words[at].casefold()]


def parse_passengers_cabins(value):
    """parse_passengers, and the class typed for each passenger: ([(name, seat or None)], [letter or None]), the two
    lists aligned. A passenger without a class word has None: its class stays as it is."""
    return _passengers_cabins(value)[:2]


def _whole(item):
    """The item read with its class word kept in the name, as stage A read it («Maria Prima 12A» -> ("Maria Prima",
    "12A")); None when that reading is no passenger («Alice 12A business»)."""
    try:
        return _passenger(item)
    except InputError:
        return None


def _passengers_cabins(value):
    """parse_passengers_cabins, and a third aligned list: for a passenger whose class word was taken, the item read
    whole (_whole), which keep_names uses when it is the name of a passenger of the flight; None for the others.
    ([], [], []) for None."""
    if value is None:
        return [], [], []
    if isinstance(value, str):
        items = _SEPARATORS.split(value)
    elif isinstance(value, list) and all(isinstance(i, str) for i in value):
        items = value
    else:
        raise InputError("bad_type", key="passengers")
    pairs, cabins, whole = [], [], []
    for item in items:
        rest, letter = _cabin(item)
        p = _passenger(rest)
        if p:
            pairs.append(p)
            cabins.append(letter)
            whole.append(_whole(item) if letter else None)
    return pairs, cabins, whole


def keep_names(rows, req):
    """The request `req` (parse_request's reply) on the flight's boarding_passes `rows`: a passenger whose last word,
    or the word before the seat, is a class word, and who read whole (req["whole"]) is the first name or the full
    name of one of the rows, is that passenger, with no class. So the passengers line of GET /choices
    (passengers_text) reads back unchanged: «Maria Prima», a name stored before stage B, stays Maria Prima and
    gets no class «prima»; on a flight without her, «Maria prima» is Maria in prima. Returns `req`
    itself when nothing changes, else a new dict with new "passengers" and "cabins" lists."""
    whole = req.get("whole")
    if not whole or not any(whole) or not req.get("passengers"):
        return req
    names = {person_key(r["first_name"]) for r in rows} | {k for r in rows if (k := _full_key(r))}
    names.discard(PLACEHOLDER)
    passengers, cabins = list(req["passengers"]), list(req["cabins"])
    for i, w in enumerate(whole):
        if w is not None and person_key(w[0]) in names:
            passengers[i], cabins[i] = w, None
    if passengers == req["passengers"]:
        return req
    return {**req, "passengers": passengers, "cabins": cabins}


def cabin_class(letter):
    """"C" -> "business": the class of a stored BCBP letter (CLASS_OF, «economy» for any other letter); None
    without one."""
    letter = letter.strip().upper()[:1] if isinstance(letter, str) else ""
    return (CLASS_OF.get(letter, "economy") if letter.isalpha() else None) if letter else None


def match_rows(rows, wanted):
    """{item index: row} of the typed list `wanted` [(name, seat)] on the flight's boarding_passes `rows`, as
    plan_passengers matches them (a full name first, then the first row not matched yet with the item's key), and
    without its refusals: an item that takes no row is left out."""
    rows = sorted(rows, key=lambda r: r["id"])
    keys = [person_key(name) for name, _ in wanted]
    first = {r["id"]: person_key(r["first_name"]) for r in rows}
    full = {r["id"]: _full_key(r) for r in rows}
    first_names, matched, found = set(first.values()), set(), {}
    for i, key in enumerate(keys):
        if key not in first_names:
            row = next((r for r in rows if r["id"] not in matched and full[r["id"]] == key), None)
            if row is not None:
                matched.add(row["id"])
                found[i] = row
    for i, key in enumerate(keys):
        if i not in found:
            row = next((r for r in rows if r["id"] not in matched and first[r["id"]] == key), None)
            if row is not None:
                matched.add(row["id"])
                found[i] = row
    return found


def plan_cabins(rows, wanted, cabins):
    """[(row id, letter)]: the class typed for each passenger of `wanted` (aligned `cabins`) on its row of `rows`
    (match_rows), when it differs from the row's by class (a pass's «J» and a typed «business» are the same class).
    A passenger without a class word: unchanged, never cleared. Used on the rows after plan_passengers is applied."""
    found, out = match_rows(rows, wanted or []), []
    for i, letter in enumerate(cabins or []):
        row = found.get(i)
        if letter and row is not None and cabin_class(row["cabin"]) != cabin_class(letter):
            out.append((row["id"], letter))
    return out


def classes_text(passes):
    """«Alice business, Bob economy»: the class of each passenger in the order of passengers_text (only the name
    for one without a class); "" when no passenger has a class."""
    rows = sorted(passes, key=lambda p: p["id"])
    if not any(cabin_class(p["cabin"]) for p in rows):
        return ""
    return ", ".join(name + (f" {cabin_class(p['cabin'])}" if cabin_class(p["cabin"]) else "")
                     for name, p in zip(_names(rows), rows))


def _hhmm(iso, zone=None):
    """«06:30» of an instant in the airport's zone (else in its own offset); "" without one."""
    try:
        return flights.local_time(iso, zone) or ""
    except (TypeError, ValueError):
        return ""


def choice_fields(v, passes):
    """The details of GET /choices added by stage B: the classes, the flight's own local times (each in its
    airport's zone) and its own terminal and gate, never AviationStack's ("" when none): the values «Modifica volo»
    offers to edit."""
    return {"classes": classes_text(passes),
            "departure_time": _hhmm(v.get("pass_departure"), flights.zone_of(v["origin"])),
            "arrival_time": _hhmm(v.get("pass_arrival"), flights.zone_of(v["destination"])),
            "terminal": v.get("pass_terminal") or "", "gate": v.get("pass_gate") or ""}


def added_details(values):
    """«, partenza alle 06:30, arrivo alle 08:45» for the reply of an add (`values`: Fields.values); "" without a
    departure, so the replies without times stay as they were."""
    if not values.get("pass_departure"):
        return ""
    arrival = text("added_arrival", arrival=_hhmm(values["pass_arrival"])) if values.get("pass_arrival") else ""
    return text("added_details", departure=_hhmm(values["pass_departure"]), arrival=arrival)


# ---------------------------------------------------------------- local times and instants
def _wall(day, t, zone):
    """`t` (a naive time) on `day` in the IANA `zone`, normalised through UTC: a time in the hour a DST change skips
    moves forward by it, an ambiguous one is the first of the two (fold 0)."""
    z = zoneinfo.ZoneInfo(zone)
    local = datetime.datetime.combine(day, t.replace(tzinfo=None, fold=0), tzinfo=z)
    return local.astimezone(datetime.timezone.utc).astimezone(z)


def local_instant(day_iso, hm, zone):
    """"2026-11-08", (6, 30), "Europe/Rome" -> "2026-11-08T06:30:00+01:00": the instant, with the zone's offset."""
    return _wall(datetime.date.fromisoformat(day_iso), datetime.time(*hm), zone).isoformat()


def arrival_instant(day_iso, departure_iso, hm, zone):
    """The arrival at the local time `hm` of `zone`: the first of the days `day_iso` - 1 .. + 2 whose instant is
    within flight_stats.DURATION_RANGE after the departure (overnight flights and the date line both ways, without
    a «+1»). None if no day fits."""
    day, dep = datetime.date.fromisoformat(day_iso), datetime.datetime.fromisoformat(departure_iso)
    for k in (-1, 0, 1, 2):
        t = _wall(day + datetime.timedelta(days=k), datetime.time(*hm), zone)
        if flight_stats.DURATION_RANGE[0] <= (t - dep).total_seconds() / 60 <= flight_stats.DURATION_RANGE[1]:
            return t.isoformat()
    return None


def local_hm(iso, zone=None):
    """(h, m) of an instant in `zone`, else in its own offset."""
    t = datetime.datetime.fromisoformat(iso)
    t = t.astimezone(zoneinfo.ZoneInfo(zone)) if zone else t
    return t.hour, t.minute


def rebase(iso, old_day_iso, new_day_iso, zone):
    """The same local time in `zone`, moved by as many days as the flight's date: a date change keeps the typed
    times (plan 5.2.4). None without the instant or the zone."""
    if not iso or not zone:
        return None
    t = datetime.datetime.fromisoformat(iso).astimezone(zoneinfo.ZoneInfo(zone))
    shift = datetime.date.fromisoformat(new_day_iso) - datetime.date.fromisoformat(old_day_iso)
    return _wall(t.date() + shift, t.time(), zone).isoformat()


def _same(a, b):
    """The same value: two instants equal as instants (one written in another offset is the same), else equal."""
    if a is None or b is None or a == b:
        return a == b
    try:
        return datetime.datetime.fromisoformat(a) == datetime.datetime.fromisoformat(b)
    except (TypeError, ValueError):
        return False


def _zone_for(code):
    zone = flights.zone_of(code)
    if zone is None:
        raise InputError("no_time_zone", city=flights.city(code), code=code)
    return zone


class Fields:
    """What plan_fields decided for the flight's own times, terminal and gate: `values` {column of OWN: its value
    after the request}, `parts` the change parts (TEXTS keys, in the order of changes_text), `moved` True when typed
    times were re-based to a new day."""
    __slots__ = ("values", "parts", "moved")

    def __init__(self, values, parts=(), moved=False):
        self.values, self.parts, self.moved = dict(values), list(parts), moved

    def __repr__(self):
        return f"Fields(values={self.values}, parts={self.parts}, moved={self.moved})"


def plan_fields(req, v, day, merge=False):
    """The request's departure_time, arrival_time, terminal and gate on the flight `v` (its row; None for a new
    flight: origin and destination from `req`), whose day becomes `day` (ISO). Per field: absent = unchanged,
    CLEAR = NULL, a value = set; `merge` (an add on a flight already there): a value only fills an empty column.
      - departure: local_instant on `day` in the origin's zone (no_time_zone without one);
      - arrival: arrival_instant after the departure (arrival_needs_departure without one, bad_duration if no day
        fits); an arrival kept while the departure changes keeps its local time and gets its day chosen again;
      - a new date (an edit): the typed times re-based to the same local times on the new day (cleared when the
        airport has no zone), terminal and gate cleared with the rest of the old day (engine._edit), unless sent.
    An instant that did not change keeps its stored text. Returns Fields; InputError otherwise."""
    origin, destination = (v or req)["origin"], (v or req)["destination"]
    stored = {k: (v or {}).get(k) for k in OWN}
    moved_day = v is not None and not merge and day != v["date"]
    if moved_day:
        base = {"pass_departure": rebase(stored["pass_departure"], v["date"], day, flights.zone_of(origin)),
                "pass_arrival": rebase(stored["pass_arrival"], v["date"], day, flights.zone_of(destination)),
                "pass_terminal": None, "pass_gate": None}
    else:
        base = dict(stored)
    given = {k: req.get(k) for k in OWN_KEYS}
    if merge:                                     # only what is empty gets filled; CLEAR is no value in an add
        given = {k: x if x != CLEAR and base[col] is None else None for (k, x), col in zip(given.items(), OWN)}
    final = dict(base)
    dep, arr = given["departure_time"], given["arrival_time"]
    if dep == CLEAR:
        final["pass_departure"] = None
    elif dep is not None:
        final["pass_departure"] = local_instant(day, dep, _zone_for(origin))
    if arr == CLEAR:
        final["pass_arrival"] = None
    elif arr is not None:
        if final["pass_departure"] is None:
            raise InputError("arrival_needs_departure")
        final["pass_arrival"] = arrival_instant(day, final["pass_departure"], arr, _zone_for(destination))
        if final["pass_arrival"] is None:
            raise InputError("bad_duration")
    elif dep is not None and final["pass_arrival"] is not None \
            and not _same(final["pass_departure"], base["pass_departure"]):
        if final["pass_departure"] is None:       # the departure cleared under an arrival that stays
            raise InputError("arrival_needs_departure")
        zone = flights.zone_of(destination)
        if zone is not None:
            final["pass_arrival"] = arrival_instant(day, final["pass_departure"],
                                                    local_hm(final["pass_arrival"], zone), zone)
            if final["pass_arrival"] is None:
                raise InputError("bad_duration")
    for key, col in (("terminal", "pass_terminal"), ("gate", "pass_gate")):
        if given[key] == CLEAR:
            final[col] = None
        elif given[key] is not None:
            final[col] = given[key]
    parts = [part for key, col, part in (("departure_time", "pass_departure", "departure_changed"),
                                         ("arrival_time", "pass_arrival", "arrival_changed"),
                                         ("terminal", "pass_terminal", "terminal_changed"),
                                         ("gate", "pass_gate", "gate_changed"))
             if given[key] is not None and not _same(final[col], base[col])]
    moved = moved_day and any(base[col] is not None and given[key] is None
                              for key, col in (("departure_time", "pass_departure"), ("arrival_time", "pass_arrival")))
    for col in ("pass_departure", "pass_arrival"):
        if _same(final[col], stored[col]):
            final[col] = stored[col]
    return Fields(final, parts, moved)
