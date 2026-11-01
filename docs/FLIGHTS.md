# Flights

Boarding passes are read with fixed rules: there is no model. Flight status comes from AviationStack within a
monthly budget, and you are told only about changes. The gate reminder is timed for each traveller. A flight can
also be typed by hand, with times, class, terminal and gate, and any flight edited or deleted, with two more
Shortcuts.

## Boarding passes

Two ways in, both through the Shortcut «Manda volo» ([INSTALL.md](INSTALL.md#manda-volo)):

- **Wallet pass** (`.pkpass`). The file is recognised by its content: a ZIP with `pass.json` at the root, with at
  most 200 entries (a ZIP with more is refused, nothing stored) and a `pass.json` of at most 512 KB. This needs no
  decoder.
- **Photo, screenshot or PDF** of a boarding pass (JPEG, PNG, WebP, HEIC, PDF). The file is stored and queued, and
  the [decoder](INSTALL.md#2-the-barcode-decoder-optional) reads its barcodes (a PDF is rendered first, up to 3
  pages). If one holds a valid BCBP, the pass is saved. Without a decoder configured, the upload is refused with
  the message «Per leggere foto e PDF serve il decodificatore, che non è configurato» and nothing is stored. A photo
  over 50 megapixels is not decoded: its size is read from the file header first (for HEIC from its own header),
  and after 3 attempts the document is in error («immagine di {w}x{h} pixel: troppo grande»). While the decoder is
  down, a photo waits in the queue (a Wallet pass is read meanwhile) and is tried again after 1, 5, 15 and 60
  minutes; after 24 hours it ends in error.

What is read:

- From the **IATA BCBP** (Bar Coded Boarding Pass, Resolution 792) in the barcode message: airline, flight number,
  date, origin, destination, seat, cabin code, passenger name, sequence number. A pass can have up to 4 legs, and
  each leg becomes a flight. The BCBP carries the operating carrier, so a codeshare printed as "AZ 7001" may be
  stored as «FR 1234», and that is the flight that gets queried.
- From the pass **semantics** (iOS 15 and later), when they are for the same flight and day: departure and boarding
  times, the arrival time, the departure terminal and the gate. Without a valid BCBP, the semantics alone are used
  if they have flight, route and departure. Otherwise the pass is archived («E' un pass del Wallet, ma non una carta
  d'imbarco che so leggere», with the reason in brackets).

**The flight date.** The BCBP gives only the day of the year (1–366), not the year. The year is chosen in this
order:

1. **The pass issue date**, when the BCBP has one: in the conditional section of the first leg, the last digit of
   the year and the day of the year. That is the latest year ending in that digit that does not put the date after
   tomorrow. The flight is the first date with its day of the year from the issue date on (from the day before, for
   time zones), in the issue year or the next one, because the flight follows the check-in. If neither year has the
   day (day 366 with no leap year), the upload date decides.
2. Otherwise **the upload date**. The candidates are that day of the year in the year of the upload, the year
   before and the year after. The one nearest to the upload date among those at most 3 days ahead and less than a
   year back is taken. A pass comes with check-in, at most 3 days before the flight, so a day that would be weeks
   ahead is a past flight uploaded later, for the statistics.

The barcode is **not a boarding pass** when: the day is 366 and no date fits; the number of legs, the day of the
year or the flight number contains digits that are not ASCII («²», «٣» or «３»); or the size of the variable part
of a leg is not two hexadecimal digits. An unreadable issue date, seat or sequence number only drops that field.
A Wallet pass whose BCBP is rejected falls back on the semantics, as above. On a photo, the next barcode is tried;
when none is a BCBP, the photo is archived unread, with the outcome «Non trovo il codice a barre di una carta
d'imbarco: l'ho archiviato.».

Storage: one flight per airline, number, date and origin. Two passengers on the same plane share one flight. One
pass per passenger; uploading a pass again updates seat and gate and triggers a new check. **The PNR is not stored
in the database or sent anywhere** (PNR plus surname is enough to manage a booking); it stays only inside the
uploaded file. The full name stays in the local database. Home Assistant only gets the first name, and only when
the pass gives one.

## Flights typed by hand

The Shortcuts «Aggiungi volo» and «Modifica volo» add a flight with no pass to send, and edit or delete any flight,
upcoming or past, through `POST /api/boarding_pass_tracker/flight`. Their recipes are in
[INSTALL.md](INSTALL.md#aggiungi-volo), the requests in [ARCHITECTURE.md](ARCHITECTURE.md#http-api). Every person
may change every flight.

### What you can type

Every field below except the flight, the day, the two airports and the passengers is **optional**.

| Field (`/flight` key) | What to type | Notes |
|---|---|---|
| flight (`flight`) | the airline's 2-character IATA code and up to 4 digits, with an optional letter | leading zeros are dropped; a 3-letter airline code is refused, because AviationStack would never match it |
| day (`date`) | the day of departure at the departure airport: `2026-11-08`, `8/11/2026`, `8.11.2026` or `8-11-2026` | from 1970 to 366 days after today |
| airports (`origin`, `destination`) | IATA codes from the built-in airport list | different from each other |
| passengers (`passengers`) | names separated by commas, each with the **seat** and the **class** after the name if you know them: `Alice 12A business, Bob` | at most 9 by hand; a name has only letters, spaces, apostrophes and hyphens |
| class (inside `passengers`) | `economy` or `economica`, `premium`, `business`, `prima` or `first`, as the last word of the item or the last before the seat | no abbreviations; stored as the BCBP compartment letter Y, W, C, F; a passenger with no class word keeps the class they have |
| departure time (`departure_time`) | the **local** time at the departure airport: `06:30`, `6:30` or `06.30` | 00:00 to 23:59 |
| arrival time (`arrival_time`) | the **local** time at the arrival airport | needs the departure time too; the day is found by itself |
| terminal (`terminal`) | at most 8 letters, digits, spaces or hyphens: `T1`, `terminal 1` | a leading word «terminal» is dropped |
| gate (`gate`) | the same rules: `B12`, `gate b12` | a leading word «gate» is dropped |

A name written all in lower or all in upper case gets capitals ("anna maria" becomes "Anna Maria"); any other is
kept as written. A name that ends in a class word is read as that class ("Anna Prima" is Anna in class prima): add
a surname, or write the class again, if you mean the name. The one exception: when that whole name is already a
passenger of the flight (for example a name stored before this version, sent back as the «Modifica volo» list shows
it), it stays that passenger and gets no class. In an **add**, a single dash `-` in a field means "not
given"; in an **edit** it **clears** the stored value.

**Every field is checked on its own**, and a field that breaks its rule refuses the whole request with an Italian
message that says what to write, for example «Orario di partenza non valido: scrivi l'ora locale dell'aeroporto di
partenza, per esempio 06:30.» or «Terminal non valido: al massimo 8 tra lettere e cifre, per esempio T1.». The
others are «Orario di arrivo non valido: scrivi l'ora locale dell'aeroporto di arrivo, per esempio 08:45.», «Per
l'orario di arrivo serve anche quello di partenza.» and «Gate non valido: al massimo 8 tra lettere e cifre, per
esempio B12.».

### Local times and airport zones

A time you type is the **clock time of that airport**, on the flight's day. The integration turns it into an
instant with the airport's time zone and stores it with its offset, so a flight from Bologna to London is entered
as two local times and arrives with the right duration.

- **Where the zones come from.** The zone of each airport of the built-in list is a data file built from
  [airportsdata](https://github.com/mborsetti/airportsdata) (MIT) plus the IANA zone table for countries that have
  a single zone; the sources and digests are in [THIRD_PARTY.md](../THIRD_PARTY.md). Nothing is downloaded at run time.
- **An airport without a zone** (a few, about 135 of the list) refuses only the time you typed for it, with
  «Non conosco il fuso orario di {city} ({code}): lascia vuoto l'orario.». The rest of the flight can still be added.
- **The arrival day.** The arrival is the first of the days from the day before the departure to two days after,
  whose duration from the departure is between 10 minutes and 24 hours. If none fits: «Orari che non tornano: tra
  partenza e arrivo ci sarebbero meno di 10 minuti o più di 24 ore.»
- **Daylight-saving changes.** A time that does not exist (the clocks jump forward) is moved forward by the gap; an
  ambiguous one (the clocks go back) is the first of the two.

### What wins: typed, pass or AviationStack

Several sources can know a time, a terminal or a gate. When the flight is shown, the order is:

1. **AviationStack's real data** (actual, then estimated, then scheduled times; its gate and terminal), once it
   has an answer for the flight;
2. otherwise what the **pass** says, or what you **typed** (they share the same stored values: a Wallet pass read
   later overwrites them, a photo leaves what you typed).

So a typed departure time is a promise until AviationStack knows better. Your own terminal and gate are never
overwritten in the database, only hidden by AviationStack's while they exist. In «Modifica volo» the list shows what
you typed, never AviationStack's values.

A typed flight from yesterday on gets its checks at 60, 30 and 10 minutes before the typed departure like any other
(see the [check schedule](#check-schedule)), and the gate reminder works for it even if AviationStack never answers.
The statistics use a typed departure and arrival as one more source of the duration (an estimate).

### Followed or only counted

The day decides, against Home Assistant's date:

- **From yesterday on**, the flight is followed exactly like an uploaded pass: its first check is due at once, then
  the change notifications, the [gate reminder](#gate-reminder) and Flightradar24. Yesterday is still followed
  because AviationStack knows yesterday's flights.
- **Before yesterday**, the flight is never checked and only counts in the [statistics](#statistics): flown, with no
  news (`no_data`), and a duration estimated from the distance (or from the typed times).

### A flight that is already there

A flight with the same airline, number, day and departure airport is not added twice: the typed passengers that are
not on it yet are added, a typed seat replaces the seat of a passenger already there, a typed time, terminal or gate
fills a value that is still empty, and nobody is removed. The same flight to another destination is refused with
«{code} del {day} da {origin} c'è già, ma va a {destination}: controlla l'arrivo.».

**A pass uploaded later** for a passenger typed by hand on that flight, with the pass's full name or the same first
name (capitals do not matter), takes that passenger's place: one passenger, not two, with the pass's full name,
document, seat and cabin. By the first name this happens only when exactly one typed passenger of the flight has it.

### Edits («Modifica volo»)

Each request names the flight with the `ref` of the list, a version of the flight, its passengers and its typed
times, terminal and gate (the clock's checks do not change it). If the flight changed after the list was read, an
edit or a delete is refused; an edit whose change is already there (the same request sent again) answers
«Nessuna modifica».

- **The date**: the flight forgets everything of the old day (status, gate, what was notified, the gate reminder
  already sent) and is checked again from scratch, or only counted, by the rule above. **Typed times move with the
  flight**: the departure and arrival keep their local clock times on the new day (with each airport's zone), and
  the reply says «orari spostati al nuovo giorno». Times sent in the same request win. If the same flight already
  exists on the new day, the change is refused.
- **The passengers**: the line of «Modifica volo» is the whole new list. A passenger is named by the first name, as
  in the line, or in full. A passenger kept keeps their row, document and owner, and gets the seat and class of the
  line (a class word changes the class: the reply says «1 classe cambiata» or «{k} classi cambiate»; no word leaves
  it as it is). A new name becomes a typed passenger; a name taken out is removed, with the boarding-pass documents
  and files no other passenger uses. Exactly one name gone and one new name is a rename. The last passenger cannot
  be removed: delete the flight instead.
- **The times, terminal and gate**: the reply says «orario di partenza cambiato», «orario di arrivo cambiato»,
  «terminal cambiato» or «gate cambiato». `-` clears a value. A departure time that still has an arrival time
  cannot be cleared on its own (the reply is «Per l'orario di arrivo serve anche quello di partenza.»): send `-` for
  both, or clear the arrival first. When you move the departure, the arrival keeps its local clock time and its day is
  chosen again.
- **Nothing to change**: when a request has no date, no passengers and none of the four new fields, the reply is
  «Niente da cambiare: manda la data, i passeggeri, gli orari, il terminal o il gate.»
- **Delete**: the flight, all its passengers and the documents that served only that flight, with their files.
  Sending the same pass again then reads it again. Not removed: the AviationStack call count, the cached gate
  positions, the notifications already on the phones, the Home Assistant history, the copy in `backups/` made at the
  upgrade to schema 2 (it keeps the flights of that day with their names; delete it by hand once no rollback is
  needed) and the Home Assistant backups.

**A limit of date changes**: a date fixed by hand on a flight that came from a pass lasts until another file of
that pass is sent: it is read with the BCBP rules and adds the flight of the old date again. The same file sent
again is a duplicate and changes nothing.

## AviationStack

`GET https://api.aviationstack.com/v1/flights?access_key=<key>&flight_iata=<airline><number>`

- **Free plan**: 100 calls a month, and only flights of yesterday and today (the integration also tries tomorrow's,
  in case of time zones). The integration counts its calls per calendar month and stops at the monthly cap
  (`monthly_cap`, default 95). At the cap, the flight's error says so and it is retried hourly; the next month
  starts over. `0` means no calls at all.
- With `flights_enabled` off, passes are still read, and the upload notification says «Lo stato del volo non lo
  seguo: manca la chiave di AviationStack.» even when a key is set; the text is the same as without a key.
- The key is part of the URL, so the integration never logs the URL and does not follow redirects. A rejected key or
  an exhausted plan (401, 403, 429) is retried after 6 hours, other errors after 30 minutes; a rejected key also
  starts the reauthentication flow once.
- Choosing the answer: among the returned entries, the one with the same departure airport and `flight_date`,
  preferring the operating carrier's entry.
- Quirks handled: times are the airport's local time with a fake `+00:00` (the airport's `timezone` applies);
  `estimated` equals `scheduled` even when delayed (the estimate is scheduled plus `delay`); in the air, `actual`
  may be missing; `flight_status` is one of `scheduled`, `active`, `landed`, `cancelled`, `diverted`, `incident`.
- Without a key nothing is called. The flight shows the data from the pass («Dalla carta d'imbarco»), or «Inserito a
  mano», and when a key appears, the flights still due catch up.

### Check schedule

| When | Why |
|---|---|
| Right after upload or add, if the flight is yesterday, today or tomorrow | First status, gate if known |
| 60, 30 and 10 minutes before the estimated departure (the typed one counts too) | Gate and delay |
| Every 30 minutes while delayed by 15 minutes or more, but only from 60 minutes before the **scheduled** departure | A delay known since the morning does not burn the budget |
| 20 minutes after departure | Takeoff, arrival estimate |
| At the expected arrival + 10 minutes (at least 20 minutes after the previous check) | Landing, arrival gate, baggage belt |
| At 04:00 on the flight day, when only the BCBP is known (no time) | First real data |

A punctual flight costs about 5 calls. Checks stop once the flight has landed, is cancelled or diverted, or is a day
past departure with no news. "Today", noon and 04:00 are in **Home Assistant's time zone** (Settings › System ›
General). At the first start after Home Assistant starts, or after a new key, the checks already planned are
recomputed with the current rules; an options reload leaves them alone.

A flight number that AviationStack does not know costs calls as an unknown pass does: asked again every hour from
04:00 of the flight day until noon of the day after, up to about 32 calls for a mistyped number. Check the reply of
«Aggiungi volo», and fix a wrong number by deleting the flight and adding it again.

A check whose flight gets another date, or is deleted, by hand while AviationStack answers is dropped: no
notification and no write, only a log line. The call still counts in the monthly budget.

### Notifications

Sent to every person, with tag `boarding_pass_tracker-f<id>`, only for changes compared with what you already know:

- a gate **different** from one already known: «Gate {gate} (prima {gate})». The first gate discovered is not announced
  on its own: it comes with the gate reminder;
- a delay that changes by at least 15 minutes, including when it shrinks again;
- cancellation, diversion, or an «incident» status.

If Home Assistant is down, the baseline does not move, so the notification is sent on the next check.

## Gate reminder

The notification «avviati al gate», once per person, to each person who uploaded a pass for the flight. A passenger
typed by hand belongs to the person whose `name` is the first word of the typed name, with accents dropped and in
lower case ("Alice" and "Alice Rossi" go to `alice`), and otherwise to the person who typed it.

- **When to be at the gate**: when boarding starts if every passenger on that flight is in first or business (BCBP
  cabin `F`, `A`, `P`, `J`, `C`, `D`, `I`, `Z`, `R`); otherwise 10 minutes before boarding (with mixed cabins, the
  economy rule applies). A typed class counts as the cabin letter.
- **Boarding time**: the one on the pass, shifted by the delay. Otherwise 30 minutes before the estimated
  departure (45 for flights over 4 hours, by AviationStack's duration), shown as «circa». For a flight typed by
  hand the departure you typed is used until AviationStack knows better.
- **When to set off**: the walk time before that. The default is 15 minutes. With the phone's position
  (`person.<name>`, accuracy 200 m or better), the walk is computed:
  - at the airport (within 3 km), and with the gate found on OpenStreetMap: straight-line distance times 1.4 at
    75 m/min, plus 3 minutes, clamped to 3–40 minutes. The message says «Da dove sei sono circa {meters} m,
    {minutes} minuti a piedi.»;
  - at the airport but without a known gate: the 15 minutes;
  - away from the airport: the 15 minutes, and the message says how far you are («Risulti a {away_km} km
    dall'aeroporto: conta anche il viaggio fin li'.»).
- The message names the terminal, when one is known (from AviationStack, the pass or what you typed), and the gate.
- Not sent once the flight has left, or if it is cancelled or diverted. If the gate is not known yet, the message
  says so, and when it is assigned later you get a «gate assegnato» notice.

**Gate positions** come from OpenStreetMap through Overpass (`aeroway=gate` with a matching `ref`, within 4 km of the
airport), and are cached. A gate that is not found is looked up again after 7 days; an Overpass error is retried
after 30 minutes. **Only the airport coordinates and the gate code are sent to Overpass, never the person's
position.**

## Flightradar24 (optional)

With the [Flightradar24 integration](https://github.com/AlexandrErohin/home-assistant-flightradar24), every 5
minutes flights that depart within 3 hours are added to its tracked list (`fr24_add`), and removed once they are
over (`fr24_remove`). A flight that Flightradar24 does not know yet is retried every 30 minutes. Flightradar24 keeps
that list in memory, so it is put back after a Home Assistant restart. The flight card reads the live position,
altitude and speed from `fr24_tracked` (its option `fr24_entity`). If the integration is missing, this step is
skipped. With `flights_enabled` off, nothing is added or removed.

A flight typed by hand is added and removed like any other. After a flight is deleted or gets another date, the next
round (within 5 minutes) removes its code from the tracked list, unless another flight with the same code is within
its tracking time. If Home Assistant gives an error, the removal is tried again at the next round.

## Statistics

`sensor.boarding_pass_tracker_flight_statistics` is computed from all flights and passes (only the flight, the first
name and the cabin are read from the passes, never the full name):

- **Outcome** per flight: `flown` (landed, departed before today, or at least two days old with no news, because the
  free plan does not know the past), `diverted` (it counts as flown too), `cancelled`, `upcoming`.
- **Duration**: takeoff to landing, then gate to gate, then the scheduled times, then the times typed by hand
  (departure and arrival, each in its own zone), then the distance. Values outside 10 minutes to 24 hours are
  discarded and the next source is used. A flight with no times at all gets a duration estimated from the distance:
  40 minutes plus the distance at 850 km/h. The last two sources are estimates: they are counted in
  `estimated_durations`, and the flight's list entry has `estimated: true`. A past flight typed by hand also has no
  delay, counts in `no_data` and has the cabin you typed, or `unknown`.
- **Distance** on the great circle between the airports, and **laps of the Earth** (`earth_laps`, 40 075 km).
- **Delays**: totals of the positive delays at departure and arrival, and the mean at arrival. **Punctuality**: share
  of flights arriving less than 15 minutes late; a diverted flight is never on time.
- **Countries** (with flag emoji), **airports**, **airlines**, **routes**, **cabins** (business / economy /
  unknown), **passengers** by first name, **records** (longest, shortest, longest duration, biggest delay), **per
  year**, and the list of flights: up to the latest 30, fewer when the attributes are cut for size.
- **Who was on board.** Every entry of the list and of the records has `passengers`, the first names on that flight.
  A flight counts for a person only if their pass was uploaded and gives a first name, or their name was typed by
  hand. A typed name counts as it was typed: "Marco Rossi" is another person than the "Marco" of a pass.
- **Per person** (`by_person`): for up to 10 people, most flights first, the same totals over the flown flights they
  were on, their cancelled and diverted flights, their countries and number of airports, their top 3 airlines and
  their longest flight. The dashboard card uses it to show one person's statistics.
- The attributes stay under 12 KB (see [HOME_ASSISTANT.md](HOME_ASSISTANT.md#size)).

The «Statistiche» view of the dashboard shows all of this in one `boarding-pass-tracker-stats-card`, for everyone or
for one person (see [dashboards/README.md](../dashboards/README.md#statistics-card-boarding-pass-tracker-stats-card)).
