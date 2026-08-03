"""스코어링 정책 검증.

실행:
    python -m pytest tests/ -v
    또는
    python tests/test_scoring.py
"""

import copy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scoring import score_routes


def make_request(**overrides):
    """기본 요청에 필요한 부분만 덮어쓴다."""
    request = copy.deepcopy(BASE_REQUEST)
    for key, value in overrides.items():
        request["userContext"][key] = value
    return request


def make_candidate(candidate_id, rank, walk_sec, walk_m, transfers, signals):
    return {
        "candidateId": candidate_id,
        "providerRank": rank,
        "metrics": {
            "totalWalkTimeSec": walk_sec,
            "totalWalkDistanceM": walk_m,
            "transferCount": transfers,
        },
        "walkSegments": [
            {
                "walkSegmentId": f"walk-{candidate_id}",
                "role": "LAST_STOP_TO_DESTINATION",
                "segmentScope": "EXTERNAL_WALK",
                "accessibilitySignals": signals,
            }
        ],
    }


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


BASE_REQUEST = {
    "requestId": "req-test",
    "userContext": {
        "walkingTolerance": "AROUND_20_MIN",
        "stairAbility": "DIFFICULT",
        "transferAbility": "AVAILABLE",
        "assistiveDevice": "CANE",
    },
    "environment": {"weatherCondition": "CLEAR", "weatherLookupStatus": "SUCCESS"},
    "candidates": [
        make_candidate("clean", 1, 600, 700, 1, signals()),
        make_candidate("stairs", 2, 480, 520, 0, signals(stair=2, underpass=1)),
        make_candidate("unknown", 3, 540, 640, 1, signals(unknown=True)),
    ],
}


def find(results, candidate_id):
    return next(r for r in results if r["candidateId"] == candidate_id)


# ---------------------------------------------------------------------------
# 점수 정책
# ---------------------------------------------------------------------------

def test_계단_어려운_사용자는_계단_없는_경로가_1위():
    result = score_routes(make_request(stairAbility="DIFFICULT"))
    assert find(result["results"], "clean")["rank"] == 1


def test_계단_가능한_사용자는_빠른_경로가_1위():
    """계단을 이용할 수 있다면 계단이 있어도 도보가 짧은 쪽이 유리해야 한다."""
    result = score_routes(make_request(stairAbility="AVAILABLE"))
    assert find(result["results"], "stairs")["rank"] == 1


def test_폭설이면_도보가_긴_경로가_불리해진다():
    request = copy.deepcopy(BASE_REQUEST)
    request["userContext"]["stairAbility"] = "AVAILABLE"
    request["environment"]["weatherCondition"] = "HEAVY_SNOW"

    clear = score_routes(BASE_REQUEST)
    snow = score_routes(request)

    assert find(snow["results"], "clean")["score"] < find(clear["results"], "clean")["score"]


# ---------------------------------------------------------------------------
# Hard Filter
# ---------------------------------------------------------------------------

def test_휠체어는_조회_실패_구간도_제외한다():
    """계단이 있으면 통행 자체가 불가능하므로, 확실하지 않으면 안전하게 제외한다."""
    result = score_routes(make_request(assistiveDevice="WHEELCHAIR", stairAbility="UNAVAILABLE"))

    assert find(result["results"], "stairs")["status"] == "FILTERED"
    assert find(result["results"], "unknown")["status"] == "FILTERED"
    assert find(result["results"], "clean")["status"] == "SCORED"


def test_계단_불가_사용자는_조회_실패_구간을_남긴다():
    """조회 실패만으로 후보를 잃지 않도록, 확인된 장애물만 제외한다."""
    result = score_routes(make_request(stairAbility="UNAVAILABLE", assistiveDevice="CANE"))

    assert find(result["results"], "stairs")["status"] == "FILTERED"
    assert find(result["results"], "unknown")["status"] == "SCORED"


def test_보행_가능_시간을_크게_초과하면_제외한다():
    request = make_request(walkingTolerance="WITHIN_10_MIN")
    request["candidates"].append(
        make_candidate("too-far", 4, 1500, 2000, 0, signals())
    )

    result = score_routes(request)
    assert find(result["results"], "too-far")["filterCodes"] == ["WALK_TIME_EXCEEDED"]


def test_역_내부_계단은_장애물로_보지_않는다():
    """수원 관내 전 지하철역에 엘리베이터가 있어 역 내부 이동은 가능하다고 가정한다."""
    request = make_request(assistiveDevice="WHEELCHAIR", stairAbility="UNAVAILABLE")
    request["candidates"] = [make_candidate("internal", 1, 600, 700, 1, signals(stair=3))]
    request["candidates"][0]["walkSegments"][0]["segmentScope"] = "STATION_INTERNAL"

    result = score_routes(request)
    assert find(result["results"], "internal")["status"] == "SCORED"


# ---------------------------------------------------------------------------
# DRT 판단
# ---------------------------------------------------------------------------

def test_휠체어는_똑버스_대신_콜택시를_안내한다():
    result = score_routes(make_request(assistiveDevice="WHEELCHAIR"))
    assert result["drtDecision"]["show"] is False
    assert result["drtDecision"]["taxiGuide"] is True


def test_보조기구_사용자는_똑버스를_옵션으로_표시한다():
    result = score_routes(make_request(assistiveDevice="CANE"))
    assert result["drtDecision"]["show"] is True


def test_악천후면_똑버스를_우선_노출한다():
    request = copy.deepcopy(BASE_REQUEST)
    request["environment"]["weatherCondition"] = "HEAVY_SNOW"

    result = score_routes(request)
    assert result["drtDecision"]["priority"] is True
    assert "SEVERE_WEATHER" in result["drtDecision"]["reasonCodes"]


def test_통과_경로가_없으면_똑버스만_안내한다():
    request = make_request(stairAbility="UNAVAILABLE")
    request["candidates"] = [make_candidate("only", 1, 600, 700, 1, signals(stair=1))]

    result = score_routes(request)
    assert result["drtDecision"]["priority"] is True
    assert "NO_PASSABLE_ROUTE" in result["drtDecision"]["reasonCodes"]


# ---------------------------------------------------------------------------
# 입력 검증
# ---------------------------------------------------------------------------

def test_중복된_candidateId는_오류를_반환한다():
    request = copy.deepcopy(BASE_REQUEST)
    request["candidates"].append(request["candidates"][0])
    result = score_routes(request)
    assert result["error"]["code"] == "VALIDATION_ERROR"


def test_날씨_조회_실패시_페널티를_적용하지_않는다():
    request = copy.deepcopy(BASE_REQUEST)
    request["environment"] = {"weatherCondition": "HEAVY_SNOW", "weatherLookupStatus": "FAILED"}
    failed = score_routes(request)
    clear = score_routes(BASE_REQUEST)
    assert find(failed["results"], "clean")["score"] == find(clear["results"], "clean")["score"]


def test_필수_metric이_없으면_오류를_반환한다():
    request = copy.deepcopy(BASE_REQUEST)
    del request["candidates"][0]["metrics"]["totalWalkTimeSec"]
    result = score_routes(request)
    assert result["error"]["code"] == "VALIDATION_ERROR"


def test_candidateId가_없으면_오류를_반환한다():
    request = copy.deepcopy(BASE_REQUEST)
    request["candidates"][0]["candidateId"] = None
    result = score_routes(request)
    assert result["error"]["code"] == "VALIDATION_ERROR"


def test_candidateId가_빈_문자열이면_오류를_반환한다():
    request = copy.deepcopy(BASE_REQUEST)
    request["candidates"][0]["candidateId"] = ""
    result = score_routes(request)
    assert result["error"]["code"] == "VALIDATION_ERROR"


def test_음수_metric은_오류를_반환한다():
    request = copy.deepcopy(BASE_REQUEST)
    request["candidates"][0]["metrics"]["totalWalkTimeSec"] = -100
    result = score_routes(request)
    assert result["error"]["code"] == "VALIDATION_ERROR"


def test_빈_후보_목록도_처리한다():
    request = copy.deepcopy(BASE_REQUEST)
    request["candidates"] = []
    result = score_routes(request)
    assert result["results"] == []
    assert "NO_PASSABLE_ROUTE" in result["drtDecision"]["reasonCodes"]


# ---------------------------------------------------------------------------
# 장애물 집계
# ---------------------------------------------------------------------------

def test_조회_실패는_구간당_한_번만_감점한다():
    request = make_request(stairAbility="DIFFICULT", assistiveDevice="NONE")
    request["candidates"] = [
        make_candidate("unknown", 1, 600, 700, 0, signals(unknown=True)),
        make_candidate("one-stair", 2, 600, 700, 0, signals(stair=1)),
    ]
    result = score_routes(request)
    assert find(result["results"], "unknown")["score"] == find(result["results"], "one-stair")["score"]


def test_육교는_계단보다_낮게_감점한다():
    request = make_request(stairAbility="DIFFICULT", assistiveDevice="NONE")
    request["candidates"] = [
        make_candidate("stair", 1, 600, 700, 0, signals(stair=1)),
        make_candidate("overpass", 2, 600, 700, 0, signals(overpass=1)),
    ]
    result = score_routes(request)
    assert find(result["results"], "overpass")["score"] > find(result["results"], "stair")["score"]


def test_역_내부와_외부_구간이_섞여도_외부만_계산한다():
    request = make_request(stairAbility="DIFFICULT", assistiveDevice="NONE")
    candidate = make_candidate("mixed", 1, 600, 700, 0, signals(stair=1))
    candidate["walkSegments"].append({
        "walkSegmentId": "internal",
        "segmentScope": "STATION_INTERNAL",
        "accessibilitySignals": signals(stair=5),
    })
    request["candidates"] = [candidate]

    only_external = make_request(stairAbility="DIFFICULT", assistiveDevice="NONE")
    only_external["candidates"] = [make_candidate("mixed", 1, 600, 700, 0, signals(stair=1))]

    mixed_result = score_routes(request)
    plain_result = score_routes(only_external)
    assert find(mixed_result["results"], "mixed")["score"] == find(plain_result["results"], "mixed")["score"]


# ---------------------------------------------------------------------------
# 정규화 경계값
# ---------------------------------------------------------------------------

def test_도보시간_경계값_정규화():
    from scoring.score import normalize
    from scoring.policy import WALK_TIME_BINS
    assert normalize(10, WALK_TIME_BINS) == 0.0
    assert normalize(10.1, WALK_TIME_BINS) == 0.3
    assert normalize(20, WALK_TIME_BINS) == 0.3
    assert normalize(30, WALK_TIME_BINS) == 0.8
    assert normalize(31, WALK_TIME_BINS) == 1.0


def test_도보거리_경계값_정규화():
    from scoring.score import normalize
    from scoring.policy import WALK_DISTANCE_BINS
    assert normalize(400, WALK_DISTANCE_BINS) == 0.0
    assert normalize(401, WALK_DISTANCE_BINS) == 0.3
    assert normalize(1200, WALK_DISTANCE_BINS) == 0.8
    assert normalize(1201, WALK_DISTANCE_BINS) == 1.0


# ---------------------------------------------------------------------------
# 응답 형식
# ---------------------------------------------------------------------------

def test_점수_상세를_함께_반환한다():
    result = score_routes(BASE_REQUEST)
    breakdown = find(result["results"], "clean")["scoreBreakdown"]
    assert set(breakdown) == {
        "walkTimePenalty", "walkDistancePenalty",
        "obstaclePenalty", "transferPenalty", "weatherPenalty",
    }


def test_완전_동점이면_providerRank를_따른다():
    request = make_request(assistiveDevice="NONE")
    request["candidates"] = [
        make_candidate("second", 2, 600, 700, 1, signals()),
        make_candidate("first", 1, 600, 700, 1, signals()),
    ]
    result = score_routes(request)
    assert find(result["results"], "first")["rank"] == 1


# ---------------------------------------------------------------------------
# 장애물 종류 구분
# ---------------------------------------------------------------------------

def test_육교만_있으면_휠체어_경로를_제외하지_않는다():
    request = make_request(assistiveDevice="WHEELCHAIR", stairAbility="UNAVAILABLE")
    request["candidates"] = [make_candidate("overpass", 1, 600, 700, 0, signals(overpass=1))]
    result = score_routes(request)
    assert find(result["results"], "overpass")["status"] == "SCORED"


def test_계단이_있으면_휠체어_경로를_제외한다():
    request = make_request(assistiveDevice="WHEELCHAIR", stairAbility="UNAVAILABLE")
    request["candidates"] = [make_candidate("stair", 1, 600, 700, 0, signals(stair=1))]
    result = score_routes(request)
    assert find(result["results"], "stair")["filterCodes"] == ["WHEELCHAIR_WITH_EXTERNAL_STAIR"]


def test_육교는_제외하지_않되_감점한다():
    request = make_request(assistiveDevice="NONE", stairAbility="DIFFICULT")
    request["candidates"] = [
        make_candidate("plain", 1, 600, 700, 0, signals()),
        make_candidate("overpass", 2, 600, 700, 0, signals(overpass=1)),
    ]
    result = score_routes(request)
    assert find(result["results"], "overpass")["score"] < find(result["results"], "plain")["score"]


def test_PRESENT인데_count가_0이면_최소_1개로_본다():
    request = make_request(assistiveDevice="WHEELCHAIR", stairAbility="UNAVAILABLE")
    request["candidates"] = [make_candidate("mismatch", 1, 600, 700, 0, {
        "stair": {"state": "PRESENT", "count": 0},
        "overpass": {"state": "ABSENT", "count": 0},
        "underpass": {"state": "ABSENT", "count": 0},
    })]
    result = score_routes(request)
    assert find(result["results"], "mismatch")["status"] == "FILTERED"


# ---------------------------------------------------------------------------
# 잘못된 입력
# ---------------------------------------------------------------------------

def test_request가_None이면_오류를_반환한다():
    assert score_routes(None)["error"]["code"] == "VALIDATION_ERROR"


def test_후보가_None이면_오류를_반환한다():
    request = copy.deepcopy(BASE_REQUEST)
    request["candidates"] = [None]
    assert score_routes(request)["error"]["code"] == "VALIDATION_ERROR"


def test_불리언은_숫자로_보지_않는다():
    request = copy.deepcopy(BASE_REQUEST)
    request["candidates"][0]["metrics"]["totalWalkTimeSec"] = True
    assert score_routes(request)["error"]["code"] == "VALIDATION_ERROR"


def test_NaN은_오류를_반환한다():
    request = copy.deepcopy(BASE_REQUEST)
    request["candidates"][0]["metrics"]["totalWalkTimeSec"] = float("nan")
    assert score_routes(request)["error"]["code"] == "VALIDATION_ERROR"


def test_환승_횟수는_정수여야_한다():
    request = copy.deepcopy(BASE_REQUEST)
    request["candidates"][0]["metrics"]["transferCount"] = 1.5
    assert score_routes(request)["error"]["code"] == "VALIDATION_ERROR"


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failures = []

    for test in tests:
        try:
            test()
            print(f"  PASS  {test.__name__}")
        except AssertionError as exc:
            failures.append(test.__name__)
            print(f"  FAIL  {test.__name__}  {exc}")

    print()
    print(f"{len(tests) - len(failures)}/{len(tests)} passed")
    sys.exit(1 if failures else 0)
