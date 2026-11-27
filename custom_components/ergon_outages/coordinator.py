"""Polling coordinator for Ergon Energy outages."""

from __future__ import annotations

import asyncio
from datetime import timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_LATITUDE, CONF_LONGITUDE, CONF_RADIUS
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr, entity_registry as er
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
    OUTAGE_MAP_KEY_PREFIX,
    STORAGE_VERSION,
    storage_key,
)
from .maprender import Zone
from .models import KIND_CLEARED, Outage, diff_outages, parse_features
from .renderer import MapRenderer

_LOGGER = logging.getLogger(__name__)

type ErgonConfigEntry = ConfigEntry[ErgonOutagesCoordinator]


def entry_settings(entry: ConfigEntry) -> dict[str, Any]:
    """Options override the values entered when the entry was created."""
    return {**entry.data, **entry.options}


class ErgonOutagesCoordinator(DataUpdateCoordinator[dict[str, Outage]]):
    """Fetches outages for one location and reports changes to the alert hub."""

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
        self.map_renderer = MapRenderer(hass, self)
        self._session = async_get_clientsession(hass)
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, storage_key(entry.entry_id)
        )
        # Outages already announced, persisted so a restart (or a failed poll
        # followed by a good one) never re-announces them. None until loaded;
        # stays None on the very first run so existing outages form a baseline.
        self._known: dict[str, dict[str, Any]] | None = None
        # Last known shape of each announced outage, so a "cleared" alert can
        # still show where it was. Persisted with the snapshot.
        self._known_geometries: dict[str, list[dict[str, Any]]] = {}
        self._entities_ready = False
        self._pending: list[tuple[str, dict[str, Any], list[dict[str, Any]]]] = []
        # Serialises polls: the alert hub can ask for an extra check while a
        # scheduled one is running.
        self._poll_lock = asyncio.Lock()

    @property
    def zone(self) -> Zone:
        return (self.latitude, self.longitude, self.radius_m)

    @property
    def entities_ready(self) -> bool:
        return self._entities_ready

    @property
    def map_entity_id(self) -> str | None:
        return er.async_get(self.hass).async_get_entity_id(
            "image", DOMAIN, f"{self.config_entry.entry_id}_outage_map"
        )

    async def _async_setup(self) -> None:
        """Load the saved snapshot before the first refresh."""
        stored = await self._store.async_load()
        if stored is not None:
            self._known = stored.get("outages", {})
            self._known_geometries = stored.get("geometries", {})

    async def _async_update_data(self) -> dict[str, Outage]:
        async with self._poll_lock:
            return await self._async_poll()

    async def _async_poll(self) -> dict[str, Outage]:
        try:
            features = await async_query_outages(
                self._session, self.latitude, self.longitude, self.radius_m
            )
        except ErgonApiError as err:
            # Keeps the previous data and snapshot, so recovery is silent.
            raise UpdateFailed(str(err)) from err

        outages = parse_features(features, dt_util.utcnow())
        events, snapshot = diff_outages(self._known or {}, outages)
        geometries = {eid: o.geometries for eid, o in outages.items()}

        if self._known is None:
            _LOGGER.debug(
                "First run for %s: recording %d existing outage(s) without alerts",
                self.config_entry.title,
                len(outages),
            )
        else:
            for kind, summary in events:
                eid = summary["event_id"]
                shape = (
                    self._known_geometries.get(eid, [])
                    if kind == KIND_CLEARED
                    else outages[eid].geometries
                )
                self._pending.append((kind, summary, shape))

        if snapshot != self._known or geometries != self._known_geometries:
            self._known = snapshot
            self._known_geometries = geometries
            await self._store.async_save({"outages": snapshot, "geometries": geometries})

        if self._entities_ready:
            self._async_submit_pending()
        return outages

    @callback
    def async_entities_ready(self) -> None:
        """Called once all platforms are set up; releases held-back alerts.

        Changes found by the first poll after a start or reload are held until
        the entities (notably the map images) exist, so alerts can link to them.
        """
        self._entities_ready = True
        self._async_submit_pending()

    @callback
    def _async_submit_pending(self) -> None:
        if not self._pending:
            return
        from .alerts import async_get_alert_hub  # noqa: PLC0415 - avoids an import cycle

        hub = async_get_alert_hub(self.hass)
        pending, self._pending = self._pending, []
        for kind, summary, shape in pending:
            hub.async_submit(self, kind, summary, shape)

    @callback
    def async_detail_entity_id(self, event_id: str) -> str | None:
        """Entity ID of the outage's map image, registering it if needed.

        The image platform adds the entity moments later; registering it now
        fixes its entity ID so the notification can link to it straight away.
        """
        ent_reg = er.async_get(self.hass)
        unique_id = f"{self.config_entry.entry_id}_{OUTAGE_MAP_KEY_PREFIX}{event_id}"
        if entity_id := ent_reg.async_get_entity_id("image", DOMAIN, unique_id):
            return entity_id
        if event_id not in (self.data or {}):
            return None
        device = dr.async_get(self.hass).async_get_device(
            identifiers={(DOMAIN, self.config_entry.entry_id)}
        )
        if device is None:
            return None
        return ent_reg.async_get_or_create(
            "image",
            DOMAIN,
            unique_id,
            config_entry=self.config_entry,
            device_id=device.id,
            suggested_object_id=f"{device.name_by_user or device.name} outage {event_id} map",
        ).entity_id
