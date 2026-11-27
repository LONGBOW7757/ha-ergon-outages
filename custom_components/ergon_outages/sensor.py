"""Sensors: summary counts/times plus one sensor per outage EVENT_ID."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import OUTAGE_SENSOR_KEY_PREFIX
from .coordinator import ErgonConfigEntry, ErgonOutagesCoordinator
from .entity import (
    ErgonOutagesEntity,
    async_track_outage_entities,
    feature_collection,
    outage_attributes,
    outages_in_phase,
)
from .models import PHASE_CURRENT, PHASE_FUTURE, Outage

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class ErgonSensorDescription(SensorEntityDescription):
    """Summary sensor with functions that read the coordinator data."""

    value_fn: Callable[[ErgonOutagesCoordinator], Any]
    attrs_fn: Callable[[ErgonOutagesCoordinator], dict[str, Any]] = lambda _: {}


def _next_future(coordinator: ErgonOutagesCoordinator) -> Outage | None:
    future = [o for o in outages_in_phase(coordinator, PHASE_FUTURE) if o.start]
    return future[0] if future else None


def _latest_restoration(coordinator: ErgonOutagesCoordinator) -> datetime | None:
    times = [
        o.estimated_restoration
        for o in outages_in_phase(coordinator, PHASE_CURRENT)
        if o.estimated_restoration
    ]
    return max(times) if times else None


def _next_attrs(coordinator: ErgonOutagesCoordinator) -> dict[str, Any]:
    outage = _next_future(coordinator)
    if outage is None:
        return {}
    return {
        "event_id": outage.event_id,
        "reason": outage.reason,
        "estimated_restoration": outage.estimated_restoration,
        "customers_affected": outage.customers_affected,
    }


SUMMARY_SENSORS: tuple[ErgonSensorDescription, ...] = (
    ErgonSensorDescription(
        key="current_outages",
        translation_key="current_outages",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda c: len(outages_in_phase(c, PHASE_CURRENT)),
        attrs_fn=lambda c: {"event_ids": [o.event_id for o in outages_in_phase(c, PHASE_CURRENT)]},
    ),
    ErgonSensorDescription(
        key="upcoming_outages",
        translation_key="upcoming_outages",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda c: len(outages_in_phase(c, PHASE_FUTURE)),
        attrs_fn=lambda c: {"event_ids": [o.event_id for o in outages_in_phase(c, PHASE_FUTURE)]},
    ),
    ErgonSensorDescription(
        key="next_outage_start",
        translation_key="next_outage_start",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda c: (o.start if (o := _next_future(c)) else None),
        attrs_fn=_next_attrs,
    ),
    ErgonSensorDescription(
        key="estimated_restoration",
        translation_key="estimated_restoration",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=_latest_restoration,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ErgonConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(ErgonSummarySensor(coordinator, desc) for desc in SUMMARY_SENSORS)

    async_track_outage_entities(
        hass,
        entry,
        "sensor",
        OUTAGE_SENSOR_KEY_PREFIX,
        lambda event_id: ErgonOutageSensor(coordinator, event_id),
        async_add_entities,
    )


class ErgonSummarySensor(ErgonOutagesEntity, SensorEntity):
    """A fixed sensor that summarises all outages for the location."""

    entity_description: ErgonSensorDescription
    _unrecorded_attributes = frozenset({"event_ids"})

    def __init__(self, coordinator: ErgonOutagesCoordinator, description: ErgonSensorDescription) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self.coordinator)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return self.entity_description.attrs_fn(self.coordinator)


class ErgonOutageSensor(ErgonOutagesEntity, SensorEntity):
    """One outage. Created when it appears in the feed, removed when it clears."""

    _attr_translation_key = "outage"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = [PHASE_CURRENT, PHASE_FUTURE]
    _unrecorded_attributes = frozenset({"geojson", "streets", "suburbs"})

    def __init__(self, coordinator: ErgonOutagesCoordinator, event_id: str) -> None:
        super().__init__(coordinator, f"{OUTAGE_SENSOR_KEY_PREFIX}{event_id}")
        self._event_id = event_id
        self._attr_name = f"Outage {event_id}"

    @property
    def _outage(self) -> Outage | None:
        return (self.coordinator.data or {}).get(self._event_id)

    @property
    def available(self) -> bool:
        return super().available and self._outage is not None

    @property
    def native_value(self) -> str | None:
        outage = self._outage
        return outage.phase if outage else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        outage = self._outage
        if outage is None:
            return {"event_id": self._event_id}
        attrs: dict[str, Any] = {
            **outage_attributes(outage),
            "geojson": feature_collection([outage]),
        }
        # latitude/longitude let the built-in Map card show a pin per outage.
        if centroid := outage.centroid:
            attrs["latitude"], attrs["longitude"] = centroid
        return attrs
