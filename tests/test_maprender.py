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


def _square(lon: float, lat: float, d: float) -> dict:
    return {
        "type": "Polygon",
        "coordinates": [[[lon - d, lat - d], [lon + d, lat - d], [lon + d, lat + d], [lon - d, lat + d], [lon - d, lat - d]]],
    }


def test_plan_zooms_to_fit_everything() -> None:
    near = plan_map(HOME, 250, [])
    far = plan_map(HOME, 250, [_square(146.30, -26.40, 0.002)])  # ~5.5 km east
    assert near.zoom > far.zoom
    for plan, geoms in ((near, []), (far, [_square(146.30, -26.40, 0.002)])):
        hx, hy = plan.to_image(HOME[1], HOME[0])
        assert 0 < hx < MAP_WIDTH and 0 < hy < MAP_HEIGHT
        for g in geoms:
            for lon, lat in g["coordinates"][0]:
                x, y = plan.to_image(lon, lat)
                assert 0 <= x <= MAP_WIDTH and 0 <= y <= MAP_HEIGHT
    assert 1 <= len(near.tiles()) <= 12
    assert near.tile_url(near.tiles()[0]).startswith(f"https://tile.openstreetmap.org/{near.zoom}/")


def test_compose_without_tiles_still_draws_outage() -> None:
    geom = _square(HOME[1], HOME[0], 0.001)
    plan = plan_map(HOME, 250, [geom])
    png = compose_map(plan, {t: None for t in plan.tiles()}, HOME, 250, [(COLOR_CURRENT, [geom])])
    image = Image.open(BytesIO(png)).convert("RGB")
    assert image.size == (MAP_WIDTH, MAP_HEIGHT)
    # Somewhere inside the polygon (but off the home marker) is tinted red.
    x, y = plan.to_image(HOME[1] + 0.0007, HOME[0] + 0.0007)
    r, g, b = image.getpixel((round(x), round(y)))
    assert r > g + 40 and r > b + 40
