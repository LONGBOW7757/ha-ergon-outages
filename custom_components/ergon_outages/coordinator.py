"""Polling coordinator for Ergon Energy outages."""

from __future__ import annotations

from datetime import timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_LATITUDE, CONF_LONGITUDE, CONF_RADIUS
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import ErgonApiError, async_query_outages
from .const import (
    CONF_SCAN_INTERVAL,
    DEFAULT_RADIUS_M,
    DEFAULT_SCAN_INTERVAL_MIN,
    DOMAIN,
    EVENT_NEW_OUTAGE,
    EVENT_OUTAGE_CLEARED,
    EVENT_OUTAGE_STARTED,
    EVENT_OUTAGE_UPDATED,
    STORAGE_VERSION,
    storage_key,
)
from .models import (
    KIND_CLEARED,
    KIND_NEW,
    KIND_STARTED,
    KIND_UPDATED,
    Outage,
    diff_outages,
    parse_features,
)

_LOGGER = logging.getLogger(__name__)

_EVENT_TYPES = {
    KIND_NEW: EVENT_NEW_OUTAGE,
    KIND_STARTED: EVENT_OUTAGE_STARTED,
    KIND_UPDATED: EVENT_OUTAGE_UPDATED,
    KIND_CLEARED: EVENT_OUTAGE_CLEARED,
}

type ErgonConfigEntry = ConfigEntry[ErgonOutagesCoordinator]


def entry_settings(entry: ConfigEntry) -> dict[str, Any]:
    """Options override the values entered when the entry was created."""
    return {**entry.data, **entry.options}


class ErgonOutagesCoordinator(DataUpdateCoordinator[dict[str, Outage]]):
    """Fetches outages for one location and announces changes on the event bus."""

    config_entry: ErgonConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ErgonConfigEntry) -> None:
        settings = entry_settings(entry)
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {entry.title}",
            update_interval=timedelta(
                minutes=int(settings.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL_MIN))
            ),
        )
        self.latitude: float = settings[CONF_LATITUDE]
        self.longitude: float = settings[CONF_LONGITUDE]
        self.radius_m: float = float(settings.get(CONF_RADIUS, DEFAULT_RADIUS_M))
        self._session = async_get_clientsession(hass)
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, storage_key(entry.entry_id)
        )
        # Outages already announced, persisted so a restart (or a failed poll
        # followed by a good one) never re-announces them. None until loaded;
        # stays None on the very first run so existing outages form a baseline.
        self._known: dict[str, dict[str, Any]] | None = None
        self._entities_ready = False
        self._pending_events: list[tuple[str, dict[str, Any]]] = []

    async def _async_setup(self) -> None:
        """Load the saved snapshot before the first refresh."""
        stored = await self._store.async_load()
        if stored is not None:
            self._known = stored.get("outages", {})

    async def _async_update_data(self) -> dict[str, Outage]:
        try:
            features = await async_query_outages(
                self._session, self.latitude, self.longitude, self.radius_m
            )
        except ErgonApiError as err:
            # Keeps the previous data and snapshot, so recovery is silent.
            raise UpdateFailed(str(err)) from err

        outages = parse_features(features, dt_util.utcnow())
        events, snapshot = diff_outages(self._known or {}, outages)

        if self._known is None:
            _LOGGER.debug(
                "First run for %s: recording %d existing outage(s) without events",
                self.config_entry.title,
                len(outages),
            )
        else:
            self._pending_events.extend(events)

        if snapshot != self._known:
            self._known = snapshot
            await self._store.async_save({"outages": snapshot})

        if self._entities_ready:
            # Deferred so listeners see the coordinator's new data first.
            self.hass.loop.call_soon(self._async_fire_pending_events)
        return outages

    @callback
    def async_entities_ready(self) -> None:
        """Called once all platforms are set up; releases held-back events.

        Events found by the first poll after a start or reload are held until
        the entities (notably the map image) exist, so automations can use them.
        """
        self._entities_ready = True
        self._async_fire_pending_events()

    @callback
    def _async_fire_pending_events(self) -> None:
        if not self._pending_events:
            return
        # Lets automations attach the map without hard-coding an entity ID.
        map_entity_id = er.async_get(self.hass).async_get_entity_id(
            "image", DOMAIN, f"{self.config_entry.entry_id}_outage_map"
        )
        events, self._pending_events = self._pending_events, []
        for kind, summary in events:
            self.hass.bus.async_fire(
                _EVENT_TYPES[kind],
                {
                    "config_entry_id": self.config_entry.entry_id,
                    "location": self.config_entry.title,
                    "map_entity_id": map_entity_id,
                    **summary,
                },
            )
