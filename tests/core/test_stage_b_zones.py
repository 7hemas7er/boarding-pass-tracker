"""Stage B: the airport time zones (INTEGRATION_PLAN.md section 11 Q3, 3.7 F3): airport_zones.json as
tools/build_airport_zones.py writes it from the pinned airportsdata (MIT) and tzdata wheels, and the tool's rules on
small made-up wheels (no download: the real wheels are not needed here). Fictional data only.

  python3 -I -B -m unittest discover -s tests/core -p 'test_stage_b_zones.py' -v
"""
import contextlib, csv, hashlib, io, json, os, sys, tempfile, unittest, zipfile
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import support  # noqa: E402  (the ported core on sys.path; ROOT the repository)
import flights  # noqa: E402

sys.path.insert(0, os.path.join(support.ROOT, "tools"))
import build_airport_zones as bz  # noqa: E402

ZONES_JSON = os.path.join(support.CORE, "airport_zones.json")
AIRPORTS_JSON = os.path.join(support.CORE, "airports.json")


def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


class Shipped(unittest.TestCase):
    """The file the integration reads (it never downloads anything)."""

    @classmethod
    def setUpClass(cls):
        cls.zones, cls.airports = load(ZONES_JSON), load(AIRPORTS_JSON)

    def test_header_records_the_sources(self):
        z = self.zones
        self.assertEqual({k for k in z if k.startswith("_")},
                         {"_fonte", "_airportsdata", "_tzdata", "_airports_json_sha256", "_counts"})
        ad, tz = bz.PINS["airportsdata"], bz.PINS["tzdata"]
        self.assertEqual((z["_airportsdata"]["version"], z["_airportsdata"]["wheel"], z["_airportsdata"]["sha256"],
                          z["_airportsdata"]["license"]), (ad[1], ad[2], ad[3], "MIT"))
        self.assertEqual((z["_tzdata"]["version"], z["_tzdata"]["wheel"], z["_tzdata"]["sha256"]), tz[1:])
        self.assertRegex(z["_airportsdata"]["airports_csv_sha256"], "^[0-9a-f]{64}$")
        self.assertRegex(z["_tzdata"]["zone_tab_sha256"], "^[0-9a-f]{64}$")
        self.assertIn("airportsdata (MIT", z["_fonte"])
        with open(AIRPORTS_JSON, "rb") as f:
            self.assertEqual(z["_airports_json_sha256"], hashlib.sha256(f.read()).hexdigest())   # built from ours

    def test_counts_and_codes(self):
        codes = {k for k in self.airports if not k.startswith("_")}
        entries = {k: v for k, v in self.zones.items() if not k.startswith("_")}
        c = self.zones["_counts"]
        self.assertEqual(c["airports"], len(codes))
        self.assertEqual(c["airportsdata"] + c["zone.tab"] + c["none"], len(codes))
        self.assertEqual(len(entries), c["airportsdata"] + c["zone.tab"])
        self.assertLessEqual(set(entries), codes)
        self.assertGreater(c["airportsdata"], 5000)              # the plan's run: 5114 of 5334

    def test_every_zone_loads_here(self):
        for code, name in self.zones.items():
            if not code.startswith("_"):
                ZoneInfo(name)
                self.assertEqual(flights.zone_of(code), name)

    def test_the_rules_on_known_airports(self):
        z = self.zones
        for code, name in (("BLQ", "Europe/Rome"), ("STN", "Europe/London"), ("JFK", "America/New_York"),
                           ("NRT", "Asia/Tokyo"), ("HNL", "Pacific/Honolulu"),
                           ("LYR", "Arctic/Longyearbyen"),        # not NO's zone in zone.tab, the same offsets
                           ("PRN", "Europe/Belgrade"),            # XK has no zone.tab row
                           ("OUI", "Europe/Paris"),               # airportsdata's OUI is another airport: FR's zone
                           ("PHH", "Asia/Kathmandu")):            # the same, NP's zone
            self.assertEqual(z.get(code), name, code)
        for code in ("GOY", "BKA"):                              # another airport there, in multi-zone RU: none
            self.assertNotIn(code, z)
            self.assertIsNone(flights.zone_of(code))


TZIF_NAMES = ("Europe/Rome", "Europe/London", "Europe/Paris", "Europe/Oslo", "Arctic/Longyearbyen", "Asia/Tokyo",
              "America/New_York", "America/Chicago")


def tzif(name):
    """The TZif bytes of a zone, from the tzdata package or the system's zoneinfo (only to build the fake wheel)."""
    try:
        import importlib.resources
        package, _, leaf = f"tzdata.zoneinfo.{name.replace('/', '.')}".rpartition(".")
        return importlib.resources.files(package).joinpath(leaf).read_bytes()
    except (ImportError, OSError, ModuleNotFoundError):
        with open(os.path.join("/usr/share/zoneinfo", name), "rb") as f:
            return f.read()


class Tool(unittest.TestCase):
    """build_airport_zones.decide and build on two fake wheels: the rules, the determinism, the pins."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="bpt-zones-")
        self.addCleanup(lambda: __import__("shutil").rmtree(self.dir, True))
        # our airports: [lat, lon, country]
        self.airports = {"_fonte": "test", "AAA": [44.53, 11.29, "IT"], "BBB": [51.88, 0.24, "GB"],
                         "CCC": [48.46, -5.06, "FR"], "DDD": [78.25, 15.47, "NO"], "EEE": [42.57, 21.04, "XK"],
                         "FFF": [40.64, -73.78, "US"], "GGG": [41.97, -87.90, "US"], "HHH": [35.76, 140.39, "JP"],
                         "III": [41.80, 12.25, "IT"]}
        rows = [  # iata, lat, lon, country, tz
            ("AAA", 44.53, 11.29, "IT", "Europe/Rome"),
            ("BBB", 51.90, 0.25, "GB", "Europe/London"),
            ("CCC", 20.26, 100.44, "LA", "Asia/Vientiane"),       # another airport: refused, FR has one zone
            ("DDD", 78.25, 15.47, "NO", "Arctic/Longyearbyen"),   # not in NO's list, same offsets as Europe/Oslo
            ("EEE", 42.57, 21.04, "XK", "Europe/Rome"),           # XK has no zone.tab row: accepted
            ("FFF", 40.64, -73.78, "US", "Asia/Tokyo"),           # the offsets of no US zone: refused, none
            ("HHH", 35.76, 140.39, "JP", "Mars/Olympus"),         # not in tzdata: refused, JP has one zone
            ("III", 41.80, 12.25, "IT", "")]                       # no zone: IT's
        self.airports_path = os.path.join(self.dir, "airports.json")
        with open(self.airports_path, "w", encoding="utf-8") as f:
            json.dump(self.airports, f)
        buf = io.StringIO()
        w = csv.writer(buf, quoting=csv.QUOTE_ALL)
        w.writerow(["icao", "iata", "name", "city", "subd", "country", "elevation", "lat", "lon", "tz", "lid"])
        for iata, lat, lon, country, tz in rows:
            w.writerow([f"X{iata}", iata, "Airport", "City", "", country, 0, lat, lon, tz, ""])
        self.ad = os.path.join(self.dir, "ad.whl")
        with zipfile.ZipFile(self.ad, "w") as z:
            z.writestr(bz.CSV_MEMBER, buf.getvalue())
        tab = ("# zone.tab\n#country-\nIT\t+4154+01229\tEurope/Rome\nGB\t+513030-0000731\tEurope/London\n"
               "FR\t+4852+00220\tEurope/Paris\nNO\t+5955+01045\tEurope/Oslo\nJP\t+353916+1394441\tAsia/Tokyo\n"
               "US\t+404251-0740023\tAmerica/New_York\nUS\t+415100-0873900\tAmerica/Chicago\n")
        self.tz = os.path.join(self.dir, "tz.whl")
        with zipfile.ZipFile(self.tz, "w") as z:
            z.writestr(bz.ZONE_TAB_MEMBER, tab)
            for name in TZIF_NAMES:
                z.writestr(bz.ZONEINFO_PREFIX + name, tzif(name))

    def build(self):
        return bz.build(self.ad, self.tz, self.airports_path, check_pins=False)

    def test_rules(self):
        text, why = self.build()
        out = json.loads(text)
        self.assertEqual({k: v for k, v in out.items() if not k.startswith("_")},
                         {"AAA": "Europe/Rome", "BBB": "Europe/London", "CCC": "Europe/Paris",
                          "DDD": "Arctic/Longyearbyen", "EEE": "Europe/Rome", "HHH": "Asia/Tokyo",
                          "III": "Europe/Rome"})
        self.assertEqual(out["_counts"], {"airports": 9, "airportsdata": 4, "zone.tab": 3, "none": 2})
        self.assertEqual(why["CCC"][0], "zone.tab")
        self.assertIn("another airport", why["CCC"][1])
        self.assertEqual(why["FFF"], ("none", "zone Asia/Tokyo not of US"))
        self.assertEqual(why["GGG"], ("none", "not in airportsdata"))
        self.assertIn("not in tzdata", why["HHH"][1])

    def test_deterministic_and_check(self):
        text, _ = self.build()
        self.assertEqual(self.build()[0], text)
        self.assertTrue(text.endswith("}\n"))
        self.assertEqual(text.count("\n"), len(json.loads(text)))           # one key per line
        out = os.path.join(self.dir, "zones.json")
        with open(out, "w", encoding="utf-8") as f:
            f.write(text)
        with mock_pins(self.ad, self.tz), contextlib.redirect_stdout(io.StringIO()) as printed:
            self.assertEqual(bz.main(["--airportsdata", self.ad, "--tzdata", self.tz, "--airports", self.airports_path,
                                      "--out", out, "--check"]), 0)
        self.assertIn("equal", printed.getvalue())
        with open(out, "a", encoding="utf-8") as f:
            f.write(" ")
        with mock_pins(self.ad, self.tz), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(bz.main(["--airportsdata", self.ad, "--tzdata", self.tz, "--airports", self.airports_path,
                                      "--out", out, "--check"]), 1)

    def test_an_unpinned_wheel_is_refused(self):
        with self.assertRaises(bz.BuildError) as e:
            bz.build(self.ad, self.tz, self.airports_path)
        self.assertIn("is not the pinned airportsdata 20261030", str(e.exception))
        with contextlib.redirect_stderr(io.StringIO()) as err, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(bz.main(["--airportsdata", self.ad, "--tzdata", self.tz, "--airports", self.airports_path,
                                      "--out", os.path.join(self.dir, "never.json")]), 2)
        self.assertIn("BuildError", err.getvalue())
        self.assertFalse(os.path.exists(os.path.join(self.dir, "never.json")))


@contextlib.contextmanager
def mock_pins(ad, tz):
    """The fake wheels' sha256 as the pins, for main()."""
    def digest(p):
        with open(p, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    saved = dict(bz.PINS)
    bz.PINS["airportsdata"] = (*saved["airportsdata"][:3], digest(ad))
    bz.PINS["tzdata"] = (*saved["tzdata"][:3], digest(tz))
    try:
        yield
    finally:
        bz.PINS.clear()
        bz.PINS.update(saved)


if __name__ == "__main__":
    unittest.main()
