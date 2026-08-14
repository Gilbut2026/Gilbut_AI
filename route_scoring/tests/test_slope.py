"""경사 민감도와 slopePenalty 계산 검증."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scoring import policy, slope


def success_summary(uphill=8.0, downhill=2.0):
    return {
        "status": policy.SLOPE_SUCCESS,
        "maxUphillGradePercent": uphill,
        "maxDownhillGradePercent": downhill,
    }


def test_slope_level을_경사_민감도에_직접_반영한다():
    assert slope.sensitivity_level({"slopeLevel": "AVAILABLE"}) == "LOW"
    assert slope.sensitivity_level({"slopeLevel": "SLIGHTLY_DIFFICULT"}) == "MEDIUM"
    assert slope.sensitivity_level({"slopeLevel": "DIFFICULT"}) == "HIGH"


def test_slope_level이_있으면_기존_proxy보다_우선한다():
    user = {
        "slopeLevel": "AVAILABLE",
        "mobilityAid": "WHEELCHAIR",
        "stairLevel": "DIFFICULT",
        "walkingDuration": "WITHIN_10_MINUTES",
    }
    assert slope.sensitivity_level(user) == "LOW"


def test_slope_level이_없으면_기존_profile을_fallback으로_사용한다():
    assert slope.sensitivity_level({"mobilityAid": "WHEELCHAIR"}) == "HIGH"
    assert slope.sensitivity_level({"stairLevel": "SLIGHTLY_DIFFICULT"}) == "MEDIUM"
    assert slope.sensitivity_level({}) == "LOW"


def test_같은_경사라도_slope_level에_따라_penalty가_커진다():
    summary = success_summary()

    available = slope.calculate_penalty(summary, {"slopeLevel": "AVAILABLE"})
    slightly_difficult = slope.calculate_penalty(
        summary,
        {"slopeLevel": "SLIGHTLY_DIFFICULT"},
    )
    difficult = slope.calculate_penalty(summary, {"slopeLevel": "DIFFICULT"})

    assert available == 1.5
    assert slightly_difficult == 2.25
    assert difficult == 3.0
    assert available < slightly_difficult < difficult


def test_success가_아니면_slope_level과_무관하게_감점하지_않는다():
    summary = {
        "status": policy.SLOPE_PARTIAL,
        "maxUphillGradePercent": 8.0,
        "maxDownhillGradePercent": 2.0,
    }
    assert slope.calculate_penalty(summary, {"slopeLevel": "DIFFICULT"}) == 0.0
