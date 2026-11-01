/**
 * boarding-pass-tracker manage card: add, change or delete a flight from the dashboard, with no token.
 *
 * Element name: "boarding-pass-tracker-manage-card" (dashboards use `type: custom:boarding-pass-tracker-manage-card`).
 *
 * It sends the requests of «Aggiungi volo» and «Modifica volo» (GET /api/boarding_pass_tracker/choices and POST
 * /api/boarding_pass_tracker/flight, docs/ARCHITECTURE.md) through hass.fetchWithAuth, so with the session of the
 * person signed in to Home Assistant: the integration answers as for that person's Shortcut (403 for a user that is
 * not a configured person). The replies, Italian, are shown as they come.
 *
 * Configuration:
 *   type: custom:boarding-pass-tracker-manage-card
 *   title: Gestisci voli                                       (default)
 */
const API = "/api/boarding_pass_tracker";
const SHOWN = 8;                                       // flights in the list before «Mostra tutti»
// The fields of an edit: [request key, label, input type, placeholder]. The request keys are the ones of POST /flight.
const EDIT_FIELDS = [
  ["passengers", "Passeggeri e posti", "text", "es. Alice 12A business, Bob"],
  ["date", "Data della partenza", "date", ""],
  ["departure_time", "Partenza: ora locale", "text", "es. 06:30"],
  ["arrival_time", "Arrivo: ora locale", "text", "es. 08:45"],
  ["terminal", "Terminal", "text", ""],
  ["gate", "Gate", "text", ""],
];
const ADD_FIELDS = [
  ["flight", "Volo", "text", "es. FR 1234"],
  ["date", "Data della partenza (quella della carta d'imbarco)", "date", ""],
  ["origin", "Partenza: codice dell'aeroporto", "text", "es. BLQ"],
  ["destination", "Arrivo: codice dell'aeroporto", "text", "es. STN"],
  ...EDIT_FIELDS.filter(([k]) => k !== "date"),
];
const CLEARABLE = ["departure_time", "arrival_time", "terminal", "gate"];   // "-" clears them in an edit

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

class ManageCard extends HTMLElement {
  static getStubConfig() {
    return {};
  }

  setConfig(config) {
    this._config = { title: "Gestisci voli", ...config };
    if (!this.shadowRoot) {
      this.attachShadow({ mode: "open" });
      this.shadowRoot.addEventListener("click", (e) => this._click(e));
      this.shadowRoot.addEventListener("submit", (e) => { e.preventDefault(); this._submit(); });
    }
    this._view = this._view || "list";                 // list | edit | add | delete
    this._render();
  }

  set hass(hass) {                                     // no render on every state change: a form keeps what is typed
    const first = !this._hass;
    this._hass = hass;
    if (first) this._load();
  }

  connectedCallback() {                                // back on the dashboard: a fresh list (a pass sent meanwhile)
    if (this._hass && this._view === "list" && !this._busy) this._load();
  }

  getCardSize() {
    return 5;
  }

  getGridOptions() {
    return { columns: 12, rows: "auto" };
  }

  async _request(method, path, body) {
    let r;
    try {
      r = await this._hass.fetchWithAuth(`${API}/${path}`, body === undefined ? { method }
        : { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    } catch (err) {
      return { ok: false, status: 0, data: { message: "Home Assistant non risponde: riprova fra poco." } };
    }
    let data = {};
    try { data = await r.json(); } catch (err) { /* an empty or non-JSON reply */ }
    if (!data.message && !r.ok) data.message = `Richiesta rifiutata (${r.status}).`;
    return { ok: r.ok, status: r.status, data };
  }

  async _load() {
    this._busy = true;
    this._render();
    const r = await this._request("GET", "choices");
    this._busy = false;
    if (r.ok) {
      this._choices = r.data.choices || [];
      this._details = r.data.details || {};
      this._empty = this._choices.length ? "" : r.data.message;
      this._error = "";
    } else {
      this._choices = [];
      this._details = {};
      this._error = r.data.message;
    }
    this._render();
  }

  _click(e) {
    const el = e.target.closest("[data-act]");
    if (!el || this._busy) return;
    const act = el.dataset.act;
    if (act === "pick") {
      this._sel = this._choices[Number(el.dataset.i)];
      this._go("edit");
    } else if (act === "add") this._go("add");
    else if (act === "delete") this._go("delete");
    else if (act === "back") this._go("list");
    else if (act === "cancel-delete") this._go("edit");
    else if (act === "more") { this._all = true; this._render(); }
    else if (act === "confirm-delete") this._send({ action: "delete", ref: this._details[this._sel].ref });
    else if (act === "reload") { this._notice = ""; this._load(); }
  }

  _go(view) {
    this._view = view;
    this._formError = "";
    if (view === "list") this._formNotice = "";
    this._render();
  }

  _values() {
    const out = {};
    for (const input of this.shadowRoot.querySelectorAll("form [name]")) out[input.name] = input.value.trim();
    return out;
  }

  _submit() {
    if (this._busy) return;
    const v = this._values();
    if (this._view === "add") {
      const body = { action: "add" };
      for (const [k] of ADD_FIELDS) if (v[k]) body[k] = v[k];
      this._send(body);
      return;
    }
    const d = this._details[this._sel];
    const body = { action: "edit", ref: d.ref };
    for (const [k] of EDIT_FIELDS) {
      const was = String(d[k] ?? "");
      if (v[k] === was) continue;
      if (v[k]) body[k] = v[k];
      else if (CLEARABLE.includes(k)) body[k] = "-";
      else {
        this._formError = k === "passengers" ? "Servono i passeggeri: per togliere il volo usa «Elimina il volo»."
          : "Serve la data.";
        this._render();
        return;
      }
    }
    if (Object.keys(body).length === 2) {
      this._formError = "Non hai cambiato niente.";
      this._render();
      return;
    }
    this._send(body);
  }

  async _send(body) {
    this._busy = true;
    this._keep = this._view === "delete" ? null : this._values();   // what was typed, shown again after a refusal
    this._render();
    const r = await this._request("POST", "flight", body);
    this._busy = false;
    if (r.ok) {
      this._keep = null;
      this._view = "list";
      this._notice = r.data.message || "Fatto.";
      await this._load();
      return;
    }
    if (r.status === 404 || r.status === 409) {        // the flight is gone or changed: back to a fresh list
      this._keep = null;
      this._view = "list";
      this._notice = r.data.message;
      await this._load();
      return;
    }
    this._formError = r.data.message;
    this._render();
  }

  _field([k, label, type, placeholder], value) {
    return `<label>${escapeHtml(label)}
      <input name="${k}" type="${type}" value="${escapeHtml(value)}" placeholder="${escapeHtml(placeholder)}"
        autocomplete="off" autocapitalize="${k === "flight" || k === "origin" || k === "destination" ? "characters" : "words"}"
        ${type === "date" ? "" : 'spellcheck="false"'}></label>`;
  }

  _list() {
    if (this._error) {
      return `<div class="error">${escapeHtml(this._error)}</div>
        <div class="actions"><button data-act="reload">Riprova</button></div>`;
    }
    if (!this._choices) return `<div class="muted">Carico i voli…</div>`;
    const shown = this._all ? this._choices : this._choices.slice(0, SHOWN);
    const rows = shown.map((label, i) => `<button class="row" data-act="pick" data-i="${i}">
        <span>${escapeHtml(label)}</span><ha-icon icon="mdi:pencil"></ha-icon></button>`).join("");
    const more = shown.length < this._choices.length
      ? `<button class="link" data-act="more">Mostra tutti (${this._choices.length})</button>` : "";
    return `${this._notice ? `<div class="notice">${escapeHtml(this._notice)}</div>` : ""}
      <div class="actions"><button class="primary" data-act="add"><ha-icon icon="mdi:plus"></ha-icon> Aggiungi volo</button></div>
      ${this._empty ? `<div class="muted">${escapeHtml(this._empty)}</div>` : `<div class="rows">${rows}</div>${more}`}`;
  }

  _form() {
    const add = this._view === "add";
    const d = add ? {} : this._details[this._sel] || {};
    const values = this._keep || d;
    const fields = (add ? ADD_FIELDS : EDIT_FIELDS).map((f) => this._field(f, values[f[0]] ?? "")).join("");
    const hint = add ? "Data e orari sono quelli locali di ogni aeroporto."
      : `Svuota orari, terminal o gate per toglierli.${d.classes ? ` Classi: ${escapeHtml(d.classes)}.` : ""}`;
    return `<div class="title">${add ? "Nuovo volo" : escapeHtml(this._sel)}</div>
      <form>${fields}
        <div class="muted small">${hint}</div>
        ${this._formError ? `<div class="error">${escapeHtml(this._formError)}</div>` : ""}
        <div class="actions">
          <button type="button" data-act="back">Annulla</button>
          ${add ? "" : `<button type="button" class="danger" data-act="delete">Elimina il volo</button>`}
          <button type="submit" class="primary">${add ? "Aggiungi" : "Salva"}</button>
        </div>
      </form>`;
  }

  _confirm() {
    return `<div class="title">${escapeHtml(this._sel)}</div>
      <div>Elimino questo volo? Non si può annullare.</div>
      ${this._formError ? `<div class="error">${escapeHtml(this._formError)}</div>` : ""}
      <div class="actions">
        <button data-act="cancel-delete">Annulla</button>
        <button class="danger" data-act="confirm-delete">Elimina</button>
      </div>`;
  }

  _render() {
    if (!this.shadowRoot || !this._config) return;
    const body = this._view === "list" ? this._list() : this._view === "delete" ? this._confirm() : this._form();
    this.shadowRoot.innerHTML = `<style>${this._styles()}</style>
      <ha-card header="${escapeHtml(this._config.title)}">
        <div class="content ${this._busy ? "busy" : ""}">${body}</div>
      </ha-card>`;
    if (this._busy) for (const b of this.shadowRoot.querySelectorAll("button, input")) b.disabled = true;
  }

  _styles() {
    return `
      .content { padding: 0 16px 16px; display: flex; flex-direction: column; gap: 10px; }
      .busy { opacity: 0.6; }
      .title { font-weight: 600; }
      .muted { color: var(--secondary-text-color); }
      .small { font-size: 0.85em; }
      .notice { padding: 8px 10px; border-radius: 8px; background: rgba(var(--rgb-success-color, 67, 160, 71), 0.15); }
      .error { padding: 8px 10px; border-radius: 8px; background: rgba(var(--rgb-error-color, 219, 68, 55), 0.15);
               color: var(--error-color, #db4437); }
      .rows { display: flex; flex-direction: column; }
      .row { display: flex; justify-content: space-between; align-items: center; gap: 8px; text-align: left;
             background: none; border: 0; border-bottom: 1px solid var(--divider-color, #e0e0e0); border-radius: 0;
             padding: 10px 2px; color: var(--primary-text-color); font: inherit; cursor: pointer; }
      .row:last-child { border-bottom: 0; }
      .row ha-icon { --mdc-icon-size: 18px; color: var(--secondary-text-color); flex: none; }
      form { display: flex; flex-direction: column; gap: 10px; }
      label { display: flex; flex-direction: column; gap: 4px; font-size: 0.9em; color: var(--secondary-text-color); }
      input { font: inherit; font-size: 16px; padding: 8px 10px; border-radius: 8px; color: var(--primary-text-color);
              border: 1px solid var(--divider-color, #ccc); background: var(--card-background-color, #fff);
              box-sizing: border-box; width: 100%; min-height: 40px; }
      input:focus { outline: 2px solid var(--primary-color, #03a9f4); outline-offset: -1px; }
      .actions { display: flex; flex-wrap: wrap; gap: 8px; justify-content: flex-end; }
      button { font: inherit; padding: 8px 14px; border-radius: 18px; cursor: pointer; min-height: 36px;
               border: 1px solid var(--divider-color, #ccc); background: none; color: var(--primary-text-color);
               display: inline-flex; align-items: center; gap: 4px; }
      button ha-icon { --mdc-icon-size: 18px; }
      button.primary { background: var(--primary-color, #03a9f4); border-color: transparent;
                       color: var(--text-primary-color, #fff); }
      button.danger { color: var(--error-color, #db4437); border-color: var(--error-color, #db4437); }
      button.link { border: 0; padding: 4px 0; color: var(--primary-color, #03a9f4); align-self: flex-start; }
      button:disabled { cursor: default; }
    `;
  }
}

if (!customElements.get("boarding-pass-tracker-manage-card")) {  // an older version already loaded must not be redefined
  customElements.define("boarding-pass-tracker-manage-card", ManageCard);
  window.customCards = window.customCards || [];
  // name and description appear in the dashboard card picker: Italian user text
  window.customCards.push({ type: "boarding-pass-tracker-manage-card", name: "Gestisci voli (Boarding Pass Tracker)",
    description: "Aggiungi, modifica o elimina un volo." });
}
