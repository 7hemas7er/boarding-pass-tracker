"""Constants of the boarding_pass_tracker integration: the domain, the keys of the config entry (data, options,
person subentries), the defaults, the limits and the timings of the shell (INTEGRATION_PLAN.md 3.2, 3.5, 5.4-5.6).

No Home Assistant import here: config_flow.py, the shell modules and the tests read these names.
"""
from datetime import timedelta

DOMAIN = "boarding_pass_tracker"
TITLE = "Boarding Pass Tracker"
DATA_DIR_NAME = "boarding_pass_tracker"        # hass.config.path(DATA_DIR_NAME): /config/boarding_pass_tracker

# ---------------------------------------------------------------- config entry (5.5)
# entry.data
CONF_AVIATIONSTACK_KEY = "aviationstack_key"   # str or None: no key, no AviationStack call

# entry.options (OptionsFlowWithReload, step init)
CONF_DECODER_URL = "decoder_url"               # str or None: the barcode decoder (5.9), photos and PDFs only with one
CONF_MONTHLY_CAP = "monthly_cap"               # AviationStack calls per month, 0..1000; 0 = no calls
CONF_FR24_ADD = "fr24_add"                     # text entity of Flightradar24: add a flight to the tracked ones
CONF_FR24_REMOVE = "fr24_remove"               # text entity: remove it
CONF_FR24_TRACKED = "fr24_tracked"             # sensor entity: the tracked flights
CONF_FLIGHTS_ENABLED = "flights_enabled"       # AviationStack + Flightradar24 on (engine NO_FLIGHTS = not)
CONF_NOTIFY_ENABLED = "notify_enabled"         # notifications on (engine NO_NOTIFY = not)

OPTION_KEYS = (CONF_DECODER_URL, CONF_MONTHLY_CAP, CONF_FR24_ADD, CONF_FR24_REMOVE, CONF_FR24_TRACKED,
               CONF_FLIGHTS_ENABLED, CONF_NOTIFY_ENABLED)

DEFAULT_MONTHLY_CAP = 95                       # the free plan has 100
MAX_MONTHLY_CAP = 1000
DEFAULT_FLIGHTS_ENABLED = True
DEFAULT_NOTIFY_ENABLED = True
# The ids of a fresh English install of Flightradar24 (the engine's defaults), used when no option is set
DEFAULT_FR24_ADD = "text.flightradar24_add_to_track"
DEFAULT_FR24_REMOVE = "text.flightradar24_remove_from_track"
DEFAULT_FR24_TRACKED = "sensor.flightradar24_additional_tracked"
DEFAULT_FR24 = {CONF_FR24_ADD: DEFAULT_FR24_ADD, CONF_FR24_REMOVE: DEFAULT_FR24_REMOVE,
                CONF_FR24_TRACKED: DEFAULT_FR24_TRACKED}
# The registry defaults the flows propose: platform flightradar24, unique id ending with these (any language): the
# integration's unique ids are <entry>_flightradar24_<description key> (keys add_track, remove_track,
# additional_tracked in Flightradar24 v2.4)
FR24_PLATFORM = "flightradar24"
FR24_UNIQUE_ID_SUFFIXES = {CONF_FR24_ADD: "_flightradar24_add_track", CONF_FR24_REMOVE: "_flightradar24_remove_track",
                           CONF_FR24_TRACKED: "_flightradar24_additional_tracked"}
FR24_DOMAINS = {CONF_FR24_ADD: "text", CONF_FR24_REMOVE: "text", CONF_FR24_TRACKED: "sensor"}

# person subentries (subentry_type "person", unique_id = name)
SUBENTRY_PERSON = "person"
CONF_NAME = "name"                             # the name stored in the rows: [a-z]+ (NAME_PATTERN)
CONF_HA_USER = "ha_user"                       # the HA user id of the person (the token of the Shortcuts), or None
CONF_NOTIFY = "notify"                         # the notify service, without "notify.": mobile_app_...
CONF_PERSON = "person"                         # person entity for the gate reminder, or None: person.<name>
CONF_SKIP = "skip"                             # step people, a name found in the rows: no subentry, no notifications
NAME_PATTERN = r"[a-z]+"                       # the standalone's load_config rules (E:360)
NOTIFY_PATTERN = r"mobile_app_\w+"
PERSON_PATTERN = r"person\.\w+"

# ---------------------------------------------------------------- entities (5.4)
SENSOR_KEYS = ("next_flight", "upcoming_flights", "flight_statistics")
PINNED_ENTITY_IDS = {f"sensor.{DOMAIN}_{key}": f"{DOMAIN}_{key}" for key in SENSOR_KEYS}   # entity id -> unique id
DEVICE_MODEL = "boarding-pass-tracker"

# ---------------------------------------------------------------- repairs (5.6); ids never hold personal data (C13)
ISSUE_ENTITY_ID_TAKEN = "entity_id_taken"
ISSUE_DECODER_UNREACHABLE = "decoder_unreachable"
ISSUE_NOTIFY_MISSING = "notify_service_missing"          # translation key; issue id: <this>_<subentry_id>
DECODER_ISSUE_AFTER = timedelta(minutes=15)              # down this long with photos waiting: the issue

# ---------------------------------------------------------------- HTTP (5.1) and cards (5.11)
API_BASE = f"/api/{DOMAIN}"
FRONTEND_URL = f"/{DOMAIN}/frontend"
CARD_FILES = ("boarding-pass-tracker-flight-card.js", "boarding-pass-tracker-stats-card.js")   # in frontend/ (C1)
MANAGE_CARD_FILE = "boarding-pass-tracker-manage-card.js"   # the third card, also in frontend/
LOADER_FILE = "boarding-pass-tracker-loader.js"   # the one extra JS module: imports CARD_FILES once HA's registry is up

# ---------------------------------------------------------------- POST /upload: the deadline of the whole body
UPLOAD_TIMEOUT_MIN_S = 120                     # at least this long for any body (a pkpass, a small photo)
UPLOAD_MIN_BYTES_PER_S = 32 * 1024             # and the time of the declared length at 256 kbit/s: 25 MiB in 800 s

# ---------------------------------------------------------------- timings (3.5, 3.6, R-C)
CLOCK_INTERVAL = timedelta(seconds=60)
WORKER_IDLE_S = 60             # the worker's wait for an upload when the queue is empty
WORKER_ERROR_S = 30            # after an unexpected error of a worker round
BRIDGE_TIMEOUT_S = 20          # an executor thread waits at most this long for the loop (notify, state, service)
SINK_TIMEOUT_S = 10            # the same for a sensor value
TASK_STOP_TIMEOUT_S = 10       # unload: how long the worker and the clock round get to end after their cancel
SHUTDOWN_TIMEOUT_S = 30        # unload / stop: how long the running executor jobs get (engine.shutdown)
BACKUP_SAFETY = timedelta(minutes=30)   # no post-backup call by then: the WAL checkpoints go back on
