/**
 * boarding-pass-tracker flight statistics card: the household's flight statistics in a single card, with no emoji.
 *
 * Element name: "boarding-pass-tracker-stats-card" (dashboards use `type: custom:boarding-pass-tracker-stats-card`).
 *
 * Data: sensor.boarding_pass_tracker_flight_statistics, published by the boarding-pass-tracker engine (state = flights
 * flown; attributes from core/flight_stats.py: totals, delays, punctuality, countries, airports, airlines, routes,
 * cabins, passengers, by_person, records, by_year, list). Many fields can be null (flights imported without times):
 * the card skips them or shows «–», never an error. The attribute keys it reads are a contract with the engine; the
 * labels it shows are Italian, like the rest of the user interface.
 *
 * Configuration:
 *   type: custom:boarding-pass-tracker-stats-card
 *   entity: sensor.boarding_pass_tracker_flight_statistics  (default; empty or null: the default)
 *   sections: [summary, years, rankings, records, flights]  (default: all of them, in this order)
 *   distance_unit: km | mi                                  (default km; 1 mi = 1.609344 km)
 *   time_format: h:mm | decimal                             (default h:mm; decimal: «39,4 h»)
 *   person: Ada                                             (default: everyone)
 * The sections are the summary, the years, the rankings, the records and the flight list. With «sections» a dashboard
 * can split the card into several cards (for example the flight list in a card of its own).
 * distance_unit, time_format and person are only the starting values: the viewer changes them from the card (the
 * people at the top, the units from the gear) and the choice stays in that browser (localStorage), the same for
 * every card.
 *
 * With a person chosen: the summary comes from by_person (countries_total, cancelled and diverted too, counted on all
 * of their flights); years, rankings, records and flight list come from the flights in list with their name in the
 * entry's passengers, with a caption when list (the latest flights) does not have all of their flights.
 *
 * Charts in SVG/CSS with no libraries and no network requests; colours only from the HA theme variables.
 * The icons are Material Design Icons paths (Pictogrammers, Apache 2.0), copied here so as not to depend on
 * <ha-icon>. The shadow-DOM internals (CSS classes, data-* attributes and their values, element ids) keep the
 * Italian names of the first release: they are not a contract.
 *
 * Install: nothing to do. The integration loads this file on every page (cards.py and the loader); see
 * dashboards/README.md.
 */
const ICONS = {
  // mdi:airplane (Pictogrammers, Apache 2.0): points north-east
  plane: "M20.56 3.91C21.15 4.5 21.15 5.45 20.56 6.03L16.67 9.92L18.79 19.11L17.38 20.53L13.5 13.1" +
    "L9.6 17L9.96 19.47L8.89 20.53L7.13 17.35L3.94 15.58L5 14.5L7.5 14.87L11.37 11L3.94 7.09L5.36 5.68" +
    "L14.55 7.8L18.44 3.91C19 3.33 20 3.33 20.56 3.91Z",
  // mdi:alert-circle-outline (Pictogrammers, Apache 2.0)
  alert: "M11,15H13V17H11V15M11,7H13V13H11V7M12,2C6.47,2 2,6.5 2,12A10,10 0 0,0 12,22A10,10 0 0,0 22,12" +
    "A10,10 0 0,0 12,2M12,20A8,8 0 0,1 4,12A8,8 0 0,1 12,4A8,8 0 0,1 20,12A8,8 0 0,1 12,20Z",
  // mdi:cog-outline (Pictogrammers, Apache 2.0)
  settings: "M12,8A4,4 0 0,1 16,12A4,4 0 0,1 12,16A4,4 0 0,1 8,12A4,4 0 0,1 12,8M12,10A2,2 0 0,0 10,12" +
    "A2,2 0 0,0 12,14A2,2 0 0,0 14,12A2,2 0 0,0 12,10M10,22C9.75,22 9.54,21.82 9.5,21.58L9.13,18.93" +
    "C8.5,18.68 7.96,18.34 7.44,17.94L4.95,18.95C4.73,19.03 4.46,18.95 4.34,18.73L2.34,15.27C2.21,15.05 2.27,14.78 2.46,14.63" +
    "L4.57,12.97L4.5,12L4.57,11L2.46,9.37C2.27,9.22 2.21,8.95 2.34,8.73L4.34,5.27C4.46,5.05 4.73,4.96 4.95,5.05" +
    "L7.44,6.05C7.96,5.66 8.5,5.32 9.13,5.07L9.5,2.42C9.54,2.18 9.75,2 10,2H14C14.25,2 14.46,2.18 14.5,2.42" +
    "L14.87,5.07C15.5,5.32 16.04,5.66 16.56,6.05L19.05,5.05C19.27,4.96 19.54,5.05 19.66,5.27L21.66,8.73" +
    "C21.79,8.95 21.73,9.22 21.54,9.37L19.43,11L19.5,12L19.43,13L21.54,14.63C21.73,14.78 21.79,15.05 21.66,15.27" +
    "L19.66,18.73C19.54,18.95 19.27,19.04 19.05,18.95L16.56,17.95C16.04,18.34 15.5,18.68 14.87,18.93L14.5,21.58" +
    "C14.46,21.82 14.25,22 14,22H10M11.25,4L10.88,6.61C9.68,6.86 8.62,7.5 7.85,8.39L5.44,7.35L4.69,8.65L6.8,10.2" +
    "C6.4,11.37 6.4,12.64 6.8,13.8L4.68,15.36L5.43,16.66L7.86,15.62C8.63,16.5 9.68,17.14 10.87,17.38L11.24,20" +
    "H12.76L13.13,17.39C14.32,17.14 15.37,16.5 16.14,15.62L18.57,16.66L19.32,15.36L17.2,13.81C17.6,12.64 17.6,11.37 17.2,10.2" +
    "L19.31,8.65L18.56,7.35L16.15,8.39C15.38,7.5 14.32,6.86 13.12,6.62L12.75,4H11.25Z",
};
const DEFAULT_ENTITY = "sensor.boarding_pass_tracker_flight_statistics";
const SECTIONS = ["summary", "years", "rankings", "records", "flights"];
const RANKING_ROWS = 5;                                  // rows of a ranking shown before «Mostra altre»
const VISIBLE_FLIGHTS = 8;                               // flights shown in the list before «Mostra tutti»
const ON_TIME_MIN = 15;                                  // minutes at arrival: below, on time (as in flight_stats.py)
const RELIABLE_FLIGHTS = 3;                              // with fewer timed flights the punctuality is only indicative
const FLOWN = ["flown", "diverted"];                     // the flights counted in «flights» and in by_year (flight_stats.py)
const EARTH_CIRCUMFERENCE_KM = 40075;                    // km at the equator (as in flight_stats.py)
const KM_PER_MILE = 1.609344;                            // km in a statute mile
const DISTANCE_UNITS = ["km", "mi"];
const HOUR_FORMATS = ["h:mm", "decimal"];
// The settings buttons: data-scelta and the ids imp-<k> keep the option names of the first release, and the button
// text (Italian) is also its data-valore. Shadow-DOM internals, not a contract.
const PANEL_KEYS = { distance_unit: "unita_distanza", time_format: "formato_ore" };
const PANEL_VALUES = { km: "km", mi: "mi", "h:mm": "h:mm", decimal: "decimali" };
const STORAGE_KEY = "boarding-pass-tracker-stats-card";            // localStorage key: the viewer's choices
// tells the other cards of the page that the choices changed
const CHOICE_EVENT = "boarding-pass-tracker-stats-card-choices";
const MONTHS = ["gen", "feb", "mar", "apr", "mag", "giu", "lug", "ago", "set", "ott", "nov", "dic"];
const MONTHS_LONG = ["gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio", "agosto",
  "settembre", "ottobre", "novembre", "dicembre"];
const AIRLINES = {
  A3: "Aegean Airlines", AA: "American Airlines", AC: "Air Canada", AF: "Air France", AI: "Air India", AM: "Aeroméxico",
  AR: "Aerolíneas Argentinas", AS: "Alaska Airlines", AT: "Royal Air Maroc", AV: "Avianca", AY: "Finnair",
  AZ: "ITA Airways", B6: "JetBlue", BA: "British Airways", BT: "airBaltic", BY: "TUI Airways", CA: "Air China",
  CM: "Copa Airlines", CX: "Cathay Pacific", D8: "Norwegian", DE: "Condor", DL: "Delta Air Lines",
  DS: "easyJet Switzerland", DY: "Norwegian", EC: "easyJet Europe", EI: "Aer Lingus", EK: "Emirates",
  EN: "Air Dolomiti", ET: "Ethiopian Airlines", EW: "Eurowings", EY: "Etihad Airways", FR: "Ryanair", HV: "Transavia",
  I2: "Iberia Express", IB: "Iberia", JL: "Japan Airlines", JU: "Air Serbia", KE: "Korean Air", KL: "KLM",
  KM: "KM Malta Airlines", LA: "LATAM", LG: "Luxair", LH: "Lufthansa", LO: "LOT Polish Airlines", LS: "Jet2",
  LX: "Swiss", MS: "EgyptAir", NH: "ANA", NO: "Neos", OS: "Austrian Airlines", OU: "Croatia Airlines", PC: "Pegasus",
  QF: "Qantas", QR: "Qatar Airways", RK: "Ryanair UK", RO: "TAROM", SA: "South African Airways", SK: "SAS",
  SN: "Brussels Airlines", SQ: "Singapore Airlines", SV: "Saudia", TK: "Turkish Airlines", TO: "Transavia France",
  TP: "TAP Air Portugal", U2: "easyJet", UA: "United Airlines", UX: "Air Europa", V7: "Volotea", VY: "Vueling",
  W4: "Wizz Air Malta", W6: "Wizz Air", WK: "Edelweiss", WN: "Southwest", WS: "WestJet", X3: "TUI fly",
  ZB: "Air Albania",
};
const STATUS_LABELS = { cancelled: ["Cancellato", "err"], diverted: ["Deviato", "warn"], upcoming: ["In programma", "pri"] };

// ---------------------------------------------------------------- formats (it-IT, without depending on Intl)
const has = (o, k) => Object.prototype.hasOwnProperty.call(o, k);   // never the prototype's properties

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function num(x) {                                        // a finite number or null (numeric strings included)
  if (x === null || x === undefined || x === "" || typeof x === "boolean") return null;
  const n = Number(x);
  return Number.isFinite(n) ? n : null;
}

const list = (x) => (Array.isArray(x) ? x.filter((v) => v && typeof v === "object") : []);
const obj = (x) => (x && typeof x === "object" && !Array.isArray(x) ? x : {});
const str = (x) => (typeof x === "string" ? x : x == null ? "" : String(x));
const capitalize = (s) => (s ? s[0].toUpperCase() + s.slice(1) : s);
const joinAnd = (xs) => (xs.length > 1 ? `${xs.slice(0, -1).join(", ")} e ${xs[xs.length - 1]}` : xs[0] || "");   // «a, b e c»

function nf(n, dec = 0) {                                // 12345 -> «12.345»; nf(0.25, 2) -> «0,25»
  n = num(n);
  if (n === null) return "–";
  let [i, d] = Math.abs(n).toFixed(dec).split(".");
  i = i.replace(/\B(?=(\d{3})+(?!\d))/g, ".");
  return (n < 0 && Number(i.replace(/\./g, "") + (d || "")) !== 0 ? "−" : "") + i + (d ? "," + d : "");
}

function nfShort(n) {                                    // for narrow labels: 12.300 -> «12,3k»
  n = num(n);
  if (n === null) return "–";
  if (Math.abs(n) < 1000) return nf(n);
  return nf(n / 1000, Math.abs(n) < 100000 ? 1 : 0).replace(/,0$/, "") + "k";
}

const flightsText = (n) => `${nf(n)} ${num(n) === 1 ? "volo" : "voli"}`;

// The units of the drawing in progress: _applyUnits() sets them before every drawing (synchronous), so the formats
// stay plain functions. miles: distances in miles; decimal: hours with decimals («10,1 h») instead of hours and
// minutes («10:05»).
const U = { miles: false, decimal: false };

const distance = (km) => { km = num(km); return km === null ? null : U.miles ? km / KM_PER_MILE : km; };
const distUnit = () => (U.miles ? "mi" : "km");
const distText = (km) => { const d = distance(km); return d === null ? "" : `${nf(d)} ${distUnit()}`; };

function hm(min) {                                       // 605 -> «10:05», 9843 -> «164:03»
  min = num(min);
  if (min === null) return "–";
  const m = Math.max(0, Math.round(min));
  return `${nf(Math.floor(m / 60))}:${String(m % 60).padStart(2, "0")}`;
}

const hoursDec = (min) => nf(Math.max(0, min) / 60, 1);  // 605 -> «10,1»

function flightDuration(min, withUnit = false) {         // one flight: «10:05» (with the unit «10:05 h») or «10,1 h»
  min = num(min);
  if (min === null) return "–";
  return U.decimal ? `${hoursDec(min)} h` : `${hm(min)}${withUnit ? " h" : ""}`;
}

function totalTime(min) {                                // a total of minutes: «45 min», then «164:03 h» or «164,1 h»
  min = num(min);
  if (min === null) return null;
  const m = Math.max(0, Math.round(min));
  if (m < 60) return { value: String(m), unit: "min" };
  return { value: U.decimal ? hoursDec(m) : hm(m), unit: "h", full: `${nf(Math.floor(m / 60))} h ${m % 60} min` };
}

function delayText(min) {                                // 3 -> «3 min», 95 -> «1:35 h» or «1,6 h», 3000 -> «2 g 2 h»
  min = num(min);
  if (min === null) return "–";
  const m = Math.max(0, Math.round(min));
  if (m < 60) return `${m} min`;
  if (m < 24 * 60) return `${U.decimal ? hoursDec(m) : hm(m)} h`;
  let days = Math.floor(m / 1440), hours = Math.round((m % 1440) / 60);
  if (hours === 24) { days += 1; hours = 0; }
  return hours && days < 10 ? `${days} g ${hours} h` : `${nf(days)} g`;
}

/** Minutes of a year or of a passenger: «air_time_min» if flight_stats.py publishes them, then the top-level
 * air_time_min when the group is the same as all the flights, otherwise the «air_time_h» (rounded to one decimal). */
function minutesOf(x, a, flights) {
  const m = num(x.air_time_min);
  if (m !== null) return m;
  const h = num(x.air_time_h), total = num(a.air_time_min);
  if (h === null) return null;
  if (total !== null && flights !== null && num(x.flights) === flights && Math.abs(h * 60 - total) <= 3) return total;
  return h * 60;
}

function hoursText(min) {                                // minutes -> «10:05 h», «10,1 h», «45 min»
  const t = totalTime(min);
  return t ? `${t.value} ${t.unit}` : "–";
}

function parseDay(s) {                                   // «YYYY-MM-DD» -> {year, month (0-11), day}; null if not a real date
  const x = /^(\d{4})-(\d{2})-(\d{2})/.exec(str(s));
  if (!x) return null;
  const year = Number(x[1]), month = Number(x[2]) - 1, day = Number(x[3]);
  return month >= 0 && month < 12 && day >= 1 && day <= new Date(Date.UTC(year, month + 1, 0)).getUTCDate()
    ? { year, month, day } : null;
}

function longDate(s) {
  const d = parseDay(s);
  return d ? `${d.day} ${MONTHS[d.month]} ${d.year}` : "";
}

let _countryNames;
function countryName(code) {                             // «NL» -> «Paesi Bassi»; the code if Intl does not know it
  const c = str(code).toUpperCase();
  if (!/^[A-Z]{2}$/.test(c)) return str(code) || "–";
  try {
    _countryNames = _countryNames || new Intl.DisplayNames(["it"], { type: "region" });
    const n = _countryNames.of(c);
    return n && n !== c ? n : c;
  } catch (e) {
    return c;
  }
}

function airlineCode(flight) {                           // «FR 1234» -> «FR»; «U21234» -> «U2»
  const s = str(flight).trim().toUpperCase();
  const x = /^([A-Z0-9]{2})(?=\s|\d)/.exec(s) || /^([A-Z0-9]{2,3})\b/.exec(s);
  return x ? x[1] : "";
}

const airlineName = (code) => { const c = str(code).toUpperCase(); return has(AIRLINES, c) ? AIRLINES[c] : ""; };
const airlineLabel = (code) => airlineName(code) || str(code);

function splitRoute(s) {                                 // «BLQ → STN» -> ["BLQ", "STN"]
  const p = str(s).split(/\s*(?:→|->)\s*/);
  return p.length === 2 && (p[0] || p[1]) ? p : null;
}

function routeHtml(s, cls = "") {                        // the codes with the faint arrow, on one line
  const p = splitRoute(s);
  if (!p) return `<span class="${cls}">${esc(s) || "–"}</span>`;
  return `<span class="${cls}">${esc(p[0]) || "–"}<span class="freccia">→</span>${esc(p[1]) || "–"}</span>`;
}

/** The cities: each one whole (at most cut short with an ellipsis), wrapping only after the arrow:
 * «Bologna →» / «Londra Stansted». */
function cityRouteHtml(s) {
  const p = splitRoute(s);
  const cn = (t, tail = "") => `<span class="cn"><span class="cn-t">${esc(t) || "–"}</span>${tail}</span>`;
  return p ? `${cn(p[0], '<span class="freccia">→</span>')} ${cn(p[1])}` : cn(s);
}

function tone(pct) {                                     // status colour for the punctuality
  return pct === null ? "var(--c-sub)" : pct >= 80 ? "var(--c-ok)" : pct >= 60 ? "var(--c-warn)" : "var(--c-err)";
}

// ---------------------------------------------------------------- one person's flights (from list)
function itemNames(v) {                                  // the names in the entry's passengers, cleaned up
  return Array.isArray(v && v.passengers) ? v.passengers.map((n) => str(n).trim()).filter(Boolean) : [];
}

const sameName = (x, y) => x.toLowerCase() === y.toLowerCase();
const countryCodes = (x) => (Array.isArray(x) ? x : [])
  .map((p) => str(p && typeof p === "object" ? p.code : p).trim()).filter(Boolean);

function coverageOf(flown) {                             // how many flights durations, delays and punctuality rest on
  const timed = flown.filter((v) => num(v.duration_min) !== null);
  return { duration: timed.length, estimated: timed.filter((v) => v.estimated === true).length,
    delay: flown.filter((v) => num(v.delay_min) !== null).length,
    punctuality: flown.filter((v) => num(v.delay_min) !== null || v.outcome === "diverted").length };
}

/** [[key, flights]] from the most frequent (ties in alphabetical order); keysOf(entry) gives the keys of one flight. */
function countBy(flown, keysOf) {
  const m = new Map();
  for (const v of flown) for (const k of new Set(keysOf(v))) if (k) m.set(k, (m.get(k) || 0) + 1);
  return [...m].sort((x, y) => y[1] - x[1] || (x[0] < y[0] ? -1 : x[0] > y[0] ? 1 : 0));
}

const routeOf = (v) => { const t = splitRoute(v.route); return t && t[0] && t[1] ? [`${t[0]} → ${t[1]}`] : []; };

/** The years of a group of flown flights, like by_year but with the exact minutes (on time as in flight_stats.py,
 * but without the departure delay, which list does not have). */
function yearsOf(flown) {
  const m = new Map();
  for (const v of flown) {
    const d = parseDay(v.date);
    if (!d) continue;
    const r = m.get(d.year) || { year: d.year, flights: 0, minutes: 0, km: 0, ok: 0, known: 0 };
    r.flights += 1;
    r.minutes += num(v.duration_min) || 0;
    r.km += num(v.km) || 0;
    const delay = num(v.delay_min);
    if (v.outcome === "diverted" || delay !== null) { r.known += 1; if (v.outcome !== "diverted" && delay < ON_TIME_MIN) r.ok += 1; }
    m.set(d.year, r);
  }
  return [...m.values()].sort((x, y) => x.year - y.year).map((r) => ({ year: r.year, flights: r.flights, minutes: r.minutes,
    km: r.km, punctuality: r.known ? Math.round((100 * r.ok) / r.known) : null }));
}

// ---------------------------------------------------------------- the viewer's choices (localStorage)
let localChoices = {};                                   // without localStorage (private window, blocked): this page's choices
// The last write failed (for example storage full) but getItem can still return the old value: then this page's
// choices win, otherwise the choice just made would be ignored
let writeFailed = false;

function readChoices() {
  try {
    const s = JSON.parse(window.localStorage.getItem(STORAGE_KEY) || "null");
    if (s && typeof s === "object" && !Array.isArray(s)) return writeFailed ? { ...s, ...localChoices } : { ...localChoices, ...s };
  } catch (e) { /* localStorage missing or blocked: this page's choices remain */ }
  return { ...localChoices };
}

function saveChoice(key, value) {
  localChoices = { ...readChoices(), [key]: value };
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(localChoices));
    writeFailed = false;
  } catch (e) { writeFailed = true; /* likewise: the choice holds until the page is closed */ }
}

// ---------------------------------------------------------------- charts
/** Columns in SVG: x in percent (it widens with the card), heights and texts in px. Labels that do not fit in their
 * column switch to the short form; if that is not enough, they stay only on the tallest and on the last column. */
function columnChart(data, aria) {
  const n = data.length, H = 150, headroom = 22, base = H - 24;
  const max = Math.max(0, ...data.map((d) => d.v || 0));
  const w = Math.max(10, Math.min(26, Math.floor((300 / n) * 0.55)));
  const slot = 300 / n, fits = (t) => str(t).length * 7 + 4 <= slot;
  const useShort = !data.every((d) => fits(d.text)) && data.every((d) => fits(d.short ?? d.text));
  const allLabels = useShort || data.every((d) => fits(d.text));
  const iMax = data.findIndex((d) => (d.v || 0) === max);
  const step = Math.max(1, Math.ceil((Math.max(...data.map((d) => str(d.x).length)) * 5.8 + 4) / slot));
  const parts = data.map((d, i) => {
    const cx = ((i + 0.5) / n) * 100, v = d.v || 0;
    const h = max > 0 && v > 0 ? Math.max(4, (v / max) * (base - headroom)) : 0;
    const foot = Math.min(h, 4);
    const label = allLabels ? (useShort ? d.short ?? d.text : d.text) : d.short ?? d.text;
    const bar = h > 0
      ? `<g transform="translate(${-w / 2} 0)"><rect class="col" x="${cx}%" y="${base - h}" width="${w}" height="${h}" rx="4"/>` +
        `<rect class="col" x="${cx}%" y="${base - foot}" width="${w}" height="${foot}"/></g>` +
        (allLabels || i === iMax || i === n - 1 ? `<text class="cv" x="${cx}%" y="${base - h - 7}" text-anchor="middle">${esc(label)}</text>` : "")
      : d.future ? "" : `<g transform="translate(${-w / 2} 0)"><rect class="col-zero" x="${cx}%" y="${base - 2}" width="${w}" height="2" rx="1"/></g>`;
    const xLabel = (n - 1 - i) % step === 0
      ? `<text class="cx${d.highlight ? " forte" : ""}${d.future ? " futuro" : ""}" x="${cx}%" y="${H - 6}" text-anchor="middle">${esc(d.x)}</text>` : "";
    return `<g><title>${esc(d.title)}</title><rect class="hit" x="${(i / n) * 100}%" y="0" width="${100 / n}%" height="${H}"/>${bar}${xLabel}</g>`;
  }).join("");
  return `<svg class="colonne" height="${H}" role="img" aria-label="${esc(aria)}">
    <line class="asse" x1="0" x2="100%" y1="${base + 0.5}" y2="${base + 0.5}"/>${parts}</svg>`;
}

function ring(pct, neutral) {                            // punctuality as a ring (computed length, no pathLength)
  const p = pct === null ? 0 : Math.max(0, Math.min(100, pct));
  const r = 36, C = 2 * Math.PI * r;
  const color = neutral ? "var(--c-sub)" : tone(pct);
  return `<svg class="anello" width="84" height="84" viewBox="0 0 88 88" role="img"
      aria-label="${pct === null ? "Puntualità non nota" : `Puntualità ${esc(nf(pct))} per cento${neutral ? ", indicativa" : ""}`}">
    <circle cx="44" cy="44" r="${r}" class="anello-fondo" stroke-width="8"/>
    ${p > 0 ? `<circle cx="44" cy="44" r="${r}" class="anello-pieno" stroke-width="8" stroke-dasharray="${((p / 100) * C).toFixed(2)} ${C.toFixed(2)}"
      transform="rotate(-90 44 44)" style="stroke:${color}"/>` : ""}
    <text x="44" y="44" text-anchor="middle" dominant-baseline="central" class="anello-txt${neutral ? " neutro" : ""}">${pct === null ? "–" : `${esc(nf(pct))}%`}</text>
  </svg>`;
}

function lapsBar(laps) {                                 // one notch per lap: the completed ones full, the last one partly
  if (laps === null || laps <= 0 || laps > 10) return "";
  const notches = Math.max(1, Math.ceil(laps - 1e-9));
  return `<div class="giri-barra" aria-hidden="true">${Array.from({ length: notches }, (_, i) =>
    `<span><i style="width:${Math.max(0, Math.min(1, laps - i)) * 100}%"></i></span>`).join("")}</div>`;
}

// ---------------------------------------------------------------- the card
class FlightStatsCard extends HTMLElement {
  static getStubConfig() {
    return { entity: DEFAULT_ENTITY };
  }

  setConfig(config) {
    const c = { ...obj(config) };
    c.entity = typeof c.entity === "string" && c.entity.trim() ? c.entity.trim() : DEFAULT_ENTITY;   // empty or null: the default
    const chosen = [].concat(c.sections ?? []).map((s) => str(s).trim().toLowerCase()).filter(Boolean);
    const unknown = chosen.filter((s) => !SECTIONS.includes(s));
    // the warnings are for whoever writes the dashboard config: Italian text, naming the config keys and values
    if (unknown.length) console.warn(`boarding-pass-tracker-stats-card: sezioni sconosciute ignorate: ${unknown.join(", ")}`);
    c.sections = SECTIONS.filter((s) => chosen.includes(s)).sort((a, b) => chosen.indexOf(a) - chosen.indexOf(b));
    if (!c.sections.length) c.sections = SECTIONS.slice();
    const dist = str(c.distance_unit).trim().toLowerCase();
    const hours = str(c.time_format).trim().toLowerCase().replace(/^hh?:?mm$/, "h:mm").replace(/^decimals$/, "decimal");
    if (dist && !DISTANCE_UNITS.includes(dist)) console.warn(`boarding-pass-tracker-stats-card: distance_unit «${dist}» sconosciuta (km o mi)`);
    if (hours && !HOUR_FORMATS.includes(hours)) console.warn(`boarding-pass-tracker-stats-card: time_format «${hours}» sconosciuto (h:mm o decimal)`);
    c.distance_unit = DISTANCE_UNITS.includes(dist) ? dist : "km";
    c.time_format = HOUR_FORMATS.includes(hours) ? hours : "h:mm";
    c.person = str(c.person).trim();
    this._config = c;
    this._st = undefined;                                // new configuration: redraw in any case
    this._drawn = false;
    if (!this.shadowRoot) {
      this.attachShadow({ mode: "open" });
      this.shadowRoot.addEventListener("click", (e) => this._onClick(e));
    }
    if (this._hass) this.hass = this._hass;
  }

  connectedCallback() {                                  // another card of the page changed person or units
    this._onChoices = this._onChoices || (() => { if (this._drawn && this._signature() !== this._drawnSignature) this._redraw(); });
    window.addEventListener(CHOICE_EVENT, this._onChoices);
  }

  disconnectedCallback() {
    if (this._onChoices) window.removeEventListener(CHOICE_EVENT, this._onChoices);
  }

  /** The valid choice: the viewer's (localStorage), then the configuration. person «» is «Tutti» (everyone). */
  _choice(k) {
    const s = readChoices()[k];
    if (k === "person") return typeof s === "string" ? s.trim() : this._config.person;
    return (k === "distance_unit" ? DISTANCE_UNITS : HOUR_FORMATS).includes(s) ? s : this._config[k];
  }

  _signature() {
    return this._config ? JSON.stringify(["distance_unit", "time_format", "person"].map((k) => this._choice(k))) : "";
  }

  _applyUnits() {
    U.miles = this._choice("distance_unit") === "mi";
    U.decimal = this._choice("time_format") === "decimal";
  }

  _onClick(e) {
    const closest = (sel) => (e.target.closest ? e.target.closest(sel) : null);
    const more = closest("[data-espandi]");
    if (more) {
      const box = more.closest(".espandibile");
      const open = box.classList.toggle("aperto");
      more.textContent = open ? "Mostra meno" : more.dataset.espandi;
      more.setAttribute("aria-expanded", String(open));
      return;
    }
    const chip = closest("[data-persona]");
    if (chip) {
      const name = chip.dataset.persona;
      if (name !== ((this._view && this._view.name) || "")) this._choose("person", name, ["[data-persona]", "data-persona", name]);
      return;
    }
    const seg = closest("[data-scelta]");
    if (seg) {                                           // from the rendered internals back to the option and its value
      const k = Object.keys(PANEL_KEYS).find((x) => PANEL_KEYS[x] === seg.dataset.scelta);
      const value = Object.keys(PANEL_VALUES).find((x) => PANEL_VALUES[x] === seg.dataset.valore);
      if (k && value && value !== this._choice(k)) {
        this._choose(k, value, [`[data-scelta="${PANEL_KEYS[k]}"]`, "data-valore", PANEL_VALUES[value]]);
      }
      return;
    }
    const gear = closest("[data-impostazioni]");
    if (gear) {
      this._panelOpen = !this._panelOpen;
      gear.setAttribute("aria-expanded", String(this._panelOpen));
      const panel = this.shadowRoot.querySelector("#impostazioni");
      if (panel) panel.hidden = !this._panelOpen;
      return;
    }
    const btn = closest("[data-metrica]");
    if (btn && btn.dataset.metrica !== this._metric) {
      this._metric = btn.dataset.metrica;
      const box = this.shadowRoot.querySelector("#anni");
      if (box && this._view) {
        this._applyUnits();
        box.innerHTML = this._years(this._view);
        const fresh = box.querySelector(`[data-metrica="${this._metric}"]`);
        if (fresh) fresh.focus();                        // with the keyboard, the focus stays on the button just pressed
      }
    }
  }

  _choose(key, value, focus) {
    saveChoice(key, value);
    this._redraw(focus);
    window.dispatchEvent(new CustomEvent(CHOICE_EVENT));   // the other cards of the page follow
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._config || !this.shadowRoot) return;
    const states = hass && hass.states && typeof hass.states === "object" ? hass.states : {};
    const st = has(states, this._config.entity) ? states[this._config.entity] : undefined;
    if (this._drawn && st === this._st) return;          // only when the sensor's state changes
    this._st = st;
    this._drawn = true;
    this._drawSafely();
  }

  _drawSafely() {
    try {
      this._draw(this._st);
    } catch (e) {                                        // unexpected data must not break the dashboard
      console.error("boarding-pass-tracker-stats-card:", e);
      this.shadowRoot.innerHTML = `<style>${this._styles()}</style><ha-card><div class="box"><div class="pannello">
        ${this._icon("alert", "pannello-icona")}<div><div class="pannello-titolo">Statistiche non leggibili</div>
        <div class="pannello-testo">Dati inattesi da ${esc(this._config.entity)}.</div></div></div></div></ha-card>`;
    }
  }

  /** After a choice (here or in another card): same data, the row of people stays where it was and the focus goes to
   * the equivalent button. focus = [selector, attribute, value]. */
  _redraw(focus) {
    const before = this.shadowRoot.querySelector(".persone");
    const x = before ? before.scrollLeft : 0;
    this._drawSafely();
    const after = this.shadowRoot.querySelector(".persone");
    if (after && x) after.scrollLeft = x;
    if (focus) {
      const btn = [...this.shadowRoot.querySelectorAll(focus[0])].find((el) => el.getAttribute(focus[1]) === focus[2]);
      if (btn) btn.focus({ preventScroll: true });
    }
  }

  getCardSize() {
    return Math.max(3, this._config ? this._config.sections.length * 3 : 12);
  }

  getGridOptions() {                                     // height from the content: with fixed rows it overlaps the card below
    return { columns: 12, rows: "auto", min_columns: 6 };
  }

  _icon(name, cls = "") {
    return `<svg class="icona ${cls}" viewBox="0 0 24 24" aria-hidden="true"><path d="${ICONS[name]}"/></svg>`;
  }

  _draw(st) {
    this._drawnSignature = this._signature();
    this._applyUnits();
    const a = obj(st && st.attributes);
    const items = list(a.list);
    const flights = num(a.flights) ?? num(st && st.state) ?? items.filter((v) => FLOWN.includes(v.outcome)).length;
    const sections = this._config.sections;
    let body, animate = false;
    this._view = null;
    if (!st || (num(a.flights) === null && num(st.state) === null && !items.length)) {
      body = `<div class="pannello">${this._icon("alert", "pannello-icona")}<div>
        <div class="pannello-titolo">Statistiche dei voli non disponibili</div>
        <div class="pannello-testo">${st ? "Il sensore non ha ancora pubblicato i dati" : "Sensore non trovato"}:
          <span class="mono">${esc(this._config.entity)}</span></div></div></div>`;
    } else if (!flights) {                               // no flight flown: the panel, and the list if a flight is there already
      const upcoming = (num(a.upcoming) || 0) > 0 || items.some((v) => v.outcome === "upcoming");
      const parts = [];
      if (sections.includes("summary") || !sections.includes("flights") || !items.length) parts.push(this._empty(upcoming));
      if (sections.includes("flights") && items.length) parts.push(this._flightList(a, this._viewOf(a, 0, true)));
      body = parts.join('<div class="stacco"></div>');
    } else {
      const v = this._view = this._viewOf(a, flights);
      const parts = sections.map((s) => {
        if (s === "summary") return this._summary(a, v);
        if (s === "years") { const h = this._years(v); return h ? `<section id="anni">${h}</section>` : ""; }
        if (s === "rankings") return v.pp ? this._personRankings(a, v) : this._rankings(a, flights);
        if (s === "records") return this._records(a, v);
        return this._flightList(a, v);
      }).filter(Boolean);
      if (!parts.length) parts.push(`<div class="didascalia">Nessun volo di ${esc(v.name)} da mostrare qui.</div>`);
      body = this._header(v) + parts.join('<div class="stacco"></div>') +
        (parseDay(a.updated_on) ? `<div class="aggiornato">Aggiornato al ${esc(longDate(a.updated_on))}</div>` : "");
      animate = !this._animated;                         // the bars grow only the first time, not at every update
      this._animated = true;
    }
    this.shadowRoot.innerHTML = `<style>${this._styles()}</style><ha-card><div class="box${animate ? " entra" : ""}">${body}</div></ha-card>`;
    const row = this.shadowRoot.querySelector(".persone"), active = row && row.querySelector(".attivo");
    if (this._ro) this._ro.disconnect();
    if (!row) return;
    if (active && active.offsetLeft + active.offsetWidth > row.clientWidth) row.scrollLeft = active.offsetLeft - 4;
    // the row scrolls: faded on the side where there are more names (also when the card gets its width later)
    const fade = () => {
      row.classList.toggle("piu-sx", row.scrollLeft > 2);
      row.classList.toggle("piu-dx", row.scrollLeft + row.clientWidth < row.scrollWidth - 2);
    };
    row.addEventListener("scroll", fade, { passive: true });
    if (typeof ResizeObserver === "function") {
      this._ro = this._ro || new ResizeObserver(() => this._fade && this._fade());
      this._ro.observe(row);
    }
    this._fade = fade;
    fade();
  }

  /** What the card shows: all the flights or those of one person in by_person (name, pp, entries of list, flown).
   * complete: list has all the flown flights of the group, so the counts made on list are exact; whole: list has
   * all the flights, the cancelled ones too (which are not among the flown). */
  _viewOf(a, flights, everyone = false) {
    const items = list(a.list), total = num(a.list_total), seen = new Set();
    const people = list(a.by_person).filter((p) => {
      const n = str(p.name).trim().toLowerCase();
      if (!n || seen.has(n)) return false;
      seen.add(n);
      return true;
    });
    const names = people.map((p) => str(p.name).trim());
    const chosen = everyone ? "" : this._choice("person");
    const i = chosen ? names.findIndex((n) => sameName(n, chosen)) : -1;
    if (i < 0) {
      return { a, people: names, name: "", pp: null, items, flown: items.filter((v) => FLOWN.includes(v.outcome)),
        flights, complete: total === null || total <= items.length };
    }
    const pp = people[i], name = names[i];
    const mine = items.filter((v) => itemNames(v).some((n) => sameName(n, name)));
    const flown = mine.filter((v) => FLOWN.includes(v.outcome));
    const theirs = Math.max(0, num(pp.flights) ?? flown.length);
    return { a, people: names, name, pp, items: mine, flown, flights: theirs, complete: flown.length >= theirs,
      whole: total === null || total <= items.length };
  }

  /** Cancelled and diverted flights of a person: from by_person, counted on all of their flights. Without them (a
   * sensor from before by_person had these counters) from list, but only when it has all of them; otherwise null,
   * and the card does not show them. */
  _personCounters(v) {
    const count = (s) => v.items.filter((x) => x.outcome === s).length;
    return { cancelled: num(v.pp.cancelled) ?? (v.whole ? count("cancelled") : null),
      diverted: num(v.pp.diverted) ?? (v.complete ? count("diverted") : null) };
  }

  // ---------- header: people and units
  _header(v) {
    const chip = (name) => {
      const on = name === v.name;
      return `<button type="button" data-persona="${esc(name)}" class="${on ? "attivo" : ""}" aria-pressed="${on}"${name ? ` title="${esc(name)}"` : ""}>${name ? esc(name) : "Tutti"}</button>`;
    };
    const row = (title, k, options) => {
      const current = this._choice(k), id = PANEL_KEYS[k];
      return `<div class="imp-riga"><span id="imp-${id}">${title}</span><div class="segmenti" role="group" aria-labelledby="imp-${id}">${options.map((o) =>
        `<button type="button" data-scelta="${id}" data-valore="${PANEL_VALUES[o]}" class="${o === current ? "attivo" : ""}" aria-pressed="${o === current}">${PANEL_VALUES[o]}</button>`).join("")}</div></div>`;
    };
    const open = Boolean(this._panelOpen);
    return `<div class="testa-card">${v.people.length
      ? `<div class="persone" role="group" aria-label="Statistiche di">${["", ...v.people].map(chip).join("")}</div>` : '<span class="riempi"></span>'}
        <button type="button" class="ingranaggio" data-impostazioni aria-expanded="${open}" aria-controls="impostazioni"
          aria-label="Unità di misura" title="Unità di misura">${this._icon("settings")}</button></div>
      <div class="impostazioni" id="impostazioni"${open ? "" : " hidden"}>
        ${row("Distanza", "distance_unit", DISTANCE_UNITS)}${row("Ore", "time_format", HOUR_FORMATS)}</div>`;
  }

  /** How many flights durations, delays and punctuality rest on: from list if it is complete, else from the counters. */
  _coverage(a, flights) {
    const items = list(a.list), total = num(a.list_total);
    if (total !== null && total <= items.length) return coverageOf(items.filter((v) => FLOWN.includes(v.outcome)));
    const noData = Math.max(0, num(a.no_data) || 0), estimated = Math.max(0, num(a.estimated_durations) || 0);
    return { duration: Math.max(0, flights - Math.max(0, noData - estimated)), estimated,
      delay: Math.max(0, flights - noData - (num(a.diverted) || 0)), punctuality: Math.max(0, flights - noData) };
  }

  // ---------- empty state
  _empty(upcoming) {
    const steps = ["Apri il Wallet e scegli la carta d'imbarco", "Tocca il pulsante ··· in alto e poi Condividi",
      "Scegli «Manda volo»"];
    return `<div class="vuoto">
      <div class="vuoto-icona">${this._icon("plane")}</div>
      <div class="vuoto-titolo">${upcoming ? "Il primo volo è in programma" : "Ancora nessun volo"}</div>
      <div class="vuoto-testo">${upcoming ? "Le statistiche compaiono dopo l'atterraggio. Per aggiungere altri voli:"
        : "Le statistiche compaiono dopo il primo volo. Per aggiungerne uno:"}</div>
      <ol class="passi">${steps.map((p, i) => `<li><span class="passo">${i + 1}</span><span>${esc(p)}</span></li>`).join("")}</ol>
      <div class="vuoto-nota">Orari, ritardi e chilometri arrivano da soli.</div>
    </div>`;
  }

  // ---------- summary
  _summaryAll(a, flights) {
    const km = num(a.km), countries = list(a.countries), airports = list(a.airports);
    return { title: "Voli effettuati", flights, km, laps: num(a.earth_laps) ?? (km !== null ? km / EARTH_CIRCUMFERENCE_KM : null),
      minutes: num(a.air_time_min) ?? (num(a.air_time_h) !== null ? num(a.air_time_h) * 60 : null),
      punctuality: num(a.on_time_pct), meanDelay: num(a.mean_delay_min), totalDelay: num(a.arrival_delay_min),
      nCountries: num(a.countries_total) ?? countries.length,
      countries: countries.filter((p) => str(p.code)).slice(0, 2).map((p) => countryName(p.code)),
      nAirports: num(a.airports_total) ?? airports.length, airports: airports.slice(0, 2).map((x) => str(x.code)).filter(Boolean),
      counts: [num(a.upcoming), num(a.cancelled), num(a.diverted)], period: this._period(a), cover: this._coverage(a, flights) };
  }

  _summaryPerson(v) {
    const pp = v.pp, km = num(pp.km), codes = countryCodes(pp.countries), airports = countBy(v.flown, (x) => splitRoute(x.route) || []);
    const count = (s) => v.items.filter((x) => x.outcome === s).length, { cancelled, diverted } = this._personCounters(v);
    const punctuality = num(pp.on_time_pct), meanDelay = num(pp.mean_delay_min), minutes = num(pp.air_time_min);
    let cover;
    if (v.complete) {
      cover = coverageOf(v.flown);
      if (num(pp.estimated_durations) !== null) cover.estimated = Math.max(0, num(pp.estimated_durations));
    } else {                                             // not all of their flights are in list: no «su N voli» notes
      cover = { duration: minutes ? v.flights : 0, estimated: Math.max(0, num(pp.estimated_durations) || 0),
        delay: meanDelay !== null ? v.flights : 0, punctuality: punctuality !== null ? v.flights : 0 };
    }
    return { title: `Voli di ${v.name}`, flights: v.flights, km, laps: km !== null ? km / EARTH_CIRCUMFERENCE_KM : null,
      minutes, punctuality, meanDelay,
      // countries_total: the pp.countries list can be shortened (30 at most, and fewer if the output is too big)
      totalDelay: num(pp.arrival_delay_min), nCountries: num(pp.countries_total) ?? (codes.length || (v.flights ? null : 0)),
      countries: codes.slice(0, 2).map(countryName), nAirports: num(pp.airports_total) ?? airports.length,
      airports: airports.slice(0, 2).map((x) => x[0]),
      counts: [count("upcoming"), cancelled, diverted],          // the upcoming flights are the latest: all in list
      period: v.complete ? this._periodOf(v.flown.map((x) => parseDay(x.date)).filter(Boolean).map((d) => d.year)) : "", cover };
  }

  _summary(a, v) {
    const R = v.pp ? this._summaryPerson(v) : this._summaryAll(a, v.flights);
    const { flights, cover, punctuality } = R;
    const t = totalTime(R.minutes), km = distance(R.km);
    const outOf = (n) => (n > 0 ? `su ${flightsText(n)} di ${nf(flights)}` : "nessun volo con orari");
    const why = ' title="Gli altri voli sono stati importati senza orari"';
    const nameList = (names, total) => {
      if (!names.length) return "";
      const rest = (total || 0) - names.length;
      return esc(names.join(", ")) + (rest > 0 ? ` <span class="nowrap">e altri ${esc(nf(rest))}</span>` : "");
    };
    const counters = R.counts.map((n, i) => [n, i])
      .filter(([n]) => n > 0).map(([n, i]) => `${esc(nf(n))} ${[() => "in programma", (x) => (x === 1 ? "cancellato" : "cancellati"),
        (x) => (x === 1 ? "deviato" : "deviati")][i](n)}`).join(" · ");
    const airNote = [cover.duration < flights ? outOf(cover.duration) : "",
      cover.estimated > 0 ? `${nf(cover.estimated)} ${cover.estimated === 1 ? "durata stimata" : "durate stimate"}` : ""]
      .filter(Boolean).map((x) => `<span class="nowrap">${esc(x)}</span>`).join(" · ");
    const laps = R.laps !== null && R.laps > 0 ? R.laps : null;
    const lapsText = laps !== null ? nf(laps, 2).replace(/,?0+$/, "") || "0" : "";
    // delays: with a single flight the mean and the total are the same, hence one tile
    const meanDelay = R.meanDelay, totalDelay = R.totalDelay;
    const partial = cover.delay < flights ? `<div class="nota"${why}>${esc(outOf(cover.delay))}</div>` : "";
    const delayTiles = cover.delay <= 1
      ? [`<div class="tessera centrata"><div class="etichetta">Ritardo all'arrivo</div>
          <div class="valore${cover.delay ? "" : " tenue"}">${cover.delay && totalDelay !== null ? esc(delayText(totalDelay)) : "–"}</div>
          ${cover.delay ? partial : `<div class="nota"${why}>nessun volo con orari</div>`}</div>`]
      : [`<div class="tessera"><div class="etichetta">Ritardo medio</div>
          <div class="valore">${meanDelay !== null ? esc(delayText(meanDelay)) : "–"}</div><div class="nota">all'arrivo</div>${partial}</div>`,
        `<div class="tessera"><div class="etichetta">Ritardo totale</div>
          <div class="valore">${totalDelay !== null ? esc(delayText(totalDelay)) : "–"}</div><div class="nota">sommato sugli arrivi</div></div>`];
    const neutral = cover.punctuality < RELIABLE_FLIGHTS;
    return `<section class="riepilogo">
      <div class="hero">
        <div>
          <div class="hero-testa"><div class="etichetta hero-titolo">${esc(R.title)}</div>${R.period}</div>
          <div class="grande">${esc(nf(flights))}</div>
          ${counters ? `<div class="hero-sotto">${counters}</div>` : ""}
        </div>
        <div class="hero-righe">
          <div><div class="etichetta">In aria</div>
            <div class="medio"${t && t.full ? ` title="${esc(t.full)}"` : ""}>${t ? `${cover.estimated > 0 ? '<span class="circa">≈</span>' : ""}${esc(t.value)}<span class="unita">${t.unit}</span>` : '<span class="tenue">–</span>'}</div>
            ${airNote ? `<div class="nota"${cover.duration < flights ? why : ""}>${airNote}</div>` : ""}</div>
          <div><div class="etichetta">Distanza</div>
            <div class="medio">${km !== null ? `${esc(nf(km))}<span class="unita">${distUnit()}</span>` : '<span class="tenue">–</span>'}</div></div>
        </div>
        ${laps !== null ? `<div class="giri"><div class="giri-testo"><b>${esc(lapsText)}</b> ${Math.abs(laps - 1) < 0.005 ? "giro" : "giri"} della Terra</div>${lapsBar(laps)}</div>` : ""}
      </div>
      <div class="tessere">
        <div class="tessera puntualita" style="grid-row: span ${delayTiles.length}"${neutral ? ' title="Pochi voli con orari: dato indicativo"' : ""}>
          ${ring(punctuality, neutral)}
          <div class="etichetta">Puntualità</div>
          <div class="nota">arrivi entro ${ON_TIME_MIN} min</div>
          ${cover.punctuality < flights ? `<div class="nota"${why}>${esc(outOf(cover.punctuality))}</div>` : ""}
        </div>
        ${delayTiles.join("")}
        <div class="tessera"><div class="etichetta">Paesi</div>
          <div class="valore">${esc(nf(R.nCountries))}</div>
          <div class="nota due-righe">${nameList(R.countries, R.nCountries) || "&nbsp;"}</div></div>
        <div class="tessera"><div class="etichetta">Aeroporti</div>
          <div class="valore">${esc(nf(R.nAirports))}</div>
          <div class="nota due-righe">${nameList(R.airports, R.nAirports) || "&nbsp;"}</div></div>
      </div>
    </section>`;
  }

  _period(a) {                                           // «nel 2026» or «2024 – 2026»
    let years = list(a.by_year).filter((x) => (num(x.flights) || 0) > 0).map((x) => num(x.year)).filter((x) => x !== null);
    if (!years.length) years = list(a.list).filter((v) => FLOWN.includes(v.outcome)).map((v) => parseDay(v.date)).filter(Boolean)
      .map((d) => d.year);
    return this._periodOf(years);
  }

  _periodOf(years) {
    if (!years.length) return "";
    const lo = Math.min(...years), hi = Math.max(...years);
    return `<div class="periodo">${lo === hi ? `nel ${esc(lo)}` : `${esc(lo)} – ${esc(hi)}`}</div>`;
  }

  // ---------- years
  _years(v) {
    const a = v.a;
    if (v.pp) {                                          // one person: years and months from their flights in list
      const rows = yearsOf(v.flown);
      if (!rows.length) return "";
      const note = v.complete ? "" : `Calcolato sugli ultimi ${nf(v.flown.length)} voli di ${v.name} (${nf(v.flights)} in tutto).`;
      return rows.length === 1 ? this._singleYear(rows[0].year, v.flown, note) : this._yearChart(rows, note);
    }
    const years = list(a.by_year).filter((x) => num(x.year) !== null).sort((x, y) => num(x.year) - num(y.year));
    if (!years.length) return "";
    if (years.length === 1) {                            // the figures are already in the summary: the flights month by month
      const year = num(years[0].year), expected = num(years[0].flights), items = list(a.list);
      const counted = v.flown.filter((x) => { const d = parseDay(x.date); return d && d.year === year; }).length;
      const partial = (num(a.list_total) ?? items.length) > items.length;
      const note = expected !== null && expected !== counted
        ? (partial ? `Mesi ricavati dagli ultimi ${nf(items.length)} voli dell'elenco: ${flightsText(counted)} su ${nf(expected)} del ${year}.`
          : "Mesi ricavati dall'elenco dei voli.") : "";
      return this._singleYear(year, v.flown, note);
    }
    const totalFlights = num(a.flights);
    return this._yearChart(years.map((x) => ({ year: num(x.year), flights: num(x.flights), km: num(x.km),
      punctuality: num(x.on_time_pct), minutes: minutesOf(x, a, totalFlights) })), "");
  }

  /** Several years: columns (flights, distance or hours) and a table. rows: [{year, flights, minutes, km, punctuality}],
   * oldest first. The metric ids voli, km and ore are also the data-metrica values. */
  _yearChart(rows, note) {
    const thisYear = new Date().getFullYear();
    const metric = ["voli", "km", "ore"].includes(this._metric) ? this._metric : "voli";
    const labels = { voli: "Voli", km: U.miles ? "Mi" : "Km", ore: "Ore" };
    const data = rows.map((x) => {
      const year = x.year;
      if (metric === "ore") {
        const v = x.minutes || 0, h = v / 60;
        return { x: String(year), v, text: U.decimal ? nf(h, 1) : hm(v), short: nf(Math.round(h)), highlight: year === thisYear,
          title: `${year}: ${hoursText(x.minutes)} in aria` };
      }
      const v = (metric === "km" ? distance(x.km) : num(x.flights)) || 0;
      return { x: String(year), v, text: nf(v), short: metric === "km" ? nfShort(v) : undefined, highlight: year === thisYear,
        title: `${year}: ${metric === "km" ? `${nf(v)} ${distUnit()}` : flightsText(v)}` };
    });
    const table = rows.slice().reverse().map((x) => {
      const p = num(x.punctuality), hours = hoursText(x.minutes);
      return `<div class="tab-riga" role="row"><span role="rowheader">${esc(x.year)}</span><span role="cell">${esc(nf(x.flights))}</span>
        <span role="cell">${esc(hours.replace(/ h$/, ""))}</span><span role="cell">${esc(nf(distance(x.km)))}</span>
        <span role="cell">${p === null ? "–" : `<i class="pallino" style="background:${tone(p)}"></i>${esc(nf(p))}%`}</span></div>`;
    }).join("");
    return `<div class="testata"><h3>Per anno</h3>
        <div class="segmenti" role="group" aria-label="Valore del grafico">${["voli", "km", "ore"].map((m) =>
          `<button type="button" data-metrica="${m}" class="${m === metric ? "attivo" : ""}" aria-pressed="${m === metric}">${labels[m]}</button>`).join("")}</div></div>
      <div class="anni-corpo">${columnChart(data, `${labels[metric]} per anno: ${data.map((d) => `${d.x} ${d.text}`).join(", ")}`)}
      <div class="tabella" role="table" aria-label="Riepilogo per anno">
        <div class="tab-riga tab-capo" role="row"><span role="columnheader">Anno</span><span role="columnheader">Voli</span>
          <span role="columnheader">Ore</span><span role="columnheader">${labels.km}</span><span role="columnheader">Puntuali</span></div>
        ${table}</div></div>${note ? `<div class="didascalia">${esc(note)}</div>` : ""}`;
  }

  /** A single year: the flights month by month (from the flown flights in list). */
  _singleYear(year, flown, note) {
    const today = new Date(), current = year === today.getFullYear(), month = today.getMonth();
    const months = new Array(12).fill(0);
    let counted = 0;
    for (const v of flown) {
      const d = parseDay(v.date);
      if (d && d.year === year) { months[d.month] += 1; counted += 1; }
    }
    if (!counted) return "";
    const chart = columnChart(months.map((n, i) => ({
      x: MONTHS[i], v: n, text: nf(n), highlight: current && i === month, future: current && i > month,
      title: `${MONTHS_LONG[i]} ${year}: ${flightsText(n)}`,
    })), `Voli per mese nel ${year}: ${months.map((n, i) => `${MONTHS[i]} ${n}`).join(", ")}`);
    return `<div class="testata"><h3>Il ${esc(year)}</h3><span class="a-lato">voli per mese</span></div>
      ${chart}${note ? `<div class="didascalia">${esc(note)}</div>` : ""}`;
  }

  // ---------- rankings
  _cities(a) {                                           // airport code -> city name, from airports, list and records
    const m = new Map();
    for (const v of list(a.list).concat(list(Object.values(obj(a.records))))) {
      const t = splitRoute(v.route), c = splitRoute(v.cities);
      if (t && c) t.forEach((k, i) => { if (k && c[i] && c[i] !== k && !m.has(k)) m.set(k, c[i]); });
    }
    for (const x of list(a.airports)) if (x.code && x.name && x.name !== x.code) m.set(str(x.code), str(x.name));
    return m;
  }

  /** One ranking: inline bars; all values equal -> pills without bars; a single entry -> one line.
   * side: the text next to the title; fem: the title's noun is feminine in Italian («altre», «ciascuna»). */
  _group(title, rows, { side = "", fem = false } = {}) {
    rows = rows.filter((r) => r.v !== null && r.v > 0);
    if (!rows.length) return "";
    const head = (aside) => `<div class="gruppo-testa"><h4>${esc(title)}</h4>${aside ? `<span>${esc(aside)}</span>` : ""}</div>`;
    const label = (r) => r.html || esc(r.name);
    if (rows.length === 1) {
      const r = rows[0];
      return `<div class="gruppo">${head(side)}<div class="unico"><span class="rk-nome">${label(r)}</span>
        <span class="rk-sub">${[r.sub, esc(flightsText(r.v)), r.extra].filter(Boolean).join(" · ")}</span></div></div>`;
    }
    if (rows.every((r) => r.v === rows[0].v)) {
      const aside = [`${flightsText(rows[0].v)} ${fem ? "ciascuna" : "ciascuno"}`, side].filter(Boolean).join(" · ");
      return `<div class="gruppo">${head(aside)}<ul class="pastiglie">${rows.map((r) =>
        `<li title="${esc(r.title || r.name)}">${label(r)}</li>`).join("")}</ul></div>`;
    }
    const max = Math.max(...rows.map((r) => r.v));
    const extra = rows.length - RANKING_ROWS > 1 ? rows.length - RANKING_ROWS : 0;   // for a single hidden row no button
    return `<div class="gruppo espandibile">${head(side)}
      <ol class="classifica" aria-label="${esc(title)}">${rows.map((r, i) => `<li class="rk${extra && i >= RANKING_ROWS ? " extra" : ""}"
        title="${esc(r.title || `${r.name}: ${flightsText(r.v)}`)}"><span class="rk-testo"><span class="rk-nome">${label(r)}</span>${r.sub ? `<span class="rk-sub">${r.sub}</span>` : ""}</span>
        <span class="rk-barra"><i style="width:${((r.v / max) * 100).toFixed(1)}%"></i></span><span class="rk-val">${esc(nf(r.v))}</span></li>`).join("")}</ol>
      ${extra ? this._moreButton(`Mostra ${fem ? "altre" : "altri"} ${extra}`) : ""}</div>`;
  }

  _routeRow(t, flights, cities) {                        // one row of the routes ranking
    const p = splitRoute(t), c = p ? [cities.get(p[0]), cities.get(p[1])] : [];
    const cityNames = c[0] && c[1] ? `${c[0]} → ${c[1]}` : "";
    return { name: str(t) || "–", html: routeHtml(t), v: num(flights),
      title: [str(t), cityNames, flightsText(flights)].filter(Boolean).join(" · "),
      sub: cityNames ? `${esc(c[0])}<span class="freccia">→</span>${esc(c[1])}` : "" };
  }

  _airlines(rows) {                                      // [[code, flights]]
    return this._group("Compagnie", rows.map(([c, n]) => ({ name: airlineLabel(c) || "–", v: num(n),
      sub: airlineName(c) ? esc(c) : "" })), { fem: true });
  }

  _rankings(a, flights) {
    const cities = this._cities(a);
    const ofTotal = (total, n) => (total > n ? `${nf(total)} in tutto` : "");
    const airports = list(a.airports), countries = list(a.countries);
    const groups = [
      this._group("Aeroporti", airports.map((x) => ({ name: str(x.name) || str(x.code) || "–", v: num(x.flights),
        sub: x.name && x.name !== x.code ? esc(x.code) : "" })), { side: ofTotal(num(a.airports_total) ?? 0, airports.length) }),
      this._airlines(list(a.airlines).map((x) => [x.code, x.flights])),
      this._group("Rotte", list(a.routes).map((x) => this._routeRow(x.route, x.flights, cities)), { fem: true }),
      this._group("Paesi", countries.map((x) => ({ name: countryName(x.code), v: num(x.flights),
        sub: countryName(x.code) !== str(x.code) ? esc(x.code) : "" })), { side: ofTotal(num(a.countries_total) ?? 0, countries.length) }),
      this._group("Passeggeri", list(a.passengers).map((x) => {
        // the exact minutes of by_person, as in the person's view (passengers[].air_time_h is rounded to 0.1 h)
        const n = str(x.name).trim(), pp = n ? list(a.by_person).find((p) => sameName(str(p.name).trim(), n)) : null;
        const min = num(pp && pp.air_time_min) ?? minutesOf(x, a, flights);
        const details = esc([distText(x.km), min !== null ? hoursText(min) : ""].filter(Boolean).join(" · "));
        return { name: str(x.name) || "–", v: num(x.flights), sub: list(a.passengers).length > 1 ? details : "", extra: details };
      })),
      this._cabins(obj(a.cabins)),
    ].filter(Boolean);
    if (!groups.length) return "";
    return `<section><div class="testata"><h3>Classifiche</h3><span class="a-lato">numero di voli</span></div>
      <div class="gruppi">${groups.join("")}</div></section>`;
  }

  /** One person's rankings: airports, routes and travel companions from their flights in list; countries (in order,
   * without counts) and, if list does not have all of their flights, the top airlines from by_person. */
  _personRankings(a, v) {
    const pp = v.pp, cities = this._cities(a), fromList = [];
    // the groups counted on list alone, named in the caption (Italian text)
    const listed = (label, html) => { if (html && !v.complete) fromList.push(label); return html; };
    const airports = countBy(v.flown, (x) => splitRoute(x.route) || []).slice(0, 8), nAirports = num(pp.airports_total);
    const topAirlines = list(pp.airlines).filter((x) => str(x.code) && num(x.flights) > 0).map((x) => [str(x.code), num(x.flights)]);
    const codes = countryCodes(pp.countries), nCountries = num(pp.countries_total);
    const moreCountries = nCountries !== null && nCountries > codes.length ? ` · ${nf(nCountries)} in tutto` : "";
    const groups = [
      listed("aeroporti", this._group("Aeroporti", airports.map(([c, n]) => {
        const name = cities.get(c);
        return { name: name || c, v: n, sub: name && name !== c ? esc(c) : "" };
      }), { side: nAirports !== null && nAirports > airports.length ? `${nf(nAirports)} in tutto` : "" })),
      !v.complete && topAirlines.length ? this._airlines(topAirlines)
        : listed("compagnie", this._airlines(countBy(v.flown, (x) => [airlineCode(x.flight)]).slice(0, 8))),
      listed("rotte", this._group("Rotte", countBy(v.flown, routeOf).slice(0, 5).map(([t, n]) => this._routeRow(t, n, cities)), { fem: true })),
      codes.length ? `<div class="gruppo"><div class="gruppo-testa"><h4>Paesi</h4><span>dal più visitato${esc(moreCountries)}</span></div>
        <ul class="pastiglie">${codes.map((c) => `<li title="${esc(c)}">${esc(countryName(c))}</li>`).join("")}</ul></div>` : "",
      listed("compagni di viaggio", this._group("Compagni di viaggio",
        countBy(v.flown, (x) => itemNames(x).filter((n) => !sameName(n, v.name))).slice(0, 10).map(([n, c]) => ({ name: n, v: c })))),
    ].filter(Boolean);
    if (!groups.length) return "";
    const note = fromList.length
      ? `${capitalize(joinAnd(fromList))} sugli ultimi ${nf(v.flown.length)} voli di ${v.name} (${nf(v.flights)} in tutto).` : "";
    return `<section><div class="testata"><h3>Classifiche</h3><span class="a-lato">numero di voli</span></div>
      <div class="gruppi">${groups.join("")}</div>${note ? `<div class="didascalia">${esc(note)}</div>` : ""}</section>`;
  }

  _cabins(c) {
    const parts = [["business", "Business", "var(--c-primary)"], ["economy", "Economy", "var(--c-scuro)"],
      ["unknown", "Non nota", "var(--c-neutro)"]].map(([k, n, col]) => ({ n, col, v: Math.max(0, num(c[k]) || 0) }));
    const total = parts.reduce((s, p) => s + p.v, 0);
    if (!total) return "";
    const shown = parts.filter((p) => p.v > 0);
    const pct = (v) => Math.round((v / total) * 100);
    return `<div class="gruppo"><div class="gruppo-testa"><h4>Classe di viaggio</h4></div>
      <div class="segmentata" role="img" aria-label="${esc(shown.map((p) => `${p.n} ${flightsText(p.v)}, ${pct(p.v)}%`).join("; "))}">
        ${shown.map((p) => `<span title="${esc(`${p.n}: ${flightsText(p.v)} (${pct(p.v)}%)`)}" style="flex:${p.v};background:${p.col}"></span>`).join("")}</div>
      <div class="legenda">${shown.map((p) => `<span><i style="background:${p.col}"></i>${esc(p.n)}
        <b>${esc(nf(p.v))}</b><em>${pct(p.v)}%</em></span>`).join("")}</div></div>`;
  }

  // ---------- records
  _records(a, v) {
    const r = obj(a.records);
    const asEntry = (x) => (x && typeof x === "object" && !Array.isArray(x) ? x : null);
    const titles = ["Volo più lungo", "Volo più corto", "Più ore in aria", "Ritardo massimo"];
    const values = [(x) => distText(x.km) || null, (x) => distText(x.km) || null,
      (x) => (num(x.duration_min) !== null ? `${x.estimated ? "≈ " : ""}${flightDuration(x.duration_min, true)}` : null),
      (x) => (num(x.delay_min) !== null ? `+${delayText(x.delay_min)}` : null)];
    let recs = [r.longest, r.shortest, r.max_duration, r.max_delay].map(asEntry), note = "";
    if (v.pp) {
      // a record of everyone with the person on board is theirs too; the longest one is in by_person; the rest comes
      // from list (on a tie the latest, as in flight_stats.py: list is already latest first)
      const onBoard = (x) => (x && itemNames(x).some((n) => sameName(n, v.name)) ? x : null);
      const best = (field, lowest, ok = () => true) => {
        let m = null;
        for (const x of v.flown) {
          const k = num(x[field]);
          if (k !== null && ok(k) && (!m || (lowest ? k < num(m[field]) : k > num(m[field])))) m = x;
        }
        return m;
      };
      const computed = [best("km"), best("km", true), best("duration_min"), best("delay_min", false, (k) => k > 0)];
      const fromList = [];
      recs = recs.map((x, i) => {
        const theirs = (i === 0 && asEntry(v.pp.longest)) || onBoard(x);
        if (theirs) return theirs;
        if (computed[i] && !v.complete) fromList.push(titles[i].toLowerCase());
        return computed[i];
      });
      if (fromList.length) note = `${capitalize(joinAnd(fromList))}: sugli ultimi ${nf(v.flown.length)} voli di ${v.name} (${nf(v.flights)} in tutto).`;
    }
    let defs = titles.map((title, i) => ({ title, i, v: recs[i] && (values[i](recs[i]) || splitRoute(recs[i].route)) ? recs[i] : null,
      value: values[i] }));
    if (v.pp && !v.complete) defs = defs.filter((d) => d.v);   // without all of their flights a missing record is unknown
    if (!defs.some((d) => d.v)) return "";
    // several records of the same flight become a single tile
    const groups = [];
    for (const d of defs) {
      const key = d.v && str(d.v.date) && str(d.v.flight) ? JSON.stringify([str(d.v.date), str(d.v.flight), str(d.v.route)]) : null;
      const g = key && groups.find((x) => x.key === key);
      if (g) g.defs.push(d); else groups.push({ key, v: d.v, defs: [d] });
    }
    const singles = groups.filter((g) => g.defs.length === 1);
    const last = singles.length % 2 ? singles[singles.length - 1] : null;   // in two columns no gap is left
    const tiles = groups.map((g) => {
      const wide = g.defs.length > 1 || g === last ? " larga" : "";
      const attr = `style="--n:${g.defs.length}"`;
      if (!g.v) {
        const d = g.defs[0];
        return `<div class="tessera record vuota${wide}" ${attr}><div class="etichetta">${esc(d.title)}</div>
          <div class="valore tenue">–</div><div class="nota">${d.i === 3 ? "Nessun ritardo all'arrivo" : "Dato non disponibile"}</div></div>`;
      }
      const x = g.v, code = airlineCode(x.flight), airline = airlineName(code);
      const valuesHtml = g.defs.map((d) => `<div class="rec-valore"><div class="etichetta">${esc(d.title)}</div>
        <div class="valore">${d.value(x) ? esc(d.value(x)) : "–"}</div></div>`).join("");
      return `<div class="tessera record${g.defs.length > 1 ? " multi" : ""}${wide}" ${attr}>
        <div class="rec-valori">${valuesHtml}</div>
        <div class="rec-tratta">${routeHtml(x.route)}</div>
        ${x.cities ? `<div class="nota rec-citta">${cityRouteHtml(x.cities)}</div>` : ""}
        <div class="nota rec-piede">${[esc(x.flight), esc(airline), esc(longDate(x.date))].filter(Boolean).join(" · ")}</div></div>`;
    }).join("");
    return `<section><div class="testata"><h3>Record</h3></div><div class="records">${tiles}</div>
      ${note ? `<div class="didascalia">${esc(note)}</div>` : ""}</section>`;
  }

  // ---------- flight list
  _monthCount(entries) {                                 // «4 voli · 1 in programma · 1 cancellato»
    const n = (s) => entries.filter((x) => s.includes(x.v.outcome)).length;
    const flown = n(FLOWN), upcoming = n(["upcoming"]), cancelled = n(["cancelled"]),
      others = entries.length - flown - upcoming - cancelled;
    return [flown ? flightsText(flown) : "", upcoming ? `${upcoming} in programma` : "",
      cancelled ? `${cancelled} ${cancelled === 1 ? "cancellato" : "cancellati"}` : "",
      others ? `${others} ${others === 1 ? "altro" : "altri"}` : ""].filter(Boolean).join(" · ");
  }

  _flightList(a, view) {
    const entries = view.items.map((v) => { const d = parseDay(v.date); return { v, d, k: d ? `${d.year}-${d.month}` : "?" }; });
    const total = num(a.list_total);
    if (!entries.length) {                               // a person whose flights are all older than list
      return view.pp && view.flights ? `<section><div class="testata"><h3>Voli</h3></div><div class="didascalia">I ${esc(nf(view.flights))}
        voli di ${esc(view.name)} sono più vecchi degli ultimi ${esc(nf(list(a.list).length))} dell'elenco.</div></section>` : "";
    }
    // where to close the list: at the end of the month of the eighth flight (or at its start), not to cut a month in two
    let cut = entries.length;
    if (entries.length - VISIBLE_FLIGHTS > 2) {
      const k = entries[VISIBLE_FLIGHTS - 1].k;
      let end = VISIBLE_FLIGHTS, start = VISIBLE_FLIGHTS - 1;
      while (end < entries.length && entries[end].k === k) end += 1;
      while (start > 0 && entries[start - 1].k === k) start -= 1;
      cut = end - VISIBLE_FLIGHTS <= 4 ? end : start >= 5 ? start : VISIBLE_FLIGHTS;
      if (entries.length - cut <= 2) cut = entries.length;
    }
    let html = "";
    entries.forEach((x, i) => {
      if (!i || x.k !== entries[i - 1].k) {
        const month = entries.filter((y) => y.k === x.k);
        const visible = month.filter((y) => entries.indexOf(y) < cut);
        const name = x.d ? `${MONTHS_LONG[x.d.month][0].toUpperCase()}${MONTHS_LONG[x.d.month].slice(1)} ${x.d.year}` : "Data non nota";
        const countText = visible.length && visible.length < month.length
          ? `<span class="solo-chiuso">${esc(this._monthCount(visible))} di ${month.length}</span><span class="solo-aperto">${esc(this._monthCount(month))}</span>`
          : esc(this._monthCount(month));
        html += `<li class="mese${i >= cut ? " extra" : ""}"><span class="mese-nome">${esc(name)}</span><span class="mese-conta">${countText}</span></li>`;
      }
      html += this._flightRow(x.v, x.d, i >= cut);
    });
    const count = (s) => view.items.filter((x) => x.outcome === s).length;
    const flights = view.flights, upcoming = view.pp ? count("upcoming") : num(a.upcoming);
    const cancelled = view.pp ? this._personCounters(view).cancelled : num(a.cancelled);
    // a person's cancelled flights older than list: counted in the header but not in the list
    const outside = view.pp && cancelled !== null ? Math.max(0, cancelled - count("cancelled")) : 0;
    const side = [flights ? `${nf(flights)} ${flights === 1 ? "effettuato" : "effettuati"}` : "",
      upcoming > 0 ? `${nf(upcoming)} in programma` : "",
      cancelled > 0 ? `${nf(cancelled)} ${cancelled === 1 ? "cancellato" : "cancellati"}` : ""].filter(Boolean).join(" · ");
    const outsideText = outside === 1 ? "un volo cancellato" : `${nf(outside)} voli cancellati`;
    const note = view.pp
      ? (!view.complete ? `Dei ${nf(flights)} voli di ${view.name} l'elenco ha gli ultimi ${nf(view.flown.length)}.` +
          (outside ? ` ${outside === 1 ? "Manca" : "Mancano"} anche ${outsideText}.` : "")
        : outside ? `${capitalize(outsideText)} di ${view.name} ${outside === 1 ? "è più vecchio" : "sono più vecchi"} degli ultimi ${nf(list(a.list).length)} dell'elenco.` : "")
      : (total !== null && total > entries.length ? `Mostrati gli ultimi ${nf(entries.length)} voli su ${nf(total)}.` : "");
    return `<section><div class="testata"><h3>Voli</h3>${side ? `<span class="a-lato">${esc(side)}</span>` : ""}</div>
      <div class="espandibile"><ol class="voli">${html}</ol>
      ${cut < entries.length ? this._moreButton(`Mostra tutti i ${nf(entries.length)} voli`) : ""}</div>
      ${note ? `<div class="didascalia">${esc(note)}</div>` : ""}
    </section>`;
  }

  _moreButton(t) {
    return `<button type="button" class="altro" data-espandi="${esc(t)}" aria-expanded="false">${esc(t)}</button>`;
  }

  _flightRow(v, d, extra) {
    const status = str(v.outcome);
    const label = has(STATUS_LABELS, status) ? STATUS_LABELS[status] : null;
    const airline = airlineName(airlineCode(v.flight));
    const dur = num(v.duration_min), delay = num(v.delay_min);
    let pill = "";
    if (delay !== null && status !== "cancelled" && status !== "upcoming") {
      const r = Math.round(delay);
      const [t, c] = r <= 0 ? ["In orario", "ok"] : r < ON_TIME_MIN ? [`+${r} min`, "ok"] : r < 60 ? [`+${r} min`, "warn"] : [`+${delayText(r)}`, "err"];
      pill = `<span class="pillola ${c}" title="Ritardo all'arrivo">${esc(t)}</span>`;
    }
    return `<li class="volo${status === "cancelled" ? " cancellato" : ""}${extra ? " extra" : ""}">
      <div class="v-data">${d ? `<span class="v-giorno">${d.day}</span><span class="v-mese">${MONTHS[d.month]}</span>` : `<span class="v-mese">–</span>`}</div>
      <div class="v-centro">
        <div class="v-testa"><span class="v-sigla">${esc(v.flight) || "–"}</span>${airline ? `<span class="v-comp">${esc(airline)}</span>` : ""}
          ${label ? `<span class="stato ${label[1]}">${label[0]}</span>` : ""}</div>
        <div class="v-tratta">${routeHtml(v.route)}</div>
        ${v.cities ? `<div class="v-citta">${cityRouteHtml(v.cities)}</div>` : ""}
      </div>
      <div class="v-lato">
        <span class="v-durata${v.estimated ? " stimata" : ""}"${v.estimated ? ' title="Durata stimata"' : ""}>${dur !== null ? `${v.estimated ? "≈ " : ""}${esc(flightDuration(dur))}` : ""}</span><span class="v-km">${esc(distText(v.km))}</span><span class="v-pill">${pill}</span>
      </div>
    </li>`;
  }

  _styles() {
    return `
      :host {
        --c-primary: var(--primary-color, #03a9f4);
        --c-ok: var(--success-color, #43a047);
        --c-warn: var(--warning-color, #ffa600);
        --c-err: var(--error-color, #db4437);
        --c-text: var(--primary-text-color, #212121);
        --c-sub: var(--secondary-text-color, #727272);
        --c-div: var(--divider-color, rgba(0, 0, 0, 0.12));
        --c-bg: var(--ha-card-background, var(--card-background-color, #fff));   /* the same order as ha-card */
        /* coloured text: the status colour mixed with the text colour, readable (>= 4.5:1) in light and dark themes */
        --t-ok: color-mix(in srgb, var(--c-ok) 55%, var(--c-text));
        --t-warn: color-mix(in srgb, var(--c-warn) 48%, var(--c-text));
        --t-err: color-mix(in srgb, var(--c-err) 55%, var(--c-text));
        --t-pri: color-mix(in srgb, var(--c-primary) 55%, var(--c-text));
        --c-scuro: color-mix(in srgb, var(--c-primary) 45%, var(--c-text));   /* second chart colour */
        --c-neutro: color-mix(in srgb, var(--c-sub) 40%, var(--c-bg));
        --c-velo: color-mix(in srgb, var(--c-text) 4.5%, transparent);
        --c-traccia: color-mix(in srgb, var(--c-text) 9%, transparent);
        --r: 12px;
      }
      ha-card { overflow: hidden; }
      .box { padding: 16px; container-type: inline-size; color: var(--c-text); line-height: 1.35; }
      h3, h4 { margin: 0; font-weight: 600; }
      ol, ul { list-style: none; margin: 0; padding: 0; }
      .icona { width: 24px; height: 24px; fill: currentColor; display: block; }
      .mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 0.92em; }
      .tenue { color: var(--c-sub); }
      .nowrap { white-space: nowrap; }
      .stacco { height: 1px; background: var(--c-div); margin: 22px -16px 18px; }
      .testata { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 12px; min-height: 28px; }
      .testata h3 { font-size: 0.75rem; letter-spacing: 0.09em; text-transform: uppercase; color: var(--c-sub); }
      .a-lato { font-size: 0.78rem; color: var(--c-sub); text-align: right; }
      .etichetta { font-size: 0.78rem; color: var(--c-sub); }
      .nota { font-size: 0.75rem; color: var(--c-sub); margin-top: 2px; }
      .due-righe { display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; overflow-wrap: anywhere; }
      .valore { font-size: 1.4rem; font-weight: 600; letter-spacing: -0.01em; margin-top: 4px; line-height: 1.15; white-space: nowrap; }
      .valore.tenue { color: var(--c-sub); font-weight: 400; }
      .freccia { color: var(--c-sub); margin: 0 0.3em; font-weight: 400; }
      .cn { display: inline-flex; max-width: 100%; white-space: nowrap; vertical-align: top; }
      .cn-t { min-width: 0; overflow: hidden; text-overflow: ellipsis; }
      .cn .freccia { margin: 0 0 0 0.3em; flex: none; }

      /* card header: people and units */
      .testa-card { display: flex; align-items: center; gap: 6px; margin: -2px 0 14px; }
      .riempi { flex: 1; }
      .persone { position: relative; flex: 1; min-width: 0; display: flex; gap: 2px; padding: 3px; border-radius: 999px;
        background: var(--c-velo); overflow-x: auto; overscroll-behavior-x: contain; scrollbar-width: none; }
      .persone::-webkit-scrollbar { display: none; }
      .persone.piu-dx { --sfuma: linear-gradient(to right, #000 calc(100% - 32px), transparent); }
      .persone.piu-sx { --sfuma: linear-gradient(to right, transparent, #000 32px); }
      .persone.piu-sx.piu-dx { --sfuma: linear-gradient(to right, transparent, #000 32px, #000 calc(100% - 32px), transparent); }
      .persone.piu-sx, .persone.piu-dx { -webkit-mask-image: var(--sfuma); mask-image: var(--sfuma); }
      .persone button { flex: none; font: inherit; font-size: 0.85rem; color: var(--c-sub); background: transparent; border: 0;
        border-radius: 999px; padding: 0 14px; min-height: 32px; max-width: 12em; cursor: pointer; white-space: nowrap;
        overflow: hidden; text-overflow: ellipsis; -webkit-tap-highlight-color: transparent; }
      .persone button.attivo { background: color-mix(in srgb, var(--c-primary) 18%, var(--c-bg)); color: var(--t-pri); font-weight: 600; }
      .ingranaggio { flex: none; width: 38px; height: 38px; margin-right: -6px; border: 0; border-radius: 50%; padding: 0;
        background: transparent; color: var(--c-sub); display: grid; place-items: center; cursor: pointer; -webkit-tap-highlight-color: transparent; }
      .ingranaggio .icona { width: 22px; height: 22px; }
      .ingranaggio[aria-expanded="true"] { background: color-mix(in srgb, var(--c-primary) 13%, transparent); color: var(--t-pri); }
      .persone button:focus-visible, .ingranaggio:focus-visible { outline: 2px solid var(--c-primary); outline-offset: -2px; }
      @media (hover: hover) {
        .persone button:not(.attivo):hover { color: var(--c-text); }
        .ingranaggio:hover { color: var(--c-text); }
      }
      .impostazioni { display: grid; gap: 8px; margin: -4px 0 14px; padding: 10px 12px; border: 1px solid var(--c-div); border-radius: var(--r); }
      .impostazioni[hidden] { display: none; }
      .imp-riga { display: flex; align-items: center; justify-content: space-between; gap: 12px; font-size: 0.85rem; }
      .imp-riga .segmenti { display: grid; grid-template-columns: 1fr 1fr; width: 160px; }

      /* summary */
      .riepilogo { display: grid; gap: 10px; }
      .hero { background: color-mix(in srgb, var(--c-primary) 8%, transparent); border-radius: var(--r); padding: 16px;
        display: flex; flex-direction: column; gap: 16px; }
      .hero-testa { display: flex; justify-content: space-between; align-items: baseline; gap: 12px; }
      .hero-titolo { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
      .periodo { font-size: 0.78rem; color: var(--c-sub); white-space: nowrap; font-variant-numeric: tabular-nums; }
      .grande { font-size: 3.4rem; font-weight: 600; line-height: 1; letter-spacing: -0.03em; margin-top: 6px; }
      .hero-sotto { font-size: 0.8rem; color: var(--c-sub); margin-top: 8px; }
      .hero-righe { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
      .medio { font-size: 1.6rem; font-weight: 600; letter-spacing: -0.015em; margin-top: 2px; white-space: nowrap; }
      .unita { font-size: 0.9rem; font-weight: 400; color: var(--c-sub); margin-left: 4px; letter-spacing: 0; }
      .circa { color: var(--c-sub); font-weight: 400; margin-right: 3px; }
      .giri { display: flex; align-items: center; gap: 14px; padding-top: 12px; font-size: 0.8rem; color: var(--c-sub);
        border-top: 1px solid color-mix(in srgb, var(--c-primary) 16%, transparent); }
      .giri-testo { flex: none; }
      .giri-testo b { color: var(--c-text); font-weight: 600; font-size: 0.95rem; font-variant-numeric: tabular-nums; }
      .giri-barra { flex: 1; display: flex; gap: 3px; min-width: 48px; }
      .giri-barra span { flex: 1; height: 6px; border-radius: 3px; overflow: hidden; background: color-mix(in srgb, var(--c-primary) 18%, transparent); }
      .giri-barra i { display: block; height: 100%; border-radius: 3px; background: var(--c-primary); transform-origin: left; }
      .tessere { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 10px; }
      .tessera { background: var(--c-velo); border-radius: var(--r); padding: 12px 14px; min-width: 0; }
      .tessera.centrata { display: flex; flex-direction: column; justify-content: center; }
      .tessera.puntualita { display: flex; flex-direction: column; align-items: center; justify-content: center; text-align: center; padding: 14px 10px; }
      .puntualita .etichetta { margin-top: 6px; color: var(--c-text); font-weight: 600; font-size: 0.85rem; }
      .anello-fondo { fill: none; stroke: var(--c-traccia); }
      .anello-pieno { fill: none; stroke-linecap: round; }
      .anello-txt { font-size: 19px; font-weight: 600; fill: var(--c-text); }
      .anello-txt.neutro { fill: var(--c-sub); }
      .pallino, .legenda i { width: 7px; height: 7px; border-radius: 50%; display: inline-block; flex: none; }
      .pallino { margin-right: 5px; vertical-align: 1px; }

      /* years */
      .segmenti { display: inline-flex; background: var(--c-velo); border-radius: 999px; padding: 2px; }
      .segmenti button { font: inherit; font-size: 0.78rem; border: 0; background: transparent; color: var(--c-sub);
        padding: 4px 11px; border-radius: 999px; cursor: pointer; min-height: 26px; }
      .segmenti button.attivo { background: var(--c-bg); color: var(--c-text); font-weight: 500; box-shadow: 0 1px 2px rgba(0, 0, 0, 0.18); }
      .segmenti button:focus-visible { outline: 2px solid var(--c-primary); outline-offset: 1px; }
      .colonne { width: 100%; display: block; overflow: visible; }
      .col { fill: var(--c-primary); }
      .col-zero { fill: var(--c-div); }
      .hit { fill: transparent; }
      .asse { stroke: var(--c-div); stroke-width: 1; }
      .cv { font-size: 12px; font-weight: 500; fill: var(--c-text); font-variant-numeric: tabular-nums; }
      .cx { font-size: 11px; fill: var(--c-sub); }
      .cx.forte { fill: var(--c-text); font-weight: 600; }
      .cx.futuro { opacity: 0.45; }
      .tabella { margin-top: 12px; font-size: 0.85rem; font-variant-numeric: tabular-nums; }
      .tab-riga { display: grid; grid-template-columns: 1fr 0.8fr 0.9fr 1.2fr 1.1fr; gap: 8px; padding: 7px 2px; border-top: 1px solid var(--c-div); }
      .tab-riga > span:first-child { font-weight: 500; }
      .tab-riga > span:not(:first-child) { text-align: right; }
      .tab-capo { border-top: 0; color: var(--c-sub); font-size: 0.74rem; padding-top: 0; }
      .tab-capo > span:first-child { font-weight: 400; }
      .didascalia { font-size: 0.75rem; color: var(--c-sub); margin-top: 8px; }

      /* rankings */
      .gruppi { display: grid; grid-template-columns: minmax(0, 1fr); gap: 20px; }
      .gruppo { min-width: 0; }
      .gruppo-testa { display: flex; justify-content: space-between; align-items: baseline; gap: 8px; margin-bottom: 4px; }
      .gruppo-testa h4 { font-size: 0.9rem; }
      .gruppo-testa span { font-size: 0.75rem; color: var(--c-sub); text-align: right; }
      .rk { display: grid; grid-template-columns: minmax(0, 1fr) 32% 2em; align-items: center; gap: 10px; min-height: 28px; font-size: 0.9rem; }
      .rk-testo { min-width: 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
      .rk-sub { color: var(--c-sub); font-size: 0.75rem; margin-left: 6px; }
      .rk-sub .freccia { margin: 0 0.2em; }
      .rk-barra { height: 6px; border-radius: 3px; background: var(--c-traccia); overflow: hidden; }
      .rk-barra i { display: block; height: 100%; border-radius: 3px; background: var(--c-primary); transform-origin: left; }
      .rk-val { text-align: right; font-weight: 500; font-variant-numeric: tabular-nums; }
      .unico { font-size: 0.9rem; min-height: 28px; display: flex; flex-wrap: wrap; align-items: baseline; column-gap: 8px; padding-top: 4px; }
      .unico .rk-sub { margin: 0; font-size: 0.8rem; }
      .pastiglie { display: flex; flex-wrap: wrap; gap: 6px; padding-top: 4px; }
      .pastiglie li { font-size: 0.85rem; background: var(--c-velo); border-radius: 8px; padding: 4px 10px; max-width: 100%;
        white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
      .segmentata { display: flex; gap: 2px; height: 10px; border-radius: 5px; overflow: hidden; margin-top: 8px; }
      .segmentata span { min-width: 4px; }
      .legenda { display: flex; flex-wrap: wrap; gap: 6px 16px; margin-top: 10px; font-size: 0.85rem; }
      .legenda span { display: inline-flex; align-items: center; gap: 6px; }
      .legenda b { font-weight: 500; font-variant-numeric: tabular-nums; }
      .legenda em { font-style: normal; color: var(--c-sub); font-size: 0.78rem; }

      /* records */
      .records { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 10px; }
      .record { display: flex; flex-direction: column; }
      .record.larga { grid-column: 1 / -1; }
      .rec-valori { display: grid; grid-template-columns: repeat(auto-fit, minmax(92px, 1fr)); gap: 10px 14px; }
      .record .valore { font-size: 1.25rem; }
      .multi .rec-valori { grid-template-columns: minmax(0, 1fr); gap: 4px; }
      .multi .rec-valore { display: flex; justify-content: space-between; align-items: baseline; gap: 12px; }
      .multi .rec-valore + .rec-valore { border-top: 1px solid var(--c-div); padding-top: 4px; }
      .multi .valore { font-size: 1.1rem; margin: 0; }
      .rec-tratta { font-size: 0.9rem; font-weight: 600; margin-top: 10px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
      .rec-citta { margin-top: 1px; }
      .rec-piede { margin-top: auto; padding-top: 6px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
      .record.vuota { background: transparent; border: 1px dashed var(--c-div); }

      /* flight list */
      .voli { display: block; }
      .mese { display: flex; justify-content: space-between; align-items: baseline; gap: 12px; padding: 18px 0 6px; font-size: 0.88rem; }
      .mese:first-child { padding-top: 0; }
      .mese-nome { font-weight: 600; }
      .mese-conta { font-size: 0.75rem; color: var(--c-sub); text-align: right; }
      .espandibile:not(.aperto) .solo-aperto, .espandibile.aperto .solo-chiuso { display: none; }
      .volo { display: grid; grid-template-columns: 40px minmax(0, 1fr) auto; gap: 12px; align-items: start;
        padding: 10px 0; border-top: 1px solid var(--c-div); }
      .mese + .volo { border-top-color: transparent; padding-top: 4px; }
      .v-data { display: flex; flex-direction: column; align-items: center; background: var(--c-velo); border-radius: 10px; padding: 5px 0 4px; }
      .v-giorno { font-size: 1.05rem; font-weight: 500; line-height: 1.1; }
      .v-mese { font-size: 0.66rem; color: var(--c-sub); text-transform: uppercase; letter-spacing: 0.04em; }
      .v-centro { min-width: 0; }
      .v-testa { display: flex; align-items: center; column-gap: 6px; flex-wrap: wrap; font-size: 0.78rem; color: var(--c-sub); }
      .v-sigla { color: var(--c-text); font-weight: 500; }
      .v-tratta { font-size: 1rem; font-weight: 600; margin-top: 2px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
      .v-citta { font-size: 0.78rem; color: var(--c-sub); margin-top: 1px; }
      .v-lato { text-align: right; display: flex; flex-direction: column; align-items: flex-end; gap: 2px; padding-top: 1px; }
      .v-lato > :empty { display: none; }
      .v-durata { font-size: 0.95rem; font-weight: 500; font-variant-numeric: tabular-nums; white-space: nowrap; }
      .v-durata.stimata { color: var(--c-sub); font-weight: 400; }
      .v-km { font-size: 0.78rem; color: var(--c-sub); font-variant-numeric: tabular-nums; white-space: nowrap; }
      .v-pill { margin-top: 3px; }
      .pillola { display: inline-block; font-size: 0.72rem; font-weight: 500; padding: 2px 8px; border-radius: 999px; white-space: nowrap; }
      .pillola.ok, .stato.ok { color: var(--t-ok); background: color-mix(in srgb, var(--c-ok) 14%, transparent); }
      .pillola.warn, .stato.warn { color: var(--t-warn); background: color-mix(in srgb, var(--c-warn) 16%, transparent); }
      .pillola.err, .stato.err { color: var(--t-err); background: color-mix(in srgb, var(--c-err) 14%, transparent); }
      .stato { font-size: 0.68rem; font-weight: 500; padding: 1px 7px; border-radius: 6px; }
      .stato.pri { color: var(--t-pri); background: color-mix(in srgb, var(--c-primary) 13%, transparent); }
      .cancellato .v-tratta, .cancellato .v-citta { text-decoration: line-through; text-decoration-color: color-mix(in srgb, var(--c-sub) 70%, transparent); }
      .cancellato .v-tratta { color: var(--c-sub); }
      .aggiornato { margin-top: 18px; font-size: 0.72rem; color: var(--c-sub); text-align: right; }

      /* empty state and panels */
      .vuoto { text-align: center; padding: 12px 4px 6px; }
      .vuoto-icona { width: 64px; height: 64px; border-radius: 50%; margin: 0 auto 14px; display: grid; place-items: center;
        background: color-mix(in srgb, var(--c-primary) 12%, transparent); color: var(--c-primary); }
      .vuoto-icona .icona { width: 30px; height: 30px; }
      .vuoto-titolo { font-size: 1.2rem; font-weight: 600; }
      .vuoto-testo { color: var(--c-sub); font-size: 0.9rem; margin: 6px auto 16px; max-width: 30em; }
      .passi { display: grid; gap: 10px; text-align: left; max-width: 340px; margin: 0 auto; }
      .passi li { display: flex; gap: 12px; align-items: center; font-size: 0.9rem; background: var(--c-velo); border-radius: 10px; padding: 10px 12px; }
      .passo { flex: none; width: 24px; height: 24px; border-radius: 50%; display: grid; place-items: center; font-size: 0.8rem;
        font-weight: 600; color: var(--t-pri); background: color-mix(in srgb, var(--c-primary) 14%, transparent); }
      .vuoto-nota { font-size: 0.78rem; color: var(--c-sub); margin-top: 14px; }
      .pannello { display: flex; gap: 12px; align-items: flex-start; }
      .pannello-icona { color: var(--c-warn); flex: none; }
      .pannello-titolo { font-weight: 600; }
      .pannello-testo { color: var(--c-sub); font-size: 0.85rem; margin-top: 2px; word-break: break-word; }

      .espandibile:not(.aperto) .extra { display: none; }
      .altro { font: inherit; font-size: 0.8rem; font-weight: 500; color: var(--t-pri); background: transparent;
        border: 1px solid var(--c-div); border-radius: 999px; padding: 5px 14px; margin-top: 10px; cursor: pointer; min-height: 32px; }
      .altro:hover { background: color-mix(in srgb, var(--c-primary) 8%, transparent); }
      .altro:focus-visible { outline: 2px solid var(--c-primary); outline-offset: 2px; }
      .voli + .altro { display: block; margin: 8px auto 0; }
      .entra .rk-barra i, .entra .giri-barra i { animation: cresci 0.5s cubic-bezier(0.2, 0.7, 0.2, 1) both; }
      @keyframes cresci { from { transform: scaleX(0); } }

      @container (min-width: 600px) {
        .riepilogo { grid-template-columns: minmax(0, 1.1fr) minmax(0, 1fr); align-items: stretch; }
        .hero { justify-content: space-between; padding: 18px 20px; }
        .grande { font-size: 4rem; }
        .persone { flex: 0 1 auto; }
        .testa-card { justify-content: space-between; }
        .impostazioni { width: fit-content; margin-left: auto; grid-auto-flow: column; gap: 24px; padding: 8px 12px; }
        .gruppi { grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 24px 32px; }
        .records { grid-template-columns: repeat(4, minmax(0, 1fr)); }
        .record, .record.larga { grid-column: span var(--n, 1); }
        .multi .rec-valori { grid-template-columns: repeat(var(--n, 1), minmax(0, 1fr)); gap: 10px 14px; }
        .multi .rec-valore { display: block; }
        .multi .rec-valore + .rec-valore { border-top: 0; padding-top: 0; }
        .multi .valore { font-size: 1.25rem; margin-top: 4px; }
        .anni-corpo { display: grid; grid-template-columns: 1fr 1fr; gap: 32px; align-items: end; }
        .anni-corpo .tabella { margin-top: 0; }
        .volo { grid-template-columns: 44px minmax(0, 1fr) 64px 92px 96px; gap: 0 16px; align-items: center; }
        .v-lato { display: contents; }
        .v-lato > :empty { display: block; }
        .v-lato > * { text-align: right; }
        .v-km { font-size: 0.85rem; }
        .v-pill { margin: 0; }
        .stacco { margin: 26px -16px 22px; }
      }
      @media (prefers-reduced-motion: reduce) {
        .entra .rk-barra i, .entra .giri-barra i { animation: none; }
      }
    `;
  }
}

if (!customElements.get("boarding-pass-tracker-stats-card")) {  // an older version already loaded must not be redefined
  customElements.define("boarding-pass-tracker-stats-card", FlightStatsCard);
  window.customCards = window.customCards || [];
  // name and description appear in the dashboard card picker: Italian user text
  window.customCards.push({ type: "boarding-pass-tracker-stats-card", name: "Statistiche voli (Boarding Pass Tracker)",
    description: "Voli, ore in aria, distanze, ritardi, classifiche e record, anche per persona." });
}
