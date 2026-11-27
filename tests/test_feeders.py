"""MV feeder lines on the maps."""

from __future__ import annotations

from datetime import timedelta
from io import BytesIO
from unittest.mock import AsyncMock, patch

from aiohttp import ClientError
from PIL import Image

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from custom_components.ergon_outages.feeders import FEEDER_QUERY_URL, Feeder, async_get_feeders
from custom_components.ergon_outages.maprender import (
    COLOR_CLEARED,
    COLOR_CURRENT,
    COLOR_FUTURE,
    COLOR_HOME,
    FEEDER_PALETTE,
    assign_feeder_colors,
    compose_map,
    feeder_color,
    line_touches_polygons,
    plan_map,
)

from .conftest import collection, feature
from .test_integration import _entry, _mock

HOME = (-26.401, 146.245)
SQUARE = {"type": "Polygon", "coordinates": [[[146.24, -26.405], [146.25, -26.405], [146.25, -26.395], [146.24, -26.395], [146.24, -26.405]]]}
THROUGH = {"type": "MultiLineString", "coordinates": [[[146.23, -26.40], [146.26, -26.40]]]}
INSIDE = {"type": "LineString", "coordinates": [[146.244, -26.401], [146.246, -26.399]]}
AWAY = {"type": "MultiLineString", "coordinates": [[[146.30, -26.30], [146.31, -26.31]]]}


def test_feeder_colours_are_stable_and_distinct_from_outages() -> None:
    assert feeder_color("F233C") == feeder_color("F233C")
    assert {feeder_color(f"F{i}C") for i in range(200)} <= set(FEEDER_PALETTE)
    assert not {COLOR_CURRENT, COLOR_FUTURE, COLOR_CLEARED, COLOR_HOME} & set(FEEDER_PALETTE)


def _parallel(n: int, gap: float) -> dict[str, dict]:
    """n horizontal feeders `gap` degrees apart."""
    return {
        f"F{i}C": {"type": "LineString", "coordinates": [[146.20, -26.40 + i * gap], [146.29, -26.40 + i * gap]]}
        for i in range(n)
    }


def test_nearby_feeders_get_contrasting_colours() -> None:
    from custom_components.ergon_outages.maprender import _color_distance

    plan = plan_map([(HOME[0], HOME[1], 5000)])
    close = _parallel(4, 0.0015)  # a few pixels apart at this zoom
    colors = assign_feeder_colors(close, plan)
    assert len(set(colors.values())) == 4
    worst = min(_color_distance(colors[a], colors[b]) for a in close for b in close if a < b)
    assert worst > 150  # clearly different, not just different shades
    assert assign_feeder_colors(dict(reversed(list(close.items()))), plan) == colors


def test_far_apart_feeders_keep_their_usual_colour() -> None:
    plan = plan_map([(HOME[0], HOME[1], 5000)])
    far = _parallel(3, 0.03)  # well apart on the map
    colors = assign_feeder_colors(far, plan)
    assert all(colors[name] == feeder_color(name) for name in far)


def test_palette_avoids_reds_and_pinks() -> None:
    for r, g, b in FEEDER_PALETTE:
        assert not (r > 150 and g < 120 and b < 150)  # red/pink reads as an outage


def test_line_polygon_intersection() -> None:
    assert line_touches_polygons(THROUGH, [SQUARE])  # crosses, no vertex inside
    assert line_touches_polygons(INSIDE, [SQUARE])   # fully inside
    assert not line_touches_polygons(AWAY, [SQUARE])


def test_bounds_cover_the_frame() -> None:
    plan = plan_map([(HOME[0], HOME[1], 250)], [SQUARE])
    west, south, east, north = plan.bounds()
    assert west < 146.24 and east > 146.25 and south < -26.405 and north > -26.395


def test_lines_are_drawn_thin_and_strong() -> None:
    plan = plan_map([(HOME[0], HOME[1], 250)], [SQUARE])
    tiles = {t: None for t in plan.tiles()}
    colour = feeder_color("F233C")
    png = compose_map(plan, tiles, [(HOME[0], HOME[1], 0)], [], [(colour, THROUGH)])  # opaque
    image = Image.open(BytesIO(png)).convert("RGB")
    x, y = plan.to_image(146.235, -26.40)

    def distance(px: tuple[int, int, int]) -> int:
        return sum(abs(a - b) for a, b in zip(px, colour))

    column = [image.getpixel((round(x), round(y) + dy)) for dy in range(-6, 7)]
    assert min(distance(p) for p in column) < 30  # opaque: the exact colour shows
    # Thin: only a few pixels in the column are close to the colour.
    assert sum(1 for p in column if distance(p) < 150) <= 4


async def test_fetch_parses_caches_and_fails_quietly(hass: HomeAssistant, aioclient_mock) -> None:
    aioclient_mock.get(FEEDER_QUERY_URL, json={"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": THROUGH, "properties": {"Feeder_Name": "F233C", "Feeder_Voltage": "22kV"}},
        {"type": "Feature", "geometry": None, "properties": {"Feeder_Name": "BROKEN"}},
    ]})
    bounds = (146.2, -26.45, 146.3, -26.35)
    feeders = await async_get_feeders(hass, bounds, 0.0001)
    assert [(f.name, f.voltage, f.label) for f in feeders] == [("F233C", "22kV", "F233C · 22kV")]
    _, url, _, _ = aioclient_mock.mock_calls[-1]
    assert url.query["geometryType"] == "esriGeometryEnvelope"
    assert url.query["maxAllowableOffset"] == "0.0001000"
    assert await async_get_feeders(hass, bounds, 0.0001) == feeders  # cached
    assert aioclient_mock.call_count == 1

    aioclient_mock.clear_requests()
    aioclient_mock.get(FEEDER_QUERY_URL, exc=ClientError())
    assert await async_get_feeders(hass, (140.0, -20.0, 141.0, -19.0), 0.0001) == []


async def test_details_panel_lists_feeders_through_the_outage(
    hass: HomeAssistant, aioclient_mock, hass_client
) -> None:
    feeders = [Feeder("F233C", "22kV", THROUGH), Feeder("F999C", "11kV", AWAY)]
    now = dt_util.utcnow()
    poly = feature("26AA1", start=now - timedelta(hours=1), lon=146.245, lat=-26.40)
    client = None

    async def picture_height() -> int:
        resp = await client.get("/api/image_proxy/image.ergon_outages_home_outage_26aa1_map")
        assert resp.status == 200
        return Image.open(BytesIO(await resp.read())).height

    fetch = AsyncMock(return_value=feeders)
    with (
        patch("custom_components.ergon_outages.renderer.async_get_feeders", fetch),
        patch("custom_components.ergon_outages.renderer._async_fetch_tile", return_value=None),
    ):
        _mock(aioclient_mock, collection(poly))
        entry = _entry()
        entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        client = await hass_client()
        with_lines = await picture_height()
        assert fetch.await_count >= 1
        renderer = entry.runtime_data.map_renderer
        legend_feeders = [
            f.name for f, _ in renderer._feeders[renderer.spec("26AA1").key]
            if line_touches_polygons(f.geometry, entry.runtime_data.data["26AA1"].geometries)
        ]
        assert legend_feeders == ["F233C"]  # the far-away feeder isn't listed

        # Turning the option off: no feeder downloads, no legend row.
        fetch.reset_mock()
        hass.config_entries.async_update_entry(entry, options={**entry.data, "show_feeders": False})
        await hass.async_block_till_done()
        without_lines = await picture_height()
        fetch.assert_not_awaited()
    assert with_lines > without_lines
