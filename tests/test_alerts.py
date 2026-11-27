"""One alert per outage, even when it overlaps several monitored locations."""

from __future__ import annotations

from datetime import timedelta
from io import BytesIO
from typing import Any
from unittest.mock import patch

from PIL import Image
import pytest

from homeassistant.const import CONF_LATITUDE, CONF_LONGITUDE, CONF_NAME, CONF_RADIUS
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_capture_events

from custom_components.ergon_outages.const import (
    CONF_SCAN_INTERVAL,
    DOMAIN,
    EVENT_NEW_OUTAGE,
    EVENT_OUTAGE_CLEARED,
)

from .conftest import feature

MITCHELL = (-26.42, 147.11)
ROMA = (-25.80, 146.58)


class FakeErgon:
    """Serves outages to each location by distance, like the real query."""

    def __init__(self) -> None:
        self.outages: list[tuple[tuple[float, float], dict[str, Any]]] = []
        self.calls: list[float] = []

    def add(self, near: list[tuple[float, float]], feat: dict[str, Any]) -> None:
        self.outages.append((tuple(near), feat))

    async def query(self, session, lat, lon, radius_m):
        self.calls.append(lat)
        return [f for near, f in self.outages if (lat, lon) in near]


@pytest.fixture
def ergon():
    fake = FakeErgon()
    with (
        patch("custom_components.ergon_outages.coordinator.async_query_outages", fake.query),
        patch("custom_components.ergon_outages.renderer._async_fetch_tile", return_value=None),
    ):
        yield fake


async def _add(hass: HomeAssistant, name: str, where: tuple[float, float]) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=name,
        unique_id=f"{where[0]:.4f}_{where[1]:.4f}",
        data={CONF_NAME: name, CONF_LATITUDE: where[0], CONF_LONGITUDE: where[1],
              CONF_RADIUS: 30_000.0, CONF_SCAN_INTERVAL: 5},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def _settle(hass: HomeAssistant) -> None:
    for _ in range(4):
        await hass.async_block_till_done(wait_background_tasks=True)


async def test_overlapping_outage_sends_one_alert(hass: HomeAssistant, ergon, hass_client_no_auth) -> None:
    mitchell = await _add(hass, "Mitchell", MITCHELL)
    await _add(hass, "Roma", ROMA)
    new = async_capture_events(hass, EVENT_NEW_OUTAGE)

    # A big outage between the towns shows up for both locations, but only
    # Mitchell's scheduled poll runs.
    ergon.add([MITCHELL, ROMA], feature("26BIG", start=None, outage_type="UNPLANNED",
                                             lon=146.85, lat=-26.1))
    await mitchell.runtime_data.async_refresh()
    await _settle(hass)

    assert len(new) == 1
    data = new[0].data
    # Alphabetical, whichever location noticed first.
    assert data["location"] == "Mitchell + Roma"
    assert data["locations"] == ["Mitchell", "Roma"]
    assert len(data["config_entry_ids"]) == 2
    assert data["detail_entity_ids"] == [
        "image.ergon_outages_mitchell_outage_26big_map",
        "image.ergon_outages_roma_outage_26big_map",
    ]
    assert data["detail_entity_id"] == data["detail_entity_ids"][0]
    client = await hass_client_no_auth()
    resp = await client.get(data["map_url"])
    assert resp.status == 200

    # Both locations now know it: the next polls are silent.
    for entry in hass.config_entries.async_entries(DOMAIN):
        await entry.runtime_data.async_refresh()
    await _settle(hass)
    assert len(new) == 1


async def test_order_does_not_depend_on_who_noticed_first(hass: HomeAssistant, ergon) -> None:
    await _add(hass, "Mitchell", MITCHELL)
    roma = await _add(hass, "Roma", ROMA)
    new = async_capture_events(hass, EVENT_NEW_OUTAGE)
    ergon.add([MITCHELL, ROMA], feature("26BIG", start=None, lon=146.85, lat=-26.1))
    await roma.runtime_data.async_refresh()  # Roma notices first this time (listed second)
    await _settle(hass)
    assert new[0].data["location"] == "Mitchell + Roma"


async def test_separate_outages_stay_separate(hass: HomeAssistant, ergon) -> None:
    mitchell = await _add(hass, "Mitchell", MITCHELL)
    await _add(hass, "Roma", ROMA)
    new = async_capture_events(hass, EVENT_NEW_OUTAGE)
    ergon.add([MITCHELL], feature("26MIT", start=None, lon=MITCHELL[1], lat=MITCHELL[0]))
    ergon.add([ROMA], feature("26ROM", start=None, lon=ROMA[1], lat=ROMA[0]))
    await mitchell.runtime_data.async_refresh()
    await _settle(hass)
    assert sorted((e.data["event_id"], e.data["location"]) for e in new) == [
        ("26MIT", "Mitchell"),
        ("26ROM", "Roma"),
    ]


async def test_cleared_alert_shows_where_it_was(hass: HomeAssistant, ergon, hass_client_no_auth) -> None:
    ergon.add([MITCHELL], feature("26GONE", start=dt_util.utcnow() - timedelta(hours=1),
                                lon=MITCHELL[1], lat=MITCHELL[0]))
    mitchell = await _add(hass, "Mitchell", MITCHELL)  # baseline: no alert
    cleared = async_capture_events(hass, EVENT_OUTAGE_CLEARED)

    ergon.outages.clear()
    await mitchell.runtime_data.async_refresh()
    await _settle(hass)

    assert len(cleared) == 1
    data = cleared[0].data
    assert data["detail_entity_id"] is None and data["detail_entity_ids"] == []
    resp = await (await hass_client_no_auth()).get(data["map_url"])
    assert resp.status == 200
    image = Image.open(BytesIO(await resp.read())).convert("RGB")
    raw = image.tobytes()
    pixels = zip(raw[0::3], raw[1::3], raw[2::3])
    greens = sum(1 for r, g, b in pixels if g > r + 40 and g > b + 40)
    assert greens > 200  # the cleared area is drawn in green


async def test_cleared_while_restarting_still_has_a_map(hass: HomeAssistant, ergon) -> None:
    """The outage's shape is saved, so it can be drawn after a restart."""
    ergon.add([MITCHELL], feature("26GONE", start=None, lon=MITCHELL[1], lat=MITCHELL[0]))
    mitchell = await _add(hass, "Mitchell", MITCHELL)
    await hass.config_entries.async_unload(mitchell.entry_id)
    await hass.async_block_till_done()

    ergon.outages.clear()
    cleared = async_capture_events(hass, EVENT_OUTAGE_CLEARED)
    assert await hass.config_entries.async_setup(mitchell.entry_id)
    await _settle(hass)
    assert len(cleared) == 1 and cleared[0].data["map_url"]
