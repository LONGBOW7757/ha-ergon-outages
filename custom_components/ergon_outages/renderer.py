"""Outage map rendering shared by map images and notification links."""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
import hashlib
import json
import logging
from typing import TYPE_CHECKING, Any

import aiohttp

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import DOMAIN, TILE_USER_AGENT
from .maprender import (
    COLOR_CLEARED,
    COLOR_CURRENT,
    COLOR_FUTURE,
    RGB,
    Zone,
    compose_map,
    plan_map,
)

if TYPE_CHECKING:
    from .coordinator import ErgonOutagesCoordinator
    from .models import Outage

_LOGGER = logging.getLogger(__name__)

_DATA_TILES = f"{DOMAIN}_tiles"
_TILE_CACHE_MAX = 256
_PNG_CACHE_MAX = 24
_TILE_TIMEOUT = aiohttp.ClientTimeout(total=15)

Geometry = dict[str, Any]


@dataclass(frozen=True)
class MapSpec:
    """Everything needed to draw one map, independent of live data."""

    zones: tuple[Zone, ...]
    layers: tuple[tuple[RGB, tuple[Geometry, ...]], ...]
    frame: tuple[Geometry, ...] = ()
    points: tuple[tuple[float, float], ...] = ()  # (lat, lon) to keep in view
    _key: str = field(default="", compare=False, repr=False)

    @property
    def key(self) -> str:
        if not self._key:
            blob = json.dumps([self.zones, self.layers, self.frame, self.points], default=str)
            object.__setattr__(self, "_key", hashlib.sha1(blob.encode()).hexdigest())
        return self._key


def build_spec(
    zones: Sequence[Zone],
    outages: Iterable[Outage],
    frame: Sequence[Geometry] = (),
    cleared: Sequence[Geometry] = (),
    points: Sequence[tuple[float, float]] = (),
) -> MapSpec:
    """Current outages red, upcoming orange, cleared green (drawn on top)."""
    current: list[Geometry] = []
    future: list[Geometry] = []
    for outage in outages:
        (current if outage.is_current else future).extend(outage.geometries)
    layers = [(COLOR_FUTURE, tuple(future)), (COLOR_CURRENT, tuple(current))]
    if cleared:
        layers.append((COLOR_CLEARED, tuple(cleared)))
    return MapSpec(
        zones=tuple(zones),
        layers=tuple(layers),
        frame=tuple(frame),
        points=tuple(points),
    )


async def async_render_spec(hass: HomeAssistant, spec: MapSpec) -> bytes:
    """Render a spec to PNG over OpenStreetMap tiles."""
    plan = plan_map(spec.zones, spec.frame, spec.points)
    tiles = plan.tiles()
    data = await asyncio.gather(*(_async_fetch_tile(hass, plan.tile_url(t)) for t in tiles))
    return await hass.async_add_executor_job(
        compose_map, plan, dict(zip(tiles, data)), spec.zones, spec.layers
    )


async def _async_fetch_tile(hass: HomeAssistant, url: str) -> bytes | None:
    cache: dict[str, bytes] = hass.data.setdefault(_DATA_TILES, {})
    if (cached := cache.get(url)) is not None:
        return cached
    try:
        async with async_get_clientsession(hass).get(
            url, headers={"User-Agent": TILE_USER_AGENT}, timeout=_TILE_TIMEOUT
        ) as resp:
            resp.raise_for_status()
            data = await resp.read()
    except (aiohttp.ClientError, TimeoutError) as err:
        _LOGGER.debug("Map tile %s unavailable: %s", url, err)
        return None
    if len(cache) >= _TILE_CACHE_MAX:
        cache.pop(next(iter(cache)))
    cache[url] = data
    return data


class MapRenderer:
    """Maps for one location's entities: the overview, or zoomed to one outage."""

    def __init__(self, hass: HomeAssistant, coordinator: ErgonOutagesCoordinator) -> None:
        self._hass = hass
        self._coordinator = coordinator
        self._lock = asyncio.Lock()
        self._pngs: OrderedDict[str, bytes] = OrderedDict()

    def signature(self) -> str:
        """Changes whenever anything drawn on this location's maps changes."""
        c = self._coordinator
        parts = [
            (o.event_id, o.phase, o.geometries)
            for o in sorted((c.data or {}).values(), key=lambda o: o.event_id)
        ]
        blob = json.dumps([parts, c.zone], sort_keys=True, default=str)
        return hashlib.sha1(blob.encode()).hexdigest()

    def spec(self, focus: str | None = None) -> MapSpec:
        c = self._coordinator
        outages = c.data or {}
        focused = outages.get(focus) if focus else None
        frame = focused.geometries if focused else [g for o in outages.values() for g in o.geometries]
        return build_spec([c.zone], outages.values(), frame)

    async def async_render(self, focus: str | None = None) -> bytes:
        spec = self.spec(focus)
        async with self._lock:
            if (png := self._pngs.get(spec.key)) is not None:
                self._pngs.move_to_end(spec.key)
                return png
            png = await async_render_spec(self._hass, spec)
            self._pngs[spec.key] = png
            while len(self._pngs) > _PNG_CACHE_MAX:
                self._pngs.popitem(last=False)
            return png
