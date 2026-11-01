# Architecture

How the integration is built, for people who read or change the code. The user's view is in
[HOME_ASSISTANT.md](HOME_ASSISTANT.md) and [CONFIGURATION.md](CONFIGURATION.md).

## Principles

- **One side that knows Home Assistant, one that does not.** `custom_components/boarding_pass_tracker/core/` never
  imports `homeassistant`: it holds the rules (passes, flights, statistics, manual entry, the database) and is
  tested with plain `unittest`. Everything else in the package is a thin shell that connects the core to Home
  Assistant. A test (`tests/core/test_layering.py`) checks the rule.
- **Fixed rules, no model.** The BCBP fields have fixed positions; a barcode that breaks a rule is not a boarding
  pass.
- **Nothing blocks the event loop.** The core runs only in Home Assistant's executor. The loop does the views, the
  entity writes, the repairs and the flows; they meet through two small bridges.
- **The database is the truth.** Sensors are computed from it and published; they hold nothing of their own.
- **Photos are read elsewhere.** The decoder is a separate, stateless program; the integration only holds its
  address.

## Modules

| Module | Runs on | Role |
|---|---|---|
| `__init__.py` | loop | `CONFIG_SCHEMA` (config entry only), `async_setup` (views, static folder), `async_setup_entry`, `async_unload_entry`, `async_remove_entry` |
| `const.py` | - | domain, option and subentry keys, defaults, limits, timings |
| `runtime.py` | loop | `Runtime`: the settings from the entry, the people (`cfg()`), `run(fn, ...)` to the executor, the worker task, the clock |
| `bridge.py` | both | `HassBridge`: the core's way to call a Home Assistant service and read a state (refuses the loop thread); `LoopWakeup`; the reauth hook |
| `sensor.py` | loop | the three sensors and `EntitySink` |
| `views.py` | loop | the seven HTTP views, the body streaming with its own caps |
| `config_flow.py` | loop | the config flow, the options flow, the person subentry flow |
| `entity_ids.py`, `issues.py` | loop | the sensor id guard; the repairs |
| `cards.py` | loop | the static folder and the loader module |
| `backup.py` | loop to executor | the pre- and post-backup hooks |
| `diagnostics.py`, `system_health.py` | loop to executor | counts for support |
| `core/engine.py` | executor | the database, uploads, the worker step, the clock round, sensor values, editing |
| `core/flights.py` | executor | Wallet passes, BCBP, AviationStack, the gate reminder and its walk, times |
| `core/manual.py` | executor | flights typed by hand: parsing, rules, the Italian texts |
| `core/flight_stats.py` | executor | the statistics |
| `core/barcodes.py` | executor | the decoder client, the pixel guard, the HEIF header reader |
| `core/api.py` | executor | what each HTTP route answers |
| `core/adopt.py` | executor | reads an existing data folder for the setup flow |
| `core/ha.py` | executor | the payload shapes of the sensors |
| `core/airports.json`, `core/airport_zones.json` | - | airport coordinates and countries; the time zone of each airport |
| `frontend/` | browser | the loader and the three cards (flight, statistics, manage) |

## Data flow

1. A Shortcut calls a view with `Authorization: Bearer <token>`. Home Assistant checks the token before the view
   runs; the view maps the token's user to a person.
2. The view streams the body into memory (with its own cap and deadline) and calls the core in the executor.
3. `POST /upload`: the file is typed by its content, stored under `file/YYYY/MM/<sha256 prefix>.<ext>` (a duplicate by
   sha256 is not stored twice), a row is queued, the worker is woken, and the reply comes at once.
4. The **worker** (one task, one document at a time) reads the queue: a Wallet pass is opened as a ZIP, a photo or
   PDF is sent to the decoder. The BCBP rules make flights and passes; the uploader is notified.
5. The **clock** (every 60 seconds, one round at a time) checks the flights that are due with AviationStack, sends
   gate reminders, keeps Flightradar24 up to date, publishes the sensors (at the start, after a change, and every 10
   minutes) and probes the decoder.
6. `POST /flight` and `GET /choices` run the manual rules in the executor; the change is one transaction; the three
   sensors are computed again and published **before** the reply.

## Threads

| Runs on the loop (never blocks) | Runs in the executor |
|---|---|
| view parsing, user mapping, streaming the body | every core function: the database, files, AviationStack (20 s), Overpass (40 s), the decoder (120 s) |
| scheduling the executor jobs | the worker step, the clock round, computing the sensors |
| `hass.services.async_call`, `hass.states.get` for the core | reading an adopted folder, backups, diagnostics |
| entity writes, repairs, flows | closing the database at unload |

Rules the tests enforce:

- No core function runs on the loop; the bridge and the sensor sink refuse a call from the loop thread.
- The loop never takes a thread lock and never waits for an executor future.
- A thread of the core waits for the loop for at most 20 s (a service call) or 10 s (a sensor value).
- Loop-only Home Assistant calls (starting a reauth flow, creating a repair) are never made from the executor; the
  core only hops to the loop and the shell does the call.
- Locks: one lock for the one database connection, one around compute-and-publish (so a value computed before an
  edit never lands after the edit's own), one for the Flightradar24 bookkeeping.
- A reload or a stop finishes the running jobs for up to 30 seconds, then closes the database. A job cannot be
  cancelled: a transaction ends or rolls back.

## Storage

Everything is in `/config/boarding_pass_tracker` (mode 0700), never under `/config/www` (served without
authentication), `.storage` or `custom_components` (replaced by a HACS update).

| Path | Content |
|---|---|
| `tracker.db`, `tracker.db-wal`, `tracker.db-shm` | SQLite in WAL mode, mode 0600 |
| `file/YYYY/MM/<sha256[:16]>.<ext>` | uploads (jpg, png, webp, pdf, pkpass, heic), mode 0600 in folders 0700 |
| `backups/tracker-v1-<timestamp>-<source id>.db` | the copy made once before the upgrade from schema 1 to 2; kept until you delete it |

Home Assistant's own umask is 022, so the core creates the files with the right mode and tightens the three database
files and the folders when it opens them.

**Schema 2** (`PRAGMA user_version = 2`, `application_id = 0x42505454`, "BPTT"):

| Table | Key columns |
|---|---|
| `documents` | `sha256` UNIQUE, `uploaded_by`, `uploaded_at`, `file`, `file_type` (`jpg`, `png`, `webp`, `pdf`, `pkpass`, `heic`), `status` (`in_coda`, `letto`, `archiviato`, `errore`), `kind` (`carta_imbarco`, `documento`), `merchant` (the flight code), `doc_date`, `attempts`, `error`, `notified`, `outcome` |
| `flights` | `airline`, `number`, `date`, `origin`, `destination`, `pass_departure`, `pass_arrival`, `pass_boarding`, `pass_gate`, `pass_terminal`, `relevant_date`, `status` (JSON), `updated_at`, `next_check`, `error`, `snapshot` (JSON: what was notified), `created_at`; UNIQUE (`airline`, `number`, `date`, `origin`) |
| `boarding_passes` | `document_id`, `flight_id`, `passenger` (full name, never published), `first_name`, `seat`, `cabin`, `uploaded_by`; UNIQUE (`flight_id`, `passenger`) |
| `api_usage` | `month` PK, `calls` (AviationStack) |
| `gate_positions` | (`airport`, `gate`) PK, coordinates (NULL = not found), `searched_at`, `error` |

`pass_arrival` and `pass_terminal` are the two columns schema 2 adds to `flights`; the `pass_*` columns hold what a
Wallet pass said **or** what you typed. The stored codes are Italian and never change; English constants name them
in the code. The PNR is not stored.

**Opening the database.** The checks read only and run in this order: the application id must be 0 or ours; a higher
`user_version` is refused (`newer_database`); a schema-0 file is refused (`old_database_v0`). A file SQLite cannot
read (not a database, malformed, or a schema-1 file that fails its `quick_check`) is a setup error, «database not
readable», with no retry. A refused database keeps its content and its mode. Then, for a schema-1 file, the
upgrade: a checkpoint, a copy to `backups/` (at most one copy per source state, so retries add no copies), and the
two `ADD COLUMN` in one transaction. A check that fails the same way every time is `migration_failed` (no retry); a
busy or locked database, a backup of Home Assistant that is running, a copy that fails its check while the source
passes, or a data folder that cannot be reached is retried by Home Assistant with its back-off. The upgrade is idempotent.

**Typed passengers need no marker.** A passenger typed by hand is a pass row with no document whose `passenger`
equals `first_name`; a flight is typed when all its passengers are.

### Backups

`backup.py` implements Home Assistant's pre- and post-backup hooks. Before a backup the core empties the WAL and
turns the automatic checkpoints off, so the main file stays frozen and the archived `tracker.db` plus any prefix of
`tracker.db-wal` is a committed state; writes are not blocked. Closing the last connection would checkpoint and delete
the WAL, so a reload or a stop during a backup closes without the checkpoint, and the new runtime picks the backup
up again. If no post-backup call arrives in 30 minutes the checkpoints come back with a warning; while no entry is
loaded, a timer of the domain does this instead of the runtime's. `tracker.db-shm` is
in the backup (see [HOME_ASSISTANT.md](HOME_ASSISTANT.md#backups)).

## Startup, unload, remove

- **Setup:** open the database in the executor (the upgrade, the check), install the bridges, publish the three
  sensors once (so a start adds no "unknown" row to the history), set up the sensor platform, check the sensor ids,
  add the loader module, register the listeners (stop, time zone change, "Home Assistant started": the worker and
  the clock start then).
- **Unload:** stop the clock and the worker, unload the platform, close the database in the executor, remove the
  loader module, delete the runtime repairs.
- **Remove:** the data folder is **kept**. A warning and a persistent notification say where it is and how to delete
  it (see [PRIVACY.md](PRIVACY.md#uninstalling-disinstallare)).

## HTTP API

All routes are under `/api/boarding_pass_tracker/`, need the header `Authorization: Bearer <long-lived token>`, and
send no CORS headers (a browser page on another origin cannot call them). A request with no or a bad token gets 401 from Home Assistant before the view runs
(after about 5 seconds: Home Assistant's ban middleware). A user flagged as local-only is refused when the request
comes through a remote path.

| Route | Method | Needs a person | Limits | Answer |
|---|---|---|---|---|
| `/api/boarding_pass_tracker/health` | GET | no (any active, non-system user) | - | `{"ok": true, "queued": n, "decoder": "off"\|"ok"\|"down"}` |
| `/api/boarding_pass_tracker/documents` | GET | yes | latest 30 | `{"user", "documents": [{id, uploaded_by, uploaded_at, status, kind, merchant, doc_date}]}` |
| `/api/boarding_pass_tracker/flights` | GET | yes | latest 30 | `{"user", "flights": [...]}` |
| `/api/boarding_pass_tracker/choices` | GET | yes | 200 flights | `{"user", "message", "choices", "details"}` |
| `/api/boarding_pass_tracker/upload` | POST | yes | `Content-Length` 1 to 26,214,400 | `{"id", "status", "message"}` |
| `/api/boarding_pass_tracker/flight` | POST | yes | `Content-Length` 1 to 16,384, body within 30 s | `{"id", "result", "message"}` |
| any other path under the base | GET, POST | token only | - | 404 `{"message": "percorso sconosciuto"}` |

**Order of checks in a view:** no loaded entry, or the integration is stopping: 503 `not_ready`; a POST with a
query string: 404; no person for the user: 403 `unknown_user`; the length: 400 or 413; the body is read, then the
core runs in the executor.

- **Upload.** The body is the file, or a multipart form with a file. A missing or zero length (chunked) is 400, over
  25 MiB is 413, both «File vuoto o oltre 25 MB.». The whole body must arrive within `max(120 s, length / 32 KiB/s)`
  (25 MiB: 800 s), else 408 `body_timeout` and nothing is stored. A client that closes early gets no reply and
  nothing is stored. The type comes from the content: a Wallet pass, JPEG, PNG, WebP, HEIC or PDF. A photo or PDF
  with no decoder configured is 415 «Per leggere foto e PDF serve il decodificatore, che non è configurato», nothing
  stored; a ZIP with `pass.json` and more than 200 entries is 415 as well; any other type is 415 «Mandami una carta
  d'imbarco».
- **`/flight`.** The body is a JSON object with the keys `action` (`add`, `edit`, `delete`), `flight`, `date`,
  `origin`, `destination`, `passengers`, `ref`, and, new in 1.0, `departure_time`, `arrival_time`, `terminal` and
  `gate`. An unknown key is refused. The whole body must arrive within 30 seconds (else 408). `edit` and `delete`
  need `ref`, the version of the flight read from `/choices`; a stale `ref` is refused. Every reply has an Italian
  `message`, also the refusals, and never a name.
- **`/choices`.** `choices` is the list of labels, `details[label]` holds `ref`, `date`, `passengers`, and the
  stage-B keys `classes` («Alice business, Bob economy»), `departure_time` and `arrival_time` (local `HH:MM`, empty
  when none), `terminal` and `gate` (the typed or pass values, never AviationStack's).
- **Errors.** An unexpected exception in a core call is 500 «Errore interno: riprova tra poco.». A `Content-Length`
  that is not a number never reaches the view: aiohttp answers 400 in plain text.
- **Logging.** One line per POST and per answer that is not 200: method, short path (escaped to printable ASCII), code
  and `message`. Never a name, a header or an address; the query string is not logged.

## The decoder API (what the integration expects)

The full API is in the decoder's own documentation (`barcode_decoder/DOCS.md` in its repository). The integration
sends `POST /v1/decode?pages=3&max_codes=50` with the raw file, through urllib in the executor, no proxy, no
redirects, a 120 s timeout, and reads at most 8 MiB of the answer. It keeps the first 20 distinct codes of at most
500 characters each. Before calling, it reads the pixel size from the header (HEIC from the `ispe` box with a
bounded reader) and refuses an image over 50 megapixels.

| Decoder answer | Result |
|---|---|
| 200 with codes | the codes go to the BCBP rules |
| 200 empty, 400, 413, 415, 422 `unreadable`, 504 `timeout` | no code: the document is archived «Non trovo il codice a barre di una carta d'imbarco: l'ho archiviato.» |
| 422 `too_many_pixels` | an error text that names the size; an attempt is used |
| 500 `internal`, a 200 over 8 MiB or a 200 without a usable list | an attempt is used; the document is in error after 3 |
| refused connection, DNS, timeout, 503, 5xx without JSON, 408, a 200 that is not the decoder's | the document stays queued, the attempt is given back, retries after 1, 5, 15, 60 minutes; after 24 hours it is in error |

The state `ok`, `down` or `off` shown by `/health` is the last call or a probe every 10 minutes. Down for 15
minutes with photos waiting raises the repair.

## External contracts

These are a promise to the cards, the dashboard, your automations and your history; a change is breaking, needs a
migration and a CHANGELOG entry:

- the three entity ids, unique ids and the attribute keys and codes ([TRANSLATION_MAP.md](TRANSLATION_MAP.md));
- the paths, keys and codes of the HTTP API above;
- the notification group `boarding_pass_tracker` and the tags `boarding_pass_tracker-d<id>` (documents) and
  `boarding_pass_tracker-f<id>` (flights);
- the card element names and options ([dashboards/README.md](../dashboards/README.md));
- the stored data codes of the database.
