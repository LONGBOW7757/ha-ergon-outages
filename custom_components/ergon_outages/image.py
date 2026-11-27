"""Map images: one overview per location, plus one per outage.

The per-outage image is what a notification opens when tapped: its pop-up
shows a map zoomed to that outage with every detail of the outage below it.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.image import ImageEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import OUTAGE_MAP_KEY_PREFIX
from .coordinator import ErgonConfigEntry, ErgonOutagesCoordinator
from .entity import ErgonOutagesEntity, async_track_outage_entities, outage_attributes
from .models import Outage

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ErgonConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities([ErgonOutageMapImage(coordinator)])
    async_track_outage_entities(
        hass,
        entry,
        "image",
        OUTAGE_MAP_KEY_PREFIX,
        lambda event_id: ErgonOutageDetailImage(coordinator, event_id),
        async_add_entities,
    )


class _ErgonMapImage(ErgonOutagesEntity, ImageEntity):
    """Common behaviour: re-render only when the drawn outages change."""

    _attr_content_type = "image/png"
    _focus: str | None = None
    _details = False

    def __init__(self, coordinator: ErgonOutagesCoordinator, key: str) -> None:
        ErgonOutagesEntity.__init__(self, coordinator, key)
        ImageEntity.__init__(self, coordinator.hass)
        self._signature: str | None = None
        self._refresh_signature()

    def _refresh_signature(self) -> None:
        signature = self.coordinator.map_renderer.signature()
        if signature != self._signature:
            self._signature = signature
            self._attr_image_last_updated = dt_util.utcnow()

    @callback
    def _handle_coordinator_update(self) -> None:
        self._refresh_signature()
        super()._handle_coordinator_update()

    async def async_image(self) -> bytes | None:
        return await self.coordinator.map_renderer.async_render(self._focus, details=self._details)


class ErgonOutageMapImage(_ErgonMapImage):
    """All current (red) and upcoming (orange) outages around the location."""

    _attr_translation_key = "outage_map"

    def __init__(self, coordinator: ErgonOutagesCoordinator) -> None:
        super().__init__(coordinator, "outage_map")


class ErgonOutageDetailImage(_ErgonMapImage):
    """One outage: a map zoomed to it, with all its details as attributes."""

    _attr_translation_key = "outage_detail_map"
    _unrecorded_attributes = frozenset({"streets", "suburbs"})
    # The picture includes a panel with every detail, because the app's pop-up
    # for a picture shows only the picture.
    _details = True

    def __init__(self, coordinator: ErgonOutagesCoordinator, event_id: str) -> None:
        self._focus = event_id
        super().__init__(coordinator, f"{OUTAGE_MAP_KEY_PREFIX}{event_id}")
        self._attr_name = f"Outage {event_id} map"

    @property
    def _outage(self) -> Outage | None:
        return (self.coordinator.data or {}).get(self._focus)

    @property
    def available(self) -> bool:
        return super().available and self._outage is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        outage = self._outage
        if outage is None:
            return {"event_id": self._focus}
        return {"location": self.coordinator.config_entry.title, **outage_attributes(outage)}
