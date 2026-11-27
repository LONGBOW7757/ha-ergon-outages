"""Shared entity base and helpers."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import datetime
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import ATTRIBUTION, DOMAIN
from .coordinator import ErgonOutagesCoordinator
from .models import Outage
from .renderer import format_local


def outages_in_phase(coordinator: ErgonOutagesCoordinator, phase: str) -> list[Outage]:
    """Outages in the given phase, earliest start first."""
    outages = [o for o in (coordinator.data or {}).values() if o.phase == phase]
    return sorted(outages, key=lambda o: (o.start is None, o.start or dt_util.utcnow(), o.event_id))


def outage_attributes(outage: Outage) -> dict[str, Any]:
    """Human-facing details of one outage, shared by its sensor and map."""
    return {
        "event_id": outage.event_id,
        "outage_type": outage.outage_type,
        "status": outage.status,
        "reason": outage.reason,
        "customers_affected": outage.customers_affected,
        "suburbs": outage.suburbs,
        "streets": outage.streets,
        "start": outage.start,
        "estimated_restoration": outage.estimated_restoration,
        "start_text": format_local(outage.start),
        "estimated_restoration_text": format_local(outage.estimated_restoration),
    }


@callback
def async_track_outage_entities(
    hass: HomeAssistant,
    entry: ConfigEntry,
    domain: str,
    key_prefix: str,
    factory: Callable[[str], Entity],
    async_add_entities: Callable[[Iterable[Entity]], None],
) -> None:
    """Keep one entity per current outage: add new ones, remove cleared ones.

    Outages that ended while Home Assistant was stopped leave registry entries
    behind; those are purged first.
    """
    coordinator = entry.runtime_data
    ent_reg = er.async_get(hass)
    uid_prefix = f"{entry.entry_id}_{key_prefix}"
    tracked: set[str] = set()

    for reg_entry in er.async_entries_for_config_entry(ent_reg, entry.entry_id):
        if reg_entry.domain != domain or not reg_entry.unique_id.startswith(uid_prefix):
            continue
        if reg_entry.unique_id.removeprefix(uid_prefix) not in (coordinator.data or {}):
            ent_reg.async_remove(reg_entry.entity_id)

    @callback
    def _async_sync() -> None:
        current_ids = set(coordinator.data or {})
        added = current_ids - tracked
        if added:
            tracked.update(added)
            async_add_entities([factory(event_id) for event_id in sorted(added)])
        for event_id in tracked - current_ids:
            tracked.discard(event_id)
            # Removing the registry entry also removes the live entity.
            if entity_id := ent_reg.async_get_entity_id(domain, DOMAIN, f"{uid_prefix}{event_id}"):
                ent_reg.async_remove(entity_id)

    _async_sync()
    entry.async_on_unload(coordinator.async_add_listener(_async_sync))


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
