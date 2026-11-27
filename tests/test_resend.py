"""Resend alerts: the per-location button and the all-locations action."""

from __future__ import annotations

from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from pytest_homeassistant_custom_component.common import async_capture_events
import pytest

from custom_components.ergon_outages.const import DOMAIN, EVENT_NEW_OUTAGE

from .conftest import feature
from .test_alerts import AUGATHELLA, MORVEN, _add, _settle, ergon  # noqa: F401 - fixture


async def test_button_resends_this_location(hass: HomeAssistant, ergon) -> None:  # noqa: F811
    ergon.add([MORVEN], feature("26MOR", start=None, lon=MORVEN[1], lat=MORVEN[0]))
    ergon.add([MORVEN, AUGATHELLA], feature("26BOTH", start=None, lon=146.85, lat=-26.1))
    await _add(hass, "Morven", MORVEN)  # first run: baseline, no alerts
    await _add(hass, "Augathella", AUGATHELLA)
    new = async_capture_events(hass, EVENT_NEW_OUTAGE)
    await _settle(hass)
    assert new == []

    await hass.services.async_call(
        "button", "press", {ATTR_ENTITY_ID: "button.ergon_outages_morven_resend_alerts"}, blocking=True
    )
    await _settle(hass)
    sent = sorted((e.data["event_id"], e.data["location"], e.data["resent"]) for e in new)
    # Only Morven was reset, so the shared outage names Morven alone.
    assert sent == [("26BOTH", "Morven", True), ("26MOR", "Morven", True)]
    assert all(e.data["map_url"] and e.data["detail_entity_id"] for e in new)

    # Pressing does not leave the location "forgetful": the next poll is quiet.
    for entry in hass.config_entries.async_entries(DOMAIN):
        await entry.runtime_data.async_refresh()
    await _settle(hass)
    assert len(new) == 2


async def test_action_resends_everything_combined(hass: HomeAssistant, ergon) -> None:  # noqa: F811
    ergon.add([MORVEN, AUGATHELLA], feature("26BOTH", start=None, lon=146.85, lat=-26.1))
    ergon.add([AUGATHELLA], feature("26AUG", start=None, lon=AUGATHELLA[1], lat=AUGATHELLA[0]))
    await _add(hass, "Morven", MORVEN)
    await _add(hass, "Augathella", AUGATHELLA)
    new = async_capture_events(hass, EVENT_NEW_OUTAGE)

    await hass.services.async_call(DOMAIN, "resend_alerts", {}, blocking=True)
    await _settle(hass)
    assert sorted((e.data["event_id"], e.data["location"]) for e in new) == [
        ("26AUG", "Augathella"),
        ("26BOTH", "Augathella + Morven"),
    ]
    assert all(e.data["resent"] for e in new)


async def test_action_for_one_location(hass: HomeAssistant, ergon) -> None:  # noqa: F811
    ergon.add([MORVEN], feature("26MOR", start=None, lon=MORVEN[1], lat=MORVEN[0]))
    ergon.add([AUGATHELLA], feature("26AUG", start=None, lon=AUGATHELLA[1], lat=AUGATHELLA[0]))
    morven = await _add(hass, "Morven", MORVEN)
    await _add(hass, "Augathella", AUGATHELLA)
    new = async_capture_events(hass, EVENT_NEW_OUTAGE)
    await hass.services.async_call(
        DOMAIN, "resend_alerts", {"config_entry_id": morven.entry_id}, blocking=True
    )
    await _settle(hass)
    assert [e.data["event_id"] for e in new] == ["26MOR"]

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN, "resend_alerts", {"config_entry_id": "nope"}, blocking=True
        )


async def test_normal_alerts_are_not_marked_resent(hass: HomeAssistant, ergon) -> None:  # noqa: F811
    morven = await _add(hass, "Morven", MORVEN)
    new = async_capture_events(hass, EVENT_NEW_OUTAGE)
    ergon.add([MORVEN], feature("26MOR", start=None, lon=MORVEN[1], lat=MORVEN[0]))
    await morven.runtime_data.async_refresh()
    await _settle(hass)
    assert [e.data["resent"] for e in new] == [False]
