"""MV feeder lines (11/22 kV) from Energy Queensland's Network Capacity Map.

Public, undocumented layer: "MV Feeders For Search". Each record is a whole
feeder (a long MultiLineString), so lines are requested simplified to the map's
pixel size and cached, since the network rarely changes. Feeders are only
decoration: any error just means a map without lines.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
import time
from typing import Any

import aiohttp

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

FEEDER_QUERY_URL = (
    "https://services-ap1.arcgis.com/bHSo7vOUI7GCbw3n/arcgis/rest/services/"
    "Network_Capacity_Layers_PROD/FeatureServer/13/query"
)
_NAME_FIELD = "Feeder_Name"
_VOLTAGE_FIELD = "Feeder_Voltage"
_CACHE_TTL = 24 * 3600
_CACHE_MAX = 64
_TIMEOUT = aiohttp.ClientTimeout(total=20)
_DATA_CACHE = f"{DOMAIN}_feeder_cache"


@dataclass(frozen=True, slots=True)
class Feeder:
    name: str
    voltage: str | None
    geometry: dict[str, Any]

    @property
    def label(self) -> str:
        return f"{self.name} · {self.voltage}" if self.voltage else self.name


async def async_get_feeders(
    hass: HomeAssistant,
    bounds: tuple[float, float, float, float],
    tolerance_deg: float,
) -> list[Feeder]:
    """MV feeders crossing the (west, south, east, north) box, simplified."""
    key = (tuple(round(v, 3) for v in bounds), round(tolerance_deg, 7))
    cache: dict[Any, tuple[float, list[Feeder]]] = hass.data.setdefault(_DATA_CACHE, {})
    if (hit := cache.get(key)) and time.monotonic() - hit[0] < _CACHE_TTL:
        return hit[1]

    west, south, east, north = bounds
    params = {
        "where": "1=1",
        "geometry": f"{west},{south},{east},{north}",
        "geometryType": "esriGeometryEnvelope",
        "inSR": "4326",
        "outSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": f"{_NAME_FIELD},{_VOLTAGE_FIELD}",
        "returnGeometry": "true",
        "maxAllowableOffset": f"{tolerance_deg:.7f}",
        "geometryPrecision": "6",
        "f": "geojson",
    }
    try:
        async with async_get_clientsession(hass).get(
            FEEDER_QUERY_URL, params=params, timeout=_TIMEOUT
        ) as resp:
            resp.raise_for_status()
            payload = await resp.json(content_type=None)
    except (aiohttp.ClientError, TimeoutError, ValueError) as err:
        _LOGGER.debug("Feeder lines unavailable: %s", err)
        return []
    if not isinstance(payload, dict) or "error" in payload:
        _LOGGER.debug("Feeder query failed: %s", payload)
        return []

    feeders: list[Feeder] = []
    for feature in payload.get("features") or []:
        props = feature.get("properties") or {}
        geometry = feature.get("geometry")
        name = props.get(_NAME_FIELD)
        if name and geometry:
            feeders.append(Feeder(str(name), props.get(_VOLTAGE_FIELD) or None, geometry))

    if len(cache) >= _CACHE_MAX:
        cache.pop(next(iter(cache)))
    cache[key] = (time.monotonic(), feeders)
    return feeders
