# Privacy

What the integration stores, what leaves your Home Assistant, and what protects each. Boarding passes carry
sensitive data: the PNR plus the surname is enough to manage a booking.

## What is stored

All in `/config/boarding_pass_tracker` on your Home Assistant, mode 0700 (the database files and the uploads 0600):

| What | Where | Notes |
|---|---|---|
| Flights, passengers, seats, cabins, statuses | `tracker.db` (SQLite) | the **full name** of a passenger is stored. For a name read from a pass, Home Assistant gets the first name only; a name you type by hand is shown there exactly as typed. Each person's walk distance to the airport or gate (from the phone's position, for the gate reminder) is kept in the flight's snapshot |
| The files you sent | `file/YYYY/MM/<hash>.<ext>` | Wallet passes, photos, screenshots and PDFs **as received**: they contain the barcode, so the PNR and the surname |
| A copy of the database before an upgrade | `backups/tracker-v1-....db` | made once when the data is upgraded to schema 2; kept until you delete it; it holds the same personal data |
| Your AviationStack key | the Home Assistant config entry | redacted from the diagnostics |
| Tokens | in Home Assistant (the token list of each user) and in each person's Shortcuts | see [Tokens](#tokens) |

- **The PNR is not stored in the database and sent nowhere.** It stays inside the uploaded file only.
- The folder is never under `/config/www`, which Home Assistant serves without authentication.
- Home Assistant's history holds the sensors: first names with seats, routes and times, and never PNRs. Surnames stay out for names read from a pass; a name typed by hand («Aggiungi volo», «Modifica volo») goes in as typed, so a surname you type reaches the attributes and the history, which every Home Assistant user can read. **Type first names only** to keep surnames out.
- The diagnostics download holds counts only; the log lines hold no passenger name, header or address (the audit lines name the person who made a change), and never the decoded
  text of a barcode.

## What leaves Home Assistant

Only the destinations below are ever contacted, and each is optional or conditional. Nothing is sent to anyone else,
and there is no telemetry.

| Destination | What it receives | When |
|---|---|---|
| AviationStack | the flight number (for example `FR1234`) and your API key, in the request URL | a key is set and flights are followed; at most the monthly cap of calls |
| Flightradar24 (through its own Home Assistant integration) | the flight code, written to that integration's entities | that integration is installed and its entities are set |
| Overpass / OpenStreetMap | the departure airport's coordinates and the gate code; **never** a person's position | flights are followed and a gate is known; cached after the first answer |
| The Home Assistant app's push relay (Apple, Google) | the title and text of a notification | the phone has no local push channel; the text holds route, times and first names (a name typed by hand as typed), never a PNR; the gate reminder also holds how far you are from the airport or gate |
| The decoder you configure | your photos, screenshots and PDFs, and nothing else | a decoder address is set and a photo or PDF is sent |

The decoder normally runs on your own network (a Home Assistant OS app, or a container next to Home Assistant), keeps
nothing, makes no outgoing connection and logs no text. If you point the integration at a decoder somewhere else,
your photos go there.

The setup's first screen says the same thing in Italian: «Boarding Pass Tracker legge le carte d'imbarco, segue i
voli e ti avvisa. Foto, carte d'imbarco e nomi restano in Home Assistant.» It goes on to list exactly the five
destinations above.

## Photos and passes under /config

Photos and passes are kept as received, for good, until you delete them: the integration does not wipe them. They
are in the folder above, so they are in every Home Assistant backup, and anyone who can read your `/config` can read
them. Do not share your `/config`, your backups or your diagnostics without looking.

Deleting a flight in «Modifica volo» also deletes the files that served only that flight. It does not touch the copy in `backups/` made at the upgrade to schema 2, which keeps every flight that existed then, with names, seats and cabins (delete it by hand once you need no rollback), nor the Home Assistant backups.

## Backups

Home Assistant backups include `/config/boarding_pass_tracker`: the database with its `-wal` and **`-shm`** files, the
photos and the passes, and the `backups/` folder. A backup is as sensitive as the folder: keep your backups private
and encrypted. Old backups keep the data even after you delete the folder or the integration. Details of how the
integration behaves during a backup are in [HOME_ASSISTANT.md](HOME_ASSISTANT.md#backups).

## Tokens

A Shortcut proves who you are with a **long-lived access token** of your Home Assistant user. A token is a password
for the **whole Home Assistant API** of that user, not only for this integration.

- A dedicated **non-admin** Home Assistant user per person is the safer choice: a leaked token then cannot change
  your configuration or read your cameras. It is not required: each person may use the token of their existing user.
- Each person creates their token on their own profile page and pastes it on their own phone. Never send a token in a
  message, and **never share a Shortcut that is filled in**: the import questions are there so that a shared
  Shortcut asks the new owner for their own address and token ([INSTALL.md](INSTALL.md#the-start-of-all-three-address-and-token)).
- To revoke a token, delete it on the profile page: it stops working at once.
- A request with no valid token is refused by Home Assistant itself, and repeated failures can ban the address.
- Your Home Assistant address is reachable from the internet only if you made it so (Nabu Casa, a reverse proxy or a
  VPN such as Tailscale). The integration adds no route outside the authenticated API; the card files are served
  without authentication and hold no data.

## Who sees what

All people see every flight and receive every flight-change notification, and each can edit or delete every flight.
The result of an upload goes to the person who uploaded it. This is a household tool, not a multi-tenant one.

## Uninstalling (Disinstallare)

Deleting the integration (**Settings › Devices & services › Boarding Pass Tracker › ⋮ › Delete**) stops it and
removes its cards, **but keeps `/config/boarding_pass_tracker`**: the photos and passes with full names would
otherwise be lost, and a rollback needs them. Home Assistant says so in a persistent notification, titled «Boarding Pass Tracker», with this text (the folder, the
number of files and their size fill the gaps):

> «Boarding Pass Tracker è stato rimosso, ma i suoi dati restano in {path} ({files} file, {size}): foto e carte d'imbarco con i nomi completi. Se non ti servono più, cancella la cartella (app SSH o File editor). I backup di Home Assistant fatti finora li contengono ancora.»

So, to remove everything:

1. delete the integration;
2. delete the folder with the SSH app or the File editor;
3. delete the Home Assistant backups made before, or accept that they still hold the data;
4. delete the tokens and the Shortcuts, and the decoder.
