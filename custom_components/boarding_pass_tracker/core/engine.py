"""boarding-pass-tracker: the engine, as the core of the Home Assistant integration. Boarding passes sent from a phone,
read with fixed rules (no AI model), then the flight status, the «go to the gate» reminder, Flightradar24 tracking,
statistics and sensors.

Flow:
  1. the iOS Shortcut «Manda volo» sends a file (a Wallet pass, or a JPEG photo, a screenshot, a HEIC photo or a PDF
     of the pass) with POST /api/boarding_pass_tracker/upload, a view of the integration (api.upload);
  2. the engine saves it under DATA_DIR and answers at once;
  3. the worker reads it by content, not by name: a Wallet pass (pkpass) from its pass.json, a photo or a PDF from
     its BCBP barcode, read by the optional barcode decoder (barcodes.py, a client); flights.py gives one flight per
     leg and one boarding pass per passenger. A photo or a PDF without a boarding pass barcode is archived; while
     the decoder does not answer, photos wait in the queue (work_one);
  4. a notification reaches the phone of whoever uploaded the file;
  5. the status from AviationStack 60, 30 and 10 minutes before departure, notifications to everyone only for
     what changes; «go to the gate» to whoever uploaded the pass, timed with the phone's GPS (the person entity in
     HA) and the gate from OpenStreetMap; the flight among Flightradar24's tracked flights in HA; the three sensors
     (the next flight, the upcoming ones, the statistics: flight_stats.py) at startup, on every change and every 10
     minutes (clock_round).
The iOS Shortcuts «Aggiungi volo» and «Modifica volo» add a flight by hand, and edit or delete any flight (GET
/choices, POST /flight; rules and texts in manual.py): a typed future flight is followed from step 5 like an
uploaded pass, a past one only counts for the statistics.

Port of the standalone tracker/engine.py (INTEGRATION_PLAN.md 3.7, patches E1-E15), with stage B (E16-E21, decision
3): the departure and arrival times, classes, terminal and gate typed by hand, the pass's terminal and arrival, and
schema v2 (a v1 file is upgraded at open_db, its copy kept in backups/). The functions keep their
signatures and their now= parameters; Home Assistant calls every one of them in its executor, through job(), never
on its event loop. The seams the integration sets: configure(settings) (every setting, SETTINGS_KEYS), _ha (an
ha.HA whose transport is the integration's bridge), _sensors (the sink of the three entities), _wakeup (wakes the
worker) and _on_auth_error (AviationStack refused the key). The HTTP server, the config file, the MQTT sensors, the
entity id check and the v0 migration are gone with the standalone service; the unit tests still set the globals
below (and the TRACKER_* variables of _env) directly.
"""
import contextlib, datetime, email.parser, email.policy, errno, hashlib, json, logging, os, re, sqlite3, stat
import threading
import time, unicodedata

if __package__:
    from . import barcodes, flight_stats, flights, ha, manual
else:
    import barcodes, flight_stats, flights, ha, manual


def _env(name, default=None):
    """TRACKER_<name>, else the default. No legacy names: the tracker has no deployments from before the split."""
    return os.environ.get(f"TRACKER_{name}", default)


# The globals start from generic defaults and the TRACKER_* variables (so the unit tests can set them); in Home
# Assistant configure() sets every one of them from the config entry (SETTINGS_KEYS), so no variable is read there.
DATA_DIR = _env("DATA_DIR")      # None: not configured (database_path refuses), never the standalone's own folder
DATABASE = _env("DATABASE")      # None: <DATA_DIR>/tracker.db (database_path)
NO_NOTIFY = _env("NO_NOTIFY") == "1"
NO_HA = _env("NO_HA") == "1"
NO_FLIGHTS = _env("NO_FLIGHTS") == "1"
# The prefix of the notification tags and group (and of the ids the standalone's MQTT sensors had): the constant
# boarding_pass_tracker in Home Assistant (configure), another one in the tests
MQTT_PREFIX = _env("MQTT_PREFIX", ha.DEFAULT_PREFIX)
# The local zone of «today», of the upload month and of the dates people type: None is the process zone (the tests);
# Home Assistant sets its configured zone (configure, and again when it changes). flights.LOCAL_TZ follows it
LOCAL_TZ = None
# The AviationStack key: in Home Assistant the config entry's ("" = none); None reads AVIATIONSTACK_KEY_FILE (tests)
AVIATIONSTACK_KEY = None
AVIATIONSTACK_KEY_FILE = _env("AVIATIONSTACK_KEY_FILE")
AVIATIONSTACK_KEY_VAR = "AVIATIONSTACK_KEY"
MAX_API_CALLS_PER_MONTH = 95   # AviationStack calls per month: the free plan has 100
# Flightradar24 in HA (HACS integration): «tracked» flights give position, altitude and speed to the card and the map
FR24_ADD = "text.flightradar24_add_to_track"
FR24_REMOVE = "text.flightradar24_remove_from_track"
FR24_TRACKED = "sensor.flightradar24_additional_tracked"   # the id of a fresh install: it depends on the HA language
FR24_LEAD = datetime.timedelta(hours=3)    # how long before departure to start tracking it
FR24_RETRY = datetime.timedelta(minutes=30)
GATE_NOT_FOUND_RETRY = datetime.timedelta(days=7)    # a gate missing from OpenStreetMap is looked up again after a week
GATE_ERROR_RETRY = datetime.timedelta(minutes=30)    # Overpass down: retry after half an hour
MAX_PIXELS = 50_000_000  # checked from the header, before decoding: a 2.5 MB JPEG can ask for 1.3 GB
MAX_BODY = 25 * 1024 * 1024
MAX_ATTEMPTS = 3
MAX_LIST = 10          # entries in the sensor attributes: the HA recorder warns above 16 KB
EVERY_S = 600          # sensors, and the decoder's /health
READ_PHOTOS = True     # photos and PDFs are accepted (api.upload): in Home Assistant, only with a decoder configured
DECODER_PAUSES = (60, 300, 900, 3600)          # seconds the photos wait after the decoder did not answer, in a row
DECODER_GIVE_UP = datetime.timedelta(hours=24)   # a photo queued for longer, the decoder still down: in error

# Data codes: stored in the database, returned by GET /documents (as the codes of _STATUS_CODES and _KIND_CODES).
# English names, Italian values that never change (rows written before the translation keep them). SQL text uses
# the same values as literals.
DOC_QUEUED, DOC_READ, DOC_ARCHIVED, DOC_ERROR = "in_coda", "letto", "archiviato", "errore"
KIND_BOARDING_PASS, KIND_DOCUMENT = "carta_imbarco", "documento"
# Stored value -> the code the HTTP API publishes. A value outside its map (a row written by hand) is published as
# it is.
_STATUS_CODES = {DOC_QUEUED: "queued", DOC_READ: "read", DOC_ARCHIVED: "archived", DOC_ERROR: "error"}
_KIND_CODES = {KIND_BOARDING_PASS: "boarding_pass", KIND_DOCUMENT: "document"}


def _code(codes, value):
    """The published code of a stored value: NULL stays None, a value outside the map stays as it is."""
    return codes.get(value, value)


SCHEMA_VERSION = 2     # PRAGMA user_version of a database with SCHEMA (stage B: 2; a v1 file is upgraded, open_db)
APPLICATION_ID = 0x42505454   # PRAGMA application_id («BPTT»): household-ledger uses user_version 1 too
# The tables of household-ledger's schema: a v1 file without an application_id (made before the split) with rows
# in them is not this service's to open (open_db)
_FOREIGN_TABLES_V1 = ("payments", "transactions")
# documents: the upload queue, the sha256 de-duplication, the file and who sent it, the notification state; the
# columns keep the relative order of v0, so a migrated file and a new one have the same shape
SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
  id INTEGER PRIMARY KEY,
  sha256 TEXT NOT NULL UNIQUE,
  uploaded_by TEXT NOT NULL,
  uploaded_at TEXT NOT NULL,
  file TEXT NOT NULL,             -- relative to DATA_DIR
  file_type TEXT NOT NULL,        -- jpg, png, webp, pdf, pkpass
  status TEXT NOT NULL,           -- in_coda, letto, archiviato, errore
  kind TEXT,                      -- carta_imbarco, documento
  merchant TEXT,                  -- the label: the flight code («FR 1234») for a boarding pass
  doc_date TEXT,                  -- YYYY-MM-DD: the flight day
  attempts INTEGER NOT NULL DEFAULT 0,
  error TEXT,
  notified INTEGER NOT NULL DEFAULT 0,
  outcome TEXT                    -- sentence of the document's notification
);
CREATE INDEX IF NOT EXISTS documents_status ON documents(status);
CREATE TABLE IF NOT EXISTS flights (
  id INTEGER PRIMARY KEY,
  airline TEXT NOT NULL,          -- IATA code of the operating carrier, from the BCBP
  number TEXT NOT NULL,           -- without leading zeros
  date TEXT NOT NULL,             -- YYYY-MM-DD, local day of the departure
  origin TEXT NOT NULL,           -- IATA
  destination TEXT NOT NULL,
  pass_departure TEXT,            -- ISO with the time zone, from the pass semantics
  pass_boarding TEXT,
  pass_gate TEXT,
  relevant_date TEXT,             -- relevantDate of the pass
  status TEXT,                    -- JSON: the last AviationStack answer (flights.normalize)
  updated_at TEXT,
  next_check TEXT,                -- ISO in UTC (utc()); NULL: over, nothing to ask
  error TEXT,
  snapshot TEXT NOT NULL DEFAULT '{}',   -- JSON: what has already been notified (flights.changes)
  created_at TEXT NOT NULL,
  pass_arrival TEXT,              -- v2: ISO instant with the destination's offset: typed or pass semantics
  pass_terminal TEXT,             -- v2: departure terminal: typed or pass semantics (departureTerminal)
  UNIQUE (airline, number, date, origin)
);
CREATE TABLE IF NOT EXISTS boarding_passes (
  id INTEGER PRIMARY KEY,
  document_id INTEGER REFERENCES documents(id),
  flight_id INTEGER NOT NULL REFERENCES flights(id),
  passenger TEXT NOT NULL,        -- full name: it stays here; HA gets only the first name. The PNR is not saved
  first_name TEXT,
  seat TEXT,
  cabin TEXT,
  uploaded_by TEXT NOT NULL,
  UNIQUE (flight_id, passenger)
);
CREATE TABLE IF NOT EXISTS api_usage (month TEXT PRIMARY KEY, calls INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS gate_positions (   -- from OpenStreetMap: lat and lon NULL = looked up and not found
  airport TEXT NOT NULL, gate TEXT NOT NULL, lat REAL, lon REAL, searched_at TEXT NOT NULL, error TEXT,
  PRIMARY KEY (airport, gate)
);
"""


_db = None
_db_lock = threading.Lock()
_wakeup = threading.Event()        # Home Assistant: an object whose set() wakes the worker task on the loop
_ha = None
_sensors = None
_LOGGER = logging.getLogger(__name__)


def _no_auth_hook():
    pass


# Called in the executor, inside check_flights, after the 6-hour wait of a refused key is written: Home Assistant's
# only hops to the event loop (it starts the reauth flow there). Its errors are logged, never raised (E7)
_on_auth_error = _no_auth_hook
_backup_active = False             # a backup of HA is running (backup_begin): survives configure() and reloads (C12)

# Executor jobs (job, shutdown): the jobs running, whether shutdown began or ended, the generation of the jobs
_jobs = threading.Condition()
_active = 0
_closing = False
_closed = False
_generation = 0
_job_local = threading.local()
_decoder_pause_until = 0.0         # time.monotonic() until which the photos wait (work_one)
_decoder_pause_index = 0


class DatabaseRefused(SystemExit):
    """open_db refused the file, untouched. `reason`: newer_database, foreign_database or old_database_v0 (the
    integration shows its translated text); str(): the English message of the standalone."""

    def __init__(self, reason, message):
        super().__init__(message)
        self.reason = reason


class MigrationFailed(Exception):
    """Stage B: the v1 -> v2 upgrade failed a check that gives the same result on every try (the row counts, the
    columns of flights): the file stays at v1 and the integration does not retry (migration_failed)."""


class Closed(Exception):
    """The engine was shut down (shutdown), or this job belongs to a configuration that was shut down."""


def log(*a):
    """One line of the log, without the timestamp (the logging handler adds it): WARNING for a line that starts
    with «warning:», else INFO."""
    text = " ".join(str(x) for x in a)
    (_LOGGER.warning if text.startswith("warning:") else _LOGGER.info)(text)


def _local_now():
    """Now in LOCAL_TZ, or in the process zone when it is None."""
    return datetime.datetime.now(LOCAL_TZ) if LOCAL_TZ is not None else datetime.datetime.now().astimezone()


def now_iso():
    return _local_now().isoformat(timespec="seconds")


def utc(t):
    """Instant in UTC with the Z: it compares as a string across daylight saving changes too."""
    return t.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_var(file, var):
    """Read again on every use: after a rotation, rewriting the file is enough."""
    with open(file, encoding="utf-8") as f:
        m = re.search(rf'^\s*{var}=["\']?([^"\'\s]+)', f.read(), re.M)
    if not m:
        raise RuntimeError(f"{var} missing in {file}")
    return m.group(1)


# ---------------------------------------------------------------- configuration
# The settings Home Assistant passes to configure(), all of them every time (runtime.settings_from_entry)
SETTINGS_KEYS = frozenset({"data_dir", "local_tz", "no_notify", "no_flights", "max_api_calls_per_month",
                           "aviationstack_key", "fr24_add", "fr24_remove", "fr24_tracked", "decoder_url",
                           "read_photos"})
_ENTITY = re.compile(r"[a-z_]+\.[a-z0-9_]+")


def configure(settings):
    """Every global from `settings` (exactly the keys of SETTINGS_KEYS): the data folder, the time zone,
    notifications and flights on or off, the monthly AviationStack cap and key ("" = none), the three Flightradar24
    entities, the decoder URL (None = none) and whether photos are accepted. The prefix is the constant
    boarding_pass_tracker. The database connection (closed if open), the shutdown state and the decoder's pause
    start again; the Flightradar24 memory (it lasts for the HA run, as in the standalone) and _backup_active stay.
    ValueError for a missing, unknown or invalid key."""
    global DATA_DIR, DATABASE, LOCAL_TZ, NO_NOTIFY, NO_HA, NO_FLIGHTS, MAX_API_CALLS_PER_MONTH, AVIATIONSTACK_KEY
    global FR24_ADD, FR24_REMOVE, FR24_TRACKED, READ_PHOTOS, MQTT_PREFIX, _db, _closing, _closed
    global _decoder_pause_until, _decoder_pause_index
    if not isinstance(settings, dict):
        raise ValueError("settings must be a dict")
    keys = set(settings)
    if keys != SETTINGS_KEYS:
        raise ValueError(f"settings: unknown {sorted(keys - SETTINGS_KEYS)}, missing {sorted(SETTINGS_KEYS - keys)}")
    s = settings
    if not isinstance(s["data_dir"], str) or not os.path.isabs(s["data_dir"]):
        raise ValueError("data_dir must be an absolute path")
    if s["local_tz"] is not None and not isinstance(s["local_tz"], datetime.tzinfo):
        raise ValueError("local_tz must be a tzinfo or None")
    for k in ("no_notify", "no_flights", "read_photos"):
        if not isinstance(s[k], bool):
            raise ValueError(f"{k} must be true or false")
    cap = s["max_api_calls_per_month"]
    if not isinstance(cap, int) or isinstance(cap, bool) or cap < 0:
        raise ValueError("max_api_calls_per_month must be a non-negative integer")
    if not isinstance(s["aviationstack_key"], str):
        raise ValueError("aviationstack_key must be a string")
    for k in ("fr24_add", "fr24_remove", "fr24_tracked"):
        if not isinstance(s[k], str) or not _ENTITY.fullmatch(s[k]):
            raise ValueError(f"{k} must be an entity id")
    if s["decoder_url"] is not None and not isinstance(s["decoder_url"], str):
        raise ValueError("decoder_url must be a string or None")
    barcodes.configure(s["decoder_url"])
    with _db_lock:
        _close_db()                                  # while a backup runs, without the checkpoint (C12), as shutdown
    with _jobs:
        _closing = _closed = False
    DATA_DIR, DATABASE = s["data_dir"], None
    LOCAL_TZ = flights.LOCAL_TZ = s["local_tz"]
    NO_NOTIFY, NO_HA, NO_FLIGHTS = s["no_notify"], False, s["no_flights"]
    MAX_API_CALLS_PER_MONTH, AVIATIONSTACK_KEY = cap, s["aviationstack_key"]
    FR24_ADD, FR24_REMOVE, FR24_TRACKED = s["fr24_add"], s["fr24_remove"], s["fr24_tracked"]
    READ_PHOTOS = s["read_photos"]
    MQTT_PREFIX = ha.DEFAULT_PREFIX
    _decoder_pause_until, _decoder_pause_index = 0.0, 0


def set_time_zone(tz):
    """Home Assistant's zone changed (EVENT_CORE_CONFIG_UPDATE): «today» and the local times follow it."""
    global LOCAL_TZ
    LOCAL_TZ = flights.LOCAL_TZ = tz


# ---------------------------------------------------------------- database
def _statements(script):
    """The statements of an SQL script one by one: executescript would COMMIT the migration's transaction."""
    buf = ""
    for line in script.splitlines(keepends=True):
        buf += line
        if sqlite3.complete_statement(buf):
            yield buf.strip()
            buf = ""


def _table_exists(c, name):
    return c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def _count(c, table):
    return c.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]


def _tables_with_rows(c, tables):
    """[(table, rows)] of the tables in `tables` that exist and are not empty."""
    return [(t, n) for t in tables if _table_exists(c, t) and (n := _count(c, t))]


def _application_id(c, path, version):
    """PRAGMA application_id: 0 or APPLICATION_ID. DatabaseRefused (foreign_database), with the file untouched,
    for another application's file: another id, or a v1 file without an id (made before the split) with rows in
    the other service's tables."""
    app_id = c.execute("PRAGMA application_id").fetchone()[0]
    if app_id not in (0, APPLICATION_ID):
        raise DatabaseRefused("foreign_database", f"{path}: this database belongs to another application "
                              f"(application_id 0x{app_id & 0xFFFFFFFF:08X}): check the database setting")
    if app_id == 0 and version > 0 and (rows := _tables_with_rows(c, _FOREIGN_TABLES_V1)):
        raise DatabaseRefused("foreign_database", f"{path}: this database holds another application's data "
                              f"({', '.join(f'{t} {n} rows' for t, n in rows)}): check the database setting")
    return app_id


def _private(path, folder=False):
    """The regular file (or, `folder`, the directory) at `path` without the group and other bits of its mode, when it
    has any: the plan's 0600 files and 0700 folder (4.1), whatever made them (Home Assistant's umask is 022, not the
    standalone's 077). Anything else, a symbolic link included, is left alone; an error is a log line."""
    try:
        st = os.lstat(path)
        kind = stat.S_ISDIR if folder else stat.S_ISREG
        if kind(st.st_mode) and st.st_mode & 0o077:
            os.chmod(path, stat.S_IMODE(st.st_mode) & 0o700)
    except FileNotFoundError:
        pass
    except OSError as e:
        log(f"warning: {os.path.basename(path)} not made private: {type(e).__name__}")


def open_db(path):
    """Connection with the current schema (SCHEMA_VERSION in PRAGMA user_version, APPLICATION_ID in PRAGMA
    application_id). Refused untouched, with DatabaseRefused (the checks only read, before WAL): a newer one
    (newer_database), another application's one (foreign_database), and a v0 one of App Casa (old_database_v0:
    the standalone 0.1.0 migrates it). While a backup of HA runs, the WAL is not checkpointed (backup_begin).
    A new file is created 0600 before SQLite opens it, so its -wal and -shm get 0600 too (SQLite gives them the
    main file's mode); an accepted file, -wal and -shm with group or other bits are made 0600 (plan 4.1).
    A v1 file (the standalone's, or stage A's) is upgraded to v2 (plan 4.4): first its copy in backups/
    (_copy_before_upgrade, at most one per source state), then _migrate_v1 in one transaction. A failure leaves it
    at v1: MigrationFailed for a check that repeats, OSError or sqlite3.OperationalError («database is locked»)
    for one a retry may pass, with the copy of the first try reused."""
    on_disk = path not in ("", ":memory:")
    if on_disk:
        try:
            os.close(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
        except OSError:                              # it exists; or sqlite3.connect raises its own error below
            pass
    c = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
    c.row_factory = sqlite3.Row
    try:
        version = c.execute("PRAGMA user_version").fetchone()[0]
        if version > SCHEMA_VERSION:
            raise DatabaseRefused("newer_database", f"{path}: database schema version {version} is newer than this "
                                  f"code ({SCHEMA_VERSION}): update {ha.PROJECT}")
        app_id = _application_id(c, path, version)
        if version == 0 and _table_exists(c, "documenti"):
            raise DatabaseRefused("old_database_v0", f"{path}: this database has the v0 schema of App Casa, which "
                                  f"is not migrated any more: open it once with {ha.PROJECT} 0.1.0, which migrates "
                                  "it, then use that file")
    except BaseException:
        c.close()
        raise
    try:
        for suffix in ("", "-wal", "-shm") if on_disk else ():
            _private(path + suffix)
        c.execute("PRAGMA journal_mode=WAL")
        if _backup_active:
            c.execute("PRAGMA wal_autocheckpoint=0")
        if version == 1 and on_disk:
            _copy_before_upgrade(c, path)
        c.executescript(SCHEMA)                      # CREATE ... IF NOT EXISTS: idempotent
        if version == 0:
            c.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        elif version == 1:
            _migrate_v1(c, path)
        if app_id != APPLICATION_ID:
            c.execute(f"PRAGMA application_id = {APPLICATION_ID}")
    except BaseException:
        c.close()
        raise
    return c


# ---------------------------------------------------------------- the v1 -> v2 upgrade (stage B, plan 4.2-4.4)
_V2_ADDED = ("pass_arrival", "pass_terminal")      # the columns v2 appends to flights, in this order
_BACKUP_NAME = re.compile(r"tracker-v1-\d{8}-\d{6}-([0-9a-f]{12})\.db")


def _flights_layout(c):
    """PRAGMA table_info(flights): (cid, name, type, notnull, dflt_value, pk) of each column."""
    return [tuple(r) for r in c.execute("PRAGMA table_info(flights)")]


def _fresh_layout():
    """The flights layout of a new file (SCHEMA): what a migrated file must end with."""
    m = sqlite3.connect(":memory:")
    try:
        m.executescript(SCHEMA)
        return _flights_layout(m)
    finally:
        m.close()


def backups_folder(path):
    """The folder of the copies made before an upgrade: backups/ next to the database (plan 4.1)."""
    return os.path.join(os.path.dirname(os.path.abspath(path)), "backups")


def _copy_before_upgrade(c, path):
    """Plan 4.4: before a v1 file is upgraded, its copy backups/tracker-v1-<YYYYMMDD-HHMMSS>-<src12>.db (0600, in a
    0700 folder), made with the sqlite3 backup API, which keeps it consistent with the WAL. <src12> is the start of
    the sha256 of the main file after a full checkpoint (the copy's own bytes differ from the source's), so a retry
    from the same state finds that copy and makes no other one; it is written under a temporary name and renamed
    once complete. Returns the copy's path. OSError while a backup of HA runs (the checkpoint would change the
    file it copies: the setup is retried), sqlite3.OperationalError if the WAL cannot be folded in, OSError (EIO)
    if the copy fails its quick_check while the source passes it, sqlite3.DatabaseError (no retry: the shell's
    ConfigEntryError) if the source fails it too."""
    if _backup_active:
        raise OSError(errno.EBUSY, "a backup of Home Assistant is running: the upgrade waits for its end")
    busy = c.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
    if busy[0]:
        raise sqlite3.OperationalError("database is locked: the WAL could not be folded in before the upgrade")
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    src12 = h.hexdigest()[:12]
    folder = backups_folder(path)
    _private_dirs(folder)
    _private(folder, folder=True)
    for name in sorted(os.listdir(folder)):
        m = _BACKUP_NAME.fullmatch(name)
        if m and m.group(1) == src12 and os.path.isfile(os.path.join(folder, name)):
            return os.path.join(folder, name)
    final = os.path.join(folder, f"tracker-v1-{_local_now():%Y%m%d-%H%M%S}-{src12}.db")
    partial = os.path.join(folder, f".tracker-v1-{src12}.partial")
    for p in (partial, partial + "-journal"):
        with contextlib.suppress(FileNotFoundError):
            os.remove(p)
    os.close(os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
    try:
        dst = sqlite3.connect(partial)
        try:
            c.backup(dst)
            dst.execute("PRAGMA journal_mode=DELETE")         # one file, whatever the source's mode
            ok = dst.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        finally:
            dst.close()
        if not ok:
            if c.execute("PRAGMA quick_check").fetchone()[0] != "ok":      # the source itself: no retry helps
                raise sqlite3.DatabaseError("database disk image is malformed: the v1 file fails its quick_check, "
                                            "not upgraded")
            raise OSError(errno.EIO, "the copy before the upgrade does not check out")
        os.replace(partial, final)
    except BaseException:
        with contextlib.suppress(OSError):
            os.remove(partial)
        raise
    log(f"database copied before the upgrade to v2: backups/{os.path.basename(final)}")
    return final


def _migrate_v1(c, path):
    """Plan 4.2: v1 -> v2 in one transaction: the v2 columns of flights that are missing appended (ADD COLUMN puts
    them after created_at, so a migrated file has a new one's columns), user_version 2, then the checks before the
    COMMIT: the columns of flights equal a new file's and every table keeps its rows; else MigrationFailed and the
    file stays at v1 (ROLLBACK)."""
    tables = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' "
                                      "ORDER BY name")]
    c.execute("BEGIN IMMEDIATE")
    try:
        before = {t: _count(c, t) for t in tables}
        have = {r[1] for r in c.execute("PRAGMA table_info(flights)")}
        for col in _V2_ADDED:
            if col not in have:
                c.execute(f"ALTER TABLE flights ADD COLUMN {col} TEXT")
        c.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        if _flights_layout(c) != _fresh_layout():
            raise MigrationFailed(f"{path}: the flights table has columns a v1 database does not have: not upgraded")
        if {t: _count(c, t) for t in tables} != before:
            raise MigrationFailed(f"{path}: the row counts changed during the upgrade: not upgraded")
        c.execute("COMMIT")
    except BaseException:
        if c.in_transaction:
            c.execute("ROLLBACK")
        raise
    log(f"database upgraded to v{SCHEMA_VERSION}: {', '.join(f'{t} {n}' for t, n in before.items())}")


def database_path():
    """The `database` setting, else <DATA_DIR>/tracker.db. RuntimeError when neither is set."""
    if DATABASE:
        return os.path.expanduser(DATABASE)
    if not DATA_DIR:
        raise RuntimeError("the data folder is not configured")
    return os.path.join(DATA_DIR, "tracker.db")


def db():
    """The connection, opened on first use. Closed once shutdown() ended, and in a job of an older generation."""
    global _db
    gen = getattr(_job_local, "generation", None)
    if _closed or (gen is not None and gen != _generation):
        raise Closed("the engine is shut down")
    if _db is None:
        _db = open_db(database_path())
    return _db


def q(sql, args=(), one=False):
    """Rows as dicts: the modules use .get, which sqlite3.Row lacks."""
    with _db_lock:
        cur = db().execute(sql, args)
        rows = [dict(r) for r in (cur.fetchmany(1) if one else cur.fetchall())]
    if one:
        return rows[0] if rows else None
    return rows


@contextlib.contextmanager
def transaction():
    """All or nothing, under _db_lock: if the process dies halfway (restart, OOM, full disk), none of the block's
    writes stay, so rows that belong together are never left half written."""
    with _db_lock:
        c = db()
        c.execute("BEGIN")
        try:
            yield c
        except BaseException:
            c.execute("ROLLBACK")
            raise
        c.execute("COMMIT")


def _update(table, row_id, fields, condition=""):
    with _db_lock:
        return db().execute(f"UPDATE {table} SET {', '.join(f'{k}=?' for k in fields)} WHERE id=?{condition}",
                            (*fields.values(), row_id)).rowcount


def update_document(doc_id, **fields):
    _update("documents", doc_id, fields)


def update_flight(flight_id, **fields):
    _update("flights", flight_id, fields)


def update_flight_if(v, **fields):
    """update_flight, only while the row is still the flight `v` was read as: an edit or delete made meanwhile
    through POST /flight wins over the clock's stale write. Returns the rows changed (0 or 1)."""
    with _db_lock:
        return db().execute(f"UPDATE flights SET {', '.join(f'{k}=?' for k in fields)} WHERE id=? AND airline=? "
                            "AND number=? AND date=? AND origin=? AND created_at=?",
                            (*fields.values(), v["id"], v["airline"], v["number"], v["date"], v["origin"],
                             v["created_at"])).rowcount


def _same_flight(v):
    """Is the flight still the one `v` was read as? False (and a log line) once its date was changed or it was
    deleted through POST /flight: the clock then drops what it was about to notify or write."""
    r = q("SELECT airline, number, date, origin, created_at FROM flights WHERE id=?", (v["id"],), one=True)
    if r is not None and all(r[k] == v[k] for k in r):
        return True
    log(f"flight #{v['id']} changed by hand during the check, result discarded")
    return False


# ---------------------------------------------------------------- formats
def file_type_from_bytes(b):
    """From the content, not from the Content-Type or the file name that the client sends. A boarding pass comes as a
    Wallet pass or as a picture of its barcode: anything else (an e-invoice, a bank statement) is None."""
    if b[:3] == b"\xff\xd8\xff":
        return "jpg"
    if b[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if b[:5] == b"%PDF-":
        return "pdf"
    if b[:4] == b"RIFF" and b[8:12] == b"WEBP":
        return "webp"
    if b[4:8] == b"ftyp" and b[8:12] in barcodes.HEIF_BRANDS:   # section 11 Q4's eight brands (hevx included)
        return "heic"
    if flights.is_pkpass(b):
        return "pkpass"
    return None


def extract_file(content_type, body):
    """The Shortcut's «File» body (the bytes as they are), or multipart/form-data («Form»)."""
    if not (content_type or "").lower().startswith("multipart/form-data"):
        return body
    msg = email.parser.BytesParser(policy=email.policy.HTTP).parsebytes(
        b"Content-Type: " + content_type.encode("latin-1") + b"\r\n\r\n" + body)
    for part in msg.iter_parts():
        data = part.get_payload(decode=True)
        if data and file_type_from_bytes(data):
            return data
    return None


def dd_mm(iso):
    return f"{iso[8:10]}/{iso[5:7]}" if iso else "data ?"


def clean(s, n=80):
    """Text taken from a document (a passenger's first name, an error) before it reaches HA or a notification:
    no control or invisible characters (U+202E reverses the text), at most n characters."""
    s = "".join(" " if unicodedata.category(c)[0] == "C" else c for c in str(s or ""))
    return re.sub(r"\s+", " ", s).strip()[:n].rstrip()


# ---------------------------------------------------------------- Home Assistant
def ha_client():
    """The ha.HA the integration installed (its bridge). HAError when there is none."""
    if _ha is None:
        raise ha.HAError("Home Assistant not connected")
    return _ha


def tag(kind, n):
    """The notification tag: a new notification with the same tag replaces the old one on the phone. Per service
    and instance, since both services number their documents from 1: <mqtt.prefix>-<kind><id>, kind d (document),
    s (payment), p (reminder) or f (flight)."""
    return f"{MQTT_PREFIX}-{kind}{int(n)}"


def notify(user, title, message, actions=None, tag=None):
    if NO_NOTIFY:
        log(f"[notification to {user['name']}] {title}: {message}"
            + (f" {[a['title'] for a in actions]}" if actions else ""))
        return True
    if ha_client().notify(user["notify"], title, message, actions, tag):
        return True
    log(f"notification to {user['name']} not sent: retrying later")
    return False


# ---------------------------------------------------------------- notifications
# The tags <mqtt.prefix>-d<document> and <mqtt.prefix>-f<flight> (tag()) are a contract with the phones; titles and
# messages are Italian user text. No buttons: nothing here asks for an answer.
def notification_text(doc):
    """(title, message, actions) for the document's status, or None if there is nothing to say. It reads only id,
    status, kind, outcome and error: the tracker's documents have no expense columns."""
    if doc["status"] == DOC_READ and doc["outcome"]:
        return ("Carta d'imbarco letta" if doc["kind"] == KIND_BOARDING_PASS else "Gia' presente"), doc["outcome"], None
    if doc["status"] == DOC_ARCHIVED:
        return "Documento archiviato", doc["outcome"] or "Non e' una carta d'imbarco: l'ho archiviato.", None
    if doc["status"] == DOC_ERROR:
        return ("Lettura non riuscita", f"Documento #{doc['id']}: {clean(doc['error'], 200) or 'errore'}. Riprova o "
                "mandalo di nuovo.", None)
    return None


def user_of(row, cfg):
    return next((u for u in cfg["users"].values() if u["name"] == row["uploaded_by"]), None)


def send_pending_notifications(cfg):
    for doc in q("SELECT * FROM documents WHERE notified=0 AND status IN ('letto','archiviato','errore')"):
        t, u = notification_text(doc), user_of(doc, cfg)
        if t is None:
            update_document(doc["id"], notified=1)
        elif u and notify(u, *t, tag=tag("d", doc["id"])):
            update_document(doc["id"], notified=1)


# ---------------------------------------------------------------- sensors (MQTT)
# Sensor names and attribute keys (English, docs/TRANSLATION_MAP.md) are read by the dashboard and the two cards;
# the state and the texts for people (status, route, cities) stay Italian
def flight_card(v, s):
    """A flight's attributes for HA: no PNR, barcode or surnames."""
    s = s or {}
    otz, dtz = s.get("origin_tz"), s.get("destination_tz")
    dep, arr, ph = flights.departure(v, s), flights.arrival(s, v), flights.phase(s)
    passes = q("SELECT first_name, seat, cabin, document_id, passenger FROM boarding_passes WHERE flight_id=? "
               "ORDER BY id", (v["id"],))
    typed = not s and manual.is_typed_flight(passes)          # typed by hand, no AviationStack status yet
    prio = flights.is_priority([c["cabin"] for c in passes])
    walks = (json.loads(v.get("snapshot") or "{}").get("walk") or {}).values()
    margin = datetime.timedelta(minutes=max([w.get("margin_min") or 0 for w in walks] or [0])) or flights.GATE_MARGIN
    (boarding, estimated), at_gate = flights.boarding_time(v, s), flights.at_gate_time(v, s, prio)
    leave = flights.leave_for_gate_time(v, s, prio, margin)
    return {"flight": flights.flight_code(v), "route": flights.route(v, s), "date": v["date"],
            "origin": v["origin"], "destination": v["destination"],
            "origin_city": flights.city(v["origin"], s.get("origin_city")),
            "destination_city": flights.city(v["destination"], s.get("destination_city")), "phase": ph,
            "takeoff": s.get("takeoff"),
            "flight_iata": f"{v['airline']}{v['number']}",
            "status": flights.status_text(v, s) if s else manual.TEXTS["status_typed"] if typed
            else "Dalla carta d'imbarco",
            "departure": dep, "departure_time": flights.local_time(dep, otz),
            "departure_scheduled_time": flights.local_time(s.get("departure_scheduled"), otz),
            "arrival": arr, "arrival_time": flights.local_time(arr, dtz),
            "delay_min": s.get("arrival_delay") if ph == flights.PHASE_LANDED else s.get("departure_delay"),
            "boarding_time": flights.local_time(boarding, otz), "boarding_estimated": estimated,
            "at_gate_time": flights.local_time(at_gate.isoformat(), otz) if at_gate else None,    # when to be there
            "leave_for_gate_time": flights.local_time(leave.isoformat(), otz) if leave else None,
            "priority": prio,
            "gate": s.get("gate") or v["pass_gate"],
            "terminal": s.get("terminal") or v.get("pass_terminal"), "arrival_gate": s.get("arrival_gate"),
            "arrival_terminal": s.get("arrival_terminal"),
            "baggage_belt": s.get("baggage_belt"),
            "passengers": [(clean(c["first_name"], 30) or "passeggero") + (f" ({c['seat']})" if c["seat"] else "")
                           for c in passes],
            "updated_at": v["updated_at"],
            "source": "aviationstack" if s else "manual" if typed else "boarding_pass"}


def _since(now):
    """The first day of the flights that may not be over yet: the day before yesterday."""
    return (now.astimezone(LOCAL_TZ).date() - datetime.timedelta(days=2)).isoformat()


def _not_over(rows, now):
    """(reference time, flight, status) of the flights among `rows` not over yet, the nearest first."""
    since, out = _since(now), []
    for v in rows:
        if v["date"] < since:
            continue
        s = json.loads(v["status"]) if v["status"] else None
        if not flights.is_over(v, s, now):
            out.append((flights.reference_time(v, s, now), v, s))
    return sorted(out, key=lambda x: (x[0], x[1]["id"]))


def active_flights(now):
    """(reference time, flight, status) of the flights not over yet, the nearest first."""
    return _not_over(q("SELECT * FROM flights WHERE date >= ? ORDER BY date, id", (_since(now),)), now)


def compute_sensors(today=None, now=None):
    """name -> (state, attributes): the next flight (its card, «Nessun volo» without one), the flights not over yet,
    the statistics (flight_stats.py). At most MAX_LIST entries in the attributes, and no PNR, barcode or surnames:
    the attributes end up in the HA recorder."""
    today = today or (_local_now().date() if LOCAL_TZ is not None else datetime.date.today())
    active = active_flights(now or datetime.datetime.now(datetime.timezone.utc))
    cards = [flight_card(v, s) for _, v, s in active[:MAX_LIST]]
    values = {"next_flight": (cards[0]["status"], cards[0]) if cards else ("Nessun volo", {})}
    values["upcoming_flights"] = (len(active), {"list": [
        {k: x[k] for k in ("flight", "route", "date", "departure_time", "status", "gate", "passengers")}
        for x in cards]})
    # never the passenger column (full name): the statistics need only the first name and the cabin
    stats = flight_stats.compute(q("SELECT * FROM flights"), q("SELECT flight_id, first_name, cabin FROM boarding_passes"),
                                 today)
    values["flight_statistics"] = (flight_stats.sensor_state(stats), stats)
    return values


def publish_sensors():
    """Never raises: actions, readings and the clock call it, and a sensor must not stop them."""
    try:
        values = compute_sensors()
        if _sensors is not None:
            for name, (state, attributes) in values.items():
                _sensors.publish(name, state, attributes)
        return values
    except Exception as e:
        log(f"sensors not published: {type(e).__name__}: {str(e)[:160]}")
        return None


_publish_lock = threading.Lock()     # compute and publish in one go: the last values sent are the newest


def publish_now():
    """publish_sensors for this service's callers (the worker, the clock, POST /flight): one at a time, so a
    publish computed before an edit never arrives after the edit's own."""
    with _publish_lock:
        return publish_sensors()


# ---------------------------------------------------------------- reading (thread)
# Exceptions raised while a document is read end up in documents.error and in the «Lettura non riuscita»
# notification: their texts stay Italian.
def aviationstack_key():
    """The config entry's key in Home Assistant (AVIATIONSTACK_KEY, "" = none), else the key file (tests).
    RuntimeError without one."""
    if AVIATIONSTACK_KEY is not None:
        if not AVIATIONSTACK_KEY:
            raise RuntimeError(f"{AVIATIONSTACK_KEY_VAR} missing")
        return AVIATIONSTACK_KEY
    if not AVIATIONSTACK_KEY_FILE:
        raise RuntimeError(f"{AVIATIONSTACK_KEY_VAR} missing")
    return read_var(AVIATIONSTACK_KEY_FILE, AVIATIONSTACK_KEY_VAR)


def read_boarding_pass(doc, path):
    """Wallet boarding pass (.pkpass). A pass that is not an airline boarding pass is archived."""
    with open(path, "rb") as f:
        data = f.read()
    try:
        legs = flights.read_boarding_pass(data, datetime.date.fromisoformat(doc["uploaded_at"][:10]))
    except flights.NotABoardingPass as e:
        update_document(doc["id"], status=DOC_ARCHIVED, kind=KIND_DOCUMENT, error=None, notified=0,
                        outcome=f"E' un pass del Wallet, ma non una carta d'imbarco che so leggere ({e}): l'ho "
                        "archiviato.")
        log(f"#{doc['id']} pkpass archived: {e}")
        return
    save_boarding_pass(doc, legs)


def save_boarding_pass(doc, legs):
    """One flight per leg (the same one for two passengers on the same plane), one boarding pass per passenger. No
    model: the BCBP decides. The PNR is not saved, it stays only in the file."""
    now, new, lines = utc(datetime.datetime.now()), 0, []
    with transaction() as c:
        for t in legs:
            key = (t["airline"], t["number"], t["date"], t["origin"])
            # a pass loaded again (gate or seat changed) updates the flight and has its status checked again at once
            c.execute("INSERT INTO flights (airline, number, date, origin, destination, pass_departure, pass_boarding, "
                      "pass_gate, relevant_date, next_check, created_at, pass_arrival, pass_terminal) "
                      "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?) "
                      "ON CONFLICT (airline, number, date, origin) DO UPDATE SET destination=excluded.destination, "
                      "pass_departure=coalesce(excluded.pass_departure, pass_departure), "
                      "pass_boarding=coalesce(excluded.pass_boarding, pass_boarding), "
                      "pass_gate=coalesce(excluded.pass_gate, pass_gate), "
                      "relevant_date=coalesce(excluded.relevant_date, relevant_date), "
                      "next_check=excluded.next_check, "
                      "pass_arrival=coalesce(excluded.pass_arrival, pass_arrival), "
                      "pass_terminal=coalesce(excluded.pass_terminal, pass_terminal)",
                      (*key, t["destination"], t["pass_departure"], t["pass_boarding"], t["pass_gate"],
                       t["relevant_date"], now, now_iso(), _at_airport(t.get("pass_arrival"), t["destination"]),
                       t.get("terminal")))
            fid = c.execute("SELECT id FROM flights WHERE airline=? AND number=? AND date=? AND origin=?",
                            key).fetchone()[0]
            known = c.execute("SELECT * FROM boarding_passes WHERE flight_id=? AND passenger=?",
                              (fid, t["passenger"])).fetchone()
            seat, cabin = t["seat"], t["cabin"]
            # the passenger typed by hand on this flight, with the pass's full name or (only one) with its first
            # name: the pass takes that row (one row, not two) as a new pass, and the typed seat stays if it has none
            typed = known if known is not None and manual.is_typed(known) else None
            if known is None and (typed := _typed_namesake(c, fid, t["first_name"])):
                c.execute("UPDATE boarding_passes SET passenger=? WHERE id=?", (t["passenger"], typed["id"]))
            if typed is not None:
                seat, cabin = seat or typed["seat"], cabin or typed["cabin"]
            c.execute("INSERT INTO boarding_passes (document_id, flight_id, passenger, first_name, seat, cabin, "
                      "uploaded_by) VALUES (?,?,?,?,?,?,?) ON CONFLICT (flight_id, passenger) DO UPDATE SET "
                      "document_id=excluded.document_id, first_name=excluded.first_name, seat=excluded.seat, "
                      "cabin=excluded.cabin, uploaded_by=excluded.uploaded_by",
                      (doc["id"], fid, t["passenger"], t["first_name"], seat, cabin, doc["uploaded_by"]))
            new += known is None or typed is not None
            when = dd_mm(t["date"]) + (f" alle {flights.local_time(t['pass_departure'])}" if t["pass_departure"] else "")
            lines.append(f"{t['airline']} {t['number']} {t['origin']} → {t['destination']}, {when}"
                         + (f", posto {t['seat']}" if t["seat"] else "")
                         + (f" ({clean(t['first_name'], 30)})" if t["first_name"] else ""))
    outcome = ("" if new else "Carta aggiornata: ") + "; ".join(lines) + "." + tracking_suffix()
    update_document(doc["id"], status=DOC_READ, kind=KIND_BOARDING_PASS,
                    merchant=f"{legs[0]['airline']} {legs[0]['number']}", doc_date=legs[0]["date"], outcome=outcome,
                    notified=0, error=None)
    log(f"#{doc['id']} boarding pass: {len(legs)} legs, {new} new boarding passes")


def _at_airport(iso, code):
    """The instant `iso` written with the offset of the airport's zone (flights.zone_of), the same instant: plan 4.2's
    pass_arrival «with the destination's offset», whatever offset the pass wrote («...Z»), so the card's arrival_time
    is the local time also before AviationStack gives the zone. As it is without an instant or a zone."""
    zone = flights._zone(flights.zone_of(code)) if iso else None
    if zone is None:
        return iso
    try:
        return datetime.datetime.fromisoformat(iso).astimezone(zone).isoformat()
    except (TypeError, ValueError):
        return iso


def _typed_namesake(c, flight_id, first_name):
    """Inside save_boarding_pass's transaction: the one passenger typed by hand on the flight with the pass's first
    name (manual.person_key), None if there is none or more than one, or if the pass has no first name."""
    if not flight_stats.clean_name(first_name):
        return None
    key = manual.person_key(first_name)
    same = [p for p in map(dict, c.execute("SELECT * FROM boarding_passes WHERE flight_id=? ORDER BY id", (flight_id,)))
            if manual.is_typed(p) and manual.person_key(p["first_name"]) == key]
    return same[0] if len(same) == 1 else None


def tracking_suffix():
    """The sentence that ends the outcome of a boarding pass and the reply of a typed flight that is followed: the
    status is followed only with AviationStack enabled and its key readable."""
    try:
        tracked = not NO_FLIGHTS and bool(aviationstack_key())
    except Exception:
        tracked = False
    return manual.text("tracked" if tracked else "untracked")


def check_flights(cfg, now=None, lookup=None):
    """Asks AviationStack for the status of the flights whose check is due, notifies everyone of what changes
    (flights.changes) and sets the next check (flights.next_check). Without a key it does nothing: the flights keep
    the data of the pass, and when the key arrives they catch up on their own. Returns the number of calls made."""
    if NO_FLIGHTS:
        return 0
    now = now or datetime.datetime.now(datetime.timezone.utc)
    due = q("SELECT * FROM flights WHERE next_check IS NOT NULL AND next_check <= ? ORDER BY next_check, id",
            (utc(now),))
    if not due:
        return 0
    try:
        key = aviationstack_key()
    except Exception:
        return 0
    lookup, cap, month, calls = lookup or flights.lookup_flight, int(MAX_API_CALLS_PER_MONTH), now.strftime("%Y-%m"), 0
    for v in due:
        s = json.loads(v["status"]) if v["status"] else None
        if not flights.queryable(v["date"], now):          # neither yesterday nor today: the call would be wasted
            p = flights.next_check(v, s, now)
            update_flight_if(v, next_check=utc(max(p, now + datetime.timedelta(hours=1))) if p else None)
            continue
        used = (q("SELECT calls FROM api_usage WHERE month=?", (month,), one=True) or {"calls": 0})["calls"]
        if used >= cap:
            # Italian, like the other flights.error texts: GET /flights shows them
            update_flight_if(v, error=f"tetto di {cap} chiamate al mese raggiunto",
                             next_check=utc(now + datetime.timedelta(hours=1)))
            log(f"flight {flights.flight_code(v)}: cap of {cap} AviationStack calls reached, retrying in an hour")
            continue
        with _db_lock:
            db().execute("INSERT INTO api_usage (month, calls) VALUES (?, 1) "
                         "ON CONFLICT (month) DO UPDATE SET calls=calls+1", (month,))
        calls += 1
        try:
            new = lookup(key, v["airline"], v["number"], v["date"], v["origin"], now)
        except flights.APIError as e:
            wait = datetime.timedelta(hours=6) if e.code in (401, 403, 429) else datetime.timedelta(minutes=30)
            update_flight_if(v, error=str(e), next_check=utc(now + wait))
            log(f"flight {flights.flight_code(v)}: {e}")
            if e.code == 401 and "access_key" in str(e):     # after the wait is written: a failing hook costs no call
                try:
                    _on_auth_error()
                except Exception as hook_error:
                    log(f"warning: reauth hook: {type(hook_error).__name__}")
            continue
        if not _same_flight(v):                         # edited or deleted by hand while AviationStack answered
            continue
        error = None if new else "AviationStack non conosce (ancora) questo volo"
        new = new or s
        snapshot = v["snapshot"]
        if new:
            before = json.loads(v["snapshot"] or "{}")
            events, after = flights.changes(before, new, v["pass_gate"])
            if not events:
                snapshot = json.dumps(after)               # a gate discovered, no change: no notification
            else:
                title, text = flights.message(v, new, events, before.get("gate") or v["pass_gate"])
                reached = [u["name"] for u in cfg["users"].values()
                           if notify(u, title, text, tag=tag("f", v["id"]))]
                # the baseline of the comparisons changes only with a notification sent: if HA was down, retry later
                if reached or not cfg["users"]:
                    snapshot = json.dumps(after)
                log(f"flight {flights.flight_code(v)}: {', '.join(events)} -> {', '.join(reached) or 'nobody'}")
        p = flights.next_check(v, new, now)
        update_flight_if(v, status=json.dumps(new, ensure_ascii=False) if new else None, updated_at=now_iso(),
                         error=error, snapshot=snapshot, next_check=utc(p) if p else None)
    if calls:
        publish_now()
    return calls


def position(u):
    """{"lat", "lon", "accuracy"} from the GPS of `u`'s phone (person.<name> in HA, or the user's «person» in the
    config), None if there is none. The position stays here: it goes to no external service."""
    if NO_HA:
        return None
    entity = u.get("person") or f"person.{u['name']}"
    st = ha_client().state(entity)
    a = (st or {}).get("attributes") or {}
    if not isinstance(a.get("latitude"), (int, float)) or not isinstance(a.get("longitude"), (int, float)):
        return None
    return {"lat": a["latitude"], "lon": a["longitude"], "accuracy": a.get("gps_accuracy") or 0}


def gate_position(airport, gate, now):
    """(lat, lon) of the gate from OpenStreetMap, cached in gate_positions: a missing gate is looked up again after
    GATE_NOT_FOUND_RETRY, an Overpass error after GATE_ERROR_RETRY. None if unknown."""
    coord = flights.coordinates(airport)
    if not gate or not coord:
        return None
    row = q("SELECT * FROM gate_positions WHERE airport=? AND gate=?", (airport, gate), one=True)
    if row:
        if row["lat"] is not None:
            return row["lat"], row["lon"]
        wait = GATE_ERROR_RETRY if row["error"] else GATE_NOT_FOUND_RETRY
        if now - datetime.datetime.fromisoformat(row["searched_at"]) < wait:
            return None
    try:
        found, error = flights.find_gate(*coord, gate), None
    except flights.APIError as e:
        found, error = None, str(e)
    with _db_lock:
        db().execute("INSERT OR REPLACE INTO gate_positions (airport, gate, lat, lon, searched_at, error) "
                     "VALUES (?,?,?,?,?,?)", (airport, gate, *(found or (None, None)), now.isoformat(), error))
    log(f"gate {gate} of {airport}: " + ("found on OpenStreetMap" if found else error or "not on OpenStreetMap"))
    return found


def notify_gate(cfg, now=None):
    """«Go to the gate» to each user who uploaded the flight's boarding passes, when they need it to be at the gate
    in time: at the start of boarding in first and business, 10 minutes before in economy (flights.at_gate_time).
    The walk is computed from the phone's GPS (flights.walk: at the airport, up to the OpenStreetMap gate);
    without it, GATE_MARGIN applies. No AviationStack call. Once per user. Returns the notifications sent."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    longest = datetime.timedelta(minutes=flights.WALK_MINUTES[1])
    sent = 0
    for _, v, s in active_flights(now):
        snapshot = json.loads(v["snapshot"] or "{}")
        passes = q("SELECT uploaded_by, cabin FROM boarding_passes WHERE flight_id=?", (v["id"],))
        prio = flights.is_priority([c["cabin"] for c in passes])
        # GPS and Overpass only near the time: with the longest possible walk the alert is due from there
        if not flights.gate_alert_due(v, s, {}, now, prio, longest):
            continue
        gate = (s or {}).get("gate") or v["pass_gate"]
        names = {c["uploaded_by"] for c in passes}
        if not _same_flight(v):
            continue
        for u in [u for u in cfg["users"].values() if u["name"] in names]:
            if not flights.gate_alert_due(v, s, snapshot, now, prio, longest, u["name"]):
                continue
            try:
                p = position(u)
                minutes, details = flights.walk(p, flights.coordinates(v["origin"]),
                                                gate_position(v["origin"], gate, now) if p else None)
            except Exception as e:                  # HA or Overpass down: the fixed margin applies
                log(f"flight {flights.flight_code(v)}: walk not computed ({type(e).__name__}: {str(e)[:120]})")
                minutes, details = None, {}
            if not _same_flight(v):                 # edited or deleted by hand while HA or Overpass answered
                break
            margin = datetime.timedelta(minutes=minutes) if minutes else flights.GATE_MARGIN
            snapshot.setdefault("walk", {})[u["name"]] = {**details, "margin_min": int(margin.total_seconds() // 60)}
            if flights.gate_alert_due(v, s, snapshot, now, prio, margin, u["name"]):
                title, text = flights.gate_message(v, s, prio, details)
                if notify(u, title, text, tag=tag("f", v["id"])):
                    snapshot["gate_sent_to"] = (snapshot.get("gate_sent_to") or []) + [u["name"]]
                    snapshot["gate_sent"] = True
                    if gate:
                        snapshot["gate"] = gate
                    sent += 1
                    log(f"flight {flights.flight_code(v)}: go to the gate -> {u['name']} "
                        f"(walk {margin.seconds // 60} min)")
        update_flight_if(v, snapshot=json.dumps({**flights.base_snapshot(v["pass_gate"]), **snapshot}))
    return sent


_fr24_attempts = {}                        # flight -> last attempt to add it (the FR24 lookup is costly)
_fr24_forget = set()                       # idents to stop tracking after a delete or a date change (track_fr24)
_fr24_lock = threading.Lock()


def fr24_forget(v):
    """After POST /flight deleted `v` or changed its date: the next track_fr24 removes its ident from the tracked
    flights, unless another flight with that ident is in its window. No HA call here (the request thread)."""
    ident = f"{v['airline']}{v['number']}"
    with _fr24_lock:
        _fr24_forget.add(ident)
        _fr24_attempts.pop(ident, None)       # the new date is added promptly


def _fr24_window(v, now):
    """From FR24_LEAD before departure until the flight is over: the time it belongs among the tracked flights."""
    s = json.loads(v["status"]) if v["status"] else None
    return not flights.is_over(v, s, now) and now >= flights.reference_time(v, s, now) - FR24_LEAD


def track_fr24(now=None):
    """The flights from FR24_LEAD before departure to their end are among Flightradar24's «tracked» flights in HA;
    those over are removed. FR24 keeps that list in memory and loses it at every HA restart: it is put back from
    here. A flight FR24 does not find (schedule not published yet) is retried every FR24_RETRY. No effect if the
    integration is missing. Returns (added, removed)."""
    if NO_HA or NO_FLIGHTS:
        return 0, 0
    now = now or datetime.datetime.now(datetime.timezone.utc)
    st = ha_client().state(FR24_TRACKED)
    if st is None:
        return 0, 0
    tracked = {f.get("flight_number") for f in (st.get("attributes") or {}).get("flights") or [] if isinstance(f, dict)}
    added = removed = done = 0
    # the idents to forget are taken before the rows are read: one forgotten meanwhile (an edit committed while
    # this runs) waits for the next run, which reads the rows after that edit
    with _fr24_lock:
        forget = sorted(_fr24_forget)
        _fr24_forget.difference_update(forget)
    try:
        rows = q("SELECT * FROM flights WHERE date >= ?", ((now.date() - datetime.timedelta(days=2)).isoformat(),))
        for ident in forget:
            if ident in tracked and not any(f"{v['airline']}{v['number']}" == ident and _fr24_window(v, now)
                                            for v in rows):
                ha_client().service("text", "set_value", {"entity_id": FR24_REMOVE, "value": ident})
                tracked.discard(ident)
                removed += 1
                log(f"flight {ident}: edited or deleted by hand, removed from the Flightradar24 tracked flights")
            done += 1
    except Exception:
        with _fr24_lock:                    # an HA error: this ident and the ones after it wait for the next run
            _fr24_forget.update(forget[done:])
        raise
    for v in rows:
        s = json.loads(v["status"]) if v["status"] else None
        ident, over = f"{v['airline']}{v['number']}", flights.is_over(v, s, now)
        if not over and now >= flights.reference_time(v, s, now) - FR24_LEAD and ident not in tracked:
            if now - _fr24_attempts.get(ident, now - FR24_RETRY) >= FR24_RETRY:
                _fr24_attempts[ident] = now
                ha_client().service("text", "set_value", {"entity_id": FR24_ADD, "value": ident})
                added += 1
                log(f"flight {flights.flight_code(v)}: asked Flightradar24 to track it")
        elif over and ident in tracked:
            ha_client().service("text", "set_value", {"entity_id": FR24_REMOVE, "value": ident})
            removed += 1
            log(f"flight {flights.flight_code(v)}: over, removed from the Flightradar24 tracked flights")
    return added, removed


def reschedule_checks(now=None):
    """At startup: the checks already set are computed again with the current rules (flights.next_check). Not
    those of flights never checked, which wait for their first call."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    for v in q("SELECT * FROM flights WHERE next_check IS NOT NULL AND updated_at IS NOT NULL"):
        p = flights.next_check(v, json.loads(v["status"]) if v["status"] else None, now)
        update_flight(v["id"], next_check=utc(p) if p else None)


def read_document(doc):
    """Routes by type, with fixed rules and no model: a Wallet pass from its pass.json, a photo or a PDF from its
    BCBP barcode. A photo or a PDF without one is archived."""
    path = os.path.join(DATA_DIR, doc["file"])
    if doc["file_type"] == "pkpass":
        return read_boarding_pass(doc, path)
    # a photo over MAX_PIXELS raises ValueError from its header: work_one records it, and after MAX_ATTEMPTS the
    # document is in error
    legs = flights.boarding_pass_from_barcodes(barcodes.read_barcodes(path, MAX_PIXELS),
                                               datetime.date.fromisoformat(doc["uploaded_at"][:10]))
    if legs:
        return save_boarding_pass(doc, legs)
    update_document(doc["id"], status=DOC_ARCHIVED, kind=KIND_DOCUMENT, notified=0, error=None,
                    outcome="Non trovo il codice a barre di una carta d'imbarco: l'ho archiviato.")
    log(f"#{doc['id']} archived: no boarding pass barcode")


def work_one(cfg):
    """One round of the worker: the pending notifications, then the first queued document. Seconds to wait before
    the next round, None if the queue is empty. While the decoder does not answer, the photos and PDFs wait in the
    queue with their attempt given back (DECODER_PAUSES between tries, the Wallet passes read meanwhile); one queued
    for more than DECODER_GIVE_UP goes in error."""
    global _decoder_pause_until, _decoder_pause_index
    send_pending_notifications(cfg)
    paused = _decoder_pause_until > time.monotonic()
    doc = q("SELECT * FROM documents WHERE status='in_coda'" + (" AND file_type='pkpass'" if paused else "")
            + " ORDER BY id LIMIT 1", one=True)
    if not doc:
        return None
    n = doc["attempts"] + 1
    if n > MAX_ATTEMPTS:                           # the earlier readings hung or crashed the engine
        update_document(doc["id"], status=DOC_ERROR,
                        error=doc["error"] or "la lettura si e' interrotta (blocco o riavvio)")
        log(f"#{doc['id']}: {MAX_ATTEMPTS} readings interrupted, not reading it again")
        return 0
    update_document(doc["id"], attempts=n)    # before reading: one that crashes the process does not stay queued forever
    try:
        read_document(doc)
        if doc["file_type"] != "pkpass":
            _decoder_pause_index = 0
        publish_now()
        return 0
    except Closed:
        raise
    except barcodes.DecoderUnavailable as e:
        wait = DECODER_PAUSES[min(_decoder_pause_index, len(DECODER_PAUSES) - 1)]
        _decoder_pause_index += 1
        _decoder_pause_until = time.monotonic() + wait
        fields = {"attempts": doc["attempts"]}       # not the document's fault: the attempt is given back
        try:
            uploaded = datetime.datetime.fromisoformat(doc["uploaded_at"])
            late = uploaded.tzinfo is not None and datetime.datetime.now(datetime.timezone.utc) - uploaded > DECODER_GIVE_UP
        except ValueError:
            late = False
        if late:
            fields = {"status": DOC_ERROR, "error": "il decodificatore delle foto non risponde da 24 ore"}
        update_document(doc["id"], **fields)
        log(f"#{doc['id']}: decoder unavailable ({str(e)[:80] or type(e).__name__}), "
            + (f"in error after {DECODER_GIVE_UP.days * 24} hours" if late else f"retrying in {wait} s"))
        return 0
    except Exception as e:
        failed = n >= MAX_ATTEMPTS
        update_document(doc["id"], error=f"{type(e).__name__}: {str(e)[:200]}",
                        **({"status": DOC_ERROR} if failed else {}))
        log(f"#{doc['id']} attempt {n} failed: {type(e).__name__}: {str(e)[:200]}")
        return 5 if failed else 30


class ClockState:
    """What the clock rounds remember between them: when the sensors, Flightradar24 and the decoder's /health were
    last done (time.time()), and when the clock started."""

    def __init__(self):
        self.last = self.last_fr24 = self.last_probe = 0.0
        self.started = time.time()


def queued_counts():
    """{"queued": documents in the queue, "queued_photos": those of them that need the decoder}."""
    r = q("SELECT count(*) AS n, coalesce(sum(file_type <> 'pkpass'), 0) AS photos FROM documents "
          "WHERE status='in_coda'", one=True)
    return {"queued": r["n"], "queued_photos": r["photos"]}


def clock_round(cfg, st, now=None):
    """One round of the clock, every minute: the flights to check again and «go to the gate»; every 5 minutes
    Flightradar24's tracked flights; at the first round and then every 10 minutes the sensors and, with a decoder,
    its /health. `st`: a ClockState; `now`: an aware instant, passed on to the jobs (None: the real time). An error
    in the flights or the gate skips the rest of the round (logged); Flightradar24 has its own. Returns the report
    the integration turns into its repair issue: {"queued_photos", "decoder_status", "decoder_down_since"}."""
    t = now.timestamp() if now is not None else time.time()
    at = {} if now is None else {"now": now}
    try:
        check_flights(cfg, **at)
        notify_gate(cfg, **at)
        if t - st.last_fr24 >= 300:
            st.last_fr24 = t
            try:                                # FR24 down must not skip the rest of the round
                track_fr24(**at)
            except Closed:
                raise
            except Exception as e:
                log(f"Flightradar24: {type(e).__name__}: {str(e)[:120]}")
        if t - st.last >= EVERY_S:
            st.last = t
            publish_now()
    except Closed:
        raise
    except Exception as e:
        log(f"clock: {type(e).__name__}: {e}")
    if barcodes.DECODER_URL and t - st.last_probe >= EVERY_S:
        st.last_probe = t
        barcodes.probe()
    try:
        photos = queued_counts()["queued_photos"]
    except Closed:
        raise
    except Exception as e:
        log(f"clock: queue not counted: {type(e).__name__}")
        photos = 0
    return {"queued_photos": photos, "decoder_status": barcodes.status(), "decoder_down_since": barcodes.down_since()}


# ---------------------------------------------------------------- flights added, edited and deleted by hand
# GET /choices lists the flights, POST /flight adds, edits or deletes one (manual.py: the rules and the texts). A
# typed future flight is the row a photo upload leaves (no times, next_check now): the clock checks, notifies and
# tracks it as any other; a past one only counts for the statistics. A change is one transaction, which uses only
# its own connection (_db_lock is not reentrant); after the commit the files no flight uses any more are removed,
# Flightradar24 is told to forget the old flight (track_fr24), the sensors are published again and only then the
# Shortcut gets its reply. The log has user names and flight codes, never passenger names.
def flight_choices(now=None):
    """The body of GET /choices without "user": first the flights not over yet, the nearest first, then all the
    others, the newest first, at most manual.MAX_CHOICES; for each label its ref, date and editable passengers
    line. Read in one transaction, so every ref matches the rows it was computed from."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    with transaction() as c:
        rows = [dict(r) for r in c.execute("SELECT * FROM flights ORDER BY date DESC, id DESC")]
        passes = [dict(r) for r in c.execute("SELECT * FROM boarding_passes ORDER BY id")]
    of = {}
    for p in passes:
        of.setdefault(p["flight_id"], []).append(p)
    first = [v for _, v, _ in _not_over(rows, now)]
    ids = {v["id"] for v in first}
    choices, details = [], {}
    for v in (first + [v for v in rows if v["id"] not in ids])[:manual.MAX_CHOICES]:
        label = manual.choice_label(v, of.get(v["id"], []))
        if label in details:                        # never with the table's cities: a code-only airport, perhaps
            label += f" · #{v['id']}"
        choices.append(label)
        details[label] = {"ref": manual.ref(v, of.get(v["id"], [])), "date": v["date"],
                          "passengers": manual.passengers_text(of.get(v["id"], [])),
                          **manual.choice_fields(v, of.get(v["id"], []))}
    return {"message": manual.text("choose" if choices else "no_flights"), "choices": choices, "details": details}


def change_flight(req_body, user, cfg, now=None):
    """POST /flight: (HTTP code, reply). `req_body`: the request's bytes; `user`: the configured user who sent it.
    A refused request writes nothing; an unexpected error is rolled back by transaction() and answered with 500."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    today = now.astimezone(LOCAL_TZ).date()         # the local day (HA's zone), as the dates people type
    try:
        req = manual.parse_request(req_body, today)
        # the key file is read outside the transaction, as after an upload
        follow = tracking_suffix() if req["action"] != "delete" else None
        with transaction() as c:
            done = {"add": _add, "edit": _edit, "delete": _delete}[req["action"]](c, req, user, cfg, now, today,
                                                                                  follow)
        _remove_files(done["files"])
        for v in done["forget"]:
            fr24_forget(v)
        if done["result"] != "unchanged":
            publish_now()
            log(done["audit"])
        return 200, {"id": done["id"], "result": done["result"], "message": done["message"]}
    except manual.InputError as e:
        return e.code, {"message": str(e)}
    except Closed:
        raise
    except Exception as e:
        log(f"flight request failed: {type(e).__name__}")
        return 500, {"message": manual.text("internal_error")}


def _flight(c, flight_id):
    """(flight, its boarding passes by id) inside a change's transaction; 404 not_found if it is gone."""
    v = c.execute("SELECT * FROM flights WHERE id=?", (flight_id,)).fetchone()
    if v is None:
        raise manual.InputError("not_found", 404)
    return dict(v), [dict(p) for p in c.execute("SELECT * FROM boarding_passes WHERE flight_id=? ORDER BY id",
                                                (flight_id,))]


def _passengers(c, flight_id):
    return c.execute("SELECT count(*) FROM boarding_passes WHERE flight_id=?", (flight_id,)).fetchone()[0]


def _insert_typed(c, flight_id, name, seat, owner):
    """A passenger typed by hand: no document, the name in passenger and first_name. Meeting a pass with that full
    name and no first name (an unnamed pass), it names it instead of failing. A passenger with that name and a
    first name is never renamed: 400 same_name (manual.plan_passengers gives such a row its item, by the full
    name, so only an odd line gets here)."""
    if not c.execute("INSERT INTO boarding_passes (document_id, flight_id, passenger, first_name, seat, cabin, "
                     "uploaded_by) VALUES (NULL,?,?,?,?,NULL,?) ON CONFLICT (flight_id, passenger) DO UPDATE SET "
                     "first_name=excluded.first_name, seat=coalesce(excluded.seat, boarding_passes.seat) "
                     "WHERE boarding_passes.first_name IS NULL", (flight_id, name, name, seat, owner)).rowcount:
        raise manual.InputError("same_name")


def _apply_plan(c, flight_id, plan, user, cfg):
    """A manual.Plan on the flight's boarding passes: deletions first, then renames, seats and the new typed rows
    (their owner: manual.owner_of). A renamed pass row keeps its document, passenger, cabin and owner and gets the
    new first name; a renamed typed row stays typed (both names) with the owner of its new name. Returns the files of the documents no flight uses any more (_documents_to_drop)."""
    docs = []
    for row_id in plan.deletes:
        r = c.execute("SELECT document_id FROM boarding_passes WHERE id=?", (row_id,)).fetchone()
        if r["document_id"] is not None:
            docs.append(r["document_id"])
        c.execute("DELETE FROM boarding_passes WHERE id=?", (row_id,))
    users = [u["name"] for u in cfg["users"].values()]
    for row_id, name in plan.renames:
        r = c.execute("SELECT * FROM boarding_passes WHERE id=?", (row_id,)).fetchone()
        try:
            if manual.is_typed(r):
                c.execute("UPDATE boarding_passes SET passenger=?, first_name=?, uploaded_by=? WHERE id=?",
                          (name, name, manual.owner_of(name, users, user["name"]), row_id))
            else:
                c.execute("UPDATE boarding_passes SET first_name=? WHERE id=?", (name, row_id))
        except sqlite3.IntegrityError:                # another row of the flight has that full name
            raise manual.InputError("same_name") from None
    for row_id, seat in plan.seats:
        c.execute("UPDATE boarding_passes SET seat=? WHERE id=?", (seat, row_id))
    for name, seat in plan.inserts:
        _insert_typed(c, flight_id, name, seat, manual.owner_of(name, users, user["name"]))
    return _documents_to_drop(c, docs)


def _documents_to_drop(c, doc_ids, flight=None):
    """Deletes, inside the change's transaction, the documents that serve no flight any more, and returns their
    files (unlinked after the commit: _remove_files). Candidates: `doc_ids` (those of the boarding passes removed)
    and, for a deleted `flight`, the boarding pass documents labelled as that flight (older versions of its pass).
    Only a boarding pass document, not queued, that no boarding pass row points to any more is deleted: the other
    leg of a two-leg pass keeps it."""
    ids = set(doc_ids)
    if flight is not None:
        ids.update(r[0] for r in c.execute("SELECT id FROM documents WHERE kind=? AND merchant=? AND doc_date=?",
                                           (KIND_BOARDING_PASS, flights.flight_code(flight), flight["date"])))
    files = []
    for doc_id in sorted(ids):
        r = c.execute("SELECT file FROM documents WHERE id=? AND kind=? AND status<>? AND NOT EXISTS "
                      "(SELECT 1 FROM boarding_passes WHERE document_id=documents.id)",
                      (doc_id, KIND_BOARDING_PASS, DOC_QUEUED)).fetchone()
        if r is not None:
            c.execute("DELETE FROM documents WHERE id=?", (doc_id,))
            files.append(r["file"])
    return files


def _remove_files(files):
    """After the commit: the files of the deleted documents, only inside DATA_DIR and only while no document row
    has that file (the same bytes sent again meanwhile are saved at the same path). A missing file is fine. The
    document's own entry is removed, never what a link points to: a link (the service makes none) is left."""
    base = os.path.realpath(DATA_DIR)
    for rel in files:
        try:
            entry = os.path.join(DATA_DIR, rel)
            folder = os.path.realpath(os.path.dirname(entry))
            if os.path.realpath(entry) == base or os.path.commonpath([base, folder]) != base:
                log("file of a deleted document outside the data folder: not removed")
            elif os.path.islink(entry):
                log("file of a deleted document is a link: not removed")
            elif not q("SELECT 1 FROM documents WHERE file=?", (rel,), one=True):
                os.remove(entry)
        except FileNotFoundError:
            pass
        except (OSError, ValueError) as e:
            log(f"file of a deleted document not removed: {type(e).__name__}")


def _done(v, result, message, user, n=0, changes=(), files=(), forget=()):
    """The outcome of _add, _edit or _delete, with its audit line (no passenger names)."""
    audit = (f"flight #{v['id']} {result} by {user['name']}: {flights.flight_code(v)} {v['date']} "
             f"{v['origin']}-{v['destination']}" + (f" ({', '.join(changes)})" if changes else "")
             + f", {n} passenger{'' if n == 1 else 's'}" + (f", documents removed {len(files)}" if files else ""))
    return {"id": v["id"], "result": result, "message": message, "audit": audit, "files": list(files),
            "forget": list(forget)}


def _counts(plan):
    return [f"{what} {k}" for what, k in (("renamed", len(plan.renames)), ("seats", len(plan.seats)),
                                           ("added", len(plan.inserts)), ("removed", len(plan.deletes))) if k]


def _unchanged(v, user):
    return _done(v, "unchanged", manual.text("unchanged", code=flights.flight_code(v), day=manual.day_text(v["date"])),
                 user)


def _suffix(day, today, follow):
    return follow if manual.tracked(day, today) else manual.text("past")


def _add(c, req, user, cfg, now, today, follow):
    """A new flight with its typed passengers; on a flight already there (same airline, number, date and origin)
    the typed passengers are added to it (manual.plan_passengers, nobody removed) and the flight row, with the
    data of its passes, is left as it is: the typed times, terminal and gate (stage B) fill only its empty columns
    (manual.plan_fields), the classes typed are set (manual.plan_cabins)."""
    key = (req["airline"], req["number"], req["date"], req["origin"])
    v = c.execute("SELECT * FROM flights WHERE airline=? AND number=? AND date=? AND origin=?", key).fetchone()
    if v is None:
        plan = manual.plan_passengers([], req["passengers"], replace=False)     # «passeggero» matches no row: 400
        own = manual.plan_fields(req, None, req["date"])
        fid = c.execute("INSERT INTO flights (airline, number, date, origin, destination, pass_departure, "
                        "pass_arrival, pass_terminal, pass_gate, next_check, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        (*key, req["destination"], *(own.values[k] for k in manual.OWN),
                         utc(now) if manual.tracked(req["date"], today) else None, now_iso())).lastrowid
        _apply_plan(c, fid, plan, user, cfg)
        _apply_cabins(c, fid, req, ())
        v, n = dict(c.execute("SELECT * FROM flights WHERE id=?", (fid,)).fetchone()), _passengers(c, fid)
        return _done(v, "added", manual.text("added", code=flights.flight_code(v), day=manual.day_text(v["date"]),
                                             route=flights.route(v), details=manual.added_details(own.values),
                                             n=manual.count_text(n), suffix=_suffix(v["date"], today, follow)),
                     user, n, _own_counts(own, 0))
    v = dict(v)
    if v["destination"] != req["destination"]:
        raise manual.InputError("other_destination", 409, code=flights.flight_code(v), day=manual.day_text(v["date"]),
                                origin=flights.city(v["origin"]), destination=flights.city(v["destination"]))
    _, passes = _flight(c, v["id"])
    req = manual.keep_names(passes, req)                   # «Maria Prima» typed for Maria Prima: no class
    plan = manual.plan_passengers(passes, req["passengers"], replace=False)
    own = manual.plan_fields(req, v, v["date"], merge=True)          # only the empty columns get filled
    columns = {k: x for k, x in own.values.items() if x != v[k]}
    if not plan and not columns and not manual.plan_cabins(passes, req["passengers"], req["cabins"]):
        return _unchanged(v, user)
    _apply_plan(c, v["id"], plan, user, cfg)
    _set_columns(c, v, columns, now)
    cabins = _apply_cabins(c, v["id"], req, passes)
    n = _passengers(c, v["id"])
    changes = manual.changes_text(None, len(plan.seats), len(plan.inserts), 0, len(plan.renames),
                                  len(plan.renames), cabins=cabins, fields=own.parts)   # only unnamed passes renamed
    return _done(v, "merged", manual.text("merged", code=flights.flight_code(v), day=manual.day_text(v["date"]),
                                          changes=changes, n=manual.count_text(n),
                                          suffix=_suffix(v["date"], today, follow)), user, n,
                 _counts(plan) + _own_counts(own, cabins))


def _edit(c, req, user, cfg, now, today, follow):
    """A new date and/or the whole new list of passengers of the flight `req["ref"]` names, and (stage B) its
    times, terminal, gate and classes (manual.plan_fields, plan_cabins). A stale ref is 409, unless what the request
    asks is already there (a request sent again after a timeout): 200 unchanged."""
    fid, version = req["ref"]
    v, passes = _flight(c, fid)
    req = manual.keep_names(passes, req)              # the line of GET /choices reads back unchanged («Maria Prima»)
    if version != manual.version(v, passes):
        try:
            same = req["date"] in (None, v["date"]) and (
                req["passengers"] is None or not manual.plan_passengers(passes, req["passengers"], replace=True))
            same = same and not any(x != v[k] for k, x in manual.plan_fields(req, v, v["date"]).values.items()) \
                and not manual.plan_cabins(passes, req["passengers"], req["cabins"])
        except manual.InputError:
            same = False
        if not same:
            raise manual.InputError("changed", 409)
        return _unchanged(v, user)
    plan = manual.Plan()
    if req["passengers"] is not None:
        plan = manual.plan_passengers(passes, req["passengers"], replace=True)
    new_date = req["date"] if req["date"] not in (None, v["date"]) else None
    if new_date and c.execute("SELECT 1 FROM flights WHERE airline=? AND number=? AND date=? AND origin=?",
                              (v["airline"], v["number"], new_date, v["origin"])).fetchone():
        raise manual.InputError("date_taken", 409, code=flights.flight_code(v), day=manual.day_text(new_date),
                                origin=flights.city(v["origin"]))
    own = manual.plan_fields(req, v, new_date or v["date"])
    columns = {k: x for k, x in own.values.items() if x != v[k]}
    if not new_date and not plan and not columns \
            and not manual.plan_cabins(passes, req["passengers"], req["cabins"]):
        return _unchanged(v, user)
    if new_date:
        # everything of the old day goes: the clock checks the flight again from scratch (updated_at NULL keeps
        # reschedule_checks off it until then), and the gate reminder may go out again for the new day; the typed
        # times move to the same local times of the new day (manual.plan_fields), unless the request sends others
        c.execute("UPDATE flights SET date=?, status=NULL, pass_departure=?, pass_arrival=?, pass_boarding=NULL, "
                  "pass_gate=?, pass_terminal=?, relevant_date=NULL, snapshot='{}', error=NULL, updated_at=NULL, "
                  "next_check=? WHERE id=?",
                  (new_date, own.values["pass_departure"], own.values["pass_arrival"], own.values["pass_gate"],
                   own.values["pass_terminal"], utc(now) if manual.tracked(new_date, today) else None, fid))
    else:
        _set_columns(c, v, columns, now)
    files = _apply_plan(c, fid, plan, user, cfg)
    cabins = _apply_cabins(c, fid, req, passes)
    day, n = new_date or v["date"], _passengers(c, fid)
    old = {p["id"]: p for p in passes}
    changes = manual.changes_text(v["date"] if new_date else None, len(plan.seats), len(plan.inserts),
                                  len(plan.deletes), len(plan.renames),
                                  sum(old[i]["document_id"] is not None for i, _ in plan.renames), len(files),
                                  cabins=cabins, fields=own.parts, moved=own.moved)
    message = manual.text("edited", code=flights.flight_code(v), day=manual.day_text(day), changes=changes,
                          n=manual.count_text(n), suffix=_suffix(day, today, follow))
    return _done({**v, "date": day}, "edited", message, user, n,
                 ([f"date from {v['date']}"] + (["times moved"] if own.moved else []) if new_date else [])
                 + _counts(plan) + _own_counts(own, cabins), files, [v] if new_date else [])


def _set_columns(c, v, columns, now):
    """The flight's own columns that change (manual.plan_fields), inside the change's transaction. When the
    departure changes on a flight already checked and still followed, its next check follows the new departure
    (flights.next_check, as reschedule_checks does): no AviationStack call here."""
    if not columns:
        return
    if "pass_departure" in columns and v["next_check"] is not None and v["updated_at"] is not None:
        p = flights.next_check({**v, **columns}, json.loads(v["status"]) if v["status"] else None, now)
        columns = {**columns, "next_check": utc(p) if p else None}
    c.execute(f"UPDATE flights SET {', '.join(f'{k}=?' for k in columns)} WHERE id=?", (*columns.values(), v["id"]))


def _apply_cabins(c, flight_id, req, passes):
    """The classes typed in the request (manual.plan_cabins on the rows after the passengers' plan), inside the
    change's transaction. Returns how many of the rows in `passes` (those before the request) changed class."""
    if not req.get("cabins") or not any(req["cabins"]):
        return 0
    _, rows = _flight(c, flight_id)
    before = {p["id"] for p in passes}
    changed = manual.plan_cabins(rows, req["passengers"], req["cabins"])
    for row_id, letter in changed:
        c.execute("UPDATE boarding_passes SET cabin=? WHERE id=?", (letter, row_id))
    return sum(row_id in before for row_id, _ in changed)


def _own_counts(own, cabins):
    """The audit's words for what stage B changed: no value, never a name."""
    return [part.removesuffix("_changed") for part in own.parts] + ([f"cabins {cabins}"] if cabins else [])


def _delete(c, req, user, cfg, now, today, follow):
    """The flight `req["ref"]` names, its boarding passes and the documents that served only them (hard delete)."""
    fid, version = req["ref"]
    v, passes = _flight(c, fid)
    if version != manual.version(v, passes):
        raise manual.InputError("changed", 409)
    c.execute("DELETE FROM boarding_passes WHERE flight_id=?", (fid,))
    c.execute("DELETE FROM flights WHERE id=?", (fid,))
    files = _documents_to_drop(c, [p["document_id"] for p in passes if p["document_id"] is not None], v)
    message = manual.text("deleted", code=flights.flight_code(v), day=manual.day_text(v["date"]),
                          route=flights.route(v))
    return _done(v, "deleted", message, user, len(passes), files=files, forget=[v])


# ---------------------------------------------------------------- HTTP (api.py routes the requests)
# The JSON keys and codes (English) and the «message» texts (Italian) are a contract with the iOS Shortcuts «Manda
# volo», «Aggiungi volo» and «Modifica volo», and with people
def save_upload(data, file_type, user):
    sha = hashlib.sha256(data).hexdigest()
    old = q("SELECT id, status, uploaded_at FROM documents WHERE sha256=?", (sha,), one=True)
    if old and old["status"] == DOC_ERROR:
        # a pkpass or a PDF has the same bytes on every send: sending it again is the only way to retry
        update_document(old["id"], status=DOC_QUEUED, attempts=0, error=None, outcome=None, notified=0)
        log(f"#{old['id']} sent again after an error: queued again")
        _wakeup.set()
        return old["id"], DOC_QUEUED, "Ricevuto: lo rileggo e ti mando una notifica."
    if old:
        return old["id"], old["status"], f"Questo l'avevi gia' mandato il {dd_mm(old['uploaded_at'][:10])}."
    now = _local_now()
    rel = f"file/{now:%Y}/{now:%m}/{sha[:16]}.{file_type}"
    path = os.path.join(DATA_DIR, rel)
    _private_dirs(os.path.dirname(path))
    # 0600 whatever the process umask (Home Assistant's is not 077, and it is not changed here)
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "wb") as f:
        f.write(data)
    with _db_lock:
        cur = db().execute("INSERT INTO documents (sha256, uploaded_by, uploaded_at, file, file_type, status) "
                           "VALUES (?,?,?,?,?, 'in_coda')", (sha, user["name"], now_iso(), rel, file_type))
    _wakeup.set()
    return cur.lastrowid, DOC_QUEUED, "Ricevuto: lo leggo e ti mando una notifica."


# GET /flights: the columns of a flights row it shows, then its status as a sentence
_FLIGHTS_JSON = ("id", "airline", "number", "date", "origin", "destination", "updated_at", "next_check", "error")


def document_json(r):
    """A row of GET /documents, with the stored codes as the English ones (NULL stays null)."""
    return {**r, "status": _code(_STATUS_CODES, r["status"]), "kind": _code(_KIND_CODES, r["kind"])}


def flight_json(v, passes=()):
    """A row of GET /flights: the flight, and its status for people (Italian), as the sensor shows it. `passes`:
    its boarding passes (document_id, passenger, first_name), which tell a flight typed by hand."""
    return {**{k: v[k] for k in _FLIGHTS_JSON},
            "status": flights.status_text(v, json.loads(v["status"])) if v["status"]
            else manual.TEXTS["status_typed"] if manual.is_typed_flight(passes) else "Dalla carta d'imbarco"}


def _passes_of(flight_ids):
    """flight id -> its boarding passes (document_id, passenger, first_name), with one query."""
    of = {}
    if flight_ids:
        for p in q(f"SELECT flight_id, document_id, passenger, first_name FROM boarding_passes WHERE flight_id IN "
                   f"({','.join('?' * len(flight_ids))}) ORDER BY id", tuple(flight_ids)):
            of.setdefault(p["flight_id"], []).append(p)
    return of


def _private_dirs(path):
    """makedirs with every folder it creates 0700 (os.makedirs gives the mode to the last one only)."""
    missing = []
    while path and not os.path.isdir(path):
        missing.append(path)
        path = os.path.dirname(path)
    for p in reversed(missing):
        try:
            os.mkdir(p, 0o700)
        except FileExistsError:
            pass


# ---------------------------------------------------------------- the executor jobs and the lifecycle (E13)
def job(fn, *args):
    """Runs fn(*args) as an executor job of Home Assistant (Runtime.run): Closed once shutdown() began (except for
    the calls that start the engine again or follow a backup), and every db() of a job that outlives a shutdown
    raises Closed, so it never writes into the next configuration's database."""
    global _active
    with _jobs:
        if _closing and fn not in (open_and_prepare, backup_begin, backup_end):
            raise Closed("the engine is shut down")
        _active += 1
        gen = _generation
    _job_local.generation = gen
    try:
        return fn(*args)
    finally:
        _job_local.generation = None
        with _jobs:
            _active -= 1
            _jobs.notify_all()


def shutdown(timeout):
    """Unload and HA stop: new jobs are refused, the running ones get up to `timeout` seconds, then the database is
    closed under _db_lock (while a backup runs, without the checkpoint that would empty the WAL the backup is
    copying). An executor job cannot be cancelled: one still running gets Closed at its next db()."""
    global _closing, _closed, _generation, _db
    with _jobs:
        _closing = True
        deadline = time.monotonic() + timeout
        while _active and (left := deadline - time.monotonic()) > 0:
            _jobs.wait(left)
        if _active:
            log(f"warning: {_active} jobs still running after {timeout} s: their database calls are refused")
        _generation += 1
    with _db_lock:
        _close_db()
        _closed = True


def _close_db():
    """Closes the connection, if open (the caller holds _db_lock): while a backup runs, without the checkpoint that
    would empty the WAL the backup is copying (C12). Used by shutdown() and configure()."""
    global _db
    if _db is None:
        return
    if _backup_active and hasattr(sqlite3, "SQLITE_DBCONFIG_NO_CKPT_ON_CLOSE"):
        try:
            _db.setconfig(sqlite3.SQLITE_DBCONFIG_NO_CKPT_ON_CLOSE, True)
        except (sqlite3.Error, AttributeError) as e:
            log(f"warning: database closed with a checkpoint: {type(e).__name__}")
    try:
        _db.close()
    except sqlite3.Error as e:
        log(f"warning: database not closed: {type(e).__name__}")
    _db = None


def open_and_prepare(settings, reschedule):
    """The setup of the config entry (in the executor): configure(settings), the data folder (0700), the database
    (DatabaseRefused or OSError here; nothing written on a refusal; opened, and upgraded, under _db_lock, so a
    backup that begins meanwhile waits and then applies its checkpoint rules to the new connection: C12),
    airports.json and the time zones read once, and, when `reschedule`, the checks computed again
    (reschedule_checks: once per HA run, and after a new key). Returns {"schema": the user_version, "counts": rows
    of the main tables and the queue}."""
    configure(settings)
    _private_dirs(DATA_DIR)
    _private(DATA_DIR, folder=True)                  # a folder made earlier, by hand or by a copy, may be 0755
    with _db_lock:                                   # a backup_begin meanwhile waits, then holds the new connection
        c = db()
    flights.prepare()
    if reschedule:
        reschedule_checks()
    with _db_lock:
        schema = c.execute("PRAGMA user_version").fetchone()[0]
        counts = {t: c.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
                  for t in ("flights", "boarding_passes", "documents")}
    return {"schema": schema, "counts": {**counts, **queued_counts()}}


def backup_begin():
    """A backup of Home Assistant starts: the WAL is folded into tracker.db and then left alone (no automatic
    checkpoint) until backup_end, so the files the backup copies stay consistent. Writes go on. Never raises."""
    global _backup_active
    _backup_active = True
    try:
        with _db_lock:
            if _db is not None:
                _db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                _db.execute("PRAGMA wal_autocheckpoint=0")
    except Exception as e:
        log(f"warning: backup start: {type(e).__name__}: {str(e)[:120]}")


def backup_end():
    """The backup ended (or its safety timer fired): automatic checkpoints again. Never raises."""
    global _backup_active
    try:
        with _db_lock:
            if _db is not None:
                _db.execute("PRAGMA wal_autocheckpoint=1000")
    except Exception as e:
        log(f"warning: backup end: {type(e).__name__}: {str(e)[:120]}")
    _backup_active = False


def diagnostics():
    """The counts of the diagnostics download: no flight code, name or file name."""
    with _db_lock:
        c = db()
        one = lambda sql: c.execute(sql).fetchone()[0]
        out = {"schema": one("PRAGMA user_version"), "application_id": f"0x{one('PRAGMA application_id'):08X}",
               "quick_check": one("PRAGMA quick_check"), "flights": one("SELECT count(*) FROM flights"),
               "boarding_passes": one("SELECT count(*) FROM boarding_passes"),
               "gate_positions": one("SELECT count(*) FROM gate_positions")}
        status = dict(c.execute("SELECT status, count(*) FROM documents GROUP BY status").fetchall())
        unsent = one("SELECT count(*) FROM documents WHERE notified=0 AND status IN ('letto','archiviato','errore')")
        out["documents"] = {code: status.get(stored, 0) for stored, code in _STATUS_CODES.items()}
        out["documents"]["unsent"] = unsent
        out["api_usage"] = dict(c.execute("SELECT month, calls FROM api_usage ORDER BY month").fetchall())
        files = [r[0] for r in c.execute("SELECT file FROM documents").fetchall()]
    count = size = missing = 0
    for rel in files:
        try:
            size += os.path.getsize(os.path.join(DATA_DIR, rel))
            count += 1
        except OSError:
            missing += 1
    known, orphans = set(files), 0
    for folder, _, names in os.walk(os.path.join(DATA_DIR, "file")):
        orphans += sum(os.path.relpath(os.path.join(folder, n), DATA_DIR) not in known for n in names)
    out["files"] = {"count": count, "bytes": size, "missing": missing, "orphans": orphans}
    return out
