"""Shared entity base and helpers."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from typing import Any

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import ATTRIBUTION, DOMAIN
from .coordinator import ErgonOutagesCoordinator
from .models import Outage


def format_local(value: datetime | None) -> str:
    """Human readable local time for map tooltips, e.g. 'Sat 15 Nov 4:30 PM'."""
    if value is None:
        return "Unknown"
    local = dt_util.as_local(value)
    return f"{local:%a} {local.day} {local:%b} {local.hour % 12 or 12}:{local:%M %p}"


def outages_in_phase(coordinator: ErgonOutagesCoordinator, phase: str) -> list[Outage]:
    """Outages in the given phase, earliest start first."""
    outages = [o for o in (coordinator.data or {}).values() if o.phase == phase]
    return sorted(outages, key=lambda o: (o.start is None, o.start or dt_util.utcnow(), o.event_id))


def feature_collection(outages: Iterable[Outage]) -> dict[str, Any]:
    """Combine outages into one GeoJSON FeatureCollection for map cards."""
    features: list[dict[str, Any]] = []
    for outage in outages:
        features.extend(outage.features(format_local))
    return {"type": "FeatureCollection", "features": features}


class ErgonOutagesEntity(CoordinatorEntity[ErgonOutagesCoordinator]):
    """Base entity: one device per configured location."""

    _attr_has_entity_name = True
    _attr_attribution = ATTRIBUTION

    def __init__(self, coordinator: ErgonOutagesCoordinator, key: str) -> None:
        super().__init__(coordinator)
        entry = coordinator.config_entry
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=f"Ergon Outages {entry.title}",
            manufacturer="Ergon Energy",
            model="Outage Finder feed",
            entry_type=DeviceEntryType.SERVICE,
        )
