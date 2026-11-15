"""Make sure the example automation in examples/ actually works."""

from __future__ import annotations

from pathlib import Path

import yaml

from homeassistant.components import automation
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import async_mock_service

EXAMPLE = Path(__file__).parent.parent / "examples" / "notifications.yaml"

BASE = {
    "config_entry_id": "abc",
    "location": "Home",
    "event_id": "26AA1",
    "outage_type": "unplanned",
    "status": "Outage reported",
    "reason": "Due to damage requiring emergency repairs.",
    "customers_affected": 150,
    "suburbs": "Cairns",
    "start": "2026-11-15T04:00:00+00:00",
    "estimated_restoration": None,
}


async def test_notification_example(hass: HomeAssistant) -> None:
    await hass.config.async_set_time_zone("Australia/Brisbane")
    calls = async_mock_service(hass, "notify", "notify")
    config = yaml.safe_load(EXAMPLE.read_text())
    assert await async_setup_component(hass, automation.DOMAIN, {automation.DOMAIN: [config]})
    await hass.async_block_till_done()

    hass.bus.async_fire("ergon_outages_new_outage", {**BASE, "phase": "current", "map_url": "/api/ergon_outages/map/abc.png"})
    hass.bus.async_fire("ergon_outages_new_outage", {**BASE, "event_id": "26BB2", "phase": "future", "outage_type": "planned"})
    hass.bus.async_fire("ergon_outages_outage_cleared", {**BASE, "phase": "current"})
    await hass.async_block_till_done()

    assert [c.data["title"] for c in calls] == [
        "⚡ Power outage now",
        "📅 Planned outage scheduled",
        "✅ Power outage cleared",
    ]
    assert calls[0].data["message"].splitlines() == [
        "Due to damage requiring emergency repairs.",
        "Unplanned · Outage reported",
        "Start: Sun 15 Nov, 2:00 PM · Restore: not yet known",
        "Customers affected: 150",
    ]
    assert calls[0].data["data"] == {"image": "/api/ergon_outages/map/abc.png"}
    assert calls[1].data["data"] == {}  # no map link in the event
    assert calls[2].data["data"] == {}  # cleared: no map
    assert calls[2].data["message"] == "Outage 26AA1 near Home is no longer listed by Ergon."
