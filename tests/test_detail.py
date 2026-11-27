"""Per-outage map entities and the notification tap target."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from homeassistant.components import automation
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    async_capture_events,
    async_mock_service,
)

from custom_components.ergon_outages.const import EVENT_NEW_OUTAGE

from .conftest import collection, feature
from .test_integration import _entry, _mock

EXAMPLE = Path(__file__).parent.parent / "examples" / "notifications.yaml"
NO_TILES = patch(
    "custom_components.ergon_outages.renderer._async_fetch_tile",
    return_value=None,
)


async def _settle(hass: HomeAssistant) -> None:
    """Let chained call_soon hand-offs (coordinator → event trigger → run) finish."""
    for _ in range(3):
        await hass.async_block_till_done(wait_background_tasks=True)


@pytest.fixture(autouse=True)
def _no_tiles():
    with NO_TILES:
        yield


async def test_detail_map_entity_has_everything(hass: HomeAssistant, aioclient_mock) -> None:
    now = dt_util.utcnow()
    _mock(aioclient_mock, collection(
        feature("26AA1", start=now - timedelta(hours=1), est_fix=now + timedelta(hours=3),
                outage_type="UNPLANNED", status="Crews on route", customers=42),
    ))
    entry = _entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    state = hass.states.get("image.ergon_outages_home_outage_26aa1_map")
    assert state is not None
    attrs = state.attributes
    assert attrs["location"] == "Home"
    assert attrs["event_id"] == "26AA1"
    assert attrs["outage_type"] == "unplanned"
    assert attrs["status"] == "Crews on route"
    assert attrs["reason"] == "Replace power poles and improve the electricity network"
    assert attrs["customers_affected"] == 42
    assert attrs["suburbs"] == "Charleville"
    assert attrs["streets"] == "Alfred St, Wills St"
    assert attrs["start"] is not None and attrs["estimated_restoration"] is not None
    assert attrs["entity_picture"].startswith("/api/image_proxy/image.ergon_outages_home_outage_26aa1_map")

    # Cleared outage: its map entity goes away too.
    _mock(aioclient_mock, collection())
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get("image.ergon_outages_home_outage_26aa1_map") is None
    assert er.async_get(hass).async_get("image.ergon_outages_home_outage_26aa1_map") is None
    assert hass.states.get("image.ergon_outages_home_outage_map") is not None  # overview stays


async def test_alert_taps_through_to_outage(hass: HomeAssistant, aioclient_mock) -> None:
    await hass.config.async_set_time_zone("Australia/Brisbane")
    calls = async_mock_service(hass, "notify", "notify")
    assert await async_setup_component(
        hass, automation.DOMAIN, {automation.DOMAIN: [yaml.safe_load(EXAMPLE.read_text())]}
    )
    _mock(aioclient_mock, collection())
    entry = _entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    events = async_capture_events(hass, EVENT_NEW_OUTAGE)

    # A new outage appears during normal running.
    now = dt_util.utcnow()
    _mock(aioclient_mock, collection(feature("26NEW", start=now + timedelta(days=2))))
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()

    await _settle(hass)
    detail = "image.ergon_outages_home_outage_26new_map"
    assert events[0].data["detail_entity_id"] == detail
    assert hass.states.get(detail) is not None  # the link target really exists

    data = calls[0].data["data"]
    assert data["url"] == data["clickAction"] == f"/?more-info-entity-id={detail}"
    assert data["image"].startswith("/api/ergon_outages/map/")


async def test_cleared_alert_has_map_but_no_tap_target(hass: HomeAssistant, aioclient_mock) -> None:
    calls = async_mock_service(hass, "notify", "notify")
    assert await async_setup_component(
        hass, automation.DOMAIN, {automation.DOMAIN: [yaml.safe_load(EXAMPLE.read_text())]}
    )
    now = dt_util.utcnow()
    _mock(aioclient_mock, collection(feature("26OLD", start=now - timedelta(hours=1))))
    entry = _entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    _mock(aioclient_mock, collection())
    await entry.runtime_data.async_refresh()
    await _settle(hass)
    assert calls[-1].data["title"].startswith("✅")
    # The outage's entity is gone, so there's nothing to open, but the alert
    # still shows where it was (drawn in green).
    assert set(calls[-1].data["data"]) == {"image"}


async def test_focused_map_differs_from_overview(hass: HomeAssistant, aioclient_mock) -> None:
    now = dt_util.utcnow()
    _mock(aioclient_mock, collection(
        feature("26NEAR", start=now, lon=146.245, lat=-26.401),
        feature("26FAR", start=now, lon=146.30, lat=-26.43),  # ~6 km away
    ))
    entry = _entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    renderer = entry.runtime_data.map_renderer
    overview = await renderer.async_render()
    near = await renderer.async_render("26NEAR")
    far = await renderer.async_render("26FAR")
    assert len({overview, near, far}) == 3
    assert await renderer.async_render("26NEAR") is near  # cached
