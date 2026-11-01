"""Config flow, options flow and person subentries of Boarding Pass Tracker (INTEGRATION_PLAN.md 4.5, 5.5).

Config flow (one entry, `single_config_entry` in the manifest):
  user      the description of what leaves Home Assistant; aborts `entity_ids_taken` when another integration holds
            the three pinned sensor ids; then the data folder is inspected (core/adopt.py, in the executor)
  adopt     an existing tracker.db: its counts, or an abort with the refusal reason
  people    adopted data first: one form per name found in the rows (person_found, which may `skip`), then one per
            config name of the export's manifest without rows (person_config); then, as on a fresh install, a
            multi-select of Home Assistant users (people) and one form each (person)
  services  AviationStack key and monthly cap, decoder URL (its /health checked on HA's aiohttp session), the three
            Flightradar24 entities (pre-filled from the entity registry)
  reconfigure / reauth_confirm   the AviationStack key (reauth is started by the runtime when AviationStack refuses
            the key: bridge/runtime hop to the loop, then entry.async_start_reauth)
Options (OptionsFlowWithReload): decoder URL, cap, Flightradar24 entities, flights and notifications on or off.
Subentry `person` (add and reconfigure, no reload: the runtime reads the subentries on every use).

Everything here runs on the event loop: the file system is only touched by adopt.inspect / adopt.names_in_use in
the executor.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any
from urllib.parse import urlsplit

import aiohttp
import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigEntryState,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryData,
    ConfigSubentryFlow,
    OptionsFlowWithReload,
    SubentryFlowResult,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.hassio import is_hassio
from homeassistant.helpers.selector import (
    BooleanSelector,
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
from homeassistant.util import slugify

from . import entity_ids, issues
from .const import (
    CONF_AVIATIONSTACK_KEY,
    CONF_DECODER_URL,
    CONF_FLIGHTS_ENABLED,
    CONF_FR24_ADD,
    CONF_FR24_REMOVE,
    CONF_FR24_TRACKED,
    CONF_HA_USER,
    CONF_MONTHLY_CAP,
    CONF_NAME,
    CONF_NOTIFY,
    CONF_NOTIFY_ENABLED,
    CONF_PERSON,
    CONF_SKIP,
    DATA_DIR_NAME,
    DEFAULT_FLIGHTS_ENABLED,
    DEFAULT_FR24,
    DEFAULT_MONTHLY_CAP,
    DEFAULT_NOTIFY_ENABLED,
    DOMAIN,
    FR24_DOMAINS,
    FR24_PLATFORM,
    FR24_UNIQUE_ID_SUFFIXES,
    MAX_MONTHLY_CAP,
    NAME_PATTERN,
    NOTIFY_PATTERN,
    PERSON_PATTERN,
    SUBENTRY_PERSON,
    TITLE,
)
from .core import adopt

_LOGGER = logging.getLogger(__name__)

CONF_USERS = "users"                    # step people: the HA users to add
DECODER_PORT = 8099
DECODER_SLUG = "barcode_decoder"        # the app: local_barcode_decoder, or <repository hash>_barcode_decoder
HEALTH_TIMEOUT_S = 5
HEALTH_MAX_BYTES = 64 * 1024
SUPERVISOR_TIMEOUT_S = 10
FR24_KEYS = (CONF_FR24_ADD, CONF_FR24_REMOVE, CONF_FR24_TRACKED)

PASSWORD = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))
URL = TextSelector(TextSelectorConfig(type=TextSelectorType.URL))
TEXT = TextSelector()
CAP = NumberSelector(NumberSelectorConfig(min=0, max=MAX_MONTHLY_CAP, step=1, mode=NumberSelectorMode.BOX))
PERSON_ENTITY = EntitySelector(EntitySelectorConfig(domain="person"))


class InvalidURL(ValueError):
    """The decoder address is not http(s)://host[:port]."""


# ---------------------------------------------------------------- data folder (executor)
def data_folder(hass: HomeAssistant) -> str:
    return hass.config.path(DATA_DIR_NAME)


async def _inspect(hass: HomeAssistant) -> dict:
    return await hass.async_add_executor_job(adopt.inspect, data_folder(hass))


async def _names_in_use(hass: HomeAssistant, entry: ConfigEntry) -> set[str] | None:
    """The names the rows use: from the running engine when the entry is loaded, else from a copy of the folder
    (adopt.inspect). None when they cannot be known (then a rename is refused)."""
    rt = getattr(entry, "runtime_data", None) if entry.state is ConfigEntryState.LOADED else None
    if rt is not None and not getattr(rt, "stopping", False):
        try:
            return set(await rt.run(adopt.names_in_use))
        except Exception:                              # engine.Closed during a stop, a database error
            _LOGGER.debug("names in use not read from the engine", exc_info=True)
            return None
    report = await _inspect(hass)
    if report["state"] == "empty":
        return set()
    if report["state"] == "ok" or report["reason"] == "file_mismatch":
        return set(report["users"])
    return None


async def _ids_taken(hass: HomeAssistant) -> list[str]:
    """The pinned sensor ids another integration (or a state without a registry entry) holds (entity_ids.py)."""
    return list(entity_ids.ids_taken(hass))


# ---------------------------------------------------------------- people: defaults and checks
async def _ha_users(hass: HomeAssistant) -> list:
    """The active, not system-generated Home Assistant users, by name."""
    users = [u for u in await hass.auth.async_get_users() if u.is_active and not u.system_generated]
    return sorted(users, key=lambda u: ((u.name or "").lower(), u.id))


def _user_options(users) -> list[SelectOptionDict]:
    return [SelectOptionDict(value=u.id, label=u.name or u.id) for u in users]


def _letters(text: str | None) -> str:
    return "".join(c for c in (text or "").lower() if "a" <= c <= "z")


def _notify_services(hass: HomeAssistant) -> list[str]:
    return sorted(s for s in hass.services.async_services_for_domain("notify") if s.startswith("mobile_app_"))


def _default_notify(hass: HomeAssistant, user_id: str | None) -> str | None:
    """The notify services of the mobile_app entries of that user (an existing service first)."""
    if not user_id:
        return None
    names = sorted({slugify(f"mobile_app_{e.data['device_name']}")
                    for e in hass.config_entries.async_entries("mobile_app")
                    if e.data.get("user_id") == user_id and e.data.get("device_name")})
    existing = set(_notify_services(hass))
    names.sort(key=lambda n: n not in existing)
    return names[0] if names else None


def _person_of_user(hass: HomeAssistant, user_id: str | None) -> str | None:
    if not user_id:
        return None
    found = sorted(s.entity_id for s in hass.states.async_all("person") if s.attributes.get("user_id") == user_id)
    return found[0] if found else None


def _guess_user(hass: HomeAssistant, users, name: str) -> str | None:
    """The HA user of a stored name: the user of person.<name>, else a user whose name has the same letters."""
    ids = {u.id for u in users}
    state = hass.states.get(f"person.{name}")
    if state is not None and state.attributes.get("user_id") in ids:
        return state.attributes["user_id"]
    same = [u.id for u in users if _letters(u.name) == name]
    return same[0] if len(same) == 1 else None


def _default_name(hass: HomeAssistant, user) -> str:
    """A new person's name: the object id of the user's person, else the user's name, letters only."""
    person = _person_of_user(hass, user.id)
    return _letters(person.split(".", 1)[1]) if person else _letters(user.name)


def _defaults(hass: HomeAssistant, user_id: str | None, name: str | None) -> dict:
    out: dict[str, Any] = {}
    if user_id:
        out[CONF_HA_USER] = user_id
    if notify := _default_notify(hass, user_id):
        out[CONF_NOTIFY] = notify
    if person := _person_of_user(hass, user_id) or (name and f"person.{name}"):
        out[CONF_PERSON] = person
    return out


def _resolve_entity(hass: HomeAssistant, value: Any) -> str | None:
    """An entity id from a selector value (an entity id, or a registry id the selector also accepts)."""
    if not value or not isinstance(value, str):
        return None
    value = value.strip()
    if "." in value:
        return value
    return er.async_resolve_entity_id(er.async_get(hass), value) or value


def _person_schema(hass: HomeAssistant, *, users=None, name: bool = True, skip: bool = False) -> vol.Schema:
    fields: dict[Any, Any] = {}
    if name:
        fields[vol.Required(CONF_NAME)] = TEXT
    if users is not None:
        fields[vol.Optional(CONF_HA_USER)] = SelectSelector(SelectSelectorConfig(
            options=_user_options(users), mode=SelectSelectorMode.DROPDOWN))
    fields[vol.Optional(CONF_NOTIFY)] = SelectSelector(SelectSelectorConfig(
        options=_notify_services(hass), custom_value=True, mode=SelectSelectorMode.DROPDOWN))
    fields[vol.Optional(CONF_PERSON)] = PERSON_ENTITY
    if skip:
        fields[vol.Optional(CONF_SKIP, default=False)] = BooleanSelector()
    return vol.Schema(fields)


def check_person(hass: HomeAssistant, user_input: dict, others: list[dict], *, name: str | None = None,
                 ha_user: str | None = None, fixed_user: bool = False) -> tuple[dict, dict[str, str]]:
    """The subentry data of a person form, and its errors: `others` are the other people (their data). `name` given:
    a name found in the rows (fixed, not checked: it is the stored one). `fixed_user`: the HA user is `ha_user`."""
    errors: dict[str, str] = {}
    if name is None:
        name = str(user_input.get(CONF_NAME) or "").strip()
        if not re.fullmatch(NAME_PATTERN, name):
            errors[CONF_NAME] = "invalid_name"
        elif any(o.get(CONF_NAME) == name for o in others):
            errors[CONF_NAME] = "name_taken"
    user_id = ha_user if fixed_user else (user_input.get(CONF_HA_USER) or None)
    if user_id and any(o.get(CONF_HA_USER) == user_id for o in others):
        errors[CONF_HA_USER] = "user_taken"
    notify = str(user_input.get(CONF_NOTIFY) or "").strip().removeprefix("notify.")
    if not re.fullmatch(NOTIFY_PATTERN, notify):
        errors[CONF_NOTIFY] = "invalid_notify"
    person = _resolve_entity(hass, user_input.get(CONF_PERSON))
    if person is not None and not re.fullmatch(PERSON_PATTERN, person):
        errors[CONF_PERSON] = "invalid_person"
    return {CONF_NAME: name, CONF_HA_USER: user_id, CONF_NOTIFY: notify, CONF_PERSON: person}, errors


def _people_of(entry: ConfigEntry, but: str | None = None) -> list[dict]:
    return [dict(s.data) for s in entry.subentries.values()
            if s.subentry_type == SUBENTRY_PERSON and s.subentry_id != but]


# ---------------------------------------------------------------- services: decoder and Flightradar24
def normalize_decoder_url(value: Any) -> str | None:
    """http(s)://host[:port] (a trailing / dropped), None when empty; InvalidURL otherwise: a path, a query, a
    fragment, user info, another scheme, a bad port."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise InvalidURL(value)
    value = value.strip()
    if not value:
        return None
    try:
        u = urlsplit(value)
        port = u.port
    except ValueError as err:
        raise InvalidURL(value) from err
    if (u.scheme.lower() not in ("http", "https") or not u.hostname or u.username is not None
            or u.password is not None or u.path not in ("", "/") or u.query or u.fragment
            or "@" in u.netloc or (port is not None and not 0 < port < 65536)):
        raise InvalidURL(value)
    return f"{u.scheme.lower()}://{u.netloc}"


async def check_decoder(hass: HomeAssistant, url: str) -> str | None:
    """GET <url>/health on HA's shared session, within HEALTH_TIMEOUT_S, no redirects: None when it is the decoder
    (api 1), else the error key (decoder_unreachable, decoder_wrong_api). The body is read to its end (in as many
    pieces as the network or a proxy's chunked encoding gives), up to HEALTH_MAX_BYTES + 1."""
    session = async_get_clientsession(hass)
    try:
        async with asyncio.timeout(HEALTH_TIMEOUT_S):
            async with session.get(f"{url}/health", allow_redirects=False) as resp:
                if resp.status >= 500:
                    return "decoder_unreachable"
                if resp.status != 200:
                    return "decoder_wrong_api"
                body, stream = b"", resp.content
                while len(body) <= HEALTH_MAX_BYTES:       # one read(n) gives only what has arrived (PLATFORM-03)
                    chunk = await stream.read(HEALTH_MAX_BYTES + 1 - len(body))
                    if not chunk:
                        break
                    body += chunk
    except (TimeoutError, aiohttp.ClientError, OSError, ValueError):
        return "decoder_unreachable"
    if len(body) > HEALTH_MAX_BYTES:
        return "decoder_wrong_api"
    try:
        health = json.loads(body)
    except ValueError:
        return "decoder_wrong_api"
    if not isinstance(health, dict) or health.get("api") != 1 or isinstance(health.get("api"), bool):
        return "decoder_wrong_api"
    return None


async def _validate_decoder(hass: HomeAssistant, value: Any, current: str | None = None
                            ) -> tuple[str | None, dict[str, str], dict[str, str]]:
    """(the URL to store, errors, placeholders). An unchanged URL (options) is not checked again."""
    try:
        url = normalize_decoder_url(value)
    except InvalidURL:
        return None, {CONF_DECODER_URL: "invalid_url"}, {}
    if url is None or url == current:
        return url, {}, {}
    if error := await check_decoder(hass, url):
        return None, {CONF_DECODER_URL: error}, {"url": url}
    return url, {}, {}


async def suggested_decoder_url(hass: HomeAssistant) -> str | None:
    """On HA OS / Supervised: http://<host name of the Barcode Decoder app>:8099 (local_barcode_decoder first). None
    elsewhere or when the app is not installed."""
    if not is_hassio(hass):
        return None
    try:
        from homeassistant.components.hassio import get_supervisor_client
        client = get_supervisor_client(hass)
        async with asyncio.timeout(SUPERVISOR_TIMEOUT_S):
            slugs = sorted((a.slug for a in await client.addons.list()
                            if a.slug == f"local_{DECODER_SLUG}" or a.slug.endswith(f"_{DECODER_SLUG}")),
                           key=lambda s: (s != f"local_{DECODER_SLUG}", s))
            if not slugs:
                return None
            info = await client.addons.addon_info(slugs[0])
        return f"http://{info.hostname}:{DECODER_PORT}" if info.hostname else None
    except Exception:                                 # no Supervisor, no app: no suggestion
        _LOGGER.debug("no decoder suggestion", exc_info=True)
        return None


def _fr24_rank(hass: HomeAssistant, e: er.RegistryEntry) -> tuple:
    """Lower is better: an enabled entity of a loaded entry with a live state before a leftover (disabled, of an
    entry not loaded, or only restored by HA: no longer provided by the integration), then by entity id."""
    entry = hass.config_entries.async_get_entry(e.config_entry_id) if e.config_entry_id else None
    st = hass.states.get(e.entity_id)
    return (e.disabled_by is not None, entry is None or entry.state is not ConfigEntryState.LOADED,
            st is None or bool(st.attributes.get("restored")), e.entity_id)


def fr24_registry_defaults(hass: HomeAssistant) -> dict[str, str]:
    """The Flightradar24 entities found in the entity registry (platform flightradar24, unique id suffix), the
    live ones first (_fr24_rank)."""
    found: dict[str, str] = {}
    entries = list(er.async_get(hass).entities.values())
    for key in FR24_KEYS:
        matches = [e for e in entries if e.platform == FR24_PLATFORM and e.domain == FR24_DOMAINS[key]
                   and str(e.unique_id).endswith(FR24_UNIQUE_ID_SUFFIXES[key])]
        if matches:
            found[key] = min(matches, key=lambda e: _fr24_rank(hass, e)).entity_id
    return found


def _fr24_options(hass: HomeAssistant, user_input: dict) -> dict[str, str]:
    """The three ids to store: the chosen one, else the registry default, else the fixed default (the engine needs
    an entity id; one that does not exist is skipped)."""
    registry = fr24_registry_defaults(hass)
    out = {}
    for key in FR24_KEYS:
        chosen = _resolve_entity(hass, user_input.get(key))
        out[key] = chosen if chosen and "." in chosen else registry.get(key, DEFAULT_FR24[key])
    return out


def _cap(value: Any) -> int:
    try:
        return max(0, min(int(value), MAX_MONTHLY_CAP))
    except (TypeError, ValueError):
        return DEFAULT_MONTHLY_CAP


def _fr24_fields() -> dict:
    return {vol.Optional(key): EntitySelector(EntitySelectorConfig(integration=FR24_PLATFORM,
                                                                   domain=FR24_DOMAINS[key]))
            for key in FR24_KEYS}


def _key(user_input: dict) -> str | None:
    return str(user_input.get(CONF_AVIATIONSTACK_KEY) or "").strip() or None


# ---------------------------------------------------------------- config flow
class BoardingPassTrackerConfigFlow(ConfigFlow, domain=DOMAIN):
    """The single config entry."""

    VERSION = 1
    MINOR_VERSION = 1

    def __init__(self) -> None:
        self._report: dict | None = None
        self._persons: list[dict] = []      # the person subentries to create (their data)
        self._found: list[str] = []         # names found in the rows, still to ask
        self._config_names: list[str] = []  # names of the export's config without rows, still to ask
        self._users: list[str] = []         # HA users chosen in `people`, still to ask
        self._people_asked = False

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> BoardingPassTrackerOptionsFlow:
        return BoardingPassTrackerOptionsFlow()

    @classmethod
    @callback
    def async_get_supported_subentry_types(cls, config_entry: ConfigEntry) -> dict[str, type[ConfigSubentryFlow]]:
        return {SUBENTRY_PERSON: PersonSubentryFlow}

    async def _abort_if_ids_taken(self) -> ConfigFlowResult | None:
        if taken := await _ids_taken(self.hass):
            return self.async_abort(reason="entity_ids_taken", description_placeholders={"ids": ", ".join(taken)})
        return None

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if abort := await self._abort_if_ids_taken():
            return abort
        if user_input is None:
            return self.async_show_form(step_id="user", data_schema=vol.Schema({}))
        report = await _inspect(self.hass)
        if report["state"] == "refused":
            return self.async_abort(reason=report["reason"],
                                    description_placeholders={"n": str(report["mismatched"])})
        if report["state"] == "ok":
            self._report = report
            return await self.async_step_adopt()
        return await self.async_step_people()

    async def async_step_adopt(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        r = self._report or {}
        if user_input is None:
            counts = r.get("counts", {})
            return self.async_show_form(step_id="adopt", data_schema=vol.Schema({}), description_placeholders={
                "flights": str(counts.get("flights", 0)), "documents": str(counts.get("documents", 0)),
                "files": str(r.get("files", 0)), "passes": str(counts.get("boarding_passes", 0)),
                "schema": str(r.get("schema", 0)), "queued": str(r.get("queued", 0)),
                "unnotified": str(r.get("unnotified", 0))})
        self._found = list(r.get("users", []))
        self._config_names = [n for n in r.get("config_users", []) if n not in self._found]
        return await self._next_person()

    async def _next_person(self) -> ConfigFlowResult:
        if self._found:
            return await self.async_step_person_found()
        if self._config_names:
            return await self.async_step_person_config()
        if not self._people_asked:
            return await self.async_step_people()
        if self._users:
            return await self.async_step_person()
        return await self.async_step_services()

    async def async_step_person_found(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """A name found in the rows: its HA user, phone and person, or `skip` (no subentry, the rows stay)."""
        name = self._found[0]
        users = await _ha_users(self.hass)
        errors: dict[str, str] = {}
        if user_input is not None:
            if user_input.get(CONF_SKIP):
                self._found.pop(0)
                return await self._next_person()
            person, errors = check_person(self.hass, user_input, self._persons, name=name)
            if not errors:
                self._persons.append(person)
                self._found.pop(0)
                return await self._next_person()
        suggested = user_input or _defaults(self.hass, _guess_user(self.hass, users, name), name)
        schema = self.add_suggested_values_to_schema(_person_schema(self.hass, users=users, name=False, skip=True),
                                                     suggested)
        return self.async_show_form(step_id="person_found", data_schema=schema, errors=errors,
                                    description_placeholders={"name": name})

    async def async_step_person_config(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """A name of the old service's config that has no rows (it gets the flight change alerts, CBV-06)."""
        config_name = self._config_names[0]
        users = await _ha_users(self.hass)
        errors: dict[str, str] = {}
        if user_input is not None:
            person, errors = check_person(self.hass, user_input, self._persons)
            if not errors:
                self._persons.append(person)
                self._config_names.pop(0)
                return await self._next_person()
        suggested = user_input or {CONF_NAME: config_name,
                                   **_defaults(self.hass, _guess_user(self.hass, users, config_name), config_name)}
        schema = self.add_suggested_values_to_schema(_person_schema(self.hass, users=users), suggested)
        return self.async_show_form(step_id="person_config", data_schema=schema, errors=errors,
                                    description_placeholders={"name": config_name})

    async def async_step_people(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """The HA users to add as people (none is fine: «Aggiungi persona» later)."""
        linked = {p[CONF_HA_USER] for p in self._persons if p.get(CONF_HA_USER)}
        users = [u for u in await _ha_users(self.hass) if u.id not in linked]
        if user_input is not None or not users:
            self._people_asked = True
            ids = {u.id for u in users}
            self._users = [i for i in (user_input or {}).get(CONF_USERS, []) if i in ids]
            return await self._next_person()
        schema = vol.Schema({vol.Optional(CONF_USERS, default=[]): SelectSelector(SelectSelectorConfig(
            options=_user_options(users), multiple=True, mode=SelectSelectorMode.LIST))})
        return self.async_show_form(step_id="people", data_schema=schema)

    async def async_step_person(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """One HA user chosen in `people`: the name, phone and person."""
        user_id = self._users[0]
        user = await self.hass.auth.async_get_user(user_id)
        if user is None:                                  # removed meanwhile
            self._users.pop(0)
            return await self._next_person()
        errors: dict[str, str] = {}
        if user_input is not None:
            person, errors = check_person(self.hass, user_input, self._persons, ha_user=user_id, fixed_user=True)
            if not errors:
                self._persons.append(person)
                self._users.pop(0)
                return await self._next_person()
        name = _default_name(self.hass, user)
        suggested = user_input or {k: v for k, v in {CONF_NAME: name, **_defaults(self.hass, user_id, name)}.items()
                                   if k != CONF_HA_USER and v}
        schema = self.add_suggested_values_to_schema(_person_schema(self.hass), suggested)
        return self.async_show_form(step_id="person", data_schema=schema, errors=errors,
                                    description_placeholders={"user": user.name or user_id})

    async def async_step_services(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        placeholders: dict[str, str] = {}
        if user_input is not None:
            url, errors, placeholders = await _validate_decoder(self.hass, user_input.get(CONF_DECODER_URL))
            if not errors:
                if abort := await self._abort_if_ids_taken():
                    return abort
                options = {CONF_DECODER_URL: url, CONF_MONTHLY_CAP: _cap(user_input.get(CONF_MONTHLY_CAP,
                                                                                         DEFAULT_MONTHLY_CAP)),
                           **_fr24_options(self.hass, user_input),
                           CONF_FLIGHTS_ENABLED: DEFAULT_FLIGHTS_ENABLED, CONF_NOTIFY_ENABLED: DEFAULT_NOTIFY_ENABLED}
                return self.async_create_entry(
                    title=TITLE, data={CONF_AVIATIONSTACK_KEY: _key(user_input)}, options=options,
                    subentries=[ConfigSubentryData(subentry_type=SUBENTRY_PERSON, title=p[CONF_NAME],
                                                   unique_id=p[CONF_NAME], data=p) for p in self._persons])
        schema = vol.Schema({vol.Optional(CONF_AVIATIONSTACK_KEY): PASSWORD,
                             vol.Optional(CONF_MONTHLY_CAP, default=DEFAULT_MONTHLY_CAP): CAP,
                             vol.Optional(CONF_DECODER_URL): URL, **_fr24_fields()})
        if user_input is None:
            suggested: dict[str, Any] = dict(fr24_registry_defaults(self.hass))
            if url := await suggested_decoder_url(self.hass):
                suggested[CONF_DECODER_URL] = url
        else:
            suggested = {k: v for k, v in user_input.items() if k != CONF_AVIATIONSTACK_KEY}
        return self.async_show_form(step_id="services", data_schema=self.add_suggested_values_to_schema(
            schema, suggested), errors=errors, description_placeholders=placeholders)

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """A new AviationStack key (empty: none)."""
        if user_input is not None:
            return self.async_update_reload_and_abort(self._get_reconfigure_entry(),
                                                      data_updates={CONF_AVIATIONSTACK_KEY: _key(user_input)})
        return self.async_show_form(step_id="reconfigure",
                                    data_schema=vol.Schema({vol.Optional(CONF_AVIATIONSTACK_KEY): PASSWORD}))

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        """AviationStack refused the key (started by the runtime, once per runtime)."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            if key := _key(user_input):
                return self.async_update_reload_and_abort(self._get_reauth_entry(),
                                                          data_updates={CONF_AVIATIONSTACK_KEY: key})
            errors[CONF_AVIATIONSTACK_KEY] = "invalid_key"
        return self.async_show_form(step_id="reauth_confirm", errors=errors,
                                    data_schema=vol.Schema({vol.Required(CONF_AVIATIONSTACK_KEY): PASSWORD}))


# ---------------------------------------------------------------- options
class BoardingPassTrackerOptionsFlow(OptionsFlowWithReload):
    """Decoder, cap, Flightradar24, flights and notifications on/off: a change reloads the entry (the data stays)."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        current = dict(self.config_entry.options)
        errors: dict[str, str] = {}
        placeholders: dict[str, str] = {}
        if user_input is not None:
            url, errors, placeholders = await _validate_decoder(self.hass, user_input.get(CONF_DECODER_URL),
                                                                current.get(CONF_DECODER_URL))
            if not errors:
                return self.async_create_entry(data={
                    CONF_DECODER_URL: url,
                    CONF_MONTHLY_CAP: _cap(user_input.get(CONF_MONTHLY_CAP, DEFAULT_MONTHLY_CAP)),
                    **_fr24_options(self.hass, user_input),
                    CONF_FLIGHTS_ENABLED: bool(user_input.get(CONF_FLIGHTS_ENABLED, DEFAULT_FLIGHTS_ENABLED)),
                    CONF_NOTIFY_ENABLED: bool(user_input.get(CONF_NOTIFY_ENABLED, DEFAULT_NOTIFY_ENABLED))})
        schema = vol.Schema({vol.Optional(CONF_DECODER_URL): URL,
                             vol.Optional(CONF_MONTHLY_CAP, default=DEFAULT_MONTHLY_CAP): CAP,
                             **_fr24_fields(),
                             vol.Optional(CONF_FLIGHTS_ENABLED, default=DEFAULT_FLIGHTS_ENABLED): BooleanSelector(),
                             vol.Optional(CONF_NOTIFY_ENABLED, default=DEFAULT_NOTIFY_ENABLED): BooleanSelector()})
        suggested = user_input if user_input is not None else {
            **fr24_registry_defaults(self.hass), **{k: v for k, v in current.items() if v is not None}}
        return self.async_show_form(step_id="init", data_schema=self.add_suggested_values_to_schema(schema, suggested),
                                    errors=errors, description_placeholders=placeholders)


# ---------------------------------------------------------------- person subentries
class PersonSubentryFlow(ConfigSubentryFlow):
    """«Aggiungi persona» and its reconfigure: no reload (the runtime reads the subentries live). A name the rows
    use cannot be changed (name_in_use): uploaded_by, gate_sent_to and walk link the rows to it."""

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        entry = self._get_entry()
        users = await _ha_users(self.hass)
        errors: dict[str, str] = {}
        if user_input is not None:
            person, errors = check_person(self.hass, user_input, _people_of(entry))
            if not errors:
                return self.async_create_entry(title=person[CONF_NAME], data=person, unique_id=person[CONF_NAME])
        schema = self.add_suggested_values_to_schema(_person_schema(self.hass, users=users), user_input or {})
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        entry = self._get_entry()
        sub = self._get_reconfigure_subentry()
        old = str(sub.data.get(CONF_NAME) or sub.title)
        users = await _ha_users(self.hass)
        errors: dict[str, str] = {}
        if user_input is not None:
            person, errors = check_person(self.hass, user_input, _people_of(entry, but=sub.subentry_id))
            if not errors and person[CONF_NAME] != old:
                in_use = await _names_in_use(self.hass, entry)
                if in_use is None or old in in_use:
                    errors[CONF_NAME] = "name_in_use"
            if not errors:
                issues.async_delete_person(self.hass, sub.subentry_id)
                return self.async_update_and_abort(entry, sub, title=person[CONF_NAME], unique_id=person[CONF_NAME],
                                                   data=person)
        suggested = user_input or {k: v for k, v in sub.data.items() if v is not None}
        schema = self.add_suggested_values_to_schema(_person_schema(self.hass, users=users), suggested)
        return self.async_show_form(step_id="reconfigure", data_schema=schema, errors=errors,
                                    description_placeholders={"name": old})
