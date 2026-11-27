"""Shared fixtures for the Ergon Energy Outages tests."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

pytest_plugins = "pytest_homeassistant_custom_component"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Allow Home Assistant to load custom_components/ in every test."""
    yield


@pytest.fixture(autouse=True)
def no_real_feeders(request):
    """Never hit the real feeder service; tests opt in with their own data."""
    if "real_feeder_fetch" in request.keywords:
        yield
        return
    from unittest.mock import patch

    with patch("custom_components.ergon_outages.renderer.async_get_feeders", return_value=[]):
        yield


def ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def feature(
    event_id: str,
    *,
    start: datetime | None,
    est_fix: datetime | None = None,
    outage_type: str = "PLANNED",
    status: str = "Work scheduled",
    customers: int = 10,
    lon: float = 146.245,
    lat: float = -26.401,
) -> dict[str, Any]:
    """A GeoJSON feature shaped like the real ArcGIS response."""
    d = 0.001
    return {
        "type": "Feature",
        "geometry": {
            "type": "Polygon",
            "coordinates": [
                [[lon - d, lat - d], [lon + d, lat - d], [lon + d, lat + d], [lon - d, lat + d], [lon - d, lat - d]]
            ],
        },
        "properties": {
            "EVENT_ID": event_id,
            "TYPE": outage_type,
            "STATUS": status,
            "CUSTOMERS_AFFECTED": customers,
            "SUBURBS": "LONGREACH",
            "STREETS": "ALFRED ST, WILLS ST",
            "START": ms(start) if start else None,
            "FINISH": None,
            "EST_FIX_TIME": ms(est_fix) if est_fix else None,
            "REASON": "The loss of supply is to replace power poles and improve the electricity network",
        },
    }


def collection(*features: dict[str, Any]) -> dict[str, Any]:
    return {"type": "FeatureCollection", "features": list(features)}


UTC = timezone.utc
