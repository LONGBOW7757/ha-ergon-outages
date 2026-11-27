"""Renders outage maps for one location, shared by every map image and link."""

from __future__ import annotations

import asyncio
from collections import OrderedDict
import hashlib
import json
import logging
from typing import TYPE_CHECKING

import aiohttp

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import TILE_USER_AGENT
from .maprender import COLOR_CURRENT, COLOR_FUTURE, compose_map, plan_map

if TYPE_CHECKING:
    from .coordinator import ErgonOutagesCoordinator

_LOGGER = logging.getLogger(__name__)

_TILE_CACHE_MAX = 256
_PNG_CACHE_MAX = 24
_TILE_TIMEOUT = aiohttp.ClientTimeout(total=15)


class MapRenderer:
    """Draws the location's outages, optionally zoomed to one outage.

    Every outage is always drawn (red = current, orange = upcoming); `focus`
    only changes what the view is framed on. Rendered PNGs are cached until the
    outages change, and map tiles are cached, so OpenStreetMap is hit rarely.
    """

    def __init__(self, hass: HomeAssistant, coordinator: ErgonOutagesCoordinator) -> None:
        self._hass = hass
        self._coordinator = coordinator
        self._lock = asyncio.Lock()
        self._tiles: dict[str, bytes] = {}
        self._pngs: OrderedDict[tuple[str | None, str], bytes] = OrderedDict()

    def signature(self) -> str:
        """Changes whenever anything drawn on the map changes."""
        c = self._coordinator
        parts = [
            (o.event_id, o.phase, o.geometries)
            for o in sorted((c.data or {}).values(), key=lambda o: o.event_id)
        ]
        blob = json.dumps([parts, c.latitude, c.longitude, c.radius_m], sort_keys=True, default=str)
        return hashlib.sha1(blob.encode()).hexdigest()

    async def async_render(self, focus: str | None = None) -> bytes:
        key = (focus, self.signature())
        async with self._lock:
            if (png := self._pngs.get(key)) is not None:
                self._pngs.move_to_end(key)
                return png
            png = await self._async_render(focus)
            self._pngs[key] = png
            while len(self._pngs) > _PNG_CACHE_MAX:
                self._pngs.popitem(last=False)
            return png

    async def _async_render(self, focus: str | None) -> bytes:
        c = self._coordinator
        outages = c.data or {}
        home = (c.latitude, c.longitude)
        current = [g for o in outages.values() if o.is_current for g in o.geometries]
        future = [g for o in outages.values() if o.is_future for g in o.geometries]
        focused = outages.get(focus) if focus else None

        plan = plan_map(home, c.radius_m, focused.geometries if focused else current + future)
        tiles = plan.tiles()
        data = await asyncio.gather(*(self._async_fetch_tile(plan.tile_url(t)) for t in tiles))
        return await self._hass.async_add_executor_job(
            compose_map,
            plan,
            dict(zip(tiles, data)),
            home,
            c.radius_m,
            [(COLOR_FUTURE, future), (COLOR_CURRENT, current)],
        )

    async def _async_fetch_tile(self, url: str) -> bytes | None:
        if (cached := self._tiles.get(url)) is not None:
            return cached
        try:
            async with async_get_clientsession(self._hass).get(
                url, headers={"User-Agent": TILE_USER_AGENT}, timeout=_TILE_TIMEOUT
            ) as resp:
                resp.raise_for_status()
                data = await resp.read()
        except (aiohttp.ClientError, TimeoutError) as err:
            _LOGGER.debug("Map tile %s unavailable: %s", url, err)
            return None
        if len(self._tiles) >= _TILE_CACHE_MAX:
            self._tiles.pop(next(iter(self._tiles)))
        self._tiles[url] = data
        return data
