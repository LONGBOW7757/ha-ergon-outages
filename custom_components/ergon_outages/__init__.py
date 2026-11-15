"""The Ergon Energy Outages integration."""

from __future__ import annotations

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import STORAGE_VERSION, storage_key
from .coordinator import ErgonConfigEntry, ErgonOutagesCoordinator

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.IMAGE, Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: ErgonConfigEntry) -> bool:
    """Set up Ergon Energy Outages from a config entry."""
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
