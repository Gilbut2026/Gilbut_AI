"""현재 BE 계약 기준의 경로 스코어링 회귀 테스트."""

import copy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scoring import score_routes


def signals(stair=0, overpass=0, underpass=0, unknown=False):
    if unknown:
        return {
            kind: {"state": "UNKNOWN", "count": None}
            for kind in ("stair", "overpass", "underpass")
        }
    return {
        "stair": {"state": "PRESENT" if stair else "ABSENT", "count": stair},
        "overpass": {"state": "PRESENT" if overpass else "ABSENT", "count": overpass},
        "underpass": {"state": "PRESENT" if underpass else "ABSENT", "count": underpass},
    }


def make_candidate(
    route_id,
    rank,
    walk_sec,
    walk_m,
    transfers,
    accessibility_signals,
    total_time_sec=None,
):
    return {
        "routeId": route_id,
        "providerRank": rank,
        "metrics": {
            "totalTimeSec": walk_sec if total_time_sec is None else total_time_sec,
            "totalWalkTimeSec": walk_sec,
            "totalWalkDistanceM": walk_m,
            "transferCount": transfers,
        },
        "walkSegments": [
            {
                "walkSegmentId": f"walk-{route_id}",
                "role": "LAST_STOP_TO_DESTINATION",
                "segmentScope": "EXTERNAL_WALK",
                "accessibilitySignals": accessibility_signals,
            }
        ],
    }


BASE_REQUEST = {
    "requestId": "req-test",
    "userContext": {
        "walkingDuration": "WITHIN_20_MINUTES",
        "stairLevel": "DIFFICULT",
        "slopeLevel": "SLIGHTLY_DIFFICULT",
        "restStopPreference": "REQUIRED",
        "transferLevel": "AVAILABLE",
        "mobilityAid": "CANE_OR_WALKER",
    },
    "environment": {"weatherCondition": "CLEAR", "weatherLookupStatus": "SUCCESS"},
    "candidates": [
        make_candidate("clean", 1, 600, 700, 1, signals(), total_time_sec=1200),
        make_candidate("stairs", 2, 480, 520, 0, signals(stair=2, underpass=1), total_time_sec=900),
        make_candidate("unknown", 3, 540, 640, 1, signals(unknown=True), total_time_sec=1100),
    ],
}


def make_request(**overrides):
    request = copy.deepcopy(BASE_REQUEST)
    request["userContext"].update(overrides)
    return request


def find(results, route_id):
    return next(result for result in results if result["routeId"] == route_id)


def test_계단_어려운_사용자는_확인된_계단_경로를_제외한다():
    result = score_routes(make_request(stairLevel="DIFFICULT"))
    assert find(result["results"], "stairs")["status"] == "FILTERED"
    assert find(result["results"], "clean")["status"] == "SCORED"


def test_계단_가능한_사용자는_계단_경로도_점수화한다():
    result = score_routes(make_request(stairLevel="AVAILABLE"))
    assert find(result["results"], "stairs")["status"] == "SCORED"


def test_폭설이면_도보가_긴_경로가_불리해진다():
    request = copy.deepcopy(BASE_REQUEST)
    request["userContext"]["stairLevel"] = "AVAILABLE"
    request["environment"]["weatherCondition"] = "HEAVY_SNOW"
    clear = score_routes({**copy.deepcopy(BASE_REQUEST), "userContext": {**BASE_REQUEST["userContext"], "stairLevel": "AVAILABLE"}})
    snow = score_routes(request)
    assert find(snow["results"], "clean")["score"] < find(clear["results"], "clean")["score"]


def test_휠체어는_조회_실패_구간도_제외한다():
    result = score_routes(make_request(mobilityAid="WHEELCHAIR", stairLevel="DIFFICULT"))
    assert find(result["results"], "stairs")["status"] == "FILTERED"
    assert find(result["results"], "unknown")["status"] == "FILTERED"


def test_계단_어려움_보행보조기구_사용자는_unknown을_남긴다():
    result = score_routes(make_request(stairLevel="DIFFICULT", mobilityAid="CANE_OR_WALKER"))
    assert find(result["results"], "stairs")["status"] == "FILTERED"
    assert find(result["results"], "unknown")["status"] == "SCORED"


def test_보행_가능_시간을_크게_초과하면_제외한다():
    request = make_request(walkingDuration="WITHIN_10_MINUTES")
    request["candidates"].append(
        make_candidate("too-far", 4, 1500, 2000, 0, signals(), total_time_sec=1500)
    )
    assert find(score_routes(request)["results"], "too-far")["filterCodes"] == ["WALK_TIME_EXCEEDED"]


def test_역_내부_계단은_외부_계단_filter에_포함하지_않는다():
    request = make_request(mobilityAid="WHEELCHAIR", stairLevel="DIFFICULT")
    request["candidates"] = [make_candidate("internal", 1, 600, 700, 1, signals(stair=3))]
    request["candidates"][0]["walkSegments"][0]["segmentScope"] = "STATION_INTERNAL"
    assert find(score_routes(request)["results"], "internal")["status"] == "SCORED"


def test_휠체어는_똑버스_대신_콜택시를_안내한다():
    result = score_routes(make_request(mobilityAid="WHEELCHAIR"))
    assert result["drtDecision"]["show"] is False
    assert result["drtDecision"]["taxiGuide"] is True


def test_보조기구_사용자는_똑버스를_옵션으로_표시한다():
    result = score_routes(make_request(mobilityAid="CANE_OR_WALKER"))
    assert result["drtDecision"]["show"] is True


def test_악천후면_똑버스를_우선_노출한다():
    request = copy.deepcopy(BASE_REQUEST)
    request["environment"]["weatherCondition"] = "HEAVY_SNOW"
    result = score_routes(request)
    assert result["drtDecision"]["priority"] is True
    assert "SEVERE_WEATHER" in result["drtDecision"]["reasonCodes"]


def test_중복_routeId는_오류를_반환한다():
    request = copy.deepcopy(BASE_REQUEST)
    request["candidates"].append(copy.deepcopy(request["candidates"][0]))
    assert score_routes(request)["error"]["code"] == "VALIDATION_ERROR"


def test_날씨_조회_실패시_페널티를_적용하지_않는다():
    request = copy.deepcopy(BASE_REQUEST)
    request["environment"] = {"weatherCondition": "HEAVY_SNOW", "weatherLookupStatus": "FAILED"}
    failed = score_routes(request)
    clear = score_routes(BASE_REQUEST)
    assert find(failed["results"], "clean")["score"] == find(clear["results"], "clean")["score"]


def test_totalTimeSec이_없으면_오류를_반환한다():
    request = copy.deepcopy(BASE_REQUEST)
    del request["candidates"][0]["metrics"]["totalTimeSec"]
    assert score_routes(request)["error"]["code"] == "VALIDATION_ERROR"


def test_routeId가_없으면_오류를_반환한다():
    request = copy.deepcopy(BASE_REQUEST)
    request["candidates"][0]["routeId"] = None
    assert score_routes(request)["error"]["code"] == "VALIDATION_ERROR"


def test_음수_metric은_오류를_반환한다():
    request = copy.deepcopy(BASE_REQUEST)
    request["candidates"][0]["metrics"]["totalTimeSec"] = -1
    assert score_routes(request)["error"]["code"] == "VALIDATION_ERROR"


def test_불리언은_숫자로_보지_않는다():
    request = copy.deepcopy(BASE_REQUEST)
    request["candidates"][0]["metrics"]["totalTimeSec"] = True
    assert score_routes(request)["error"]["code"] == "VALIDATION_ERROR"


def test_정수_metric에_float가_오면_오류를_반환한다():
    request = copy.deepcopy(BASE_REQUEST)
    request["candidates"][0]["metrics"]["totalWalkDistanceM"] = 700.5
    assert score_routes(request)["error"]["code"] == "VALIDATION_ERROR"


def test_빈_후보_목록도_처리한다():
    request = copy.deepcopy(BASE_REQUEST)
    request["candidates"] = []
    result = score_routes(request)
    assert result["results"] == []
    assert "NO_PASSABLE_ROUTE" in result["drtDecision"]["reasonCodes"]


def test_조회_실패는_구간당_한_번만_감점한다():
    request = make_request(stairLevel="SLIGHTLY_DIFFICULT", mobilityAid="NOT_USED")
    request["candidates"] = [
        make_candidate("unknown", 1, 600, 700, 0, signals(unknown=True)),
        make_candidate("one-stair", 2, 600, 700, 0, signals(stair=1)),
    ]
    result = score_routes(request)
    assert find(result["results"], "unknown")["score"] == find(result["results"], "one-stair")["score"]


def test_육교는_계단보다_낮게_감점한다():
    request = make_request(stairLevel="SLIGHTLY_DIFFICULT", mobilityAid="NOT_USED")
    request["candidates"] = [
        make_candidate("stair", 1, 600, 700, 0, signals(stair=1)),
        make_candidate("overpass", 2, 600, 700, 0, signals(overpass=1)),
    ]
    result = score_routes(request)
    assert find(result["results"], "overpass")["score"] > find(result["results"], "stair")["score"]


def test_도보시간_경계값_정규화():
    from scoring.policy import WALK_TIME_BINS
    from scoring.score import normalize
    assert normalize(10, WALK_TIME_BINS) == 0.0
    assert normalize(10.1, WALK_TIME_BINS) == 0.3
    assert normalize(20, WALK_TIME_BINS) == 0.3
    assert normalize(30, WALK_TIME_BINS) == 0.8
    assert normalize(31, WALK_TIME_BINS) == 1.0


def test_도보거리_경계값_정규화():
    from scoring.policy import WALK_DISTANCE_BINS
    from scoring.score import normalize
    assert normalize(400, WALK_DISTANCE_BINS) == 0.0
    assert normalize(401, WALK_DISTANCE_BINS) == 0.3
    assert normalize(1200, WALK_DISTANCE_BINS) == 0.8
    assert normalize(1201, WALK_DISTANCE_BINS) == 1.0


def test_점수_상세는_BE_response_contract와_일치한다():
    breakdown = find(score_routes(BASE_REQUEST)["results"], "clean")["scoreBreakdown"]
    assert set(breakdown) == {
        "walkTimePenalty",
        "walkDistancePenalty",
        "obstaclePenalty",
        "transferPenalty",
        "weatherPenalty",
        "slopePenalty",
    }


def test_완전_동점이면_providerRank를_따른다():
    request = make_request(mobilityAid="NOT_USED", stairLevel="AVAILABLE")
    request["candidates"] = [
        make_candidate("second", 2, 600, 700, 1, signals(), total_time_sec=1200),
        make_candidate("first", 1, 600, 700, 1, signals(), total_time_sec=1200),
    ]
    assert find(score_routes(request)["results"], "first")["rank"] == 1


def test_request가_None이면_오류를_반환한다():
    assert score_routes(None)["error"]["code"] == "VALIDATION_ERROR"
