"""Data model and pure helpers for Ergon outages.

Nothing in this module imports Home Assistant, so it can be unit tested on its
own against real feed data.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

PHASE_CURRENT = "current"
PHASE_FUTURE = "future"

KIND_NEW = "new"
KIND_STARTED = "started"
KIND_UPDATED = "updated"
KIND_CLEARED = "cleared"

# Fields whose change produces an "updated" event.
_TRACKED_FIELDS = ("status", "start", "estimated_restoration", "customers_affected")
_REASON_PREFIX = "the loss of supply is "


def ms_to_datetime(value: Any) -> datetime | None:
    """Convert an ArcGIS epoch-milliseconds value to an aware UTC datetime."""
    if value is None or isinstance(value, bool):
        return None
    try:
        return datetime.fromtimestamp(float(value) / 1000, tz=timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def tidy_reason(reason: Any) -> str | None:
    """Shorten Ergon's boilerplate reason text.

    "The loss of supply is to replace power poles..." -> "Replace power poles..."
    "The loss of supply is due to damage..."          -> "Due to damage..."
    """
    if not reason:
        return None
    text = " ".join(str(reason).split())
    if text.lower().startswith(_REASON_PREFIX):
        text = text[len(_REASON_PREFIX) :]
        if text.lower().startswith("to "):
            text = text[3:]
    return (text[:1].upper() + text[1:]) if text else None


def tidy_place(value: Any) -> str | None:
    """Turn Ergon's upper-case suburb/street lists into title case."""
    if not value:
        return None
    return " ".join(str(value).split()).title()


def _to_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


@dataclass(slots=True)
class Outage:
    """A single Ergon outage event (one EVENT_ID, one or more polygons)."""

    event_id: str
    outage_type: str | None
    status: str | None
    reason: str | None
    customers_affected: int | None
    suburbs: str | None
    streets: str | None
    start: datetime | None
    estimated_restoration: datetime | None
    phase: str
    geometries: list[dict[str, Any]] = field(default_factory=list)

    @property
    def is_current(self) -> bool:
        return self.phase == PHASE_CURRENT

    @property
    def is_future(self) -> bool:
        return self.phase == PHASE_FUTURE

    @property
    def centroid(self) -> tuple[float, float] | None:
        """Approximate centre (lat, lon): mean of the outer-ring vertices."""
        points: list[tuple[float, float]] = []
        for geom in self.geometries:
            gtype = geom.get("type")
            coords = geom.get("coordinates") or []
            polygons = [coords] if gtype == "Polygon" else coords if gtype == "MultiPolygon" else []
            for polygon in polygons:
                if not polygon:
                    continue
                ring = polygon[0]
                # GeoJSON rings repeat the first vertex at the end.
                if len(ring) > 1 and ring[0] == ring[-1]:
                    ring = ring[:-1]
                points.extend((pt[1], pt[0]) for pt in ring if len(pt) >= 2)
        if not points:
            return None
        lat = sum(p[0] for p in points) / len(points)
        lon = sum(p[1] for p in points) / len(points)
        return round(lat, 6), round(lon, 6)

    def summary(self) -> dict[str, Any]:
        """JSON-serialisable description, used for events and storage."""
        return {
            "event_id": self.event_id,
            "phase": self.phase,
            "outage_type": self.outage_type,
            "status": self.status,
            "reason": self.reason,
            "customers_affected": self.customers_affected,
            "suburbs": self.suburbs,
            "start": _iso(self.start),
            "estimated_restoration": _iso(self.estimated_restoration),
        }

    def features(self, fmt_time: Callable[[datetime | None], str]) -> list[dict[str, Any]]:
        """GeoJSON features for this outage, with tooltip-friendly properties."""
        props = {
            "Outage": self.event_id,
            "Type": (self.outage_type or "unknown").capitalize(),
            "Status": self.status or "Unknown",
            "Reason": self.reason or "Unknown",
            "Customers": self.customers_affected if self.customers_affected is not None else "Unknown",
            "Start": fmt_time(self.start),
            "Est. restoration": fmt_time(self.estimated_restoration),
        }
        return [
            {"type": "Feature", "geometry": geom, "properties": props}
            for geom in self.geometries
        ]


def parse_features(features: Iterable[Mapping[str, Any]], now: datetime) -> dict[str, Outage]:
    """Build Outage objects from ArcGIS GeoJSON features, keyed by EVENT_ID.

    An event that is split across several polygons is merged into one Outage.
    An outage with no start time, or a start time in the past, is current.
    """
    outages: dict[str, Outage] = {}
    for feature in features:
        if not isinstance(feature, Mapping):
            continue
        props = feature.get("properties") or {}
        raw_id = props.get("EVENT_ID")
        if raw_id in (None, ""):
            continue
        event_id = str(raw_id).strip()
        geometry = feature.get("geometry")

        if event_id in outages:
            if geometry:
                outages[event_id].geometries.append(geometry)
            continue

        start = ms_to_datetime(props.get("START"))
        # FINISH has been null in every record observed; EST_FIX_TIME is the
        # field Ergon actually populates. Fall back to FINISH just in case.
        restore = ms_to_datetime(props.get("EST_FIX_TIME")) or ms_to_datetime(props.get("FINISH"))
        outage_type = props.get("TYPE")

        outages[event_id] = Outage(
            event_id=event_id,
            outage_type=str(outage_type).lower() if outage_type else None,
            status=props.get("STATUS") or None,
            reason=tidy_reason(props.get("REASON")),
            customers_affected=_to_int(props.get("CUSTOMERS_AFFECTED")),
            suburbs=tidy_place(props.get("SUBURBS")),
            streets=tidy_place(props.get("STREETS")),
            start=start,
            estimated_restoration=restore,
            phase=PHASE_FUTURE if start is not None and start > now else PHASE_CURRENT,
            geometries=[geometry] if geometry else [],
        )
    return outages


def diff_outages(
    known: Mapping[str, Mapping[str, Any]],
    outages: Mapping[str, Outage],
) -> tuple[list[tuple[str, dict[str, Any]]], dict[str, dict[str, Any]]]:
    """Compare the previous snapshot with the latest poll.

    Returns (events, new_snapshot) where each event is (kind, summary). Kinds:
      new      - EVENT_ID not seen before
      started  - was future, now current
      updated  - status, times or customer count changed
      cleared  - EVENT_ID no longer in the feed (summary is the last known one)
    """
    events: list[tuple[str, dict[str, Any]]] = []
    snapshot: dict[str, dict[str, Any]] = {}

    for event_id, outage in outages.items():
        summary = outage.summary()
        previous = known.get(event_id)
        if previous is None:
            events.append((KIND_NEW, summary))
        elif previous.get("phase") == PHASE_FUTURE and outage.is_current:
            events.append((KIND_STARTED, summary))
        elif any(previous.get(key) != summary.get(key) for key in _TRACKED_FIELDS):
            events.append((KIND_UPDATED, summary))
        snapshot[event_id] = summary

    for event_id, previous in known.items():
        if event_id not in outages:
            events.append((KIND_CLEARED, dict(previous)))

    return events, snapshot
