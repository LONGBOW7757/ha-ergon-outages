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
