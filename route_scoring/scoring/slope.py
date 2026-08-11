"""도보 경로의 고도 샘플링, 경사 요약 및 감점 계산."""

from __future__ import annotations

import math
from typing import Iterable

from . import policy


EARTH_RADIUS_M = 6_371_000.0
DEFAULT_SAMPLE_INTERVAL_M = 50.0
DEFAULT_MIN_SEGMENT_M = 25.0
DEFAULT_MAX_NODES = 2_000


class SlopeDataError(ValueError):
    """경사 분석에 사용할 수 없는 좌표 또는 고도 데이터."""


def haversine_meters(first, second):
    """두 ``[longitude, latitude]`` 좌표 사이의 수평거리를 반환한다."""
    lon1, lat1 = first[:2]
    lon2, lat2 = second[:2]
    lat_delta = math.radians(lat2 - lat1)
    lon_delta = math.radians(lon2 - lon1)
    lat1_rad = math.radians(lat1)
    lat2_rad = math.radians(lat2)
    value = (
        math.sin(lat_delta / 2) ** 2
        + math.cos(lat1_rad)
        * math.cos(lat2_rad)
        * math.sin(lon_delta / 2) ** 2
    )
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(min(1.0, value)))


def resample_geometry(
    coordinates,
    interval_m=DEFAULT_SAMPLE_INTERVAL_M,
    min_segment_m=DEFAULT_MIN_SEGMENT_M,
    max_nodes=DEFAULT_MAX_NODES,
):
    """2D 경로를 거리 기준으로 리샘플링한다.

    마지막 잔여 구간이 ``min_segment_m``보다 짧으면 직전 구간에 합친다.
    전체 길이가 그보다 짧으면 빈 목록을 반환해 분석 대상에서 제외한다.
    """
    coordinates, _ = resample_geometry_with_distances(
        coordinates,
        interval_m=interval_m,
        min_segment_m=min_segment_m,
        max_nodes=max_nodes,
    )
    return coordinates


def resample_geometry_with_distances(
    coordinates,
    interval_m=DEFAULT_SAMPLE_INTERVAL_M,
    min_segment_m=DEFAULT_MIN_SEGMENT_M,
    max_nodes=DEFAULT_MAX_NODES,
):
    """리샘플링 좌표와 각 좌표의 원본 경로상 누적거리를 함께 반환한다."""
    if not _is_positive_number(interval_m):
        raise SlopeDataError("sample interval must be positive")
    if not _is_positive_number(min_segment_m):
        raise SlopeDataError("minimum segment length must be positive")
    if isinstance(max_nodes, bool) or not isinstance(max_nodes, int) or max_nodes < 2:
        raise SlopeDataError("maximum nodes must be at least 2")

    cleaned = _clean_coordinates(coordinates, dimensions=2)
    if len(cleaned) < 2:
        raise SlopeDataError("geometry must contain at least two distinct points")

    cumulative = [0.0]
    for index in range(1, len(cleaned)):
        cumulative.append(
            cumulative[-1] + haversine_meters(cleaned[index - 1], cleaned[index])
        )

    total_distance_m = cumulative[-1]
    if total_distance_m < min_segment_m:
        return [], []

    sample_distances = [0.0]
    distance_m = float(interval_m)
    while distance_m < total_distance_m:
        sample_distances.append(distance_m)
        distance_m += interval_m

    remainder_m = total_distance_m - sample_distances[-1]
    if remainder_m < min_segment_m and len(sample_distances) > 1:
        sample_distances.pop()
    sample_distances.append(total_distance_m)

    if len(sample_distances) > max_nodes:
        raise SlopeDataError("maximum elevation nodes exceeded")

    return (
        [
            _interpolate_coordinate(cleaned, cumulative, target_m)
            for target_m in sample_distances
        ],
        sample_distances,
    )


def summarize_elevated_coordinates(coordinates, sample_distances_m=None):
    """ORS가 반환한 3D 좌표에서 최대 경사와 누적 상승·하강을 계산한다."""
    points = _clean_coordinates(coordinates, dimensions=3, deduplicate=False)
    if len(points) < 2:
        raise SlopeDataError("elevated geometry must contain at least two points")
    horizontal_distances = _horizontal_distances(points, sample_distances_m)

    max_uphill = 0.0
    max_downhill = 0.0
    total_ascent = 0.0
    total_descent = 0.0

    for first, second, horizontal_m in zip(
        points,
        points[1:],
        horizontal_distances,
    ):
        elevation_delta = second[2] - first[2]
        grade_percent = elevation_delta / horizontal_m * 100
        if elevation_delta >= 0:
            max_uphill = max(max_uphill, grade_percent)
            total_ascent += elevation_delta
        else:
            max_downhill = max(max_downhill, abs(grade_percent))
            total_descent += abs(elevation_delta)

    return {
        "maxUphillGradePercent": max_uphill,
        "maxDownhillGradePercent": max_downhill,
        "totalAscentM": total_ascent,
        "totalDescentM": total_descent,
    }


def build_summary(
    status,
    successful_profiles: Iterable[dict] = (),
    total_eligible_segments=0,
    sample_interval_m=DEFAULT_SAMPLE_INTERVAL_M,
):
    """후보 경로 단위 공개 경사 요약을 만든다."""
    profiles = list(successful_profiles)
    has_profile = bool(profiles)
    return {
        "status": status,
        "sampleIntervalM": int(sample_interval_m),
        "analyzedSegmentCount": len(profiles),
        "totalEligibleSegmentCount": total_eligible_segments,
        "maxUphillGradePercent": _rounded_or_none(
            max((item["maxUphillGradePercent"] for item in profiles), default=None)
        ),
        "maxDownhillGradePercent": _rounded_or_none(
            max((item["maxDownhillGradePercent"] for item in profiles), default=None)
        ),
        "totalAscentM": _rounded_or_none(
            sum(item["totalAscentM"] for item in profiles) if has_profile else None
        ),
        "totalDescentM": _rounded_or_none(
            sum(item["totalDescentM"] for item in profiles) if has_profile else None
        ),
    }


def not_requested_summary():
    return build_summary(policy.SLOPE_NOT_REQUESTED)


def calculate_penalty(summary, user):
    """성공한 경사 요약에 사용자 민감도를 적용해 최대 3점을 감점한다."""
    if not isinstance(summary, dict) or summary.get("status") != policy.SLOPE_SUCCESS:
        return 0.0

    uphill = _finite_non_negative(summary.get("maxUphillGradePercent"))
    downhill = _finite_non_negative(summary.get("maxDownhillGradePercent"))
    if uphill is None or downhill is None:
        return 0.0

    base_penalty = max(
        _band_penalty(uphill, policy.SLOPE_UPHILL_PENALTY),
        _band_penalty(downhill, policy.SLOPE_DOWNHILL_PENALTY),
    )
    multiplier = policy.SLOPE_SENSITIVITY_MULTIPLIER[sensitivity_level(user)]
    return min(policy.SLOPE_MAX_PENALTY, base_penalty * multiplier)


def sensitivity_level(user):
    """기존 프로필 값 중 가장 취약한 항목으로 경사 민감도를 결정한다."""
    user = user if isinstance(user, dict) else {}
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


def _band_penalty(grade_percent, penalties):
    if grade_percent >= policy.SLOPE_STEEP_GRADE_PERCENT:
        return penalties[2]
    if grade_percent >= policy.SLOPE_MODERATE_GRADE_PERCENT:
        return penalties[1]
    return penalties[0]


def _clean_coordinates(coordinates, dimensions, deduplicate=True):
    if not isinstance(coordinates, list):
        raise SlopeDataError("geometry coordinates must be a list")

    cleaned = []
    for point in coordinates:
        if not isinstance(point, (list, tuple)) or len(point) < dimensions:
            raise SlopeDataError(f"geometry points must have {dimensions} dimensions")
        values = point[:dimensions]
        if any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            for value in values
        ):
            raise SlopeDataError("geometry values must be finite numbers")
        lon, lat = values[:2]
        if not -180 <= lon <= 180 or not -90 <= lat <= 90:
            raise SlopeDataError("geometry coordinate is outside longitude/latitude bounds")

        normalized = [float(value) for value in values]
        if deduplicate and cleaned and normalized[:2] == cleaned[-1][:2]:
            continue
        cleaned.append(normalized)
    return cleaned


def _interpolate_coordinate(coordinates, cumulative, target_m):
    if target_m <= 0:
        return list(coordinates[0][:2])
    if target_m >= cumulative[-1]:
        return list(coordinates[-1][:2])

    right_index = 1
    while cumulative[right_index] < target_m:
        right_index += 1
    left_index = right_index - 1
    span_m = cumulative[right_index] - cumulative[left_index]
    ratio = (target_m - cumulative[left_index]) / span_m
    left = coordinates[left_index]
    right = coordinates[right_index]
    return [
        left[0] + (right[0] - left[0]) * ratio,
        left[1] + (right[1] - left[1]) * ratio,
    ]


def _horizontal_distances(points, sample_distances_m):
    if sample_distances_m is None:
        distances = [
            haversine_meters(first, second)
            for first, second in zip(points, points[1:])
        ]
    else:
        if not isinstance(sample_distances_m, list) or len(sample_distances_m) != len(points):
            raise SlopeDataError("sample distances must match elevated geometry")
        if any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            for value in sample_distances_m
        ):
            raise SlopeDataError("sample distances must be finite numbers")
        distances = [
            right - left
            for left, right in zip(sample_distances_m, sample_distances_m[1:])
        ]

    if any(distance <= 0 for distance in distances):
        raise SlopeDataError("sample distances must be strictly increasing")
    return distances


def _is_positive_number(value):
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(value)
        and value > 0
    )


def _finite_non_negative(value):
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value < 0
    ):
        return None
    return float(value)


def _rounded_or_none(value):
    return None if value is None else round(value, 2)
