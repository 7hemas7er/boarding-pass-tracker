// Boarding Pass Tracker: the loader of its three Lovelace cards (INTEGRATION_PLAN.md 5.11), the only module the
// integration adds to every page (add_extra_js_url, with ?v=<version>). The frontend starts its own bundles and the
// extra modules at the same time, and its app bundle then puts its own element registry in window.customElements:
// a card defined before that is invisible to Lovelace, which shows an error card. So the three card files are
// imported only once the frontend's registry is in place: window.customElements replaced, or <home-assistant>
// defined (a frontend without the replacement). Same ?v= as this file (the version and a hash of the frontend files).
const CARDS = ["boarding-pass-tracker-flight-card.js", "boarding-pass-tracker-stats-card.js",
  "boarding-pass-tracker-manage-card.js"];
const first = window.customElements;
const ready = () => window.customElements !== first || window.customElements.get("home-assistant") !== undefined;
const load = () => {
  const query = new URL(import.meta.url).search;
  for (const name of CARDS) {
    import(new URL(name + query, import.meta.url).href).catch((err) => {
      console.error(`Boarding Pass Tracker: ${name} not loaded`, err);
    });
  }
};
const wait = () => (ready() ? load() : setTimeout(wait, 50));
wait();
