# Configuration

Everything is set from Home Assistant's screens: **Settings › Devices & services › Boarding Pass Tracker**. There is
no configuration file and no environment variable. The screens are in Italian; this page quotes their labels
between «…» and gives the key each field is stored under.

Where things live:

| What | Where | Changed with |
|---|---|---|
| The AviationStack key | the config entry's `data` (`aviationstack_key`) | **Reconfigure**, or the repair that asks for a new key |
| Decoder address, monthly cap, Flightradar24 entities, the two switches | the entry's options | **Configure** (the options flow) |
| People | one **person** subentry each: `name`, `ha_user`, `notify`, `person` | **Add person**, and the person's own menu |
| Flights, passes, documents, photos | `/config/boarding_pass_tracker` | the Shortcuts; never by hand |

Changing an option reloads the integration (a few seconds, no restart). Changing a person does not reload it.

## The setup flow

Started with **Add integration › Boarding Pass Tracker**. Only one entry can exist (`single_config_entry`).

### Step `user`: «Boarding Pass Tracker»

No fields. The text says what leaves Home Assistant (see [PRIVACY.md](PRIVACY.md#what-leaves-home-assistant)). The
flow goes on to `adopt` when `/config/boarding_pass_tracker/tracker.db` already exists, otherwise to `people`.

The flow stops with the abort `entity_ids_taken` when the entity ids of the three sensors are already used by
another integration (an old MQTT entity, for example).

### Step `adopt`: «Dati trovati»

No fields. It shows what the existing data holds: flights, documents, files, boarding passes, the schema version,
how many documents are queued or still to be notified. **Submit** uses the data where it is; if it has to be
upgraded to a newer schema, a copy is made first in `backups/`.

Aborts of this step, each leaving the folder untouched:

| Abort | Meaning |
|---|---|
| `newer_database` | «Questi dati sono di una versione più recente di Boarding Pass Tracker: aggiorna l'integrazione.» |
| `foreign_database` | «Il file tracker.db non è di Boarding Pass Tracker: spostalo e riprova.» |
| `old_database_v0` | «Questi dati sono della vecchia App Casa (schema v0): convertili prima con il servizio boarding-pass-tracker 0.1.0.» |
| `broken_database` | «Il file tracker.db è danneggiato: rifai l'esportazione.» |
| `file_mismatch` | «{n} file mancano o non corrispondono: rifai l'esportazione.» |

### Steps for the people

With data found, the flow shows one form per name found in the flight data (`person_found`), then one per name of
the old configuration that has no data yet (`person_config`), then the same user picker as a fresh install
(`people`) for anyone else. A fresh install starts at `people`.

**Step `people`: «Persone»**

| Key | Label | Meaning |
|---|---|---|
| `users` | «Utenti di Home Assistant» | The active, non-system Home Assistant users to add as people. May be empty. One form follows for each. |

**Step `person`: «Persona per {user}»** (a fresh install, one per picked user)

| Key | Label | Meaning |
|---|---|---|
| `name` | «Nome» | The name stored in the flight data: lower-case letters only, no spaces (it defaults to the user's name). |
| `notify` | «Telefono per le notifiche» | A `notify.mobile_app_…` service of that person's phone. Required. The default is the phone of the Companion app registered by that user. You can type a service that is not listed yet. |
| `person` | «Persona» | The `person` entity whose position times the gate reminder. Optional; the default is the person linked to that user, else `person.<name>`. |

**Step `person_found`**, titled Persona and the name (a name found in the data). The name cannot be changed: it links the
data to the person.

| Key | Label | Meaning |
|---|---|---|
| `ha_user` | «Utente di Home Assistant» | The user whose token identifies this person in the Shortcuts. Optional: without it that person cannot use the Shortcuts. A user can be linked to one person only. |
| `notify` | «Telefono per le notifiche» | As above. Required unless you tick `skip`. |
| `person` | «Persona» | As above. |
| `skip` | «Nessuna notifica per questo nome» | No person is created for this name: its flights and documents stay, but it gets no notifications. |

**Step `person_config`**, titled the same way (a name of the old configuration, no data yet)

| Key | Label | Meaning |
|---|---|---|
| `name` | «Nome» | The name, lower-case letters. |
| `ha_user` | «Utente di Home Assistant» | As above. |
| `notify` | «Telefono per le notifiche» | As above. |
| `person` | «Persona» | As above. |

Errors on these forms (also on the person subentry):

| Error | Text |
|---|---|
| `invalid_name` | «Il nome deve avere solo lettere minuscole, senza spazi (per esempio alice).» |
| `name_taken` | «C'è già una persona con questo nome.» |
| `user_taken` | «Questo utente di Home Assistant è già collegato a un'altra persona.» |
| `invalid_notify` | «Il servizio di notifica deve essere un notify.mobile_app_… (l'app Home Assistant sul telefono).» |
| `invalid_person` | «Scegli un'entità person.» |

### Step `services`: «Servizi»

All fields are optional. The AviationStack key is **not tested**: a test call would use one of the month's calls.

| Key | Label | Meaning |
|---|---|---|
| `aviationstack_key` | «Chiave di AviationStack» | For times, delays and gates of the flights. Without a key the flights stay what the passes say. Stored in the entry's `data`. |
| `monthly_cap` | «Chiamate ad AviationStack al mese» | 0 to 1000, default 95 (the free plan has 100). `0` means no call at all. |
| `decoder_url` | «Indirizzo del decodificatore» | `http://host:port` of the Barcode Decoder, no path. Empty means no decoder. On Home Assistant OS the field suggests `http://local-barcode-decoder:8099`, or the repository app's host name. |
| `fr24_add` | «Flightradar24: aggiungi volo» | The `text` entity of the Flightradar24 integration that adds a flight to follow. Filled in from the entity registry when that integration is installed. |
| `fr24_remove` | «Flightradar24: togli volo» | The `text` entity that removes a followed flight. |
| `fr24_tracked` | «Flightradar24: voli seguiti» | The `sensor` entity with the followed flights (the card reads the live position from it). |

The decoder address is checked as you submit: the format first, then a `GET /health` of 5 seconds that must answer
with `api` 1. Errors of this step and of the options:

| Error | Text |
|---|---|
| `invalid_url` | «Indirizzo non valido: scrivi http://host:porta, senza percorso.» |
| `decoder_unreachable` | «Il decodificatore non risponde a {url}: controlla l'app Barcode Decoder o lascia vuoto il campo.» |
| `decoder_wrong_api` | «A {url} risponde qualcosa che non è il decodificatore (api 1).» |

The entry is created with the title «Boarding Pass Tracker».

## Reconfigure and reauthentication

**Reconfigure** (the entry's ⋮ menu) has one field: `aviationstack_key`, «Chiave di AviationStack» (empty means no
key). Step `reconfigure` is titled «Chiave di AviationStack».

When AviationStack rejects the key, Home Assistant starts the step `reauth_confirm`, titled «Chiave di
AviationStack rifiutata», once per run: «AviationStack ha rifiutato la chiave: incollane una valida (piano gratuito:
100 chiamate al mese).» Its field is the same `aviationstack_key`. The error `invalid_key` («Incolla la chiave di
AviationStack.») shows when the field is empty.

A new key makes the integration check the flights still due again, in the next minutes.

## Options (the **Configure** button)

Step `init`, titled «Opzioni». A change reloads the integration.

| Key | Label | Default | Meaning |
|---|---|---|---|
| `decoder_url` | «Indirizzo del decodificatore» | none | As in `services`; checked the same way. |
| `monthly_cap` | «Chiamate ad AviationStack al mese» | 95 | As in `services`. |
| `fr24_add` | «Flightradar24: aggiungi volo» | `text.flightradar24_add_to_track` | As in `services`. |
| `fr24_remove` | «Flightradar24: togli volo» | `text.flightradar24_remove_from_track` | As in `services`. |
| `fr24_tracked` | «Flightradar24: voli seguiti» | `sensor.flightradar24_additional_tracked` | As in `services`. |
| `flights_enabled` | «Segui i voli (AviationStack, gate, Flightradar24)» | on | Off: no call to AviationStack, OpenStreetMap or Flightradar24. Passes are still read. |
| `notify_enabled` | «Manda le notifiche» | on | Off: notifications wait and go out when you turn it on again. |

The Flightradar24 defaults are those of an English installation of that integration; on another language set the
three entities by hand (the options screen lists the integration's own entities).

## People: the person subentry

Subentry type `person`, shown as «Persona». **Add person** («Aggiungi persona») opens the step `user`; a person's
own menu opens `reconfigure`, titled Persona and the name. Both have the same fields:

| Key | Label | Meaning |
|---|---|---|
| `name` | «Nome» | Lower-case letters. It can be changed only while the name is not in the flight data: otherwise «Questo nome è già nei dati dei voli: non si può cambiare. Aggiungi un'altra persona se serve.» (error `name_in_use`). |
| `ha_user` | «Utente di Home Assistant» | The user whose token identifies the person. |
| `notify` | «Telefono per le notifiche» | The `notify.mobile_app_…` service. |
| `person` | «Persona» | The `person` entity for the gate reminder. |

Removing a person keeps its rows. That name then gets no notification, and notifications already waiting for it
stay queued.

**Who may do what.** Identity is the Home Assistant user of the token: the person whose `ha_user` is that user.
A user with no person gets «Non so chi sei: il tuo utente di Home Assistant non è collegato a nessuna persona di
Boarding Pass Tracker.» from every route but `/health`.

## What the integration raises

Home Assistant **Repairs** shows these (they hold no name in their id and clear themselves when fixed):

| Issue | Title | Raised when |
|---|---|---|
| `entity_id_taken` | «Sensore con id diverso» | One of the sensors got another entity id because the wanted one belongs to another entity. |
| `decoder_unreachable` | «Decodificatore non raggiungibile» | The decoder has not answered for 15 minutes and photos are waiting. |
| `notify_service_missing` (one per person) | «Notifiche per {name} ferme» | The person's `notify` service no longer exists (phone renamed or removed): update the person. |

When the entry cannot start because of its data, Home Assistant shows the error instead of loading: `newer_database`,
`foreign_database`, `old_database_v0` (the texts of the aborts above) or `migration_failed`: «L'aggiornamento dei
dati alla versione 2 non è riuscito e i dati sono rimasti come prima: c'è una copia in backups/. Apri una
segnalazione con i log.»
