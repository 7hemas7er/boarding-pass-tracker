#!/usr/bin/env python3
"""Builds custom_components/boarding_pass_tracker/core/airport_zones.json: the IANA time zone of every airport of
airports.json, for the times typed by hand (stage B, decision 3; INTEGRATION_PLAN.md section 11 Q3). Offline and
reproducible: the inputs are two pinned wheels from PyPI, checked by their sha256 before anything is read, and read
as ZIP archives (nothing in them is imported or run). The integration never downloads anything: it only reads the
JSON this writes.

  python3 -m pip download --no-deps --only-binary=:all: airportsdata==20261030 tzdata==2026.6 -d <empty dir>
  python3 -I -B tools/build_airport_zones.py --airportsdata <dir>/airportsdata-20261030-py3-none-any.whl \\
      --tzdata <dir>/tzdata-2026.6-py2.py3-none-any.whl [--out FILE | --check] [--report]

Sources:
  - airportsdata (MIT, (c) Mike Borsetti; it includes data from mwgg/Airports, (c) 2014 mwgg): airports.csv, an
    IANA zone per IATA code (its README: «Timezone was originally sourced from TimeZoneDB»). The licence is kept in
    THIRD_PARTY.md at the root of the repository.
  - tzdata (the IANA tz database as a Python package): zone.tab (public domain), the zones of each country; and the
    zone files themselves, read from the wheel (ZoneInfo.from_file), so the checks below do not depend on the time
    zones of the machine that runs this.

The rule, for each code of airports.json ([lat, lon, country], OurAirports):
  1. airportsdata's zone, when its row is the same airport (its coordinates within MAX_KM of ours: some IATA codes
     name another airport there) and the zone exists in tzdata and agrees with our country: listed for it in
     zone.tab, or with the same UTC offsets in January and July as one of its zones (Arctic/Longyearbyen for NO,
     Europe/Mariehamn for FI), or the country has no zone.tab row (XK);
  2. else, when zone.tab has exactly one zone for our country: that zone;
  3. else no zone: typed times are refused for that airport (manual.TEXTS["no_time_zone"]).
The output: the "_" keys (the sources with their versions and sha256, the counts), then one "IATA": "zone" line per
airport with a zone, sorted, as airports.json is written. --check compares a fresh build with the file instead of
writing it (exit 1 if they differ); --report prints the airports of rules 2 and 3 and every airportsdata row
refused, with the reason.
"""
import argparse, csv, datetime, hashlib, io, json, math, os, sys, zipfile
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
CORE = os.path.join(os.path.dirname(HERE), "custom_components", "boarding_pass_tracker", "core")
AIRPORTS = os.path.join(CORE, "airports.json")
OUT = os.path.join(CORE, "airport_zones.json")
# the pinned inputs: (package, version, wheel file name, sha256 of the wheel as PyPI lists it)
PINS = {
    "airportsdata": ("airportsdata", "20261030", "airportsdata-20261030-py3-none-any.whl",
                     "0b50327f8d026be56cc18e1b83df356d2550f26127e13518883dc0f62533eb87"),
    "tzdata": ("tzdata", "2026.6", "tzdata-2026.6-py2.py3-none-any.whl",
               "0a10eec6e13ef16b627fabed9a5f2ba61a62b1c67f327305f4e7e412a70b4aec"),
}
CSV_MEMBER = "airportsdata/airports.csv"
LICENSE_MEMBER = "airportsdata-{version}.dist-info/licenses/LICENSE"
ZONE_TAB_MEMBER = "tzdata/zoneinfo/zone.tab"
ZONEINFO_PREFIX = "tzdata/zoneinfo/"
MAX_KM = 300                 # an airportsdata row farther than this from our coordinates is another airport
# the two instants whose UTC offsets compare a zone with a country's zones (winter and summer of both hemispheres)
INSTANTS = (datetime.datetime(2026, 1, 15, 12, tzinfo=datetime.timezone.utc),
            datetime.datetime(2026, 7, 15, 12, tzinfo=datetime.timezone.utc))
MAX_MEMBER = 16 * 1024 * 1024            # bytes of one archive member read (airports.csv is about 3 MB)


class BuildError(Exception):
    pass


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def read_wheel(path, pin, check_pin=True):
    """(the wheel's bytes, an open ZipFile on them), after its sha256 is checked against the pin."""
    with open(path, "rb") as f:
        data = f.read()
    if check_pin and sha256(data) != pin[3]:
        raise BuildError(f"{os.path.basename(path)}: sha256 {sha256(data)} is not the pinned {pin[0]} {pin[1]} "
                         f"({pin[3]})")
    return data, zipfile.ZipFile(io.BytesIO(data))


def member(z, name):
    info = z.getinfo(name)
    if info.file_size > MAX_MEMBER:
        raise BuildError(f"{name}: {info.file_size} bytes, more than expected")
    return z.read(info)


def km(lat1, lon1, lat2, lon2):
    r = math.radians
    h = math.sin(r(lat2 - lat1) / 2) ** 2 + math.cos(r(lat1)) * math.cos(r(lat2)) * math.sin(r(lon2 - lon1) / 2) ** 2
    return 12_742 * math.asin(min(1.0, math.sqrt(h)))


class Zones:
    """The zone files of the tzdata wheel, loaded on demand."""

    def __init__(self, z):
        self.z, self.cache = z, {}

    def get(self, name):
        if name not in self.cache:
            try:
                data = member(self.z, ZONEINFO_PREFIX + name)
                self.cache[name] = ZoneInfo.from_file(io.BytesIO(data), key=name)
            except (KeyError, ValueError, BuildError):
                self.cache[name] = None
        return self.cache[name]

    def offsets(self, name):
        zone = self.get(name)
        return tuple(t.astimezone(zone).utcoffset() for t in INSTANTS) if zone else None


def zone_tab(text):
    """{country: [zones]} of zone.tab, in its order."""
    out = {}
    for line in text.splitlines():
        if line.startswith("#") or not line.strip():
            continue
        fields = line.split("\t")
        out.setdefault(fields[0], []).append(fields[2])
    return out


def decide(airports, rows, countries, zones):
    """{code: zone}, {code: (rule, reason)} for every code of airports."""
    out, why = {}, {}
    for code, value in sorted(airports.items()):
        lat, lon, country = value[0], value[1], value[2] if len(value) > 2 else None
        own = countries.get(country, [])
        row, refused = rows.get(code), None
        if row is not None:
            try:
                d = km(lat, lon, float(row["lat"]), float(row["lon"]))
            except (TypeError, ValueError):
                d = math.inf
            tz = row.get("tz") or ""
            if d > MAX_KM:
                refused = f"another airport ({round(d)} km away)"
            elif zones.get(tz) is None:
                refused = f"zone {tz!r} not in tzdata"
            elif own and tz not in own and zones.offsets(tz) not in {zones.offsets(n) for n in own}:
                refused = f"zone {tz} not of {country}"
            else:
                out[code], why[code] = tz, ("airportsdata", None)
                continue
        if len(own) == 1 and zones.get(own[0]) is not None:
            out[code], why[code] = own[0], ("zone.tab", refused)
        else:
            why[code] = ("none", refused or ("not in airportsdata" if row is None else None))
    return out, why


def build(airportsdata_wheel, tzdata_wheel, airports_path=AIRPORTS, check_pins=True):
    """(the JSON text, {code: (rule, reason)})."""
    ad_bytes, ad = read_wheel(airportsdata_wheel, PINS["airportsdata"], check_pins)
    tz_bytes, tz = read_wheel(tzdata_wheel, PINS["tzdata"], check_pins)
    csv_bytes = member(ad, CSV_MEMBER)
    tab_bytes = member(tz, ZONE_TAB_MEMBER)
    rows = {}
    for r in csv.DictReader(io.StringIO(csv_bytes.decode("utf-8"))):
        if r.get("iata") and r["iata"] not in rows:
            rows[r["iata"]] = r
    with open(airports_path, encoding="utf-8") as f:
        airports = {k: v for k, v in json.load(f).items() if not k.startswith("_")}
    with open(airports_path, "rb") as f:
        airports_sha = sha256(f.read())
    zones, why = decide(airports, rows, zone_tab(tab_bytes.decode("utf-8")), Zones(tz))
    counts = {rule: sum(1 for r, _ in why.values() if r == rule) for rule in ("airportsdata", "zone.tab", "none")}
    head = {
        "_fonte": ("Fuso orario IANA di ogni aeroporto di airports.json, per gli orari scritti a mano: airportsdata "
                   "(MIT, (c) Mike Borsetti; dati anche da mwgg/Airports, (c) 2014 mwgg; licenza in THIRD_PARTY.md) "
                   "quando la sua riga è lo stesso aeroporto e il fuso è del suo paese, altrimenti il fuso unico "
                   "del paese in zone.tab (tzdata, pubblico dominio). Generato da tools/build_airport_zones.py"),
        "_airportsdata": {"version": PINS["airportsdata"][1], "wheel": os.path.basename(airportsdata_wheel),
                          "sha256": sha256(ad_bytes), "airports_csv_sha256": sha256(csv_bytes), "license": "MIT"},
        "_tzdata": {"version": PINS["tzdata"][1], "wheel": os.path.basename(tzdata_wheel), "sha256": sha256(tz_bytes),
                    "zone_tab_sha256": sha256(tab_bytes), "license": "zone.tab: public domain"},
        "_airports_json_sha256": airports_sha,
        "_counts": {"airports": len(airports), **counts},
    }
    lines = [f"{json.dumps(k)}: {json.dumps(v, ensure_ascii=False, sort_keys=True)}" for k, v in head.items()]
    lines += [f"{json.dumps(code)}: {json.dumps(zone)}" for code, zone in sorted(zones.items())]
    return "{" + ",\n".join(lines) + "}\n", why


def license_text(airportsdata_wheel):
    """The MIT licence of airportsdata, as the wheel holds it (THIRD_PARTY.md quotes it)."""
    _, ad = read_wheel(airportsdata_wheel, PINS["airportsdata"])
    return member(ad, LICENSE_MEMBER.format(version=PINS["airportsdata"][1])).decode("utf-8")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--airportsdata", required=True, help="the pinned airportsdata wheel")
    ap.add_argument("--tzdata", required=True, help="the pinned tzdata wheel")
    ap.add_argument("--airports", default=AIRPORTS, help="airports.json (default: the core's)")
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--check", action="store_true", help="compare with --out instead of writing it")
    ap.add_argument("--report", action="store_true", help="print the airports without airportsdata's zone")
    ap.add_argument("--license", action="store_true", help="print airportsdata's licence and exit")
    a = ap.parse_args(argv)
    try:
        if a.license:
            sys.stdout.write(license_text(a.airportsdata))
            return 0
        text, why = build(a.airportsdata, a.tzdata, a.airports)
    except (BuildError, OSError, KeyError, zipfile.BadZipFile, ValueError) as e:
        print(f"error: {type(e).__name__}: {e}", file=sys.stderr)
        return 2
    counts = json.loads(text)["_counts"]
    print(f"airports {counts['airports']}: airportsdata {counts['airportsdata']}, zone.tab {counts['zone.tab']}, "
          f"none {counts['none']}")
    if a.report:
        for code, (rule, reason) in sorted(why.items()):
            if rule != "airportsdata" or reason:
                print(f"  {code}: {rule}" + (f" (airportsdata refused: {reason})" if reason else ""))
    if a.check:
        try:
            with open(a.out, encoding="utf-8") as f:
                same = f.read() == text
        except OSError:
            same = False
        print("equal" if same else f"DIFFERS from {a.out}")
        return 0 if same else 1
    with open(a.out, "w", encoding="utf-8") as f:
        f.write(text)
    print(f"written {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
