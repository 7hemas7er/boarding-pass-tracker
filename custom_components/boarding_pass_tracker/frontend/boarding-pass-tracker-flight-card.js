/**
 * boarding-pass-tracker flight card: the next flight on an arc, with the plane moving along it.
 *
 * Element name: "boarding-pass-tracker-flight-card" (dashboards use
 * `type: custom:boarding-pass-tracker-flight-card`).
 *
 * Data: sensor.boarding_pass_tracker_next_flight (published by the boarding-pass-tracker engine: times, gate, status,
 * phase) and, while the flight is airborne, its Flightradar24 position (attribute `flights` of the Flightradar24 HACS
 * integration's tracked-flights sensor: sensor.flightradar24_additional_tracked on a fresh install; set fr24_entity
 * when yours has another id). With the position, progress is the share of the great-circle distance already flown;
 * without it, the share of the time elapsed between takeoff and arrival. A tap opens the flight on
 * flightradar24.com. The element name and the attribute keys it reads are a contract with the engine; the labels it
 * shows are Italian, like the rest of the user interface.
 *
 * Configuration:
 *   type: custom:boarding-pass-tracker-flight-card
 *   entity: sensor.boarding_pass_tracker_next_flight          (default)
 *   fr24_entity: sensor.flightradar24_additional_tracked      (default)
 *
 * Install: nothing to do. The integration loads this file on every page (cards.py and the loader); see
 * dashboards/README.md.
 */
const DEFAULT_ENTITY = "sensor.boarding_pass_tracker_next_flight";
const DEFAULT_FR24_ENTITY = "sensor.flightradar24_additional_tracked";
const PLANE_PATH = "M20.56 3.91C21.15 4.5 21.15 5.45 20.56 6.03L16.67 9.92L18.79 19.11L17.38 20.53L13.5 13.1" +
  "L9.6 17L9.96 19.47L8.89 20.53L7.13 17.35L3.94 15.58L5 14.5L7.5 14.87L11.37 11L3.94 7.09L5.36 5.68" +
  "L14.55 7.8L18.44 3.91C19 3.33 20 3.33 20.56 3.91Z";   // mdi:airplane (Pictogrammers, Apache 2.0): points north-east
const P0 = [26, 92], P1 = [160, -36], P2 = [294, 92];    // the arc: a quadratic Bezier, top at y=28 (the viewBox starts at y=16)

function pointAt(t) {
  const u = 1 - t;
  return [u * u * P0[0] + 2 * u * t * P1[0] + t * t * P2[0], u * u * P0[1] + 2 * u * t * P1[1] + t * t * P2[1]];
}

function angleAt(t) {                                    // SVG degrees of the tangent (0 = pointing right)
  const dx = 2 * (1 - t) * (P1[0] - P0[0]) + 2 * t * (P2[0] - P1[0]);
  const dy = 2 * (1 - t) * (P1[1] - P0[1]) + 2 * t * (P2[1] - P1[1]);
  return Math.atan2(dy, dx) * 180 / Math.PI;
}

function greatCircleKm(lat1, lon1, lat2, lon2) {         // km on the great circle
  const r = Math.PI / 180, dLat = (lat2 - lat1) * r, dLon = (lon2 - lon1) * r;
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(lat1 * r) * Math.cos(lat2 * r) * Math.sin(dLon / 2) ** 2;
  return 12742 * Math.asin(Math.min(1, Math.sqrt(h)));
}

function formatDuration(ms) {
  const m = Math.max(0, Math.round(ms / 60000));
  return m < 60 ? `${m} min` : `${Math.floor(m / 60)} h${m % 60 ? ` ${m % 60} min` : ""}`;
}

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

class FlightCard extends HTMLElement {
  static getStubConfig() {
    return { entity: DEFAULT_ENTITY };
  }

  setConfig(config) {
    this._config = { entity: DEFAULT_ENTITY, fr24_entity: DEFAULT_FR24_ENTITY, ...config };
    if (!this.shadowRoot) {
      this.attachShadow({ mode: "open" });
      this.shadowRoot.addEventListener("click", () => this._open());
    }
  }

  set hass(hass) {
    this._hass = hass;
    this._render();
  }

  connectedCallback() {                                  // the plane moves on even without new states
    this._timer = setInterval(() => this._render(), 30000);
  }

  disconnectedCallback() {
    clearInterval(this._timer);
  }

  getCardSize() {
    return 4;
  }

  getGridOptions() {                                     // height from the content: with fixed rows it overlaps the card below
    return { columns: 12, rows: "auto" };
  }

  _live(a) {
    const st = this._hass.states[this._config.fr24_entity];
    const tracked = (st && st.attributes.flights) || [];
    return tracked.find((f) => f && f.flight_number === a.flight_iata && f.tracked_type === "live" && f.latitude != null) || null;
  }

  _open() {
    const st = this._hass && this._hass.states[this._config.entity];
    const ident = st && st.attributes.flight_iata;
    if (ident) window.open(`https://www.flightradar24.com/data/flights/${encodeURIComponent(ident.toLowerCase())}`, "_blank");
  }

  _progress(a, live, now) {
    if (a.phase === "landed") return 1;
    if (!["airborne", "departed"].includes(a.phase) && !live) return 0;
    if (live && live.airport_origin_latitude != null && live.airport_destination_latitude != null) {
      const flown = greatCircleKm(live.airport_origin_latitude, live.airport_origin_longitude, live.latitude, live.longitude);
      const left = greatCircleKm(live.latitude, live.longitude, live.airport_destination_latitude, live.airport_destination_longitude);
      if (flown + left > 0) return Math.min(0.98, Math.max(0.02, flown / (flown + left)));
    }
    const start = Date.parse(a.takeoff || a.departure), end = live && live.time_estimated_arrival
      ? live.time_estimated_arrival * 1000 : Date.parse(a.arrival);
    if (!(start && end && end > start)) return 0.5;
    return Math.min(0.98, Math.max(0.02, (now - start) / (end - start)));
  }

  _render() {
    if (!this._hass || !this._config || !this.shadowRoot) return;
    const st = this._hass.states[this._config.entity];
    const a = (st && st.attributes) || {};
    const state = st ? st.state : "";
    if (!a.flight) {
      this.shadowRoot.innerHTML = `<style>${this._styles()}</style><ha-card><div class="empty">
        <ha-icon icon="mdi:airplane-off"></ha-icon> Nessun volo in programma</div></ha-card>`;
      return;
    }
    const now = Date.now(), live = this._live(a), p = this._progress(a, live, now);
    const color = ["cancelled", "diverted"].includes(a.phase) ? "var(--error-color, #db4437)"
      : (a.delay_min || 0) >= 15 ? "var(--warning-color, #ffa600)"
      : a.phase === "landed" ? "var(--primary-color, #03a9f4)" : "var(--success-color, #43a047)";
    const [x, y] = pointAt(p), rot = angleAt(p) + 45;
    const length = 300;                                  // the arc length need not be exact: pathLength
    let line;                                            // the status line under the arc (Italian user text)
    if (a.phase === "cancelled") line = "Volo cancellato";
    else if (a.phase === "landed") line = `Atterrato${a.baggage_belt ? ` · bagagli al nastro ${escapeHtml(a.baggage_belt)}` : ""}`;
    else if (live) {
      const altitude = live.altitude != null ? `${Math.round(live.altitude * 0.3048).toLocaleString("it-IT")} m` : null;
      const speed = live.ground_speed != null ? `${Math.round(live.ground_speed * 1.852)} km/h` : null;
      const arrival = live.time_estimated_arrival ? live.time_estimated_arrival * 1000 : Date.parse(a.arrival);
      line = [altitude, speed, arrival ? `atterra fra ${formatDuration(arrival - now)}` : null].filter(Boolean).join(" · ");
    } else if (["airborne", "departed"].includes(a.phase)) {
      line = a.arrival ? `In volo · atterra fra ${formatDuration(Date.parse(a.arrival) - now)}` : "In volo";
    } else {
      const departure = Date.parse(a.departure);
      const gate = a.gate ? `gate ${escapeHtml(a.gate)}` : "gate non ancora assegnato";
      line = departure ? `Parte fra ${formatDuration(departure - now)} · ${gate}` : gate;
    }
    const scheduled = a.departure_scheduled_time && a.departure_scheduled_time !== a.departure_time
      ? `<s>${escapeHtml(a.departure_scheduled_time)}</s> ` : "";
    this.shadowRoot.innerHTML = `<style>${this._styles()}</style>
      <ha-card style="--color:${color}">
        <div class="head">
          <div><span class="flight">${escapeHtml(a.flight)}</span> <span class="date">${escapeHtml(a.date?.split("-").reverse().slice(0, 2).join("/"))}</span></div>
          <div class="status">${escapeHtml(state)}</div>
        </div>
        <svg viewBox="0 16 320 104" role="img" aria-label="${escapeHtml(a.route)}: ${escapeHtml(state)}">
          <path d="M${P0} Q${P1} ${P2}" class="arc" pathLength="${length}"/>
          <path d="M${P0} Q${P1} ${P2}" class="done" pathLength="${length}"
                stroke-dasharray="${p * length} ${length}"/>
          <circle cx="${P0[0]}" cy="${P0[1]}" r="5" class="dot filled"/>
          <circle cx="${P2[0]}" cy="${P2[1]}" r="5" class="dot ${p >= 1 ? "filled" : ""}"/>
          <g transform="translate(${x} ${y}) rotate(${rot}) translate(-13 -13) scale(1.08)">
            <circle cx="12" cy="12" r="13" class="halo"/>
            <path d="${PLANE_PATH}" class="plane"/>
          </g>
          <text x="${P0[0]}" y="114" class="code" text-anchor="start">${escapeHtml(a.origin)}</text>
          <text x="${P2[0]}" y="114" class="code" text-anchor="end">${escapeHtml(a.destination)}</text>
        </svg>
        <div class="ends">
          <div><div class="city">${escapeHtml(a.origin_city)}</div><div class="time">${scheduled}${escapeHtml(a.departure_time || "–")}</div>
            ${a.boarding_time && !["airborne", "landed", "departed"].includes(a.phase)
              ? `<div class="note">imbarco ${escapeHtml(a.boarding_time)}${a.boarding_estimated ? " circa" : ""}</div>` : ""}</div>
          <div class="right"><div class="city">${escapeHtml(a.destination_city)}</div><div class="time">${escapeHtml(a.arrival_time || "–")}</div>
            ${a.arrival_terminal ? `<div class="note">terminal ${escapeHtml(a.arrival_terminal)}</div>` : ""}</div>
        </div>
        <div class="line">${line}</div>
      </ha-card>`;
  }

  _styles() {
    return `
      ha-card { padding: 14px 16px 12px; cursor: pointer; }
      .head { display: flex; justify-content: space-between; align-items: baseline; gap: 8px; }
      .flight { font-size: 1.25em; font-weight: 600; }
      .date { color: var(--secondary-text-color); }
      .status { color: var(--color); font-weight: 600; text-align: right; }
      svg { width: 100%; display: block; margin: 4px 0 0; overflow: visible; }
      .arc { fill: none; stroke: var(--divider-color, #ccc); stroke-width: 2; stroke-dasharray: 3 5; }
      .done { fill: none; stroke: var(--color); stroke-width: 3; stroke-linecap: round; }
      .dot { fill: var(--card-background-color, #fff); stroke: var(--color); stroke-width: 2; }
      .dot.filled { fill: var(--color); }
      .halo { fill: var(--card-background-color, #fff); }
      .plane { fill: var(--color); }
      .code { font-size: 15px; font-weight: 700; fill: var(--primary-text-color); }
      .ends { display: flex; justify-content: space-between; gap: 12px; margin-top: 2px; }
      .right { text-align: right; }
      .city { color: var(--secondary-text-color); font-size: 0.9em; }
      .time { font-size: 1.15em; font-weight: 600; }
      .time s { color: var(--secondary-text-color); font-weight: 400; }
      .note { color: var(--secondary-text-color); font-size: 0.85em; }
      .line { margin-top: 8px; text-align: center; color: var(--primary-text-color); }
      .empty { padding: 8px 0; color: var(--secondary-text-color); display: flex; gap: 8px; align-items: center; }
    `;
  }
}

if (!customElements.get("boarding-pass-tracker-flight-card")) {  // an older version already loaded must not be redefined
  customElements.define("boarding-pass-tracker-flight-card", FlightCard);
  window.customCards = window.customCards || [];
  // name and description appear in the dashboard card picker: Italian user text
  window.customCards.push({ type: "boarding-pass-tracker-flight-card", name: "Volo (Boarding Pass Tracker)",
    description: "Il prossimo volo su un arco." });
}
