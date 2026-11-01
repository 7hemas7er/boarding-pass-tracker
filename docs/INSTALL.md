# Installation

Boarding Pass Tracker is a Home Assistant integration. You install it with HACS, optionally add a decoder for
photos and PDFs, set it up from the Home Assistant screens, and build three iOS Shortcuts on each phone.

## What you need

- Home Assistant **2026.10.0 or later**, and [HACS](https://hacs.xyz/).
- The Home Assistant **Companion app** on each phone, and a **person** entity for each user: the notifications go
  to the app, and the person's position times the gate reminder.
- Optional: the **Barcode Decoder**, to read photos, screenshots and PDFs (Wallet passes and flights typed by hand
  work without it); a free [AviationStack](https://aviationstack.com/) key for flight status; the
  [Flightradar24 integration](https://github.com/AlexandrErohin/home-assistant-flightradar24) for a live position.
- An address of your Home Assistant that your phone can reach **from outside your home**, if you want to send a
  pass from an airport. Nabu Casa, a reverse proxy or a VPN such as Tailscale all work: the Shortcuts only need a
  URL and a token. The integration accepts up to 25 MiB; a reverse proxy or relay may set a lower limit of its own (nginx: 1 MiB by default) and answer with its own error page. Send one large photo through your remote address once to check.

## 1. Install the integration with HACS

1. In HACS open **⋮ › Custom repositories**.
2. Add `https://github.com/7hemas7er/boarding-pass-tracker` with the type **Integration**.
3. Find **Boarding Pass Tracker** in HACS, **Download** it, and restart Home Assistant.

The integration needs nothing else from you: no Lovelace resource, no `www` folder and no configuration file. The two
cards are loaded by the integration itself.

## 2. The barcode decoder (optional)

The decoder is a small separate program: a photo or PDF goes in, the texts of its barcodes come out, and it keeps
nothing. Without it, «Manda volo» still works for Wallet passes, and a photo or PDF is refused with the message
«Per leggere foto e PDF serve il decodificatore, che non è configurato». It reads JPEG, PNG, WebP, HEIC and PDF.

### As a Home Assistant OS app

1. In Home Assistant open **Settings › Apps › App store › ⋮ › Repositories** and add
   `https://github.com/7hemas7er/barcode-decoder`.
2. Open **Barcode Decoder**, **Install** and **Start**. The Supervisor builds the image on your device; the app has
   no host port and nothing to configure.

Its address for Home Assistant is the app's host name on the internal network: the app ID with `_` replaced by `-`,
then `:8099`.

| Installed as | Address |
|---|---|
| a local app (copied to the local app share) | `http://local-barcode-decoder:8099` |
| from the repository above | `http://<repository hash>-barcode-decoder:8099` (the hash is in the app's address in the app store) |

When you add the integration, the setup suggests the right address by itself on Home Assistant OS.

### As a plain Docker container

For Home Assistant Container, or any other host:

```sh
docker run -d --name barcode-decoder --restart unless-stopped --read-only --tmpfs /tmp:size=256m \
  --memory 1500m --pids-limit 64 --cap-drop ALL --security-opt no-new-privileges \
  -p 127.0.0.1:8099:8099 ghcr.io/7hemas7er/barcode-decoder:1.0.0
```

- Home Assistant with `network_mode: host`: the address is `http://127.0.0.1:8099`.
- Home Assistant on a Docker network: put both containers on it, leave out `-p`, and use
  `http://barcode-decoder:8099`. **The host name must be the container's name on that network**: Home Assistant
  does not resolve `127.0.0.1` to another container.

**Never publish port 8099 on your LAN or the internet.** The decoder has no authentication: it holds no data, and
the private network is its only protection. The details are in the decoder's own documentation
(`barcode_decoder/DOCS.md` in its repository).

### Check it

One command per route (`-sS` shows a resolve error instead of an empty answer):

```sh
# local app, from the Terminal & SSH app
curl -sS 'http://local-barcode-decoder:8099/health?selftest=1'
# app from the repository: the host name is <repository hash>-barcode-decoder
curl -sS 'http://<repository hash>-barcode-decoder:8099/health?selftest=1'
# Docker, on the host
curl -sS 'http://127.0.0.1:8099/health?selftest=1'
```

The answer has `"ok": true` and `"selftest": "ok"`.

## 3. Add the integration

Open **Settings › Devices & services › Add integration › Boarding Pass Tracker**. Only one entry can exist. The
screens, in order (every field is explained in [CONFIGURATION.md](CONFIGURATION.md)):

1. «Boarding Pass Tracker» (`user`). A text that says what leaves your Home Assistant (see
   [PRIVACY.md](PRIVACY.md)); no fields. If another integration already uses one of the three sensor ids, the setup
   stops and says which.
2. «Dati trovati» (`adopt`), only when `/config/boarding_pass_tracker/tracker.db` exists already (a reinstall,
   or data copied there by hand): the counts of what it holds.
3. «Persone» (`people`): pick the Home Assistant users who will use the Shortcuts (you can pick nobody and add
   people later with «Aggiungi persona»).
4. One form per person (`person`, titled «Persona per {user}»): the **name** stored in the flight data (lower-case letters), the **Home
   Assistant user** whose token identifies them, the **phone** for notifications (a `notify.mobile_app_…` service),
   and the **person** entity for the gate reminder.
5. «Servizi» (`services`), all optional: the AviationStack key, the monthly cap of calls (95), the decoder
   address (tested at once), and the three Flightradar24 entities, filled in by themselves when that integration is
   there.

Everything but the AviationStack key can be changed later under the entry's **Configure** button; the key under
**Reconfigure**; people under **Add person** and the person's own menu.

## 4. A token for each person

A Shortcut proves who you are with a **long-lived access token** of your Home Assistant user. Create it on your own
profile page (bottom left of the Home Assistant sidebar): **Security › Long-lived access tokens › Create token**,
give it a name such as `shortcuts`, and copy the token at once: Home Assistant shows it only once.

- **Each person pastes their own token on their own phone.** Never send a token in a message.
- A token opens the whole Home Assistant API of its user, not only this integration. **A dedicated non-admin Home
  Assistant user per person is safer** (the token then cannot change your configuration), but it is not required:
  each person may use the token of their existing user. The user must be linked to a person here, active, and not
  limited to the local network whenever the Shortcut uses a remote address: Nabu Casa, a proxy, or a VPN such as Tailscale, whose 100.x addresses Home Assistant does not count as local (the Shortcut gets 401 through them, even at home).
- If a token leaks, delete it on the same page: it stops working at once. See [PRIVACY.md](PRIVACY.md#tokens).

## 5. The three Shortcuts

Each phone needs three Shortcuts: «Manda volo» (a Wallet pass, photo, screenshot or PDF), «Aggiungi volo» (a flight
typed by hand) and «Modifica volo» (change or delete a flight). The names are up to you; the notifications and the
dashboard call them by these names.

### The start of all three: address and token

Begin each Shortcut with two **Text** actions, and give each one an **import question** (the Shortcut's settings,
under its Setup or Import Questions):

| Text action | Contains | Import question (for example) |
|---|---|---|
| address | your Home Assistant address, for example `https://<your-home-assistant>`: no `/` at the end and no path | "Indirizzo di Home Assistant" |
| token | your token | "Il tuo token" |

Every **Get Contents of URL** action below uses these two texts, so you write them once per Shortcut.

**Never share a Shortcut that is filled in.** A field with an import question is cleared when the Shortcut is
shared, and the person who adds it is asked for their own value. A field without one may keep your value: Apple's
guide does not say, so check before you share, and prefer to rebuild the Shortcut on the other phone. Each person
pastes their own token on their own device.

In every **Get Contents of URL** action open **Show More** and add the header `Authorization` with the value
`Bearer ` followed by the token text. The three Shortcuts below use the paths `/api/boarding_pass_tracker/upload`,
`/api/boarding_pass_tracker/choices` and `/api/boarding_pass_tracker/flight` ([ARCHITECTURE.md](ARCHITECTURE.md#http-api)).
**Get Contents of URL** returns the body of an error reply too, so these Shortcuts show every `message`, also the
refusals.

### «Manda volo»

1. Shortcuts › **+** › name it «Manda volo». In its details (ⓘ) turn on **Show in Share Sheet** and set the input
   types to **Wallet Passes**, **Images**, **PDFs** and **Files**.
2. Add the address and token texts (above). If there is no input, ask **Select Photos** (for a screenshot of a
   boarding pass).
3. Add **Get Contents of URL**:
   - URL: the address text followed by `/api/boarding_pass_tracker/upload`
   - Method: **POST**
   - Header: `Authorization` = `Bearer ` and the token text
   - Request Body: **File**, with **Shortcut Input** as the file.
4. Add **Get Dictionary Value**: key `message` in **Contents of URL**.
5. Add **Show Notification** (or **Show Result**) with the **Dictionary Value**.

**No photo conversion.** The old recipe converted HEIC photos to JPEG with a **Convert Image** action. With a
decoder configured you do not need it: the decoder reads HEIC itself, and Wallet passes and PDFs must never be
converted. Without a decoder, a photo gets the reply «Per leggere foto e PDF serve il decodificatore, che non è
configurato» and nothing is stored.

Usage:

- Wallet pass: in Wallet open the pass, tap **···** › **Share** › «Manda volo».
- Photo or screenshot of a boarding pass: in Photos, Share › «Manda volo». The whole barcode must be in the picture.
- PDF from the airline, in Mail or Files: Share › «Manda volo».

The reply is, for example, «Ricevuto: lo leggo e ti mando una notifica.»; the notification follows once the pass has
been read, usually within seconds. Sending the same file twice answers «Questo l'avevi gia' mandato il» and the
date, and changes nothing. The limit is 25 MiB.

### «Aggiungi volo»

Adds a flight by hand, upcoming or past. Use the address and token of «Manda volo».

1. Shortcuts › **+** › name it «Aggiungi volo» (**Show in Share Sheet** off). Add the address and token texts.
2. **Ask for Input**, Text, prompt "Volo (es. FR 1234)" › **Set Variable** `Volo`.
3. **Ask for Input**, Date, prompt "Giorno della partenza (quello della carta d'imbarco)" › **Format Date**, format
   **Custom** `yyyy-MM-dd` › **Set Variable** `Data`.
4. **Ask for Input**, Text, prompt "Partenza: codice dell'aeroporto (es. BLQ)" › **Set Variable** `Da`; then
   "Arrivo: codice dell'aeroporto (es. STN)" › **Set Variable** `A`.
5. **Ask for Input**, Text, prompt "Passeggeri e posti (es. Alice 12A business, Bob)", with your first name as the
   default answer (type first names only: a name typed by hand is shown in Home Assistant, history included, exactly as typed) › **Set Variable** `Passeggeri`.
6. Four more **Ask for Input**, Text, each optional (an empty answer means "I do not know"):
   - "Partenza: ora locale (es. 06:30), vuoto se non la sai" › `OraPartenza`
   - "Arrivo: ora locale (es. 08:45), vuoto se non la sai" › `OraArrivo`
   - "Terminal (vuoto se non lo sai)" › `Terminal`
   - "Gate (vuoto se non lo sai)" › `Gate`
7. **Get Contents of URL**: URL the address text followed by `/api/boarding_pass_tracker/flight`, Method **POST**,
   the `Authorization` header, Request Body **JSON** with ten **Text** fields: `action` = `add`, `flight` = `Volo`,
   `date` = `Data`, `origin` = `Da`, `destination` = `A`, `passengers` = `Passeggeri`, `departure_time` =
   `OraPartenza`, `arrival_time` = `OraArrivo`, `terminal` = `Terminal`, `gate` = `Gate`.
8. **Get Dictionary Value**: key `message` in **Contents of URL** › **Show Notification**.

What to type, and what the integration does with it, is in [FLIGHTS.md](FLIGHTS.md#flights-typed-by-hand): the
flight as on the pass, the airport codes, the passengers separated by commas with the seat and the class after the
name (`business`, `economy`, `premium`, `prima`), and the **local** time of each airport. The reply says what was
stored: check the airport codes in it.

### «Modifica volo»

Changes any flight, also one sent with «Manda volo» or already flown, or deletes it.

1. Shortcuts › **+** › name it «Modifica volo» (**Show in Share Sheet** off). Add the address and token texts.
2. **Get Contents of URL**: URL the address text followed by `/api/boarding_pass_tracker/choices`, Method **GET**,
   the `Authorization` header › **Set Variable** `Risposta`.
3. **Get Dictionary Value**: key `choices` in `Risposta` › **Set Variable** `Elenco`. If **Count** of `Elenco` is
   `0`: show the `message` of `Risposta` and **Stop This Shortcut**.
4. **Choose from List** `Elenco`, prompt "Quale volo?" › **Set Variable** `Scelta`.
5. **Get Dictionary Value**: key `details` in `Risposta`, then the key `Scelta` in it › **Set Variable** `Dettagli`.
   Then the key `ref` in `Dettagli` › **Set Variable** `Rif`.
6. **Choose from Menu**, prompt "Cosa cambio?", with five items:
   - "Passeggeri e posti": ask for Text with the `passengers` value of `Dettagli` as the default (the whole new
     list: a name taken out is removed, a class word after a name changes its class) › `Passeggeri`; `Azione` =
     `edit`.
   - "Data": ask for a Date with the `date` value as the default › **Format Date** `yyyy-MM-dd` › `Data`;
     `Azione` = `edit`.
   - "Orari": two Text questions with the `departure_time` and `arrival_time` values as defaults, "-" to clear one
     › `OraPartenza`, `OraArrivo`; `Azione` = `edit`.
   - "Terminal e gate": two Text questions with the `terminal` and `gate` values as defaults, "-" to clear one ›
     `Terminal`, `Gate`; `Azione` = `edit`.
   - "Elimina il volo": **Choose from Menu**, prompt "Elimino questo volo?", with "Elimina" (`Azione` = `delete`)
     and "Annulla" (**Stop This Shortcut**).
7. After the menu: **Get Contents of URL**, URL the address text followed by `/api/boarding_pass_tracker/flight`,
   Method **POST**, the `Authorization` header, Request Body **JSON** with the fields `action` = `Azione`,
   `ref` = `Rif`, `date` = `Data`, `passengers` = `Passeggeri`, `departure_time` = `OraPartenza`, `arrival_time` =
   `OraArrivo`, `terminal` = `Terminal` and `gate` = `Gate`. A variable the chosen item did not set arrives empty,
   and an empty field is ignored.
8. **Get Dictionary Value**: key `message` › **Show Notification**.

The list starts with the flights that are not over yet, the nearest first, then the others, newest first (at most
200). `details[...]` holds, for the chosen flight, its `ref`, `date`, `passengers`, `classes`, `departure_time`,
`arrival_time`, `terminal` and `gate`. If the flight changed after the list was read, the request is refused with
«Il volo è cambiato nel frattempo: riapri»; **a delete is final**.

## Verify

Do these once, on each phone, after the Shortcuts are built.

1. **Settings › Devices & services › Boarding Pass Tracker**: the entry is loaded, its device has three sensors
   (`sensor.boarding_pass_tracker_next_flight`, `..._upcoming_flights`, `..._flight_statistics`) and there is no
   repair.
2. Open a browser on `https://<your-home-assistant>/api/boarding_pass_tracker/choices` without the token: Home
   Assistant answers 401. With the token (for example `curl -H @<file> ...`, with the header in a private file),
   it answers 200 and your person's name in `user`.
3. «Aggiungi volo» with a test flight in the future: the reply starts with «Aggiunto», and the «Voli» sensor counts
   one more. Then «Modifica volo» › "Elimina il volo": the flight goes.
4. «Manda volo» with a real Wallet pass: the reply says it was received and the notification «Carta d'imbarco letta»
   follows. Send the same pass again: the reply says you sent it before.
5. With a decoder: «Manda volo» with a screenshot of a boarding pass (also a HEIC photo).
6. Open the dashboard (see [dashboards/README.md](../dashboards/README.md)): both cards show.
7. Look at the Home Assistant log: no error from `custom_components.boarding_pass_tracker`. For the audit lines,
   set `logger › logs › custom_components.boarding_pass_tracker: info`.

## Update

Update from HACS and restart Home Assistant. The database is upgraded in place on the first start, after a copy
is made in `/config/boarding_pass_tracker/backups/`; the copy is kept until you delete it. If an update cannot
upgrade the data, the entry refuses to start and the data stays as it was; the message says where the copy is.

## Uninstall (Disinstallare)

1. **Settings › Devices & services › Boarding Pass Tracker › ⋮ › Delete.** The integration stops and its cards go
   with it.
2. **The data stays.** Your flights, photos and passes with full names remain in
   `/config/boarding_pass_tracker`, and Home Assistant shows a persistent notification that says where, how many
   files and how big. To delete them, remove that folder with the SSH app or the File editor. Home Assistant
   backups made so far still contain them: see [PRIVACY.md](PRIVACY.md#uninstalling-disinstallare).
3. Delete the tokens on each profile page, the three Shortcuts on each phone, the decoder app or container, and the
   HACS repository if you want.
