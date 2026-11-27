"""Tests for the pure parsing and diff helpers."""

from __future__ import annotations

from datetime import datetime, timedelta

from custom_components.ergon_outages.models import (
    KIND_CLEARED,
    KIND_NEW,
    KIND_STARTED,
    KIND_UPDATED,
    PHASE_CURRENT,
    PHASE_FUTURE,
    diff_outages,
    parse_features,
    tidy_reason,
)

from .conftest import UTC, feature

NOW = datetime(2026, 11, 15, 6, 0, tzinfo=UTC)


def test_tidy_reason() -> None:
    assert tidy_reason("The loss of supply is to replace power poles") == "Replace power poles"
    assert tidy_reason("The loss of supply is due to damage. ") == "Due to damage."
    assert tidy_reason("Something else") == "Something else"
    assert tidy_reason(None) is None


def test_parse_phases_and_fields() -> None:
    outages = parse_features(
        [
            feature("A", start=NOW - timedelta(hours=1), est_fix=NOW + timedelta(hours=2)),
            feature("B", start=NOW + timedelta(days=2)),
            feature("C", start=None, outage_type="UNPLANNED"),
        ],
        NOW,
    )
    assert outages["A"].phase == PHASE_CURRENT
    assert outages["B"].phase == PHASE_FUTURE
    assert outages["C"].phase == PHASE_CURRENT  # no start time => happening now
    a = outages["A"]
    assert a.estimated_restoration == NOW + timedelta(hours=2)
    assert a.suburbs == "Longreach"
    assert a.streets == "Alfred St, Wills St"
    assert a.reason == "Replace power poles and improve the electricity network"
    lat, lon = a.centroid
    assert abs(lat - -26.401) < 1e-6 and abs(lon - 146.245) < 1e-6


def test_parse_merges_multi_polygon_events_and_skips_bad_rows() -> None:
    outages = parse_features(
        [
            feature("A", start=NOW),
            feature("A", start=NOW, lon=146.3),
            {"type": "Feature", "properties": {"EVENT_ID": None}},
            "garbage",
        ],
        NOW,
    )
    assert list(outages) == ["A"]
    assert len(outages["A"].geometries) == 2


def test_diff_detects_every_transition() -> None:
    before = parse_features(
        [
            feature("KEEP", start=NOW - timedelta(hours=1)),
            feature("GONE", start=NOW - timedelta(hours=1)),
            feature("SOON", start=NOW + timedelta(minutes=5)),
            feature("CHANGE", start=NOW - timedelta(hours=1), status="Outage reported"),
        ],
        NOW,
    )
    _, snapshot = diff_outages({}, before)

    later = NOW + timedelta(minutes=10)
    after = parse_features(
        [
            feature("KEEP", start=NOW - timedelta(hours=1)),
            feature("SOON", start=NOW + timedelta(minutes=5)),
            feature("CHANGE", start=NOW - timedelta(hours=1), status="Crews on route"),
            feature("FRESH", start=None, outage_type="UNPLANNED"),
        ],
        later,
    )
    events, new_snapshot = diff_outages(snapshot, after)
    kinds = {(kind, s["event_id"]) for kind, s in events}
    assert kinds == {
        (KIND_STARTED, "SOON"),
        (KIND_UPDATED, "CHANGE"),
        (KIND_NEW, "FRESH"),
        (KIND_CLEARED, "GONE"),
    }
    assert set(new_snapshot) == {"KEEP", "SOON", "CHANGE", "FRESH"}

    # Same data again: nothing to announce.
    events, _ = diff_outages(new_snapshot, after)
    assert events == []
