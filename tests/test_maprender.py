"""Tests for the static map renderer."""

from __future__ import annotations

from io import BytesIO

from PIL import Image

from custom_components.ergon_outages.maprender import (
    COLOR_CURRENT,
    MAP_HEIGHT,
    MAP_WIDTH,
    compose_map,
    plan_map,
)

HOME = (-26.401, 146.245)


def Z(radius):  # a monitored zone at HOME
    return [(HOME[0], HOME[1], radius)]


def _square(lon: float, lat: float, d: float) -> dict:
    return {
        "type": "Polygon",
        "coordinates": [[[lon - d, lat - d], [lon + d, lat - d], [lon + d, lat + d], [lon - d, lat + d], [lon - d, lat - d]]],
    }


def _inside(plan, lon, lat) -> bool:
    x, y = plan.to_image(lon, lat)
    return 0 <= x <= MAP_WIDTH and 0 <= y <= MAP_HEIGHT


def test_no_outages_shows_search_circle() -> None:
    plan = plan_map(Z(2000))
    assert _inside(plan, HOME[1], HOME[0])
    assert _inside(plan, HOME[1] + 0.019, HOME[0])  # ~1.9 km east, inside circle
    assert 1 <= len(plan.tiles()) <= 12
    assert plan.tile_url(plan.tiles()[0]).startswith(f"https://tile.openstreetmap.org/{plan.zoom}/")


def test_outages_are_framed_not_the_radius() -> None:
    a = _square(146.30, -26.40, 0.002)    # ~5.5 km east
    b = _square(146.31, -26.405, 0.001)
    radius_only = plan_map(Z(10_000))
    framed = plan_map(Z(10_000), [a, b])
    assert framed.zoom > radius_only.zoom + 2
    for g in (a, b):
        assert all(_inside(framed, lon, lat) for lon, lat in g["coordinates"][0])


def test_tiny_outage_zoom_is_capped() -> None:
    assert plan_map(Z(250), [_square(146.245, -26.401, 0.0001)]).zoom == 17


def test_compose_without_tiles_still_draws_outage() -> None:
    geom = _square(HOME[1], HOME[0], 0.001)
    plan = plan_map(Z(250), [geom])
    png = compose_map(plan, {t: None for t in plan.tiles()}, Z(250), [(COLOR_CURRENT, [geom])])
    image = Image.open(BytesIO(png)).convert("RGB")
    assert image.size == (MAP_WIDTH, MAP_HEIGHT)
    # Somewhere inside the polygon (but off the home marker) is tinted red.
    x, y = plan.to_image(HOME[1] + 0.0007, HOME[0] + 0.0007)
    r, g, b = image.getpixel((round(x), round(y)))
    assert r > g + 40 and r > b + 40


def test_points_keep_several_locations_in_view() -> None:
    outage = _square(146.27, -26.41, 0.001)
    other = (-26.48, 146.33)  # a second monitored town ~11 km away
    plan = plan_map(Z(5000) + [(other[0], other[1], 5000)], [outage], [HOME, other])
    assert _inside(plan, HOME[1], HOME[0]) and _inside(plan, other[1], other[0])
    assert all(_inside(plan, lon, lat) for lon, lat in outage["coordinates"][0])


def test_details_panel_wraps_and_truncates() -> None:
    from custom_components.ergon_outages.maprender import compose_details

    plan = plan_map(Z(250))
    base = compose_map(plan, {t: None for t in plan.tiles()}, Z(250), [])
    short = compose_details(base, "Outage X", "Planned", [("Reason", "Short")])
    long = compose_details(base, "Outage X", "Planned", [("Streets", "Long Street Name, " * 60)], max_lines=4)
    h_base = Image.open(BytesIO(base)).height
    h_short = Image.open(BytesIO(short)).height
    h_long = Image.open(BytesIO(long)).height
    assert h_base == MAP_HEIGHT < h_short < h_long
    # 60 street names are capped at 4 lines, so the panel stays a sane size.
    assert h_long - h_short <= 4 * 22



def test_doubled_plan_is_the_same_view_at_twice_the_resolution() -> None:
    plan = plan_map(Z(2000))
    hi = plan.doubled()
    assert (hi.width, hi.height, hi.zoom) == (2 * plan.width, 2 * plan.height, plan.zoom + 1)
    x1, y1 = plan.to_image(HOME[1] + 0.01, HOME[0] - 0.01)
    x2, y2 = hi.to_image(HOME[1] + 0.01, HOME[0] - 0.01)
    assert abs(x2 - 2 * x1) < 1e-6 and abs(y2 - 2 * y1) < 1e-6
    assert all(abs(a - b) < 1e-9 for a, b in zip(plan.bounds(), hi.bounds()))


def test_scaled_output_and_details_panel() -> None:
    from custom_components.ergon_outages.maprender import compose_details

    hi = plan_map(Z(250)).doubled()
    png = compose_map(hi, {t: None for t in hi.tiles()}, Z(250), [], scale=2)
    assert Image.open(BytesIO(png)).size == (2 * MAP_WIDTH, 2 * MAP_HEIGHT)
    one = compose_details(compose_map(plan_map(Z(250)), {}, Z(250), []), "T", "S", [("Reason", "x")])
    two = compose_details(png, "T", "S", [("Reason", "x")])
    h1 = Image.open(BytesIO(one)).height - MAP_HEIGHT
    h2 = Image.open(BytesIO(two)).height - 2 * MAP_HEIGHT
    assert h2 == 2 * h1  # the panel scales with the map
