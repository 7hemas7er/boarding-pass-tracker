# Dashboards

A Lovelace dashboard and three custom cards for Home Assistant. Their texts are in Italian, like the rest of the user
interface; their element names, options and option values are English.

| File | What it is |
|---|---|
| `flights.json` | «Voli» view (next flight on the flight card, map, details, all flights) and «Statistiche» view (one stats card with all the flight statistics). |
| *(in the integration)* `boarding-pass-tracker-flight-card.js` | The custom card `boarding-pass-tracker-flight-card`: the next flight on an arc, with the plane moving along it. Used by the «Voli» view. |
| *(in the integration)* `boarding-pass-tracker-stats-card.js` | The custom card `boarding-pass-tracker-stats-card`: totals, year by year, rankings, records and the flight list from `sensor.boarding_pass_tracker_flight_statistics`, for everyone or for one passenger. Used by the «Statistiche» view. |
| *(in the integration)* `boarding-pass-tracker-manage-card.js` | The custom card `boarding-pass-tracker-manage-card`: add, change or delete a flight from the dashboard, signed in as you are, with no token. Not in `flights.json`: add it where you like. |

The three card files are in `custom_components/boarding_pass_tracker/frontend/`; this folder holds the dashboard.

## 1. The cards come with the integration

**You do not install the cards.** The three cards come with the integration: it adds one small loader module to every
Home Assistant page, and the loader loads the card files once the frontend is ready. **No resource is needed**:
there is nothing to copy into `/config/www`, no entry under Settings › Dashboards › Resources, and no `?v=` number
to raise. A new version of the integration brings the new cards by itself.

The cards appear in the dashboard card picker, as «Volo (Boarding Pass Tracker)», «Statistiche voli (Boarding
Pass Tracker)» and «Gestisci voli (Boarding Pass Tracker)», with their default entity filled in.

If a card shows "Custom element doesn't exist", the integration is not loaded (check Settings › Devices & services),
or the browser still holds an old page: reload it, and on a phone reset the Companion app's frontend cache (in the
app's settings, under Debugging). If you once added these cards by hand as Lovelace resources, remove those
resources and the files in `/config/www`: the old file would define the card first, and the integration's copy
would be ignored.

`flights.json` uses the default entity ids (see [docs/HOME_ASSISTANT.md](../docs/HOME_ASSISTANT.md)). If your
Flightradar24 entities have other ids, set them in the integration's options and in the cards:

| Default id in the dashboard and the cards | Comes from |
|---|---|
| `sensor.boarding_pass_tracker_*` | the integration (pinned ids) |
| `sensor.flightradar24_additional_tracked` | the Flightradar24 integration's "Additional tracked" sensor. The dashboard does not name it: the flight card uses it by default. If yours has another id, add `fr24_entity: <your id>` to the flight card. |
| `device_tracker.flightradar24` | the Flightradar24 integration with `enable_tracker` on |

### Flight card (`boarding-pass-tracker-flight-card`)

```yaml
type: custom:boarding-pass-tracker-flight-card
entity: sensor.boarding_pass_tracker_next_flight         # default
fr24_entity: sensor.flightradar24_additional_tracked     # default; the card works without Flightradar24, with less detail
```

| Option | Default | What it does |
|---|---|---|
| `entity` | `sensor.boarding_pass_tracker_next_flight` | The next-flight sensor. |
| `fr24_entity` | `sensor.flightradar24_additional_tracked` | The Flightradar24 sensor of tracked flights. The card finds the flight in its `flights` attribute by the `flight_iata` attribute of the next flight. The default is the id of a fresh install, and the id depends on Home Assistant's language: look yours up in Settings → Entities (search `flightradar24`) and set `fr24_entity` to it when it differs. |

Without Flightradar24 the plane moves by elapsed time (takeoff to arrival). With it, the card uses the live
position, altitude and speed. A tap on the card opens the flight on flightradar24.com.

### Statistics card (`boarding-pass-tracker-stats-card`)

```yaml
type: custom:boarding-pass-tracker-stats-card
entity: sensor.boarding_pass_tracker_flight_statistics     # default
sections: [summary, years, rankings, records, flights]     # default: all of them, in this order
distance_unit: km                                          # km (default) or mi
time_format: h:mm                                          # h:mm (default) or decimal
person: Ada                                                # default: everyone («Tutti»)
```

| Option | Default | What it does |
|---|---|---|
| `entity` | `sensor.boarding_pass_tracker_flight_statistics` | The statistics sensor. An empty value means the default. |
| `sections` | all five, in this order | The sections to show, in the order given: `summary` (flights, hours in the air, distance and laps of the Earth, delays, punctuality, countries, airports), `years` (a chart of flights, distance or hours per year, with a table; month by month when there is only one year), `rankings` (rankings of airports, airlines, routes, countries, passengers and travel class), `records` (longest and shortest flight, most hours in the air, biggest delay) and `flights` (the latest flights, by month). Unknown values are ignored with a warning in the browser console (`boarding-pass-tracker-stats-card: ...`), and an empty list means all of them. With `sections` you can split the statistics over several cards, for example the flight list in a card of its own. |
| `distance_unit` | `km` | Distances in kilometres (`km`) or in statute miles (`mi`). |
| `time_format` | `h:mm` | Hours as hours and minutes («164:03 h») or with decimals (`decimal`, «164,1 h»). `hh:mm`, `hmm` and `hhmm` mean `h:mm`; `decimals` means `decimal`. |
| `person` | everyone | A passenger's first name, as in the sensor's `by_person` (capitals do not matter): the card shows only that person's flights. A name that is not in `by_person` shows everyone. |

`distance_unit`, `time_format` and `person` are only starting values. The viewer changes them from the card:
the names at the top switch between «Tutti» and one person (they appear once the sensor has `by_person`), and
the gear opens «Distanza» (`km`, `mi`) and «Ore» (`h:mm`, «decimali», which is `decimal`). The choice is
remembered in that browser (`localStorage`, key `boarding-pass-tracker-stats-card`, with the keys `person`,
`distance_unit` and `time_format`) and applies to every statistics card there: the other cards on the page
follow at once (window event `boarding-pass-tracker-stats-card-choices`). A value picked in the card replaces the
configured one in that browser; another browser or phone starts again from the configuration. With site storage
blocked, the choice lasts until the page is closed (in a private window, until the window is closed).

With a person chosen, the summary comes from that person's entry in `by_person`. Years, rankings, records and
the list come from the flights in `list` (up to the latest 30, fewer when the attributes are cut for size) that
have the name in their `passengers`; a caption says so when `list` does not hold all of that person's flights.
A flight counts for a person only if their boarding pass gives a first name (see
[docs/FLIGHTS.md](../docs/FLIGHTS.md#statistics)).

The card draws its charts in SVG and CSS, with no libraries and no network requests, and takes its colours from
the Home Assistant theme.

### Manage card (`boarding-pass-tracker-manage-card`)

```yaml
type: custom:boarding-pass-tracker-manage-card
title: Gestisci voli        # optional
```

It does in the dashboard what the Shortcuts «Aggiungi volo» and «Modifica volo» do: the same list of flights (the
ones not over yet first), the same fields and the same replies, sent to the same `/api/boarding_pass_tracker/`
paths with the session of the person signed in to Home Assistant, so **no token**. That Home Assistant user must be
linked to a person of the integration, else the card shows «Non so chi sei…» (the 403 of the API). Tap a flight to
change its passengers and seats, date, local times, terminal or gate; empty a time, the terminal or the gate to
remove it; «Elimina il volo» asks once more, and **a delete is final**. «Aggiungi volo» takes the same fields as the
Shortcut. Uploading a Wallet pass or a PDF stays a job for «Manda volo».

## 2. Import the dashboard

1. **Settings → Dashboards → Add dashboard → New dashboard from scratch**. Pick a title (for example «Voli») and
   an icon, then open the new dashboard.
2. **✏️ Edit → ⋮ → Raw configuration editor**.
3. Replace everything with the content of `flights.json`. JSON is valid YAML, so the editor accepts it as it is.
   **Save**.

If a card shows "Custom element doesn't exist: boarding-pass-tracker-flight-card" or "Custom element doesn't
exist: boarding-pass-tracker-stats-card", the integration is not loaded or the browser still has an old copy: see section 1.

The markdown cards of the «Voli» view read the sensor attributes (the flight card of
`sensor.boarding_pass_tracker_next_flight`, the `list` of `sensor.boarding_pass_tracker_upcoming_flights`). They
show placeholder texts until the integration has published its sensors. The «Statistiche» view has no markdown cards:
the stats card reads `sensor.boarding_pass_tracker_flight_statistics` itself. Until the sensor has data it shows
«Statistiche dei voli non disponibili», and until the first flight has landed it shows how to add a boarding pass
with the Shortcut «Manda volo».

In the «Dettagli» markdown card, «Fonte:» tells where the flight's status comes from (the `source` attribute):
«AviationStack»; before AviationStack has a status for the flight, «carta d'imbarco» when it came from a boarding
pass, or «inserimento a mano» when every passenger on it was typed by hand with the Shortcuts «Aggiungi volo» and
«Modifica volo» (see [docs/INSTALL.md](../docs/INSTALL.md)).
