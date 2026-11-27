"""Combine the same outage seen by several locations into one alert.

Each location polls on its own schedule, so Mitchell and Roma would spot
the same new outage minutes apart. When any location reports a change, the hub
immediately re-checks the other locations, then fires ONE event per
(kind, EVENT_ID) naming every location it affects, e.g. "Mitchell + Roma".
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import logging
from typing import TYPE_CHECKING, Any

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, callback

from .const import (
    DOMAIN,
    EVENT_NEW_OUTAGE,
    EVENT_OUTAGE_CLEARED,
    EVENT_OUTAGE_STARTED,
    EVENT_OUTAGE_UPDATED,
)
from .maplinks import async_create_map_link
from .models import KIND_CLEARED, KIND_NEW, KIND_STARTED, KIND_UPDATED
from .renderer import build_spec

if TYPE_CHECKING:
    from .coordinator import ErgonOutagesCoordinator

_LOGGER = logging.getLogger(__name__)

_DATA_HUB = f"{DOMAIN}_alert_hub"
_RECHECK_TIMEOUT = 60  # seconds to wait for the other locations to re-check

EVENT_TYPES = {
    KIND_NEW: EVENT_NEW_OUTAGE,
    KIND_STARTED: EVENT_OUTAGE_STARTED,
    KIND_UPDATED: EVENT_OUTAGE_UPDATED,
    KIND_CLEARED: EVENT_OUTAGE_CLEARED,
}


@dataclass
class _Report:
    coordinator: ErgonOutagesCoordinator
    summary: dict[str, Any]
    geometries: list[dict[str, Any]]
    resent: bool = False


@dataclass
class _PendingAlert:
    kind: str
    event_id: str
    reports: dict[str, _Report] = field(default_factory=dict)  # by entry_id


@callback
def async_get_alert_hub(hass: HomeAssistant) -> AlertHub:
    if (hub := hass.data.get(_DATA_HUB)) is None:
        hub = hass.data[_DATA_HUB] = AlertHub(hass)
    return hub


class AlertHub:
    """Collects outage changes from every location and fires merged events."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass
        self._pending: dict[tuple[str, str], _PendingAlert] = {}
        self._window: asyncio.Task | None = None

    @callback
    def async_submit(
        self,
        coordinator: ErgonOutagesCoordinator,
        kind: str,
        summary: dict[str, Any],
        geometries: list[dict[str, Any]],
        *,
        resent: bool = False,
    ) -> None:
        event_id = summary["event_id"]
        pending = self._pending.setdefault((kind, event_id), _PendingAlert(kind, event_id))
        pending.reports[coordinator.config_entry.entry_id] = _Report(
            coordinator, summary, geometries, resent
        )
        if self._window is None:
            self._window = self._hass.async_create_task(
                self._async_window(coordinator), f"{DOMAIN} alert window"
            )

    async def _async_window(self, initiator: ErgonOutagesCoordinator) -> None:
        try:
            # Let the initiating location finish submitting its whole batch.
            await asyncio.sleep(0)
            others = [
                entry.runtime_data
                for entry in self._hass.config_entries.async_entries(DOMAIN)
                if entry.state is ConfigEntryState.LOADED
                and entry.runtime_data is not initiator
                and entry.runtime_data.entities_ready
            ]
            if others:
                tasks = [self._hass.async_create_task(c.async_refresh()) for c in others]
                await asyncio.wait(tasks, timeout=_RECHECK_TIMEOUT)
        finally:
            pending, self._pending = self._pending, {}
            self._window = None
        for alert in pending.values():
            try:
                self._async_fire(alert)
            except Exception:  # noqa: BLE001 - never lose the other alerts
                _LOGGER.exception("Could not send Ergon outage alert %s", alert.event_id)

    @callback
    def _async_fire(self, alert: _PendingAlert) -> None:
        # Alphabetical, so the same towns always read the same way.
        reports = sorted(alert.reports.values(), key=lambda r: r.coordinator.config_entry.title.casefold())
        coordinators = [r.coordinator for r in reports]
        titles = [c.config_entry.title for c in coordinators]
        active = alert.kind != KIND_CLEARED
        geometries = next((r.geometries for r in reports if r.geometries), [])

        # Map: the outage (green if cleared) with every affected location's
        # circle; when several locations are involved, keep them all in view.
        outages = {
            o.event_id: o
            for c in coordinators
            for o in (c.data or {}).values()
            if o.event_id != alert.event_id or active
        }
        spec = build_spec(
            zones=[c.zone for c in coordinators],
            outages=outages.values(),
            frame=geometries,
            cleared=geometries if not active else (),
            points=[(c.zone[0], c.zone[1]) for c in coordinators] if len(coordinators) > 1 else (),
            feeders=any(c.show_feeders for c in coordinators),
        )
        map_url = async_create_map_link(self._hass, spec) if geometries else None

        detail_ids = (
            [d for c in coordinators if (d := c.async_detail_entity_id(alert.event_id))]
            if active
            else []
        )
        self._hass.bus.async_fire(
            EVENT_TYPES[alert.kind],
            {
                **reports[0].summary,
                "location": " + ".join(titles),
                "locations": titles,
                "config_entry_id": coordinators[0].config_entry.entry_id,
                "config_entry_ids": [c.config_entry.entry_id for c in coordinators],
                "map_entity_id": coordinators[0].map_entity_id,
                "map_url": map_url,
                "detail_entity_id": detail_ids[0] if detail_ids else None,
                "detail_entity_ids": detail_ids,
                # True when sent again via the Resend alerts button/action.
                "resent": any(r.resent for r in reports),
            },
        )
