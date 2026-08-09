"""경로 접근성 스코어링 진입점.

사용법:
    from scoring import score_routes

    result = score_routes(request)
"""

from . import drt, filters, obstacles, policy, score, validation


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
            })
        else:
            passed.append({"candidate": candidate, "obstacles": found})

    return passed, filtered


def _rank(passed, user, weather):
    """점수를 계산하고 순위를 매긴다.

    Returns:
        (정렬된 항목 목록, 응답용 결과 목록)
    """
    entries = [
        {
            **entry,
            "breakdown": score.calculate(entry["candidate"], entry["obstacles"], user, weather),
        }
        for entry in passed
    ]

    entries.sort(key=_sort_key)

    results = [
        {
            "routeId": entry["candidate"]["routeId"],
            "status": policy.SCORED,
            "score": round(entry["breakdown"].total, 2),
            "rank": rank,
            "filterCodes": [],
            "scoreBreakdown": entry["breakdown"].as_dict(),
        }
        for rank, entry in enumerate(entries, start=1)
    ]

    return entries, results


def _sort_key(entry):
    """점수 내림차순. 동점이면 장애물 → 도보시간 → 환승 → 원본 순서.

    장애물을 첫 번째 기준으로 둔 것은, 이 서비스의 기준이 "빠른 길"이 아니라
    "편한 길"이기 때문이다.
    """
    metrics = entry["candidate"]["metrics"]
    return (
        -entry["breakdown"].total,
        entry["obstacles"].weight,
        metrics["totalWalkTimeSec"],
        metrics["transferCount"],
        entry["candidate"].get("providerRank", float("inf")),
    )


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
