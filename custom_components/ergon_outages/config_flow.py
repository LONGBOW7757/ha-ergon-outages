"""Config and options flow for Ergon Energy Outages."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_LATITUDE, CONF_LONGITUDE, CONF_NAME, CONF_RADIUS
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import ErgonApiError, async_query_outages
from .const import (
    CONF_LOCATION,
    CONF_SCAN_INTERVAL,
    DEFAULT_NAME,
    DEFAULT_RADIUS_M,
    DEFAULT_SCAN_INTERVAL_MIN,
    DOMAIN,
    MAX_RADIUS_M,
    MAX_SCAN_INTERVAL_MIN,
    MIN_SCAN_INTERVAL_MIN,
)
from .coordinator import ErgonConfigEntry, entry_settings


def _location_fields(defaults: dict[str, Any]) -> dict[Any, Any]:
    return {
        vol.Required(CONF_LOCATION, default=defaults[CONF_LOCATION]): selector.LocationSelector(
            selector.LocationSelectorConfig(radius=True, icon="mdi:transmission-tower")
        ),
        vol.Required(
            CONF_SCAN_INTERVAL, default=defaults[CONF_SCAN_INTERVAL]
        ): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=MIN_SCAN_INTERVAL_MIN,
                max=MAX_SCAN_INTERVAL_MIN,
                step=1,
                unit_of_measurement="min",
                mode=selector.NumberSelectorMode.BOX,
            )
        ),
    }


def _parse_location(user_input: dict[str, Any]) -> dict[str, Any]:
    """Flatten the location selector into latitude/longitude/radius."""
    location = user_input[CONF_LOCATION]
    radius = float(location.get(CONF_RADIUS, DEFAULT_RADIUS_M))
    return {
        CONF_LATITUDE: float(location[CONF_LATITUDE]),
        CONF_LONGITUDE: float(location[CONF_LONGITUDE]),
        CONF_RADIUS: min(max(radius, 0.0), MAX_RADIUS_M),
        CONF_SCAN_INTERVAL: int(user_input[CONF_SCAN_INTERVAL]),
    }


async def _async_can_connect(hass: HomeAssistant, settings: dict[str, Any]) -> bool:
    try:
        await async_query_outages(
            async_get_clientsession(hass),
            settings[CONF_LATITUDE],
            settings[CONF_LONGITUDE],
            settings[CONF_RADIUS],
        )
    except ErgonApiError:
        return False
    return True


class ErgonOutagesConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the initial setup."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ErgonConfigEntry) -> OptionsFlow:
        return ErgonOutagesOptionsFlow()

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        defaults = {
            CONF_NAME: DEFAULT_NAME,
            CONF_LOCATION: {
                CONF_LATITUDE: self.hass.config.latitude,
                CONF_LONGITUDE: self.hass.config.longitude,
                CONF_RADIUS: DEFAULT_RADIUS_M,
            },
            CONF_SCAN_INTERVAL: DEFAULT_SCAN_INTERVAL_MIN,
        }

        if user_input is not None:
            settings = _parse_location(user_input)
            await self.async_set_unique_id(
                f"{settings[CONF_LATITUDE]:.4f}_{settings[CONF_LONGITUDE]:.4f}"
            )
            self._abort_if_unique_id_configured()

            if await _async_can_connect(self.hass, settings):
                name = user_input[CONF_NAME].strip() or DEFAULT_NAME
                return self.async_create_entry(
                    title=name, data={CONF_NAME: name, **settings}
                )
            errors["base"] = "cannot_connect"
            defaults = {**defaults, **user_input}

        schema = vol.Schema(
            {
                vol.Required(CONF_NAME, default=defaults[CONF_NAME]): selector.TextSelector(),
                **_location_fields(defaults),
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)


class ErgonOutagesOptionsFlow(OptionsFlow):
    """Change the location, radius or update interval after setup."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        current = entry_settings(self.config_entry)
        defaults = {
            CONF_LOCATION: {
                CONF_LATITUDE: current[CONF_LATITUDE],
                CONF_LONGITUDE: current[CONF_LONGITUDE],
                CONF_RADIUS: current.get(CONF_RADIUS, DEFAULT_RADIUS_M),
            },
            CONF_SCAN_INTERVAL: current.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL_MIN),
        }

        if user_input is not None:
            settings = _parse_location(user_input)
            if await _async_can_connect(self.hass, settings):
                return self.async_create_entry(data=settings)
            errors["base"] = "cannot_connect"
            defaults = {**defaults, **user_input}

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(_location_fields(defaults)),
            errors=errors,
        )
