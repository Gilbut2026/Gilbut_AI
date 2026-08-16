"""경로 접근성 스코어링 진입점.

사용법:
    from scoring import score_routes

    result = score_routes(request)
"""

from . import drt, filters, obstacles, policy, ranking, score, slope, validation


def score_routes(request):
    """경로 후보를 점수순으로 정렬하고 DRT 안내 여부를 판단한다.

    Args:
        request: requestId, userContext, environment, candidates를 담은 dict

    Returns:
        results(점수·순위·필터 사유)와 drtDecision을 담은 dict.
        입력이 유효하지 않으면 error를 담은 dict.
    """
    error = validation.validate(request)
    if error:
        return _error(request, "VALIDATION_ERROR", error)

    user = request.get("userContext", {})
    weather = _resolve_weather(request.get("environment", {}))

    passed, filtered = _partition(request["candidates"], user)
    ranked, results = _rank(passed, user, weather)

    top = ranked[0]["candidate"] if ranked else None
    decision = drt.decide(user, weather, top, len(ranked))

    return {
        "requestId": request.get("requestId"),
        "scoringVersion": policy.SCORING_VERSION,
        "results": results + filtered,
        "drtDecision": decision,
    }


def _partition(candidates, user):
    """후보를 필터 통과분과 제외분으로 나눈다."""
    passed = []
    filtered = []

    for candidate in candidates:
        found = obstacles.collect(candidate.get("walkSegments"))
        codes = filters.check(candidate, found, user)

        if codes:
            filtered.append({
                "routeId": candidate["routeId"],
                "status": policy.FILTERED,
                "score": None,
                "rank": None,
                "filterCodes": codes,
                "slopeSummary": _slope_summary(candidate),
            })
        else:
            passed.append({"candidate": candidate, "obstacles": found})

    return passed, filtered


def _rank(passed, user, weather):
    """기존 Score Function으로 점수를 계산한 뒤 ranking 정책을 적용한다.

    ``score.calculate``의 계산식은 그대로 유지한다. totalTimeSec는 점수에
    더하지 않고, 접근성 점수가 near-tie인 후보의 정렬에만 사용한다.

    Returns:
        (정렬된 항목 목록, 응답용 결과 목록)
    """
    entries = [
        {
            **entry,
            "breakdown": score.calculate(
                entry["candidate"],
                entry["obstacles"],
                user,
                weather,
            ),
        }
        for entry in passed
    ]

    entries = ranking.order(entries)

    results = [
        {
            "routeId": entry["candidate"]["routeId"],
            "status": policy.SCORED,
            "score": round(entry["breakdown"].total, 2),
            "rank": rank,
            "filterCodes": [],
            "scoreBreakdown": entry["breakdown"].as_dict(),
            "slopeSummary": _slope_summary(entry["candidate"]),
        }
        for rank, entry in enumerate(entries, start=1)
    ]

    return entries, results


def _slope_summary(candidate):
    summary = candidate.get("slopeSummary")
    return summary if isinstance(summary, dict) else slope.not_requested_summary()


def _resolve_weather(environment):
    """날씨 조회에 실패했으면 페널티를 적용하지 않는다.

    조회 실패를 "날씨가 나쁘다"로도 "좋다"로도 단정하지 않기 위해,
    페널티가 없는 CLEAR로 처리한다.
    """
    if environment.get("weatherLookupStatus") not in (None, "SUCCESS"):
        return "CLEAR"
    return environment.get("weatherCondition", "CLEAR")


def _error(request, code, message, retryable=False):
    request_id = request.get("requestId") if isinstance(request, dict) else None
    return {
        "requestId": request_id,
        "error": {"code": code, "message": message, "retryable": retryable},
    }
