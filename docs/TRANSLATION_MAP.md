# Developer note: Italian names, English code

Boarding Pass Tracker began as the flight half of «App Casa», an Italian-only household service. Its code was
translated into English with no change in behaviour, and then every name that crosses a boundary (Home Assistant,
the HTTP API, the cards) became English too. This note says which Italian names stay and why, and lists the
names of the earlier versions next to the current ones. Read it before you rename anything that crosses a
boundary.

This file is generated: `INT/harness/docs/gen_translation_map.py` rebuilds it from the naming contract and the
code, and the documentation check fails if the committed file differs from that output. Edit the generator, not
the file.

## Rules that apply

1. **Text a person reads stays Italian, byte for byte.** That covers notification titles and messages, sensor
   states and the texts inside attribute values (`status`, `route`, `cities`, the city names, `passengers`), the
   `message` of every HTTP reply, the flight `status` sentence, the config flow, the repairs and the card labels.
   When unsure whether a person will see a string, keep it Italian and add an English comment.
2. **Stored codes keep their values.** The codes in the database (`in_coda`, `carta_imbarco`, ...) have English
   constant names in the code (`DOC_QUEUED = "in_coda"`), but the strings never change, so old rows keep working.
3. **Names at a boundary are English**: entity ids, unique ids, attribute keys, the codes in attribute values
   (`phase`, `outcome`, `cabin`, `source`), the HTTP paths, keys and codes, the card elements and their options.
4. **Comments, docstrings, logs and exception messages are English**, unless the message reaches a person (an
   error stored in `documents.error` or `flights.error` and shown in a notification stays Italian).
5. Changing one of these names is a breaking change: it needs a migration and a CHANGELOG entry.

## Glossary

One English word per Italian word, everywhere: columns, dictionary keys, variables and function names.

| Italian | English | Italian | English |
|---|---|---|---|
| carta d'imbarco | boarding_pass | volo | flight |
| passeggero / nome | passenger / first_name | posto / classe | seat / cabin |
| tratta | leg, or route (text) | compagnia | airline |
| da / a | origin / destination | partenza / arrivo | departure / arrival |
| imbarco | boarding | nastro | baggage_belt |
| decollo / atterraggio | takeoff / landing | ritardo | delay |
| previsto / stimato / effettivo | scheduled / estimated / actual | fuso | tz |
| tragitto | walk | margine | margin |
| avvisi (volo) | snapshot | fase | phase |
| esito | outcome | fonte | source |
| stato | status (database) / state (Home Assistant) | tipo / tipo_file | kind / file_type |
| persona / persone | person / people | elenco | list |
| paesi / aeroporti / rotte | countries / airports / routes | scelta | choice |
| aggiungi / modifica / elimina | add / edit / delete | (inserito) a mano | typed; `manual` as a source code |

## Data codes (stored values never change)

| Domain | Stored value | Shown by the HTTP API as |
|---|---|---|
| `documents.status` | `in_coda` | `queued` |
| `documents.status` | `letto` | `read` |
| `documents.status` | `archiviato` | `archived` |
| `documents.status` | `errore` | `error` |
| `documents.kind` | `carta_imbarco` | `boarding_pass` |
| `documents.kind` | `documento` | `document` |
| `documents.file_type` | `jpg`, `png`, `webp`, `pdf`, `pkpass`, `heic` | the same |

Codes that are computed and never stored:

| Where | Codes |
|---|---|
| `next_flight.phase` | `scheduled`, `departed`, `airborne`, `landed`, `cancelled`, `diverted` |
| `outcome` of a flight entry | `flown`, `diverted`, `cancelled`, `upcoming` |
| keys of `flight_statistics.cabins` | `business`, `economy`, `unknown` |
| `next_flight.source` | `aviationstack`, `boarding_pass`, `manual` |

## Entities and attributes

Home Assistant files each sensor under the integration and its unique id. The entity id, unique id and attribute keys below are the contract with the cards, the dashboard and the recorded history.

| Sensor key | Entity id | Unique id | Name shown (Italian) |
|---|---|---|---|
| `next_flight` | `sensor.boarding_pass_tracker_next_flight` | `boarding_pass_tracker_next_flight` | «Prossimo volo» |
| `upcoming_flights` | `sensor.boarding_pass_tracker_upcoming_flights` | `boarding_pass_tracker_upcoming_flights` | «Voli» |
| `flight_statistics` | `sensor.boarding_pass_tracker_flight_statistics` | `boarding_pass_tracker_flight_statistics` | «Statistiche voli» |

### `sensor.boarding_pass_tracker_next_flight`: attribute keys

`[]` is each list item, `.` a nested key. A key that kept its name is listed with the same name twice. The earlier name of the sensor was `prossimo_volo`.

| Earlier name | Name now | Note |
|---|---|---|
| `volo` | `flight` |  |
| `tratta` | `route` |  |
| `data` | `date` |  |
| `da` | `origin` |  |
| `a` | `destination` |  |
| `citta_da` | `origin_city` |  |
| `citta_a` | `destination_city` |  |
| `fase` | `phase` | codes: phase, may be null |
| `decollo` | `takeoff` | may be null |
| `fr24` | `flight_iata` |  |
| `stato` | `status` |  |
| `partenza` | `departure` | may be null |
| `ora_partenza` | `departure_time` | may be null |
| `ora_prevista` | `departure_scheduled_time` | may be null |
| `arrivo` | `arrival` | may be null |
| `ora_arrivo` | `arrival_time` | may be null |
| `ritardo_min` | `delay_min` | may be null |
| `imbarco` | `boarding_time` | may be null |
| `imbarco_stimato` | `boarding_estimated` |  |
| `ora_gate` | `at_gate_time` | may be null |
| `ora_avviarsi` | `leave_for_gate_time` | may be null |
| `prioritario` | `priority` |  |
| `gate` | `gate` | may be null |
| `terminal` | `terminal` | may be null |
| `gate_arrivo` | `arrival_gate` | may be null |
| `terminal_arrivo` | `arrival_terminal` | may be null |
| `nastro` | `baggage_belt` | may be null |
| `passeggeri` | `passengers` |  |
| `aggiornato` | `updated_at` | may be null |
| `fonte` | `source` | codes: source |

### `sensor.boarding_pass_tracker_upcoming_flights`: attribute keys

`[]` is each list item, `.` a nested key. A key that kept its name is listed with the same name twice. The earlier name of the sensor was `voli`.

| Earlier name | Name now | Note |
|---|---|---|
| `elenco` | `list` |  |
| `elenco[].volo` | `list[].flight` |  |
| `elenco[].tratta` | `list[].route` |  |
| `elenco[].data` | `list[].date` |  |
| `elenco[].ora_partenza` | `list[].departure_time` | may be null |
| `elenco[].stato` | `list[].status` |  |
| `elenco[].gate` | `list[].gate` | may be null |
| `elenco[].passeggeri` | `list[].passengers` |  |

### `sensor.boarding_pass_tracker_flight_statistics`: attribute keys

`[]` is each list item, `.` a nested key. A key that kept its name is listed with the same name twice. The earlier name of the sensor was `statistiche_voli`.

| Earlier name | Name now | Note |
|---|---|---|
| `voli` | `flights` |  |
| `cancellati` | `cancelled` |  |
| `deviati` | `diverted` |  |
| `in_programma` | `upcoming` |  |
| `minuti_volo` | `air_time_min` |  |
| `ore_volo` | `air_time_h` |  |
| `durate_stimate` | `estimated_durations` |  |
| `senza_dati` | `no_data` |  |
| `km` | `km` |  |
| `giri_terra` | `earth_laps` |  |
| `ritardo_partenza_min` | `departure_delay_min` |  |
| `ritardo_arrivo_min` | `arrival_delay_min` |  |
| `ritardo_medio_min` | `mean_delay_min` | may be null |
| `puntualita` | `on_time_pct` | may be null |
| `paesi` | `countries` |  |
| `paesi[].codice` | `countries[].code` |  |
| `paesi[].bandiera` | `countries[].flag` |  |
| `paesi[].voli` | `countries[].flights` |  |
| `paesi_totale` | `countries_total` |  |
| `aeroporti` | `airports` |  |
| `aeroporti[].codice` | `airports[].code` |  |
| `aeroporti[].nome` | `airports[].name` |  |
| `aeroporti[].voli` | `airports[].flights` |  |
| `aeroporti_totale` | `airports_total` |  |
| `compagnie` | `airlines` |  |
| `compagnie[].codice` | `airlines[].code` |  |
| `compagnie[].voli` | `airlines[].flights` |  |
| `rotte` | `routes` |  |
| `rotte[].tratta` | `routes[].route` |  |
| `rotte[].voli` | `routes[].flights` |  |
| `classi` | `cabins` |  |
| `passeggeri` | `passengers` |  |
| `passeggeri[].nome` | `passengers[].name` |  |
| `passeggeri[].voli` | `passengers[].flights` |  |
| `passeggeri[].km` | `passengers[].km` |  |
| `passeggeri[].ore` | `passengers[].air_time_h` |  |
| `per_persona` | `by_person` |  |
| `per_persona[].nome` | `by_person[].name` |  |
| `per_persona[].voli` | `by_person[].flights` |  |
| `per_persona[].cancellati` | `by_person[].cancelled` |  |
| `per_persona[].deviati` | `by_person[].diverted` |  |
| `per_persona[].minuti_volo` | `by_person[].air_time_min` |  |
| `per_persona[].durate_stimate` | `by_person[].estimated_durations` |  |
| `per_persona[].km` | `by_person[].km` |  |
| `per_persona[].ritardo_arrivo_min` | `by_person[].arrival_delay_min` |  |
| `per_persona[].ritardo_medio_min` | `by_person[].mean_delay_min` | may be null |
| `per_persona[].puntualita` | `by_person[].on_time_pct` | may be null |
| `per_persona[].paesi` | `by_person[].countries` |  |
| `per_persona[].paesi_totale` | `by_person[].countries_total` |  |
| `per_persona[].aeroporti` | `by_person[].airports_total` |  |
| `per_persona[].compagnie` | `by_person[].airlines` |  |
| `per_persona[].compagnie[].codice` | `by_person[].airlines[].code` |  |
| `per_persona[].compagnie[].voli` | `by_person[].airlines[].flights` |  |
| `per_persona[].piu_lungo` | `by_person[].longest` | may be null |
| `per_persona[].piu_lungo.data` | `by_person[].longest.date` | may be null |
| `per_persona[].piu_lungo.volo` | `by_person[].longest.flight` |  |
| `per_persona[].piu_lungo.tratta` | `by_person[].longest.route` |  |
| `per_persona[].piu_lungo.citta` | `by_person[].longest.cities` |  |
| `per_persona[].piu_lungo.durata_min` | `by_person[].longest.duration_min` | may be null |
| `per_persona[].piu_lungo.km` | `by_person[].longest.km` | may be null |
| `per_persona[].piu_lungo.ritardo_min` | `by_person[].longest.delay_min` | may be null |
| `per_persona[].piu_lungo.stato` | `by_person[].longest.outcome` | codes: outcome |
| `per_persona[].piu_lungo.stimato` | `by_person[].longest.estimated` |  |
| `per_persona[].piu_lungo.passeggeri` | `by_person[].longest.passengers` |  |
| `record` | `records` |  |
| `record.piu_lungo` | `records.longest` | may be null |
| `record.piu_lungo.data` | `records.longest.date` | may be null |
| `record.piu_lungo.volo` | `records.longest.flight` |  |
| `record.piu_lungo.tratta` | `records.longest.route` |  |
| `record.piu_lungo.citta` | `records.longest.cities` |  |
| `record.piu_lungo.durata_min` | `records.longest.duration_min` | may be null |
| `record.piu_lungo.km` | `records.longest.km` | may be null |
| `record.piu_lungo.ritardo_min` | `records.longest.delay_min` | may be null |
| `record.piu_lungo.stato` | `records.longest.outcome` | codes: outcome |
| `record.piu_lungo.stimato` | `records.longest.estimated` |  |
| `record.piu_lungo.passeggeri` | `records.longest.passengers` |  |
| `record.piu_corto` | `records.shortest` | may be null |
| `record.piu_corto.data` | `records.shortest.date` | may be null |
| `record.piu_corto.volo` | `records.shortest.flight` |  |
| `record.piu_corto.tratta` | `records.shortest.route` |  |
| `record.piu_corto.citta` | `records.shortest.cities` |  |
| `record.piu_corto.durata_min` | `records.shortest.duration_min` | may be null |
| `record.piu_corto.km` | `records.shortest.km` | may be null |
| `record.piu_corto.ritardo_min` | `records.shortest.delay_min` | may be null |
| `record.piu_corto.stato` | `records.shortest.outcome` | codes: outcome |
| `record.piu_corto.stimato` | `records.shortest.estimated` |  |
| `record.piu_corto.passeggeri` | `records.shortest.passengers` |  |
| `record.durata_max` | `records.max_duration` | may be null |
| `record.durata_max.data` | `records.max_duration.date` | may be null |
| `record.durata_max.volo` | `records.max_duration.flight` |  |
| `record.durata_max.tratta` | `records.max_duration.route` |  |
| `record.durata_max.citta` | `records.max_duration.cities` |  |
| `record.durata_max.durata_min` | `records.max_duration.duration_min` | may be null |
| `record.durata_max.km` | `records.max_duration.km` | may be null |
| `record.durata_max.ritardo_min` | `records.max_duration.delay_min` | may be null |
| `record.durata_max.stato` | `records.max_duration.outcome` | codes: outcome |
| `record.durata_max.stimato` | `records.max_duration.estimated` |  |
| `record.durata_max.passeggeri` | `records.max_duration.passengers` |  |
| `record.ritardo_max` | `records.max_delay` | may be null |
| `record.ritardo_max.data` | `records.max_delay.date` | may be null |
| `record.ritardo_max.volo` | `records.max_delay.flight` |  |
| `record.ritardo_max.tratta` | `records.max_delay.route` |  |
| `record.ritardo_max.citta` | `records.max_delay.cities` |  |
| `record.ritardo_max.durata_min` | `records.max_delay.duration_min` | may be null |
| `record.ritardo_max.km` | `records.max_delay.km` | may be null |
| `record.ritardo_max.ritardo_min` | `records.max_delay.delay_min` | may be null |
| `record.ritardo_max.stato` | `records.max_delay.outcome` | codes: outcome |
| `record.ritardo_max.stimato` | `records.max_delay.estimated` |  |
| `record.ritardo_max.passeggeri` | `records.max_delay.passengers` |  |
| `per_anno` | `by_year` |  |
| `per_anno[].anno` | `by_year[].year` |  |
| `per_anno[].voli` | `by_year[].flights` |  |
| `per_anno[].ore` | `by_year[].air_time_h` |  |
| `per_anno[].km` | `by_year[].km` |  |
| `per_anno[].ritardo_arrivo_min` | `by_year[].arrival_delay_min` |  |
| `per_anno[].puntualita` | `by_year[].on_time_pct` | may be null |
| `elenco` | `list` |  |
| `elenco[].data` | `list[].date` | may be null |
| `elenco[].volo` | `list[].flight` |  |
| `elenco[].tratta` | `list[].route` |  |
| `elenco[].citta` | `list[].cities` |  |
| `elenco[].durata_min` | `list[].duration_min` | may be null |
| `elenco[].km` | `list[].km` | may be null |
| `elenco[].ritardo_min` | `list[].delay_min` | may be null |
| `elenco[].stato` | `list[].outcome` | codes: outcome |
| `elenco[].stimato` | `list[].estimated` |  |
| `elenco[].passeggeri` | `list[].passengers` |  |
| `elenco_totale` | `list_total` |  |
| `aggiornato` | `updated_on` |  |

## Names of earlier versions

Everything below is for people who read old history, old dashboards or old notes. The current documentation does not use these names.

### Identity of «App Casa» and of the standalone service

| What | App Casa | Standalone service 0.1.0 |
|---|---|---|
| MQTT prefix (default) | `casa` | `boarding_pass_tracker` |
| Availability topic | `casa/stato` | `boarding_pass_tracker/availability` |
| Notification group | `casa` | `boarding_pass_tracker` (still the group of the integration) |
| HTTP port | `9995` | `10000` |

| App Casa entity id | Entity id now |
|---|---|
| `sensor.casa_prossimo_volo` | `sensor.boarding_pass_tracker_next_flight` |
| `sensor.casa_voli` | `sensor.boarding_pass_tracker_upcoming_flights` |
| `sensor.casa_statistiche_voli` | `sensor.boarding_pass_tracker_flight_statistics` |

### Database tables of the older Italian schema

| Italian table | English table |
|---|---|
| `documenti` | `documents` |
| `voli` | `flights` |
| `carte` | `boarding_passes` |
| `uso_api` | `api_usage` |
| `gate_pos` | `gate_positions` |

The integration refuses a database of that older schema untouched.
