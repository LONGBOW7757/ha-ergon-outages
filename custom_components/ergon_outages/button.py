"""Button: re-check Ergon and resend alerts for every outage at this location."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import ErgonConfigEntry, ErgonOutagesCoordinator
from .entity import ErgonOutagesEntity

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ErgonConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([ErgonResendAlertsButton(entry.runtime_data)])


class ErgonResendAlertsButton(ErgonOutagesEntity, ButtonEntity):
    """Forget what has been announced here, re-check, and alert again.

    Every outage the location currently covers (including upcoming planned
    ones) is sent again as a new alert, marked resent. Handy for testing.
    """

    _attr_translation_key = "resend_alerts"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: ErgonOutagesCoordinator) -> None:
        super().__init__(coordinator, "resend_alerts")

    async def async_press(self) -> None:
        await self.coordinator.async_resend_alerts()
