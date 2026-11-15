"""Binary sensors: is there an outage now / is one coming up."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import ErgonConfigEntry, ErgonOutagesCoordinator
from .entity import ErgonOutagesEntity, feature_collection, outages_in_phase
from .models import PHASE_CURRENT, PHASE_FUTURE

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ErgonConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(
        [
            ErgonPhaseBinarySensor(coordinator, "outage_now", PHASE_CURRENT),
            ErgonPhaseBinarySensor(coordinator, "upcoming_outage", PHASE_FUTURE),
        ]
    )


class ErgonPhaseBinarySensor(ErgonOutagesEntity, BinarySensorEntity):
    """On while at least one outage in the given phase covers the location.

    The `geojson` attribute holds every matching polygon, so a map card can
    draw them without knowing the per-outage entity IDs.
    """

    # Polygons can be large; keep them out of the history database.
    _unrecorded_attributes = frozenset({"geojson", "event_ids"})

    def __init__(self, coordinator: ErgonOutagesCoordinator, key: str, phase: str) -> None:
        super().__init__(coordinator, key)
        self._phase = phase
        self._attr_translation_key = key
        if phase == PHASE_CURRENT:
            self._attr_device_class = BinarySensorDeviceClass.PROBLEM

    @property
    def is_on(self) -> bool:
        return bool(outages_in_phase(self.coordinator, self._phase))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        outages = outages_in_phase(self.coordinator, self._phase)
        return {
            "event_ids": [o.event_id for o in outages],
            # The monitored point, so map cards that need a marker position work.
            "latitude": self.coordinator.latitude,
            "longitude": self.coordinator.longitude,
            "radius_m": self.coordinator.radius_m,
            "geojson": feature_collection(outages),
        }
