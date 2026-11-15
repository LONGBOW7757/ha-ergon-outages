"""Thin client for the Ergon outage ArcGIS FeatureServer."""

from __future__ import annotations

from typing import Any

import aiohttp

from .const import OUT_FIELDS, QUERY_URL, REQUEST_TIMEOUT


class ErgonApiError(Exception):
    """Raised when the outage service cannot be queried."""


async def async_query_outages(
    session: aiohttp.ClientSession,
    latitude: float,
    longitude: float,
    radius_m: float,
) -> list[dict[str, Any]]:
    """Return GeoJSON features for outage areas touching the given circle.

    ArcGIS does the point-in-polygon / buffer maths server side, so only the
    outages that actually affect the location are downloaded.
    """
    params = {
        "where": "1=1",
        "geometry": f"{longitude},{latitude}",  # ArcGIS wants x (lon) first
        "geometryType": "esriGeometryPoint",
        "inSR": "4326",
        "outSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": OUT_FIELDS,
        "returnGeometry": "true",
        "f": "geojson",
    }
    if radius_m and radius_m > 0:
        params["distance"] = str(int(radius_m))
        params["units"] = "esriSRUnit_Meter"

    try:
        async with session.get(
            QUERY_URL,
            params=params,
            timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
        ) as resp:
            resp.raise_for_status()
            payload = await resp.json(content_type=None)
    except (aiohttp.ClientError, TimeoutError, ValueError) as err:
        raise ErgonApiError(f"Error querying Ergon outage service: {err}") from err

    if not isinstance(payload, dict):
        raise ErgonApiError("Unexpected response from Ergon outage service")
    # ArcGIS reports query errors with HTTP 200 and an "error" object.
    if "error" in payload:
        raise ErgonApiError(f"Ergon outage service error: {payload['error']}")

    features = payload.get("features")
    if not isinstance(features, list):
        raise ErgonApiError("Ergon outage service returned no feature list")
    return features
