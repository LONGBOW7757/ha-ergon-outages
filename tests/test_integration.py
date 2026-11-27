"""End-to-end tests running the integration inside Home Assistant."""

from __future__ import annotations

from datetime import timedelta

from aiohttp import ClientError
import pytest

from homeassistant import config_entries
from homeassistant.const import (
    CONF_LATITUDE,
    CONF_LONGITUDE,
    CONF_NAME,
    CONF_RADIUS,
    STATE_OFF,
    STATE_ON,
    STATE_UNAVAILABLE,
)
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_capture_events,
)

from custom_components.ergon_outages.const import (
    CONF_LOCATION,
    CONF_SCAN_INTERVAL,
    DOMAIN,
    EVENT_NEW_OUTAGE,
    EVENT_OUTAGE_CLEARED,
    EVENT_OUTAGE_STARTED,
    EVENT_OUTAGE_UPDATED,
    QUERY_URL,
)

from .conftest import collection, feature

NOW_SENSOR = "binary_sensor.ergon_outages_home_outage_now"
UPCOMING_SENSOR = "binary_sensor.ergon_outages_home_upcoming_outage"


def _mock(aioclient_mock, payload=None, exc=None) -> None:
    aioclient_mock.clear_requests()
    if exc is not None:
        aioclient_mock.get(QUERY_URL, exc=exc)
    else:
        aioclient_mock.get(QUERY_URL, json=payload)


def _entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title="Home",
        unique_id="-26.4010_146.2450",
        data={
            CONF_NAME: "Home",
            CONF_LATITUDE: -26.401,
            CONF_LONGITUDE: 146.245,
            CONF_RADIUS: 250.0,
            CONF_SCAN_INTERVAL: 5,
        },
    )


async def _all_events(hass: HomeAssistant):
    return {
        kind: async_capture_events(hass, event_type)
        for kind, event_type in (
            ("new", EVENT_NEW_OUTAGE),
            ("started", EVENT_OUTAGE_STARTED),
            ("updated", EVENT_OUTAGE_UPDATED),
            ("cleared", EVENT_OUTAGE_CLEARED),
        )
    }


def _ids(events) -> list[str]:
    return sorted(e.data["event_id"] for e in events)


async def test_config_flow_creates_entry(hass: HomeAssistant, aioclient_mock) -> None:
    _mock(aioclient_mock, collection())
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_NAME: "Home",
            CONF_LOCATION: {CONF_LATITUDE: -26.401, CONF_LONGITUDE: 146.245, CONF_RADIUS: 300},
            CONF_SCAN_INTERVAL: 5,
        },
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Home"
    assert result["data"] == {
        CONF_NAME: "Home",
        CONF_LATITUDE: -26.401,
        CONF_LONGITUDE: 146.245,
        CONF_RADIUS: 300.0,
        CONF_SCAN_INTERVAL: 5,
        "show_feeders": True,
    }
    # The query sent to ArcGIS: lon,lat order and the radius buffer.
    _, url, _, _ = aioclient_mock.mock_calls[-1]
    assert url.query["geometry"] == "146.245,-26.401"
    assert url.query["distance"] == "300"
    assert url.query["f"] == "geojson"


async def test_config_flow_cannot_connect(hass: HomeAssistant, aioclient_mock) -> None:
    _mock(aioclient_mock, exc=ClientError())
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_NAME: "Home",
            CONF_LOCATION: {CONF_LATITUDE: -26.4, CONF_LONGITUDE: 146.2, CONF_RADIUS: 250},
            CONF_SCAN_INTERVAL: 5,
        },
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


async def test_arcgis_error_payload_is_a_failure(hass: HomeAssistant, aioclient_mock) -> None:
    _mock(aioclient_mock, {"error": {"code": 400, "message": "Invalid query"}})
    entry = _entry()
    entry.add_to_hass(hass)
    assert not await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is config_entries.ConfigEntryState.SETUP_RETRY


async def test_outage_lifecycle(hass: HomeAssistant, aioclient_mock, freezer) -> None:
    now = dt_util.utcnow()
    current = feature("26AA1", start=now - timedelta(hours=1), est_fix=now + timedelta(hours=3),
                      outage_type="UNPLANNED", status="Outage reported")
    soon = feature("26BB2", start=now + timedelta(minutes=7), est_fix=now + timedelta(hours=5))
    events = await _all_events(hass)

    # --- First ever run: entities appear, existing outages are NOT announced.
    _mock(aioclient_mock, collection(current, soon))
    entry = _entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert all(not ev for ev in events.values())
    assert hass.states.get(NOW_SENSOR).state == STATE_ON
    assert hass.states.get(UPCOMING_SENSOR).state == STATE_ON
    assert hass.states.get(NOW_SENSOR).attributes["event_ids"] == ["26AA1"]
    assert len(hass.states.get(NOW_SENSOR).attributes["geojson"]["features"]) == 1
    assert hass.states.get("sensor.ergon_outages_home_current_outages").state == "1"
    assert hass.states.get("sensor.ergon_outages_home_upcoming_outages").state == "1"
    assert hass.states.get("sensor.ergon_outages_home_next_outage_start").state != "unknown"
    outage = hass.states.get("sensor.ergon_outages_home_outage_26aa1")
    assert outage.state == "current"
    assert outage.attributes["status"] == "Outage reported"
    assert outage.attributes["reason"].startswith("Replace power poles")
    assert "latitude" in outage.attributes
    assert hass.states.get("sensor.ergon_outages_home_outage_26bb2").state == "future"

    coordinator = entry.runtime_data

    # --- Same data again: nothing announced (the every-5-minutes problem).
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert all(not ev for ev in events.values())

    # --- Time passes: planned work starts, the fault clears, a new fault
    #     appears, all within one poll.
    freezer.tick(timedelta(minutes=10))
    fresh = feature("26CC3", start=None, outage_type="UNPLANNED", status="Outage reported")
    _mock(aioclient_mock, collection(soon, fresh))
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    assert _ids(events["started"]) == ["26BB2"]
    assert _ids(events["new"]) == ["26CC3"]
    assert _ids(events["cleared"]) == ["26AA1"]
    assert events["new"][0].data["phase"] == "current"
    assert events["new"][0].data["location"] == "Home"
    assert hass.states.get("sensor.ergon_outages_home_outage_26aa1") is None
    assert hass.states.get("sensor.ergon_outages_home_outage_26bb2").state == "current"
    assert hass.states.get("sensor.ergon_outages_home_outage_26cc3").state == "current"
    assert hass.states.get(UPCOMING_SENSOR).state == STATE_OFF
    assert hass.states.get("sensor.ergon_outages_home_current_outages").state == "2"
    ent_reg = er.async_get(hass)
    assert ent_reg.async_get("sensor.ergon_outages_home_outage_26aa1") is None

    # --- A status change produces an "updated" event.
    fresh_on_route = feature("26CC3", start=None, outage_type="UNPLANNED", status="Crews on route")
    _mock(aioclient_mock, collection(soon, fresh_on_route))
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert _ids(events["updated"]) == ["26CC3"]

    # --- A failed poll followed by recovery must not re-announce anything.
    for ev in events.values():
        ev.clear()
    _mock(aioclient_mock, exc=ClientError())
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get(NOW_SENSOR).state == STATE_UNAVAILABLE
    _mock(aioclient_mock, collection(soon, fresh_on_route))
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get(NOW_SENSOR).state == STATE_ON
    assert all(not ev for ev in events.values())

    # --- Restart: nothing re-announced; outage that ended while "off" is
    #     announced as cleared and its entity is purged.
    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    _mock(aioclient_mock, collection(soon))
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert not events["new"] and not events["started"] and not events["updated"]
    assert _ids(events["cleared"]) == ["26CC3"]
    assert ent_reg.async_get("sensor.ergon_outages_home_outage_26cc3") is None
    assert hass.states.get("sensor.ergon_outages_home_outage_26bb2").state == "current"


async def test_options_flow_updates_radius(hass: HomeAssistant, aioclient_mock) -> None:
    _mock(aioclient_mock, collection())
    entry = _entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_LOCATION: {CONF_LATITUDE: -26.401, CONF_LONGITUDE: 146.245, CONF_RADIUS: 1000},
            CONF_SCAN_INTERVAL: 10,
        },
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    coordinator = entry.runtime_data  # new coordinator after reload
    assert coordinator.radius_m == 1000
    assert coordinator.update_interval == timedelta(minutes=10)


@pytest.mark.parametrize("radius", [0])
async def test_zero_radius_is_exact_point(hass: HomeAssistant, aioclient_mock, radius) -> None:
    _mock(aioclient_mock, collection())
    entry = _entry()
    entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(entry, options={**entry.data, CONF_RADIUS: radius})
    assert await hass.config_entries.async_setup(entry.entry_id)
    _, url, _, _ = aioclient_mock.mock_calls[-1]
    assert "distance" not in url.query


async def test_brand_icon_is_bundled(hass: HomeAssistant) -> None:
    """HA serves custom integration icons from the brand/ folder."""
    from homeassistant.loader import async_get_integration

    integration = await async_get_integration(hass, DOMAIN)
    assert integration.has_branding


async def test_outage_map_image(hass: HomeAssistant, aioclient_mock, hass_client, freezer) -> None:
    """The map image renders, and only changes when the outages change."""
    from unittest.mock import patch

    now = dt_util.utcnow()
    current = feature("26AA1", start=now - timedelta(hours=1))
    _mock(aioclient_mock, collection(current))
    entry = _entry()
    entry.add_to_hass(hass)
    with patch(
        "custom_components.ergon_outages.renderer._async_fetch_tile",
        return_value=None,
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        map_id = "image.ergon_outages_home_outage_map"
        state = hass.states.get(map_id)
        assert state is not None
        first_updated = state.state
        assert state.attributes["entity_picture"].startswith(f"/api/image_proxy/{map_id}?token=")

        client = await hass_client()
        resp = await client.get(f"/api/image_proxy/{map_id}")
        assert resp.status == 200
        assert resp.content_type == "image/png"
        assert (await resp.read())[:8] == b"\x89PNG\r\n\x1a\n"

        # Same outages: picture unchanged.
        coordinator = entry.runtime_data
        freezer.tick(timedelta(minutes=5))
        await coordinator.async_refresh()
        await hass.async_block_till_done()
        assert hass.states.get(map_id).state == first_updated

        # New outage: picture changes and the event points at the map.
        events = async_capture_events(hass, EVENT_NEW_OUTAGE)
        _mock(aioclient_mock, collection(current, feature("26BB2", start=now + timedelta(days=1))))
        await coordinator.async_refresh()
        await hass.async_block_till_done()
        assert hass.states.get(map_id).state != first_updated
        assert events[0].data["map_entity_id"] == map_id


async def test_notification_gets_map_on_startup(
    hass: HomeAssistant, aioclient_mock, hass_storage, hass_client_no_auth
) -> None:
    """Regression: an outage found on the first poll after a start (e.g. the
    first start after upgrading to the version with the map) was announced
    before the map entity existed, so the notification had no image."""
    from pathlib import Path
    from unittest.mock import patch

    import yaml

    from homeassistant.components import automation
    from homeassistant.setup import async_setup_component
    from pytest_homeassistant_custom_component.common import async_mock_service

    calls = async_mock_service(hass, "notify", "notify")
    example = Path(__file__).parent.parent / "examples" / "notifications.yaml"
    assert await async_setup_component(
        hass, automation.DOMAIN, {automation.DOMAIN: [yaml.safe_load(example.read_text())]}
    )
    await hass.async_block_till_done()

    entry = _entry()
    entry.add_to_hass(hass)
    # Previous run already recorded a snapshot (no outages), as after an upgrade.
    hass_storage[f"{DOMAIN}.{entry.entry_id}"] = {
        "version": 1, "minor_version": 1, "key": f"{DOMAIN}.{entry.entry_id}",
        "data": {"outages": {}},
    }
    now = dt_util.utcnow()
    _mock(aioclient_mock, collection(feature("26PL9", start=now + timedelta(days=5), customers=2)))
    with patch(
        "custom_components.ergon_outages.renderer._async_fetch_tile",
        return_value=None,
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        assert len(calls) == 1
        assert calls[0].data["title"] == "📅 Planned outage · Home"
        image = calls[0].data["data"]["image"]
        assert image.startswith("/api/ergon_outages/map/") and image.endswith(".png")

        # The phone downloads it without logging in...
        client = await hass_client_no_auth()
        resp = await client.get(image)
        assert resp.status == 200
        assert resp.headers["Content-Type"] == "image/png"
        first = await resp.read()
        assert first[:4] == b"\x89PNG"

        # ...and gets the same snapshot when it re-downloads much later, even
        # after the image entity's own token has rotated and outages changed.
        _mock(aioclient_mock, collection())
        await entry.runtime_data.async_refresh()
        await hass.async_block_till_done()
        resp = await client.get(image)
        assert resp.status == 200 and await resp.read() == first

        # Unknown tokens are rejected.
        resp = await client.get("/api/ergon_outages/map/AAAAAAAAAAAAAAAAAAAAAAAA.png")
        assert resp.status == 404
