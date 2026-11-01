"""The repair issues of the integration (INTEGRATION_PLAN.md 5.6): created and deleted only on the event loop, from
@callback functions (rule R-D: never from the executor). Issue ids hold no personal data (C13): a person's issue is
keyed by its subentry id, and the name and the notify service go only into the translation placeholders.

  entity_id_taken                          a sensor could not get its pinned id (entity_ids.async_check_pinned)
  decoder_unreachable                      the decoder is down for 15 minutes and photos wait (after a clock round)
  notify_service_missing_<subentry_id>     a person's notify service does not exist (bridge, on ServiceNotFound)
"""
from __future__ import annotations

import time

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir

from .const import (CONF_NAME, CONF_NOTIFY, DECODER_ISSUE_AFTER, DOMAIN, ISSUE_DECODER_UNREACHABLE,
                    ISSUE_ENTITY_ID_TAKEN, ISSUE_NOTIFY_MISSING, SUBENTRY_PERSON)


def notify_issue_id(subentry_id: str) -> str:
    return f"{ISSUE_NOTIFY_MISSING}_{subentry_id}"


def notify_service_of(data) -> str:
    """The notify service of a person subentry, without a leading "notify."."""
    service = str(data.get(CONF_NOTIFY) or "")
    return service.removeprefix("notify.")


@callback
def _create(hass: HomeAssistant, issue_id: str, translation_key: str, placeholders: dict[str, str]) -> None:
    ir.async_create_issue(hass, DOMAIN, issue_id, is_fixable=False, is_persistent=False,
                          severity=ir.IssueSeverity.WARNING, translation_key=translation_key,
                          translation_placeholders=placeholders)


@callback
def async_entity_ids_taken(hass: HomeAssistant, pairs: list[tuple[str, str]]) -> None:
    """[(wanted, actual)]: raise the issue for these sensors, or delete it when there is none."""
    if not pairs:
        ir.async_delete_issue(hass, DOMAIN, ISSUE_ENTITY_ID_TAKEN)
        return
    _create(hass, ISSUE_ENTITY_ID_TAKEN, ISSUE_ENTITY_ID_TAKEN,
            {"wanted": ", ".join(w for w, _ in pairs), "actual": ", ".join(a for _, a in pairs)})


@callback
def async_decoder_report(hass: HomeAssistant, url: str | None, report: dict | None, now: float | None = None) -> None:
    """After a clock round: the decoder down for DECODER_ISSUE_AFTER with photos waiting raises the issue; up, no
    photo waiting or no decoder deletes it. `report`: what engine.clock_round returned."""
    report = report or {}
    since = report.get("decoder_down_since")
    photos = int(report.get("queued_photos") or 0)
    now = time.time() if now is None else now
    if (url and report.get("decoder_status") == "down" and since is not None and photos > 0
            and now - since >= DECODER_ISSUE_AFTER.total_seconds()):
        _create(hass, ISSUE_DECODER_UNREACHABLE, ISSUE_DECODER_UNREACHABLE,
                {"url": url, "minutes": str(int((now - since) // 60)), "n": str(photos)})
    else:
        ir.async_delete_issue(hass, DOMAIN, ISSUE_DECODER_UNREACHABLE)


@callback
def async_delete_decoder(hass: HomeAssistant) -> None:
    ir.async_delete_issue(hass, DOMAIN, ISSUE_DECODER_UNREACHABLE)


def _people_with(entry, service: str):
    for sub in entry.subentries.values():
        if sub.subentry_type == SUBENTRY_PERSON and notify_service_of(sub.data) == service:
            yield sub


@callback
def async_notify_missing(hass: HomeAssistant, entry, service: str) -> None:
    """notify.<service> does not exist: an issue for each person who uses it."""
    for sub in _people_with(entry, service):
        _create(hass, notify_issue_id(sub.subentry_id), ISSUE_NOTIFY_MISSING,
                {"name": str(sub.data.get(CONF_NAME) or sub.title), "service": service})


@callback
def async_notify_ok(hass: HomeAssistant, entry, service: str) -> None:
    """notify.<service> worked: the issues of the persons who use it go."""
    for sub in _people_with(entry, service):
        ir.async_delete_issue(hass, DOMAIN, notify_issue_id(sub.subentry_id))


@callback
def async_delete_person(hass: HomeAssistant, subentry_id: str) -> None:
    """A person subentry was removed or reconfigured (config_flow): its issue goes."""
    ir.async_delete_issue(hass, DOMAIN, notify_issue_id(subentry_id))


@callback
def async_prune_people(hass: HomeAssistant, entry) -> None:
    """The notify issues of person subentries that no longer exist (a person removed from the UI, where no flow
    runs): setup and every clock round."""
    alive = {notify_issue_id(sub_id) for sub_id in entry.subentries}
    prefix = f"{ISSUE_NOTIFY_MISSING}_"
    for domain, issue_id in list(ir.async_get(hass).issues):
        if domain == DOMAIN and issue_id.startswith(prefix) and issue_id not in alive:
            ir.async_delete_issue(hass, DOMAIN, issue_id)


@callback
def async_delete_all(hass: HomeAssistant) -> None:
    """Every issue of the domain (async_remove_entry)."""
    registry = ir.async_get(hass)
    for domain, issue_id in list(registry.issues):
        if domain == DOMAIN:
            ir.async_delete_issue(hass, DOMAIN, issue_id)
