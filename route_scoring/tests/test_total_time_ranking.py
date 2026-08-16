"""접근성 score를 바꾸지 않고 totalTimeSec를 ranking에만 쓰는지 검증."""

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scoring import ranking, validation


class Breakdown:
    def __init__(self, total):
        self.total = total


def _entry(
    route_id,
    score,
    total_time,
    obstacle=0.0,
    walk_time=600,
    transfers=0,
    provider_rank=1,
):
    return {
        "candidate": {
            "routeId": route_id,
            "providerRank": provider_rank,
            "metrics": {
                "totalTimeSec": total_time,
                "totalWalkTimeSec": walk_time,
                "totalWalkDistanceM": 500,
                "transferCount": transfers,
            },
        },
        "obstacles": SimpleNamespace(weight=obstacle),
        "breakdown": Breakdown(score),
    }


def _request(total_time=1200):
    return {
        "requestId": "req-total-time",
        "candidates": [
            {
                "routeId": "route-1",
                "metrics": {
                    "totalTimeSec": total_time,
                    "totalWalkTimeSec": 600,
                    "totalWalkDistanceM": 500,
                    "transferCount": 1,
                },
            }
        ],
    }


def test_backend_integer_total_time_is_valid():
    assert validation.validate(_request(1200)) is None


def test_total_time_is_required_for_ranking():
    request = _request()
    del request["candidates"][0]["metrics"]["totalTimeSec"]
    assert validation.validate(request) == "totalTimeSec is required: route-1"


def test_total_time_must_match_backend_integer_contract():
    assert validation.validate(_request(1200.5)) == (
        "totalTimeSec must be an integer: route-1"
    )


def test_near_tie_prefers_shorter_total_time():
    entries = [
        _entry("more-accessible-slower", -2.0, 3600),
        _entry("slightly-lower-score-faster", -2.1, 2400),
    ]
    ordered = ranking.order(entries)
    assert [entry["candidate"]["routeId"] for entry in ordered] == [
        "slightly-lower-score-faster",
        "more-accessible-slower",
    ]


def test_score_gap_over_threshold_keeps_accessibility_first():
    entries = [
        _entry("accessible-slower", -2.0, 3600),
        _entry("much-lower-score-faster", -2.31, 1200),
    ]
    ordered = ranking.order(entries)
    assert [entry["candidate"]["routeId"] for entry in ordered] == [
        "accessible-slower",
        "much-lower-score-faster",
    ]


def test_threshold_boundary_is_included_in_near_tie():
    entries = [
        _entry("score-best", -2.0, 3600),
        _entry("gap-point-three", -2.3, 1800),
    ]
    ordered = ranking.order(entries)
    assert ordered[0]["candidate"]["routeId"] == "gap-point-three"


def test_float_noise_at_threshold_does_not_break_near_tie():
    entries = [
        _entry("score-best", -2.0, 3600),
        _entry("floating-boundary", -2.3000000000000003, 1800),
    ]
    ordered = ranking.order(entries)
    assert ordered[0]["candidate"]["routeId"] == "floating-boundary"


def test_groups_are_anchored_to_best_remaining_score():
    entries = [
        _entry("a", -2.0, 3600),
        _entry("b", -2.2, 1800),
        _entry("c", -2.4, 600),
    ]
    ordered = ranking.order(entries)
    assert [entry["candidate"]["routeId"] for entry in ordered] == [
        "b",
        "a",
        "c",
    ]


def test_same_total_time_keeps_existing_secondary_order():
    entries = [
        _entry("more-obstacles", -2.0, 2400, obstacle=2.0, provider_rank=1),
        _entry("fewer-obstacles", -2.1, 2400, obstacle=1.0, provider_rank=2),
    ]
    ordered = ranking.order(entries)
    assert ordered[0]["candidate"]["routeId"] == "fewer-obstacles"


def test_negative_threshold_is_rejected():
    with pytest.raises(ValueError):
        ranking.order([], threshold=-0.1)
