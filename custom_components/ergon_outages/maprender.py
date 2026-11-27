"""Render a small static map of outage polygons over OpenStreetMap tiles.

Pure Pillow + maths (no Home Assistant imports) so it can be tested directly.
Fetching tiles is left to the caller: plan_map() says which tiles are needed,
compose_map() draws the PNG from whatever tile bytes were obtained.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from io import BytesIO
import math
import zlib
from typing import Any

from PIL import Image, ImageDraw, ImageFont

TILE_SIZE = 256
TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
ATTRIBUTION = "© OpenStreetMap contributors"

MAP_WIDTH = 640
MAP_HEIGHT = 400
_PADDING = 48
_MIN_ZOOM = 5
_MAX_ZOOM = 17  # keeps a few streets of context around very small outages
_SCALE = 2  # overlay supersampling for smooth edges

RGB = tuple[int, int, int]
COLOR_CURRENT: RGB = (229, 57, 53)
COLOR_FUTURE: RGB = (251, 140, 0)
COLOR_CLEARED: RGB = (67, 160, 71)
COLOR_HOME: RGB = (25, 118, 210)

# A monitored location: (latitude, longitude, radius in metres).
Zone = tuple[float, float, float]
_BACKGROUND: RGB = (229, 227, 223)


def lonlat_to_world_px(lon: float, lat: float, zoom: int) -> tuple[float, float]:
    """Web Mercator: degrees -> global pixel coordinates at a zoom level."""
    lat = max(min(lat, 85.05112878), -85.05112878)
    size = TILE_SIZE * (1 << zoom)
    x = (lon + 180.0) / 360.0 * size
    sin_lat = math.sin(math.radians(lat))
    y = (0.5 - math.log((1 + sin_lat) / (1 - sin_lat)) / (4 * math.pi)) * size
    return x, y


def world_px_to_lonlat(x: float, y: float, zoom: int) -> tuple[float, float]:
    """Inverse of lonlat_to_world_px."""
    size = TILE_SIZE * (1 << zoom)
    lon = x / size * 360.0 - 180.0
    lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / size))))
    return lon, lat


def metres_per_pixel(lat: float, zoom: int) -> float:
    return 156543.03392 * math.cos(math.radians(lat)) / (1 << zoom)


@dataclass(frozen=True, slots=True)
class MapPlan:
    """Viewport: zoom level and the world-pixel position of the top-left corner."""

    zoom: int
    left: float
    top: float
    width: int = MAP_WIDTH
    height: int = MAP_HEIGHT

    def to_image(self, lon: float, lat: float) -> tuple[float, float]:
        x, y = lonlat_to_world_px(lon, lat, self.zoom)
        return x - self.left, y - self.top

    def bounds(self) -> tuple[float, float, float, float]:
        """(west, south, east, north) of the viewport in degrees."""
        west, north = world_px_to_lonlat(self.left, self.top, self.zoom)
        east, south = world_px_to_lonlat(self.left + self.width, self.top + self.height, self.zoom)
        return west, south, east, north

    @property
    def degrees_per_pixel(self) -> float:
        return 360.0 / (TILE_SIZE * (1 << self.zoom))

    def tiles(self) -> list[tuple[int, int]]:
        """Tiles (x, y) covering the viewport. x is unwrapped; use tile_url()."""
        limit = 1 << self.zoom
        x0 = math.floor(self.left / TILE_SIZE)
        x1 = math.floor((self.left + self.width - 1) / TILE_SIZE)
        y0 = max(math.floor(self.top / TILE_SIZE), 0)
        y1 = min(math.floor((self.top + self.height - 1) / TILE_SIZE), limit - 1)
        return [(x, y) for y in range(y0, y1 + 1) for x in range(x0, x1 + 1)]

    def tile_url(self, tile: tuple[int, int]) -> str:
        x, y = tile
        return TILE_URL.format(z=self.zoom, x=x % (1 << self.zoom), y=y)


def iter_rings(geometries: Iterable[Mapping[str, Any]]) -> Iterable[Sequence[Sequence[float]]]:
    """Outer rings of every Polygon / MultiPolygon."""
    for geom in geometries:
        coords = geom.get("coordinates") or []
        if geom.get("type") == "Polygon":
            polygons = [coords]
        elif geom.get("type") == "MultiPolygon":
            polygons = coords
        else:
            continue
        for polygon in polygons:
            if polygon and len(polygon[0]) >= 3:
                yield polygon[0]


# Feeder lines: one colour per feeder, stable across maps. Deliberately avoids
# the outage colours (red/orange/green) and the location blue.
FEEDER_PALETTE: tuple[RGB, ...] = (
    (20, 20, 20),     # near-black
    (156, 39, 176),   # purple
    (0, 150, 136),    # teal
    (0, 188, 212),    # cyan
    (40, 53, 147),    # indigo
    (158, 157, 36),   # olive
    (141, 110, 99),   # taupe
)


def _color_distance(a: RGB, b: RGB) -> float:
    """Rough perceptual distance ("redmean" weighted RGB)."""
    rmean = (a[0] + b[0]) / 2
    dr, dg, db = a[0] - b[0], a[1] - b[1], a[2] - b[2]
    return math.sqrt((2 + rmean / 256) * dr * dr + 4 * dg * dg + (2 + (255 - rmean) / 256) * db * db)


def feeder_color(name: str) -> RGB:
    """A feeder's usual colour, used when nothing nearby competes for it."""
    return FEEDER_PALETTE[zlib.crc32(name.encode()) % len(FEEDER_PALETTE)]


def _nearby_feeders(
    lines: Mapping[str, Mapping[str, Any]], plan: MapPlan, near_px: float
) -> dict[str, set[str]]:
    """Which feeders come within ~near_px of each other on this map."""
    cell_names: dict[tuple[int, int], set[str]] = {}
    for name, geometry in lines.items():
        for path in iter_lines(geometry):
            pts = [plan.to_image(pt[0], pt[1]) for pt in path]
            for (x0, y0), (x1, y1) in zip(pts, pts[1:] or pts):
                steps = max(1, int(math.hypot(x1 - x0, y1 - y0) / (near_px / 2)))
                for i in range(steps + 1):
                    x = x0 + (x1 - x0) * i / steps
                    y = y0 + (y1 - y0) * i / steps
                    if -near_px <= x <= plan.width + near_px and -near_px <= y <= plan.height + near_px:
                        cell_names.setdefault((int(x // near_px), int(y // near_px)), set()).add(name)
    neighbours: dict[str, set[str]] = {name: set() for name in lines}
    for (cx, cy), names in cell_names.items():
        around = set().union(*(cell_names.get((cx + dx, cy + dy), set()) for dx in (-1, 0, 1) for dy in (-1, 0, 1)))
        for name in names:
            neighbours[name] |= around - {name}
    return neighbours


def assign_feeder_colors(
    lines: Mapping[str, Mapping[str, Any]], plan: MapPlan | None = None, near_px: float = 28
) -> dict[str, RGB]:
    """Colour each feeder so that ones running near each other contrast.

    Busiest feeders are coloured first; each takes the palette colour most
    different from its already-coloured neighbours, preferring its usual colour
    when there's a tie. Without a plan, feeders just avoid sharing colours.
    """
    names = sorted(lines)
    if plan is None:
        neighbours = {n: set(names) - {n} for n in names}
    else:
        neighbours = _nearby_feeders(lines, plan, near_px)
    colors: dict[str, RGB] = {}
    for name in sorted(names, key=lambda n: (-len(neighbours[n]), n)):
        preferred = feeder_color(name)
        taken = [colors[n] for n in neighbours[name] if n in colors]

        def score(color: RGB) -> tuple[float, bool]:
            nearest = min((_color_distance(color, t) for t in taken), default=math.inf)
            return (min(nearest, 400.0), color == preferred)  # past 400 counts as "different enough"

        colors[name] = max(FEEDER_PALETTE, key=score)
    return colors


def iter_lines(geometry: Mapping[str, Any]) -> Iterable[Sequence[Sequence[float]]]:
    """Paths of a LineString / MultiLineString."""
    coords = geometry.get("coordinates") or []
    if geometry.get("type") == "LineString":
        yield coords
    elif geometry.get("type") == "MultiLineString":
        yield from coords


def _point_in_ring(x: float, y: float, ring: Sequence[Sequence[float]]) -> bool:
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-12) + xi:
            inside = not inside
        j = i
    return inside


def _segments_cross(a, b, c, d) -> bool:
    def orient(p, q, r):
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])
    return (orient(a, b, c) > 0) != (orient(a, b, d) > 0) and (orient(c, d, a) > 0) != (orient(c, d, b) > 0)


def line_touches_polygons(line: Mapping[str, Any], polygons: Iterable[Mapping[str, Any]]) -> bool:
    """True if a (multi)line enters or crosses any of the polygons."""
    rings = list(iter_rings(polygons))
    for path in iter_lines(line):
        for ring in rings:
            if any(_point_in_ring(pt[0], pt[1], ring) for pt in path):
                return True
            edges = list(zip(ring, ring[1:]))
            for a, b in zip(path, path[1:]):
                if any(_segments_cross(a, b, c, d) for c, d in edges):
                    return True
    return False


def _circle_corners(zone: Zone) -> list[tuple[float, float]]:
    lat, lon, radius = zone
    r = max(radius, 100.0)
    dlat = r / 111_320
    dlon = r / (111_320 * max(math.cos(math.radians(lat)), 0.01))
    return [(lon - dlon, lat - dlat), (lon + dlon, lat + dlat)]


def plan_map(
    zones: Sequence[Zone],
    geometries: Iterable[Mapping[str, Any]] = (),
    points: Iterable[tuple[float, float]] = (),
    width: int = MAP_WIDTH,
    height: int = MAP_HEIGHT,
) -> MapPlan:
    """Pick the closest zoom that frames what matters.

    With outage polygons, the view fits them (plus any extra `points`, given as
    (lat, lon), e.g. the monitored locations when an alert covers several).
    Without polygons it shows the monitored circles.
    """
    frame = [(pt[0], pt[1]) for ring in iter_rings(geometries) for pt in ring]
    if frame:
        frame.extend((lon, lat) for lat, lon in points)
    else:
        frame = [corner for zone in zones for corner in _circle_corners(zone)]

    zoom = _MIN_ZOOM
    for z in range(_MAX_ZOOM, _MIN_ZOOM - 1, -1):
        xs, ys = zip(*(lonlat_to_world_px(x, y, z) for x, y in frame))
        if max(xs) - min(xs) <= width - 2 * _PADDING and max(ys) - min(ys) <= height - 2 * _PADDING:
            zoom = z
            break

    xs, ys = zip(*(lonlat_to_world_px(x, y, zoom) for x, y in frame))
    cx, cy = (max(xs) + min(xs)) / 2, (max(ys) + min(ys)) / 2
    return MapPlan(zoom=zoom, left=cx - width / 2, top=cy - height / 2, width=width, height=height)


def compose_map(
    plan: MapPlan,
    tiles: Mapping[tuple[int, int], bytes | None],
    zones: Sequence[Zone],
    layers: Sequence[tuple[RGB, Iterable[Mapping[str, Any]]]],
    lines: Sequence[tuple[RGB, Mapping[str, Any]]] = (),
) -> bytes:
    """Draw tiles, outage polygons, feeder lines, search circles and location
    markers as PNG. Feeder lines are thin and nearly opaque, on top of the
    outage shading so you can see which lines run through an outage."""
    image = Image.new("RGBA", (plan.width, plan.height), _BACKGROUND + (255,))
    for (tx, ty), data in tiles.items():
        if not data:
            continue
        try:
            tile = Image.open(BytesIO(data)).convert("RGBA")
        except Exception:  # noqa: BLE001 - a bad tile just leaves a grey square
            continue
        image.paste(tile, (round(tx * TILE_SIZE - plan.left), round(ty * TILE_SIZE - plan.top)))

    s = _SCALE
    overlay = Image.new("RGBA", (plan.width * s, plan.height * s), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    def px(lon: float, lat: float) -> tuple[float, float]:
        x, y = plan.to_image(lon, lat)
        return x * s, y * s

    # Search circles
    for lat, lon, radius in zones:
        if radius > 0:
            hx, hy = px(lon, lat)
            rpx = radius / metres_per_pixel(lat, plan.zoom) * s
            draw.ellipse([hx - rpx, hy - rpx, hx + rpx, hy + rpx],
                         fill=COLOR_HOME + (28,), outline=COLOR_HOME + (200,), width=2 * s)

    # Outage polygons (upcoming first so current ones sit on top)
    for color, geometries in layers:
        for ring in iter_rings(geometries):
            points = [px(pt[0], pt[1]) for pt in ring]
            draw.polygon(points, fill=color + (100,))
            draw.line(points + [points[0]], fill=color + (255,), width=3 * s, joint="curve")

    # Feeder lines: opaque, ~2/3 the width of an outage outline, with a thin
    # black outline so light colours (e.g. cyan) stand out from the map.
    paths = [
        (color, [px(pt[0], pt[1]) for pt in path])
        for color, geometry in lines
        for path in iter_lines(geometry)
        if len(path) >= 2
    ]
    for _, points in paths:
        draw.line(points, fill=(0, 0, 0, 230), width=3 * s, joint="curve")
    for color, points in paths:
        draw.line(points, fill=color + (255,), width=2 * s, joint="curve")

    # Location markers
    for lat, lon, _ in zones:
        hx, hy = px(lon, lat)
        draw.ellipse([hx - 9 * s, hy - 9 * s, hx + 9 * s, hy + 9 * s], fill=(255, 255, 255, 255))
        draw.ellipse([hx - 6 * s, hy - 6 * s, hx + 6 * s, hy + 6 * s], fill=COLOR_HOME + (255,))

    overlay = overlay.resize((plan.width, plan.height), Image.LANCZOS)
    image = Image.alpha_composite(image, overlay)

    # Attribution (required by the OpenStreetMap tile policy)
    draw = ImageDraw.Draw(image)
    try:
        font = ImageFont.load_default(size=11)
    except TypeError:  # Pillow < 10.1
        font = ImageFont.load_default()
    credit = ATTRIBUTION + (" · Feeders: Energy Queensland" if lines else "")
    left, top, right, bottom = draw.textbbox((0, 0), credit, font=font)
    tw, th = right - left, bottom - top
    x0, y0 = plan.width - tw - 8, plan.height - th - 6
    draw.rectangle([x0 - 4, y0 - 3, plan.width, plan.height], fill=(255, 255, 255, 200))
    draw.text((x0 - left, y0 - top), credit, fill=(60, 60, 60, 255), font=font)

    out = BytesIO()
    image.convert("RGB").save(out, format="PNG", optimize=True)
    return out.getvalue()


# ── Details panel (drawn under a map for the per-outage pop-up) ─────────────

_PANEL_BG = (255, 255, 255)
_TEXT = (33, 33, 33)
_MUTED = (110, 110, 110)
_RULE = (225, 225, 225)
_PAD = 20
_LABEL_W = 150


def _font(size: int) -> ImageFont.ImageFont:
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow < 10.1 (no scalable default font)
        return ImageFont.load_default()


def _wrap(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, width: int, max_lines: int) -> list[str]:
    lines: list[str] = []
    for paragraph in str(text).splitlines() or [""]:
        line = ""
        for word in paragraph.split():
            candidate = f"{line} {word}".strip()
            if draw.textlength(candidate, font=font) <= width or not line:
                line = candidate
            else:
                lines.append(line)
                line = word
        lines.append(line)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        last = lines[-1]
        while last and draw.textlength(last + " …", font=font) > width:
            last = last[:-1]
        lines[-1] = last.rstrip(" ,") + " …"
    return lines


def compose_details(
    map_png: bytes,
    title: str,
    subtitle: str,
    rows: Sequence[tuple[str, str]],
    max_lines: int = 4,
    legend_label: str = "",
    legend: Sequence[tuple[RGB, str]] = (),
) -> bytes:
    """Append a white panel with the outage details under a rendered map.

    `legend` adds a row of colour swatches (e.g. the feeders in the area).
    """
    base = Image.open(BytesIO(map_png)).convert("RGB")
    width = base.width
    title_font, sub_font, label_font, value_font = _font(22), _font(16), _font(15), _font(16)
    scratch = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    value_w = width - 2 * _PAD - _LABEL_W
    line_h = 22

    wrapped = [(label, _wrap(scratch, value or "Unknown", value_font, value_w, max_lines)) for label, value in rows]
    height = _PAD + 30 + 26 + 14 + sum(len(v) * line_h + 10 for _, v in wrapped) + _PAD
    if legend:
        height += len(legend) * line_h + 10

    image = Image.new("RGB", (width, base.height + height), _PANEL_BG)
    image.paste(base, (0, 0))
    draw = ImageDraw.Draw(image)
    y = base.height + _PAD
    draw.text((_PAD, y), title, font=title_font, fill=_TEXT)
    y += 30
    draw.text((_PAD, y), subtitle, font=sub_font, fill=_MUTED)
    y += 26
    draw.line([(_PAD, y), (width - _PAD, y)], fill=_RULE, width=1)
    y += 14
    for label, lines in wrapped:
        draw.text((_PAD, y + 1), label, font=label_font, fill=_MUTED)
        for i, line in enumerate(lines):
            draw.text((_PAD + _LABEL_W, y + i * line_h), line, font=value_font, fill=_TEXT)
        y += len(lines) * line_h + 10
    if legend:
        draw.text((_PAD, y + 1), legend_label, font=label_font, fill=_MUTED)
        for i, (color, text) in enumerate(legend):
            ly = y + i * line_h
            swatch = [(_PAD + _LABEL_W, ly + 10), (_PAD + _LABEL_W + 22, ly + 10)]
            draw.line(swatch, fill=(0, 0, 0), width=6)  # same black outline as the map
            draw.line(swatch, fill=color, width=4)
            draw.text((_PAD + _LABEL_W + 32, ly), text, font=value_font, fill=_TEXT)

    out = BytesIO()
    image.save(out, format="PNG", optimize=True)
    return out.getvalue()
