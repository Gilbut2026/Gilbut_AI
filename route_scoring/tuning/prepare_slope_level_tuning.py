#!/usr/bin/env python3
"""기존 synthetic trade-off case를 slopeLevel 포함 재라벨링 세트로 변환한다.

기존 human label은 평가 화면에 slopeLevel이 없던 상태에서 수집됐으므로 재사용하지
않는다. 이 스크립트는 경로/날씨 조건은 유지하되 새로운 case id와 명시적인
userContext.slopeLevel을 부여하고, slopeLevel이 보이는 Label Studio task를 생성한다.
"""

from __future__ import annotations

import json
from collections import defaultdict
from copy import deepcopy
from pathlib import Path

HERE = Path(__file__).resolve().parent
SOURCE_CASES_PATH = HERE / "data" / "cases.json"
OUTPUT_CASES_PATH = HERE / "data" / "cases_slope_level.json"
OUTPUT_TASKS_PATH = HERE / "label_studio" / "label_studio_tasks_slope_level.json"

SLOPE_LEVELS = (
    "AVAILABLE",
    "SLIGHTLY_DIFFICULT",
    "DIFFICULT",
)

SLOPE_LEVEL_LABELS = {
    "AVAILABLE": "오르막길 이동 가능",
    "SLIGHTLY_DIFFICULT": "오르막길 이동이 조금 어려움",
    "DIFFICULT": "오르막길 이동이 어려움",
}

WALKING_DURATION_LABELS = {
    "UNABLE_TO_WALK": "보행 어려움",
    "WITHIN_10_MINUTES": "10분 이내",
    "WITHIN_20_MINUTES": "20분 이내",
    "OVER_30_MINUTES": "30분 이상",
}

STAIR_LEVEL_LABELS = {
    "AVAILABLE": "이용 가능",
    "SLIGHTLY_DIFFICULT": "조금 어려움",
    "DIFFICULT": "어려움",
}

TRANSFER_LEVEL_LABELS = {
    "AVAILABLE": "환승 가능",
    "FEWER_PREFERRED": "적은 환승 선호",
    "AVOID_PREFERRED": "환승 회피 선호",
}

MOBILITY_AID_LABELS = {
    "NOT_USED": "사용 안 함",
    "CANE_OR_WALKER": "지팡이 / 보행기",
    "WHEELCHAIR": "휠체어",
}

WEATHER_LABELS = {
    "CLEAR": "맑음",
    "RAIN": "비",
    "HEAVY_RAIN": "강한 비",
    "SNOW": "눈",
    "HEAVY_SNOW": "강한 눈",
    "HEAT": "더움",
    "SEVERE_HEAT": "매우 더움",
    "COLD": "추움",
    "SEVERE_COLD": "매우 추움",
}


def load_source_cases():
    with SOURCE_CASES_PATH.open("r", encoding="utf-8") as f:
        payload = json.load(f)
    if not isinstance(payload, dict) or not isinstance(payload.get("cases"), list):
        raise ValueError("cases.json 형식이 올바르지 않습니다.")
    return payload


def assign_slope_levels(cases):
    """trade-off 종류별로 slopeLevel이 균형 있게 나타나도록 결정적으로 배정한다."""
    grouped = defaultdict(list)
    for case in cases:
        grouped[str(case.get("tradeoffType") or "UNKNOWN")].append(case)

    assigned = []
    for group_index, tradeoff_type in enumerate(sorted(grouped)):
        group = sorted(grouped[tradeoff_type], key=lambda x: str(x.get("caseId")))
        for index, source in enumerate(group):
            case = deepcopy(source)
            source_case_id = str(case.get("caseId") or f"case_{index + 1:03d}")
            slope_level = SLOPE_LEVELS[(index + group_index) % len(SLOPE_LEVELS)]

            user = case.get("userContext")
            if not isinstance(user, dict):
                raise ValueError(f"{source_case_id}: userContext가 없습니다.")
            user["slopeLevel"] = slope_level

            case["sourceCaseId"] = source_case_id
            case["caseId"] = f"slv2_{source_case_id}"
            assigned.append(case)

    return sorted(assigned, key=lambda x: str(x["caseId"]))


def user_text(case):
    user = case["userContext"]
    slope_level = user["slopeLevel"]
    return "\n".join(
        [
            f"한 번에 걸을 수 있는 시간: {WALKING_DURATION_LABELS.get(user.get('walkingDuration'), user.get('walkingDuration'))}",
            f"계단 이용: {STAIR_LEVEL_LABELS.get(user.get('stairLevel'), user.get('stairLevel'))}",
            f"오르막길 이동: {SLOPE_LEVEL_LABELS[slope_level]}",
            f"환승 선호: {TRANSFER_LEVEL_LABELS.get(user.get('transferLevel'), user.get('transferLevel'))}",
            f"보조기구: {MOBILITY_AID_LABELS.get(user.get('mobilityAid'), user.get('mobilityAid'))}",
            f"현재 날씨: {WEATHER_LABELS.get(case.get('weatherCondition'), case.get('weatherCondition'))}",
        ]
    )


def route_text(route):
    return "\n".join(
        [
            f"도보 시간: {route.get('walkTimeMin')}분",
            f"도보 거리: {route.get('walkDistanceM')}m",
            f"환승 횟수: {route.get('transferCount')}회",
            f"계단: {route.get('stairCount')}개",
            f"육교: {route.get('overpassCount')}개",
            f"지하보도: {route.get('underpassCount')}개",
            f"최대 오르막 경사: {route.get('maxUphillGradePercent')}%",
            f"최대 내리막 경사: {route.get('maxDownhillGradePercent')}%",
            f"장애물 조회 실패: {'있음' if route.get('obstacleUnknown') else '없음'}",
        ]
    )


def build_tasks(cases):
    tasks = []
    for case in cases:
        tasks.append(
            {
                "data": {
                    "case_id": case["caseId"],
                    "source_case_id": case.get("sourceCaseId"),
                    "slope_level": case["userContext"]["slopeLevel"],
                    "user": user_text(case),
                    "route_a": route_text(case["routeA"]),
                    "route_b": route_text(case["routeB"]),
                    "tradeoff_type": case.get("tradeoffType"),
                }
            }
        )
    return tasks


def main():
    payload = load_source_cases()
    cases = assign_slope_levels(payload["cases"])

    output = deepcopy(payload)
    metadata = dict(output.get("metadata") or {})
    metadata.update(
        {
            "datasetName": "gilbut_route_weight_tuning_cases_v2_slope_level",
            "caseCount": len(cases),
            "labelIncluded": False,
            "purpose": "Human preference labeling with explicit onboarding slopeLevel",
            "generationMethod": (
                "Existing controlled synthetic trade-off routes with stratified explicit "
                "slopeLevel; requires fresh human labels"
            ),
            "sourceDataset": metadata.get("datasetName"),
        }
    )
    output["metadata"] = metadata
    output["cases"] = cases

    OUTPUT_CASES_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_TASKS_PATH.parent.mkdir(parents=True, exist_ok=True)

    with OUTPUT_CASES_PATH.open("w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    with OUTPUT_TASKS_PATH.open("w", encoding="utf-8") as f:
        json.dump(build_tasks(cases), f, ensure_ascii=False, indent=2)

    counts = {level: 0 for level in SLOPE_LEVELS}
    for case in cases:
        counts[case["userContext"]["slopeLevel"]] += 1

    print(f"cases: {len(cases)} -> {OUTPUT_CASES_PATH}")
    print(f"tasks: {len(cases)} -> {OUTPUT_TASKS_PATH}")
    print("slopeLevel distribution:", counts)
    print("기존 human_labels.json은 재사용하지 말고 새 task를 다시 라벨링하세요.")


if __name__ == "__main__":
    main()
