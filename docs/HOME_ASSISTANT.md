# Home Assistant integration

Everything the integration creates or uses in Home Assistant. Entity ids, attribute keys and the codes in attribute
values are English and are a stable contract with the cards, the dashboard and your history. States, the texts
inside the attributes, the notifications and the screens are Italian, quoted between «…».

## What the integration uses

| Entity, service or command | How |
|---|---|
| `notify.mobile_app_*` | Notifications to each person's phone, with the person's `notify` service (no buttons). |
| `person.<name>` | The position (`latitude`, `longitude`, `gps_accuracy`), read only to time the gate reminder. |
| Flightradar24: `text.flightradar24_add_to_track`, `text.flightradar24_remove_from_track` (`text.set_value`), `sensor.flightradar24_additional_tracked` | Optional live tracking of the next flight. The three entities are options. |
| The Companion app's push relay | Carries a notification when the phone has no local push channel (see [PRIVACY.md](PRIVACY.md)). |
| `persistent_notification` | One message, when the entry is removed. |
| `/config/boarding_pass_tracker` | The integration's own data folder. |

## Sensors

All three belong to one device, «Boarding Pass Tracker» (model `boarding-pass-tracker`, type service). Home
Assistant shows each sensor with the device name in front: "Boarding Pass Tracker Prossimo volo". The sensors have no unit, device class or
state class, so Home Assistant keeps no long-term statistics, only the normal history. They are published when the
integration starts, after every change, and every 10 minutes; after a flight is added, edited or deleted by hand
all three are computed again and published before the Shortcut gets its reply. The flight lists hold at most 10
entries (the statistics hold more, but stay under 12 KB). The attributes never include the PNR, the surnames of the
passes or the barcode. A name typed by hand is shown as it was typed.

| Entity id | Name | State |
|---|---|---|
| `sensor.boarding_pass_tracker_next_flight` | «Prossimo volo» | Italian status text |
| `sensor.boarding_pass_tracker_upcoming_flights` | «Voli» | number of flights not over yet |
| `sensor.boarding_pass_tracker_flight_statistics` | «Statistiche voli» | number of flights flown |

The sensors are available while the integration is loaded. The entity ids are pinned: the integration will not take
`_2` for a taken id, see [the id check](#the-sensor-id-check).

### `sensor.boarding_pass_tracker_next_flight`: «Prossimo volo»

State: the status of the nearest flight that is not over yet, in Italian. For example «In orario», «In ritardo di
{r} min», «Partito dal gate», «In volo», «Atterrato», «Atterrato con {r} min di ritardo», «Cancellato», «Deviato»,
«Problema segnalato», «Dalla carta d'imbarco» (no status yet), «Inserito a mano» (no status yet, and every passenger
typed by hand) or «Nessun volo» (then the attributes are empty).

Attributes (the flight card reads them too):

| Attribute | Meaning |
|---|---|
| `flight`, `flight_iata` | Flight code for display («FR 1234») and for Flightradar24 (`FR1234`) |
| `route`, `origin`, `destination`, `origin_city`, `destination_city` | Route text, IATA codes, Italian city names |
| `date` | Flight date `YYYY-MM-DD` |
| `phase` | `scheduled`, `departed` (left the gate), `airborne`, `landed`, `cancelled`, `diverted`; null without status data |
| `status` | Same text as the state |
| `departure`, `arrival`, `takeoff` | ISO instants (best known departure, arrival, takeoff) |
| `departure_time`, `departure_scheduled_time`, `arrival_time` | Local airport times `HH:MM` (current and scheduled departure, arrival) |
| `boarding_time`, `boarding_estimated` | Boarding time, and whether it is an estimate |
| `at_gate_time`, `leave_for_gate_time`, `priority` | When to be at the gate, when to set off, priority cabin (first or business) |
| `gate`, `terminal`, `arrival_gate`, `arrival_terminal`, `baggage_belt` | Gates, terminals, baggage belt |
| `delay_min` | Departure delay in minutes (arrival delay once landed) |
| `passengers` | First names with seat, for example `["Alice (12A)"]` («passeggero» when the pass has no first name) |
| `updated_at`, `source` | Last update (ISO instant); source: `aviationstack`, or with no status yet `manual` (every passenger typed by hand) or `boarding_pass` (any other flight) |

Times, terminal and gate that were typed by hand or read from a Wallet pass fill the same attributes: AviationStack's
own values win as soon as they exist (see [FLIGHTS.md](FLIGHTS.md#what-wins-typed-pass-or-aviationstack)).

### `sensor.boarding_pass_tracker_upcoming_flights`: «Voli»

State: the number of flights not over yet. Attribute `list`: up to 10 `{flight, route, date, departure_time, status,
gate, passengers}`, the nearest first, with the same meanings as above.

### `sensor.boarding_pass_tracker_flight_statistics`: «Statistiche voli»

State: the number of flights flown, the same as the `flights` attribute. A flight counts as flown as defined in
[FLIGHTS.md](FLIGHTS.md#statistics). Attributes (read by `boarding-pass-tracker-stats-card`):

| Attribute | Meaning |
|---|---|
| `flights`, `cancelled`, `diverted`, `upcoming` | Flights flown, cancelled, diverted, upcoming |
| `air_time_min`, `air_time_h` | Time in the air, in minutes and in hours (one decimal) |
| `estimated_durations`, `no_data` | Flown flights whose duration is an estimate; flown flights with no news about the arrival |
| `km`, `earth_laps` | Great-circle distance, laps of the Earth |
| `departure_delay_min`, `arrival_delay_min` | Total of the positive delays at departure and at arrival, in minutes |
| `mean_delay_min`, `on_time_pct` | Mean delay at arrival (an early arrival counts as 0); percentage of flights on time (both null when unknown) |
| `countries`, `countries_total` | Up to 30 `{code, flag, flights}`, most flights first; the number of countries |
| `airports`, `airports_total` | Up to 8 `{code, name, flights}`; the number of airports |
| `airlines`, `routes` | Up to 8 `{code, flights}`; up to 5 `{route, flights}` |
| `cabins` | Flights by cabin: `{business, economy, unknown}`, always all three keys |
| `passengers` | Up to 10 `{name, flights, km, air_time_h}` by first name, most flights first |
| `by_person` | Up to 10 people, most flights first (fields below) |
| `records` | `longest`, `shortest`, `max_duration`, `max_delay`: one flight entry each, or null |
| `by_year` | `{year, flights, air_time_h, km, arrival_delay_min, on_time_pct}`, newest year first |
| `list`, `list_total` | Up to 30 flight entries (every outcome), newest first, fewer when the attributes are cut for size; the number of flights |
| `updated_on` | Date of the computation, `YYYY-MM-DD` |

A **flight entry** (in `list`, in `records` and in `longest`) is `{date, flight, route, cities, duration_min, km,
delay_min, outcome, estimated, passengers}`: `route` holds the airport codes («BLQ → STN») and `cities` the city
names, `delay_min` is the delay at arrival, `outcome` is `flown`, `diverted`, `cancelled` or `upcoming`, `estimated`
is true when the duration is an estimate, and `passengers` lists the first names on board.

Each **`by_person`** item counts only the flown flights that person was on:

| Field | Meaning |
|---|---|
| `name` | First name, as in `passengers` |
| `flights`, `cancelled`, `diverted` | Flights flown; cancelled and diverted flights, over all of that person's flights |
| `air_time_min`, `estimated_durations`, `km` | Time in the air, estimated durations, distance |
| `arrival_delay_min`, `mean_delay_min`, `on_time_pct` | Total and mean delay at arrival, percentage on time |
| `countries`, `countries_total` | Up to 30 country codes, most flights first; the number of countries |
| `airports_total` | The number of airports |
| `airlines` | Up to 3 `{code, flights}` |
| `longest` | That person's longest flight (a flight entry), or null |

### Size

The statistics attributes stay under 12,000 bytes (UTF-8 JSON), because Home Assistant does not record attributes
over 16 KiB. If they are bigger, entries are removed from the tail of these lists, one step after the other, each
only while the attributes are still too big:

1. `list` down to 10 entries, `countries` down to 10, `by_year` down to 5;
2. `by_person.airlines`, then `by_person.countries`: one entry at a time from the person with the longest list;
3. `by_person`: whole people, from the tail;
4. `list`, `countries`, `by_year`, down to nothing;
5. `passengers`, `airports`, `airlines`, `routes`.

The counters (`list_total`, `countries_total`, `airports_total`) are never cut. Neither are the `passengers` of an
entry, so a flight with dozens of passengers can keep the attributes over the limit.

## Notifications

All notifications are sent with `group: boarding_pass_tracker` and have no buttons. The tag decides which
notification replaces which on the phone.

| When | Title | Tag |
|---|---|---|
| Boarding pass read | «Carta d'imbarco letta» (the message lists each flight, with seat and first name, and whether the status is followed; «Carta aggiornata: » for a passenger already known), or «Gia' presente» | `boarding_pass_tracker-d<document>` |
| Not a boarding pass | «Documento archiviato»: a Wallet pass that is not a boarding pass («E' un pass del Wallet, ma non una carta d'imbarco che so leggere»), or a photo or PDF without a boarding-pass barcode («Non trovo il codice a barre di una carta d'imbarco: l'ho archiviato.») | `boarding_pass_tracker-d<document>` |
| Reading failed (after 3 attempts) | «Lettura non riuscita» | `boarding_pass_tracker-d<document>` |
| Flight change | «{flight}: gate assegnato», «{flight}: cambia il gate», «{flight}: problema segnalato», «{flight} cancellato», «{flight} deviato», and a delay change | `boarding_pass_tracker-f<flight>` |
| Gate reminder | «{flight}: avviati al gate {gate}» («{flight}: avviati verso i gate» while the gate is unknown) | `boarding_pass_tracker-f<flight>` |

The same file sent twice gets no notification: the Shortcut shows «Questo l'avevi gia' mandato il» and the date. A
file that ended in error is read again instead («Ricevuto: lo rileggo e ti mando una notifica.»).

Who receives what: the result of an upload goes to the person who uploaded it. Flight-change notices go to every
person. The gate reminder goes to each person who uploaded a pass for that flight, and for a passenger typed by hand
to the person with that first name, otherwise to the person who typed it. The rules behind them are in
[FLIGHTS.md](FLIGHTS.md#notifications). With the option `notify_enabled` off, notifications wait and are sent when
you turn it on.

A flight added, edited or deleted with «Aggiungi volo» or «Modifica volo» sends no notification: the reply shows in
the Shortcut.

## The cards

The integration adds **one** small JavaScript module to every Home Assistant page: the loader
`/boarding_pass_tracker/frontend/boarding-pass-tracker-loader.js?v=<version>-<hash>`. The loader waits until the Home
Assistant frontend has its element registry in place, and then loads the three card files from the same folder with
the same `?v=`. That is why:

- **no Lovelace resource and no `/config/www` file is needed.** If you installed the cards by hand in the past,
  remove those resources: the cards would otherwise be defined twice;
- a new version of the integration, or a changed card file, brings the new cards by itself (the `?v=`, the version
  and a hash of the frontend files, changes, so the 31-day cache is not an obstacle);
- the three cards show up in the card picker, as «Volo (Boarding Pass Tracker)», «Statistiche voli (Boarding Pass
  Tracker)» and «Gestisci voli (Boarding Pass Tracker)».

The files are served without authentication, like every file of a frontend folder; they hold code only and no data.
The cards' options and the dashboard are in [dashboards/README.md](../dashboards/README.md).

## Services

The integration registers **no service** of its own. It calls these:

| Service | When | Data |
|---|---|---|
| `notify.<mobile_app_x>` (the person's `notify`) | every notification | `title`, `message`, and `data` with `group` and `tag` |
| `text.set_value` on the Flightradar24 entities | the next flight enters or leaves the tracked list | the flight code, for example `FR1234` |

A notify service that does not exist raises the repair `notify_service_missing`; the notification is kept and retried.

## Repairs

| Issue | Title | When it clears |
|---|---|---|
| `entity_id_taken` | «Sensore con id diverso» | the other entity is renamed or deleted, and the sensor has its id |
| `decoder_unreachable` | «Decodificatore non raggiungibile» | the decoder answers, or no photo waits |
| `notify_service_missing` | «Notifiche per {name} ferme» | the next notification of that person is sent, or the person is changed |

## Diagnostics and system health

**Download diagnostics** (the entry's ⋮ menu) holds counts and states only: the entry (version, options, how many
people, whether a key is set), the database (`schema`, `application_id`, `quick_check`, counts of flights, passes
and documents by status, the AviationStack calls of each month, the number and size of the files, missing and
orphan files), the decoder (configured or not, status, since when it is down, its last `/health`), the data
folder, the time zone, the language, whether the three Flightradar24 entities exist, and the last clock round.
The key, the people's names, notify services, person entities and user ids are redacted. There is no flight code, passenger
name or file name.

**System health** (Settings › System › Repairs › ⋮ › System information) shows `data_folder`, `queued` (documents
waiting), `decoder` (reachable or `off`) and `aviationstack_calls` as `n/cap`.

## The sensor id check

Home Assistant files an entity under its integration and unique id; the entity id is a separate name. If
`sensor.boarding_pass_tracker_next_flight` is already taken (by another integration, by an old orphaned entry or by a
YAML entity), Home Assistant would silently register this one as `..._2`, and the cards would read the wrong entity.

- **When you add the integration**, the setup stops with the abort `entity_ids_taken` and names the ids.
- **After setup**, the integration compares each sensor's id with the one it wants. A different id held by another
  entity raises the repair `entity_id_taken`; a rename you did on purpose, with the wanted id free, raises nothing.

## Backups

Home Assistant backups include `/config/boarding_pass_tracker`: the database, the photos and the passes, with full
names. Do not share a backup carelessly.

- The integration prepares for a backup: it flushes the database log and keeps it frozen while the backup runs, so
  that the archived `tracker.db` plus its `tracker.db-wal` is a state the database can open. Writes during a backup
  are allowed.
- **`tracker.db-shm` is in the backup too.** Home Assistant's exclusion of `*.db-shm` files matches only one folder
  level and does not reach a file in `boarding_pass_tracker/`. A restored backup holds `tracker.db`, `-wal` and
  `-shm`, and the database opens and checks out normally.
- If no "backup finished" call arrives within 30 minutes, the integration lets the log shrink again and writes a
  warning, also when the integration was unloaded or reloaded during the backup.
- A restore brings the data folder back as it was at the time of the backup.

## Automations

The sensors are ordinary entities. For example, a notification on a wall tablet when the next flight lands (`phase`
becomes `landed`):

```yaml
triggers:
  - trigger: state
    entity_id: sensor.boarding_pass_tracker_next_flight
    attribute: phase
    to: landed
actions:
  - action: notify.mobile_app_tablet_ingresso
    data:
      title: "{{ state_attr('sensor.boarding_pass_tracker_next_flight', 'flight') }} atterrato"
      message: "{{ state_attr('sensor.boarding_pass_tracker_next_flight', 'route') }}"
```

Compare `phase` with the codes above, not the state: the state is a text for people, and it changes with the delay.
