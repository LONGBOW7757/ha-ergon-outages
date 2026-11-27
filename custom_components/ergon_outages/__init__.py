"""The Ergon Energy Outages integration."""

from __future__ import annotations

import asyncio

import voluptuous as vol

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.storage import Store
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN, STORAGE_VERSION, storage_key
from .coordinator import ErgonConfigEntry, ErgonOutagesCoordinator
from .maplinks import async_setup_map_links

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.IMAGE,
    Platform.SENSOR,
]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

SERVICE_RESEND_ALERTS = "resend_alerts"
ATTR_CONFIG_ENTRY_ID = "config_entry_id"


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the integration-wide actions."""

    async def _async_resend_alerts(call: ServiceCall) -> None:
        entry_id = call.data.get(ATTR_CONFIG_ENTRY_ID)
        coordinators = [
            entry.runtime_data
            for entry in hass.config_entries.async_entries(DOMAIN)
            if entry.state is ConfigEntryState.LOADED
            and (entry_id is None or entry.entry_id == entry_id)
        ]
        if entry_id is not None and not coordinators:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="unknown_location"
            )
        # Reset every chosen location first, so outages they share are
        # combined into one alert instead of one per location.
        for coordinator in coordinators:
            coordinator.async_prepare_resend()
        await asyncio.gather(*(c.async_refresh() for c in coordinators))

    hass.services.async_register(
        DOMAIN,
        SERVICE_RESEND_ALERTS,
        _async_resend_alerts,
        schema=vol.Schema({vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string}),
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ErgonConfigEntry) -> bool:
    """Set up Ergon Energy Outages from a config entry."""
    async_setup_map_links(hass)
    coordinator = ErgonOutagesCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    coordinator.async_entities_ready()
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
    return True


async def _async_reload_entry(hass: HomeAssistant, entry: ErgonConfigEntry) -> None:
    """Reload after the options (location, radius, interval) change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ErgonConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: ErgonConfigEntry) -> None:
    """Delete the saved snapshot when the integration entry is deleted."""
    await Store(hass, STORAGE_VERSION, storage_key(entry.entry_id)).async_remove()
