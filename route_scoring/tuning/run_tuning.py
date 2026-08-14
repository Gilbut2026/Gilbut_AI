#!/usr/bin/env python3
"""slopeLevel-aware entry point for weight tuning.

기존 120건 라벨에는 slopeLevel이 포함되지 않았으므로, 현재 데이터로는
경사 관련 파라미터를 튜닝하지 않는다. 새 라벨 데이터에 모든 case의
userContext.slopeLevel이 포함되면 경사 파라미터 튜닝을 다시 허용한다.
"""

from __future__ import annotations

import optimize_weights as tuning


SLOPE_PARAMS = {
    "slope_up.0",
    "slope_up.1",
    "slope_down.0",
    "slope_down.1",
    "slope_sensitivity.MEDIUM",
    "slope_sensitivity.HIGH",
}


def slope_sensitivity(user):
    """서비스 코드와 동일하게 slopeLevel을 우선 사용한다."""
    user = user if isinstance(user, dict) else {}

    slope_level = user.get("slopeLevel")
    if slope_level == "DIFFICULT":
        return "HIGH"
    if slope_level == "SLIGHTLY_DIFFICULT":
        return "MEDIUM"
    if slope_level == "AVAILABLE":
        return "LOW"

    # 과거 라벨/구버전 데이터와의 호환용 fallback.
    if (
        user.get("mobilityAid") == "WHEELCHAIR"
        or user.get("stairLevel") == "DIFFICULT"
        or user.get("walkingDuration") in {"UNABLE_TO_WALK", "WITHIN_10_MINUTES"}
    ):
        return "HIGH"
    if (
        user.get("mobilityAid") == "CANE_OR_WALKER"
        or user.get("stairLevel") == "SLIGHTLY_DIFFICULT"
        or user.get("walkingDuration") == "WITHIN_20_MINUTES"
    ):
        return "MEDIUM"
    return "LOW"


def tunable_parameters(cases):
    params = tuning.tunable_parameters(cases)
    has_explicit_slope_level = all(
        isinstance(case.get("userContext"), dict)
        and case["userContext"].get("slopeLevel")
        in {"AVAILABLE", "SLIGHTLY_DIFFICULT", "DIFFICULT"}
        for case in cases.values()
    )

    if not has_explicit_slope_level:
        params = [param for param in params if param not in SLOPE_PARAMS]
        print(
            "[weight-tuning] slopeLevel이 없는 라벨 데이터이므로 "
            "slope 관련 파라미터는 baseline으로 고정합니다."
        )
    return params


def main():
    # optimize_weights.py 내부 호출이 서비스와 동일한 slopeLevel 의미를 사용하도록 교체.
    tuning.slope_sensitivity = slope_sensitivity
    tuning.tunable_parameters = tunable_parameters
    tuning.main()


if __name__ == "__main__":
    main()
