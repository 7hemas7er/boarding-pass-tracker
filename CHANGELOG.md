# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/).

## [1.0.0] - 2026-11-01

The first public release: Boarding Pass Tracker as a Home Assistant integration, installed with HACS, with an
optional separate decoder for photos and PDFs.

### Added

- **The integration** `boarding_pass_tracker`: a config flow (people, AviationStack key, monthly cap, decoder address,
  Flightradar24 entities), options, one subentry per person, reconfigure and reauthentication, in Italian and
  English. Only one entry can exist. See [docs/CONFIGURATION.md](docs/CONFIGURATION.md).
- **The HTTP API of the Shortcuts** under `/api/boarding_pass_tracker/`, signed in with the long-lived token of the
  person's Home Assistant user (`Authorization: Bearer`), also from outside the home through Nabu Casa, a reverse
  proxy or a VPN. The three Shortcuts are built once, with import questions for the address and the token.
- **Boarding passes** from Wallet (`.pkpass`), and from photos, screenshots and PDFs through the decoder; flights
  followed with AviationStack (free plan, at most 95 calls a month by default) and Flightradar24; notifications for
  every change and a reminder to walk to the gate. See [docs/FLIGHTS.md](docs/FLIGHTS.md).
- **Flights typed by hand:** the flight, the day, the airports, the passengers with seat and class, and optionally the
  local departure and arrival times, the terminal and the gate. The time zone of each airport comes from
  airportsdata (MIT) and the IANA table. AviationStack's data wins over what you typed.
- **Three sensors** (next flight, upcoming flights, flight statistics) and **three cards**, loaded by the integration
  through one loader module: no Lovelace resource and no `/config/www` file. The cards are the next flight on an arc,
  the statistics, and **«Gestisci voli»**, which adds, changes or deletes a flight from the dashboard with the Home
  Assistant session of the person signed in, so with no token. See [dashboards/README.md](dashboards/README.md).
- **The barcode decoder** as a separate repository (a Home Assistant OS app and a Docker image): JPEG, PNG, WebP,
  **HEIC** and PDF in, barcode texts out, nothing kept. Photos and PDFs are refused with a clear message when no
  decoder is configured.
- **Repairs:** the sensor id taken, the decoder unreachable, and a person's notify service missing. Diagnostics with
  counts only, and system health.
- **Backup hooks**, so that a Home Assistant backup holds a database that opens. The data is in
  `/config/boarding_pass_tracker` (mode 0700, files 0600); a database of an earlier schema is upgraded after a copy
  in `backups/`.
- The card files are loaded with `?v=<version>-<hash of the frontend files>`: a changed card reaches the browsers
  without a new version number.

### Earlier history

Boarding Pass Tracker started as the flight part of a private household service, then as a private standalone
service (0.1.0, Linux host, MQTT sensors). Neither was published; the integration keeps their sensor ids and can
open their databases (schema 1).
