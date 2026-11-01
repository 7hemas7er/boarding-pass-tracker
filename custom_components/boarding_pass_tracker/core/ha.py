"""Home Assistant for the boarding-pass-tracker core: the service's identity, the three sensors, the notification
facade and the strings a sensor state is published as. No import of Home Assistant: the core runs in HA's executor
and reaches HA only through the two transport hooks of HA, which the integration implements (bridge.HassBridge).

Port of the standalone tracker/ha.py (INTEGRATION_PLAN.md 3.7 H1-H3): the identity constants, SENSORS, HAError and
HA.notify are kept word for word; the REST and websocket transport, the entity registry reads, the entity id check
and the MQTT sensors (paho) are gone with the standalone service.
"""
import datetime, json, logging, re

# The service's identity: the per-repo constants that the code shared with household-ledger reads
PROJECT = "boarding-pass-tracker"     # the device model and the MQTT origin
PRODUCT = "Boarding Pass Tracker"     # the HA device name: a proper noun, not translated
VERSION = "0.1.0"
DEFAULT_PREFIX = "boarding_pass_tracker"   # mqtt.prefix: topics, unique_ids, entity ids, client id, tags, group

SENSORS = {   # name -> discovery config (unique_id <prefix>_<name>, entity_id sensor.<prefix>_<name>)
    "next_flight": {"name": "Prossimo volo", "icon": "mdi:airplane-clock"},     # Italian state; attributes: a card
    "upcoming_flights": {"name": "Voli", "icon": "mdi:airplane"},               # a count; attributes: list
    "flight_statistics": {"name": "Statistiche voli", "icon": "mdi:chart-box-outline"},  # flown; flight_stats.compute
}

_NAME = re.compile(r"[a-z0-9_]+")     # HA domains and services: they end up in the URL path
_LOGGER = logging.getLogger(__name__)


def log(*a):
    """Like engine.log: WARNING for a line that starts with «warning:», else INFO; the handler adds the time."""
    text = " ".join(str(x) for x in a)
    (_LOGGER.warning if text.startswith("warning:") else _LOGGER.info)(text)


class HAError(RuntimeError):
    """HA answered with an error, or did not answer. `code`: the HTTP status, None if there is none."""
    def __init__(self, message, code=None):
        super().__init__(message)
        self.code = code


class HA:
    """The Home Assistant calls of the engine: notify, state and service. Errors become HAError (except notify,
    which returns False). The transport is two hooks, _get_state and _call_service, implemented by the
    integration's bridge (bridge.HassBridge), which runs them on HA's event loop and waits for the result.

    Positional arguments (the url and the token reader of the standalone's REST client) are accepted and ignored,
    so that code written for the standalone still builds one; `group` is data.group of the notifications."""

    def __init__(self, *_unused, timeout=20, group=DEFAULT_PREFIX):
        self.timeout = timeout
        self.group = group            # data.group of the notifications: the phone stacks them per service

    # ------------------------------------------------------------ transport (the integration's bridge)
    def _get_state(self, entity):
        """{"state", "attributes", ...} of `entity`, None if it does not exist. HAError if HA did not answer."""
        raise HAError("Home Assistant not connected")

    def _call_service(self, domain, name, data):
        """Calls the service <domain>.<name> with `data`. HAError if it failed or HA did not answer."""
        raise HAError("Home Assistant not connected")

    # ------------------------------------------------------------ the engine's calls
    def service(self, domain, name, data):
        """The service <domain>.<name> (the Flightradar24 text entities, the phones' notify services)."""
        if not (isinstance(domain, str) and _NAME.fullmatch(domain) and isinstance(name, str) and _NAME.fullmatch(name)):
            raise ValueError(f"invalid service: {domain!r}.{name!r}")
        self._call_service(domain, name, data or {})

    def state(self, entity):
        """{"state", "attributes", ...} of `entity`; None if the entity does not exist."""
        if not isinstance(entity, str) or not re.fullmatch(r"[a-z_]+\.[a-z0-9_]+", entity):
            raise ValueError(f"invalid entity: {entity!r}")
        return self._get_state(entity)

    def notify(self, notify, title, message, actions=None, tag=None):
        """notify.<notify> (mobile_app_...) with the app's actionable buttons, as in engine.notify.
        False if it did not go out: the caller retries later."""
        data = {"group": self.group}
        if tag:
            data["tag"] = tag
        if actions:
            data["actions"] = actions
        try:
            self.service("notify", notify, {"title": title, "message": message, "data": data})
            return True
        except Exception as e:
            log(f"notification not sent ({type(e).__name__}: {str(e)[:120]})")
            return False


def _state_payload(v):
    """For HA's MQTT sensors "None" is unknown: an empty date or "unknown" would cause a parsing error."""
    if v is None or v == "unknown":
        return "None"
    if isinstance(v, (datetime.date, datetime.datetime)):
        return v.isoformat()
    return str(v)[:255]                               # HA truncates states beyond 255 characters: better here


def payloads(state, attributes):
    """(state, attributes JSON): the two strings the standalone published over MQTT for a sensor, the same here
    (the integration's sensors are fed from them). State None or "unknown" -> "None"; at most 255 characters; a
    lone surrogate (half an emoji) is replaced, so the value can always be encoded."""
    return tuple(p.encode("utf-8", "replace").decode()
                 for p in (_state_payload(state), json.dumps(attributes or {}, ensure_ascii=False, default=str)))


def sensor_entity_ids(prefix):
    """{default entity id: unique_id} of this service's sensors."""
    return {f"sensor.{prefix}_{name}": f"{prefix}_{name}" for name in SENSORS}
