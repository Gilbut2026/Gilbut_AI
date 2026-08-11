"""OpenRouteService Elevation Line을 이용한 도보 경사 보강 계층."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import dataclass
import logging
import os
import time

import requests

from route_scoring.scoring import policy, slope


LOGGER = logging.getLogger(__name__)
DEFAULT_ORS_ELEVATION_URL = (
    "https://api.heigit.org/openelevationservice/v0/elevation/line"
)


@dataclass(frozen=True)
class OrsSlopeConfig:
    enabled: bool = False
    api_key: str = ""
    url: str = DEFAULT_ORS_ELEVATION_URL
    connect_timeout_seconds: float = 2.0
    read_timeout_seconds: float = 5.0
    total_budget_seconds: float = 8.0
    max_calls: int = 20
    max_workers: int = 4
    sample_interval_m: float = slope.DEFAULT_SAMPLE_INTERVAL_M
    min_segment_m: float = slope.DEFAULT_MIN_SEGMENT_M
    max_nodes: int = slope.DEFAULT_MAX_NODES

    @classmethod
    def from_env(cls):
        return cls(
            enabled=_env_bool("ORS_SLOPE_ENABLED", False),
            api_key=os.environ.get("ORS_API_KEY", "").strip(),
            url=os.environ.get("ORS_ELEVATION_URL", DEFAULT_ORS_ELEVATION_URL).strip()
            or DEFAULT_ORS_ELEVATION_URL,
            connect_timeout_seconds=_env_float("ORS_CONNECT_TIMEOUT_SEC", 2.0),
            read_timeout_seconds=_env_float("ORS_READ_TIMEOUT_SEC", 5.0),
            total_budget_seconds=_env_float("ORS_TOTAL_BUDGET_SEC", 8.0),
            max_calls=_env_int("ORS_MAX_CALLS", 20),
            max_workers=_env_int("ORS_MAX_WORKERS", 4),
        )


def enrich_routes_with_slopes(scoring_request, config=None, post=None):
    """요청 후보에 ``slopeSummary``를 추가한다.

    ORS 오류는 후보별 FAILED/PARTIAL 상태로 변환하며 예외를 호출자에게 전파하지
    않는다. 반환값은 관측용 집계이고 실제 스코어링 입력은 제자리에서 갱신된다.
    """
    config = config or OrsSlopeConfig.from_env()
    post = post or requests.post
    candidates = scoring_request.get("candidates") if isinstance(scoring_request, dict) else None
    if not isinstance(candidates, list):
        return {"calls": 0, "statusCounts": {}}

    if not config.enabled or not config.api_key:
        mark_slope_not_requested(scoring_request)
        return {
            "calls": 0,
            "statusCounts": {policy.SLOPE_NOT_REQUESTED: len(candidates)},
        }

    started_at = time.monotonic()
    states = [
        {"eligible": 0, "failed": 0, "profiles": [], "failureCodes": []}
        for _ in candidates
    ]
    prepared = []

    ordered_candidates = sorted(
        enumerate(candidates),
        key=lambda item: (_provider_rank(item[1]), item[0]),
    )
    for candidate_index, candidate in ordered_candidates:
        segments = candidate.get("walkSegments") if isinstance(candidate, dict) else None
        if not isinstance(segments, list):
            continue
        for segment_index, segment in enumerate(segments):
            try:
                coordinates = _geometry_coordinates(segment)
                samples, sample_distances = slope.resample_geometry_with_distances(
                    coordinates,
                    interval_m=config.sample_interval_m,
                    min_segment_m=config.min_segment_m,
                    max_nodes=config.max_nodes,
                )
            except (SlopeEnrichmentError, slope.SlopeDataError):
                states[candidate_index]["eligible"] += 1
                states[candidate_index]["failed"] += 1
                states[candidate_index]["failureCodes"].append("INVALID_GEOMETRY")
                continue

            if not samples:
                continue
            states[candidate_index]["eligible"] += 1
            prepared.append(
                (candidate_index, segment_index, samples, sample_distances)
            )

    scheduled = prepared[: max(config.max_calls, 0)]
    for candidate_index, _, _, _ in prepared[len(scheduled):]:
        states[candidate_index]["failed"] += 1
        states[candidate_index]["failureCodes"].append("CALL_BUDGET_EXCEEDED")

    if scheduled:
        executor = ThreadPoolExecutor(max_workers=max(1, config.max_workers))
        futures = {
            executor.submit(
                _fetch_profile,
                samples,
                sample_distances,
                config,
                post,
            ): candidate_index
            for candidate_index, _, samples, sample_distances in scheduled
        }
        done, pending = wait(
            futures,
            timeout=max(config.total_budget_seconds, 0.0),
        )
        for future in done:
            candidate_index = futures[future]
            try:
                profile = future.result()
            except Exception as error:
                states[candidate_index]["failed"] += 1
                states[candidate_index]["failureCodes"].append(
                    _failure_code(error)
                )
            else:
                states[candidate_index]["profiles"].append(profile)
        for future in pending:
            states[futures[future]]["failed"] += 1
            states[futures[future]]["failureCodes"].append("TOTAL_TIMEOUT")
            future.cancel()
        executor.shutdown(wait=False)

    status_counts = {}
    failure_counts = {}
    for candidate, state in zip(candidates, states):
        status = _candidate_status(state)
        status_counts[status] = status_counts.get(status, 0) + 1
        if isinstance(candidate, dict):
            candidate["slopeSummary"] = slope.build_summary(
                status,
                state["profiles"],
                total_eligible_segments=state["eligible"],
                sample_interval_m=config.sample_interval_m,
            )
        for failure_code in state["failureCodes"]:
            failure_counts[failure_code] = failure_counts.get(failure_code, 0) + 1

    elapsed_ms = round((time.monotonic() - started_at) * 1_000)
    LOGGER.info(
        "ORS slope enrichment requestId=%s calls=%s elapsedMs=%s statuses=%s failures=%s",
        scoring_request.get("requestId"),
        len(scheduled),
        elapsed_ms,
        status_counts,
        failure_counts,
    )
    return {
        "calls": len(scheduled),
        "statusCounts": status_counts,
        "failureCounts": failure_counts,
    }


def mark_slope_not_requested(scoring_request):
    candidates = scoring_request.get("candidates") if isinstance(scoring_request, dict) else None
    for candidate in candidates or []:
        if isinstance(candidate, dict):
            candidate["slopeSummary"] = slope.not_requested_summary()


def mark_slope_failed(scoring_request):
    """예상하지 못한 보강 오류에도 추천 계산을 계속하기 위한 최종 폴백."""
    candidates = scoring_request.get("candidates") if isinstance(scoring_request, dict) else None
    for candidate in candidates or []:
        if not isinstance(candidate, dict):
            continue
        segments = candidate.get("walkSegments")
        eligible = len(segments) if isinstance(segments, list) else 0
        status = policy.SLOPE_FAILED if eligible else policy.SLOPE_NOT_REQUESTED
        candidate["slopeSummary"] = slope.build_summary(
            status,
            total_eligible_segments=eligible,
        )


class SlopeEnrichmentError(ValueError):
    pass


def _fetch_profile(samples, sample_distances, config, post):
    response = post(
        config.url,
        headers={
            "Authorization": config.api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        json={
            "format_in": "polyline",
            "format_out": "polyline",
            "geometry": samples,
        },
        timeout=(config.connect_timeout_seconds, config.read_timeout_seconds),
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict) or not isinstance(payload.get("geometry"), list):
        raise SlopeEnrichmentError("ORS response geometry is missing")
    if len(payload["geometry"]) != len(samples):
        raise SlopeEnrichmentError("ORS response geometry length changed")
    return slope.summarize_elevated_coordinates(
        payload["geometry"],
        sample_distances,
    )


def _geometry_coordinates(segment):
    if not isinstance(segment, dict):
        raise SlopeEnrichmentError("walk segment must be an object")
    geometry = segment.get("geometry")
    if not isinstance(geometry, dict) or geometry.get("type") != "LineString":
        raise SlopeEnrichmentError("walk segment LineString geometry is missing")
    return geometry.get("coordinates")


def _candidate_status(state):
    if state["eligible"] == 0:
        return policy.SLOPE_NOT_REQUESTED
    success_count = len(state["profiles"])
    if success_count == state["eligible"]:
        return policy.SLOPE_SUCCESS
    if success_count == 0:
        return policy.SLOPE_FAILED
    return policy.SLOPE_PARTIAL


def _provider_rank(candidate):
    if not isinstance(candidate, dict):
        return float("inf")
    value = candidate.get("providerRank")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return float("inf")
    return value


def _failure_code(error):
    if isinstance(error, requests.Timeout):
        return "HTTP_TIMEOUT"
    if isinstance(error, requests.HTTPError):
        response = error.response
        status_code = getattr(response, "status_code", None)
        return f"HTTP_{status_code}" if status_code is not None else "HTTP_ERROR"
    if isinstance(error, requests.RequestException):
        return "HTTP_REQUEST_FAILED"
    if isinstance(error, (SlopeEnrichmentError, slope.SlopeDataError)):
        return "INVALID_RESPONSE"
    return "UNEXPECTED_ERROR"


def _env_bool(name, default):
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_float(name, default):
    try:
        value = float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def _env_int(name, default):
    try:
        value = int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default
