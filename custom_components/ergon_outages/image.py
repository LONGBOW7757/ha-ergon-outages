"""Image entity: a static map of the outages, for dashboards and notifications."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging

import aiohttp

from homeassistant.components.image import ImageEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import TILE_USER_AGENT
from .coordinator import ErgonConfigEntry, ErgonOutagesCoordinator
from .entity import ErgonOutagesEntity, outages_in_phase
from .maprender import COLOR_CURRENT, COLOR_FUTURE, compose_map, plan_map
from .models import PHASE_CURRENT, PHASE_FUTURE

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 0
_TILE_CACHE_MAX = 256
_TILE_TIMEOUT = aiohttp.ClientTimeout(total=15)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ErgonConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([ErgonOutageMapImage(entry.runtime_data)])


class ErgonOutageMapImage(ErgonOutagesEntity, ImageEntity):
    """Map of current (red) and upcoming (orange) outages around the location.

    The picture is only re-rendered when the set of outages or their areas
    change, so the OpenStreetMap tile servers are hit rarely.
    """

    _attr_translation_key = "outage_map"
    _attr_content_type = "image/png"

    def __init__(self, coordinator: ErgonOutagesCoordinator) -> None:
        ErgonOutagesEntity.__init__(self, coordinator, "outage_map")
        ImageEntity.__init__(self, coordinator.hass)
        self._signature: str | None = None
        self._png: bytes | None = None
        self._png_signature: str | None = None
        self._render_lock = asyncio.Lock()
        self._tile_cache: dict[str, bytes] = {}
        self._refresh_signature()
        coordinator.map_image = self

    def _current_signature(self) -> str:
        parts = [
            (o.event_id, o.phase, o.geometries)
            for o in sorted((self.coordinator.data or {}).values(), key=lambda o: o.event_id)
        ]
        blob = json.dumps(
            [parts, self.coordinator.latitude, self.coordinator.longitude, self.coordinator.radius_m],
            sort_keys=True,
            default=str,
        )
        return hashlib.sha1(blob.encode()).hexdigest()

    def _refresh_signature(self) -> bool:
        signature = self._current_signature()
        if signature == self._signature:
            return False
        self._signature = signature
        self._attr_image_last_updated = dt_util.utcnow()
        return True

    @callback
    def _handle_coordinator_update(self) -> None:
        self._refresh_signature()
        super()._handle_coordinator_update()

    async def async_image(self) -> bytes | None:
        async with self._render_lock:
            if self._png is not None and self._png_signature == self._signature:
                return self._png
            signature = self._signature
            png = await self._async_render()
            self._png, self._png_signature = png, signature
            return png

    async def _async_render(self) -> bytes:
        coordinator = self.coordinator
        home = (coordinator.latitude, coordinator.longitude)
        current = [g for o in outages_in_phase(coordinator, PHASE_CURRENT) for g in o.geometries]
        future = [g for o in outages_in_phase(coordinator, PHASE_FUTURE) for g in o.geometries]

        plan = plan_map(home, coordinator.radius_m, current + future)
        tiles = plan.tiles()
        results = await asyncio.gather(*(self._async_fetch_tile(plan.tile_url(t)) for t in tiles))
        return await self.hass.async_add_executor_job(
            compose_map,
            plan,
            dict(zip(tiles, results)),
            home,
            coordinator.radius_m,
            [(COLOR_FUTURE, future), (COLOR_CURRENT, current)],
        )

    async def _async_fetch_tile(self, url: str) -> bytes | None:
        if (cached := self._tile_cache.get(url)) is not None:
            return cached
        session = async_get_clientsession(self.hass)
        try:
            async with session.get(
                url, headers={"User-Agent": TILE_USER_AGENT}, timeout=_TILE_TIMEOUT
            ) as resp:
                resp.raise_for_status()
                data = await resp.read()
        except (aiohttp.ClientError, TimeoutError) as err:
            _LOGGER.debug("Map tile %s unavailable: %s", url, err)
            return None
        if len(self._tile_cache) >= _TILE_CACHE_MAX:
            self._tile_cache.pop(next(iter(self._tile_cache)))
        self._tile_cache[url] = data
        return data
