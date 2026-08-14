#!/usr/bin/env python3
"""slopeLevel을 포함한 v2 preference data로 weight tuning을 실행한다.

서비스의 경사 개인화와 동일하게 userContext.slopeLevel을 직접 사용한다.
기존 120개 라벨은 slopeLevel이 보이지 않은 상태에서 수집됐으므로 이 실행 경로에서는
재사용하지 않는다. 먼저 prepare_slope_level_tuning.py로 v2 case/task를 만들고,
새 Label Studio 결과를 human_labels_slope_level.json으로 저장한 뒤 실행한다.
"""

from __future__ import annotations

from pathlib import Path

import optimize_weights as tuning

HERE = Path(__file__).resolve().parent
CASES_PATH = HERE / "data" / "cases_slope_level.json"
LABEL_JSON_PATH = HERE / "data" / "human_labels_slope_level.json"
LABEL_DIR = HERE / "data" / "labels_slope_level"
RESULT_DIR = HERE / "results_slope_level"

VALID_SLOPE_LEVELS = {
    "AVAILABLE",
    "SLIGHTLY_DIFFICULT",
    "DIFFICULT",
}


def slope_sensitivity(user):
    """서비스 코드와 동일하게 slopeLevel로 경사 민감도를 결정한다."""
    if not isinstance(user, dict):
        raise ValueError("userContext가 올바르지 않습니다.")

    slope_level = user.get("slopeLevel")
    if slope_level == "DIFFICULT":
        return "HIGH"
    if slope_level == "SLIGHTLY_DIFFICULT":
        return "MEDIUM"
    if slope_level == "AVAILABLE":
        return "LOW"

    raise ValueError(
        "weight tuning v2의 모든 case에는 slopeLevel이 필요합니다: "
        f"{slope_level!r}"
    )


def validate_cases(cases):
    missing = []
    for case_id, case in cases.items():
        user = case.get("userContext") if isinstance(case, dict) else None
        slope_level = user.get("slopeLevel") if isinstance(user, dict) else None
        if slope_level not in VALID_SLOPE_LEVELS:
            missing.append(case_id)

    if missing:
        preview = ", ".join(missing[:10])
        raise RuntimeError(
            "slopeLevel이 없는 v2 case가 있습니다. "
            f"prepare_slope_level_tuning.py를 다시 실행하세요: {preview}"
        )


def main():
    if not CASES_PATH.exists():
        raise FileNotFoundError(
            f"{CASES_PATH}가 없습니다. 먼저 `python prepare_slope_level_tuning.py`를 실행하세요."
        )

    if not LABEL_JSON_PATH.exists() and not LABEL_DIR.exists():
        raise FileNotFoundError(
            "slopeLevel이 표시된 새 라벨이 없습니다. "
            "label_studio/label_studio_tasks_slope_level.json을 새로 라벨링한 뒤 "
            "data/human_labels_slope_level.json으로 export하세요."
        )

    # optimize_weights.py의 입출력 경로를 v2 dataset으로 전환한다.
    tuning.CASES_PATH = CASES_PATH
    tuning.LABEL_JSON_PATH = LABEL_JSON_PATH
    tuning.LABEL_DIR = LABEL_DIR
    tuning.RESULT_DIR = RESULT_DIR

    cases = tuning.load_cases()
    validate_cases(cases)

    # production scoring과 같은 slopeLevel 의미를 사용한다.
    tuning.slope_sensitivity = slope_sensitivity

    # 기존 tunable_parameters를 그대로 사용하므로 slope_up/down 및
    # slope_sensitivity.MEDIUM/HIGH도 실제 튜닝 대상에 포함된다.
    tuning.main()


if __name__ == "__main__":
    main()
