#!/usr/bin/env python3
from __future__ import annotations

import csv
import importlib.util
import json
import math
import random
from collections import Counter, defaultdict
from copy import deepcopy
from pathlib import Path

HERE = Path(__file__).resolve().parent
CASES_PATH = HERE / "data" / "cases.json"
LABEL_JSON_PATH = HERE / "data" / "human_labels.json"
LABEL_DIR = HERE / "data" / "labels"
RESULT_DIR = HERE / "results"
POLICY_PATH = HERE.parent / "scoring" / "policy.py"

SEED = 20260813
REGULARIZATION = 0.015
REPORT_TIE_MARGIN = 0.35


def load_policy():
    spec = importlib.util.spec_from_file_location("gilbut_policy", POLICY_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def load_cases():
    with CASES_PATH.open("r", encoding="utf-8") as f:
        payload = json.load(f)
    return {c["caseId"]: c for c in payload["cases"]}


def load_labels():
    """Label Studio JSON 또는 labels/*.csv를 읽어 표준 라벨로 변환."""
    choice_map = {
        "경로 A": "A",
        "경로 B": "B",
        "비슷함": "SIMILAR",
        "A": "A",
        "B": "B",
        "SIMILAR": "SIMILAR",
    }

    labels = []
    source_files = []

    # 1) Label Studio JSON export 우선
    if LABEL_JSON_PATH.exists():
        with LABEL_JSON_PATH.open("r", encoding="utf-8") as f:
            tasks = json.load(f)

        if not isinstance(tasks, list):
            raise ValueError("human_labels.json은 Label Studio JSON export(list) 형식이어야 합니다.")

        for task in tasks:
            case_id = str((task.get("data") or {}).get("case_id") or "").strip()
            annotations = task.get("annotations") or []
            if not case_id or not annotations:
                continue

            # 한 evaluator 기준으로 가장 최근 annotation 사용
            ann = max(
                annotations,
                key=lambda x: x.get("updated_at") or x.get("created_at") or "",
            )

            raw_choice = None
            for result in ann.get("result") or []:
                if result.get("from_name") != "preferred_route":
                    continue
                choices = (result.get("value") or {}).get("choices") or []
                if choices:
                    raw_choice = choices[0]
                    break

            choice = choice_map.get(str(raw_choice).strip())
            if choice not in {"A", "B", "SIMILAR"}:
                continue

            labels.append(
                {
                    "evaluator": str(ann.get("completed_by") or "label_studio_user"),
                    "caseId": case_id,
                    "choice": choice,
                    "answeredAt": ann.get("updated_at") or ann.get("created_at") or "",
                }
            )

        source_files.append(LABEL_JSON_PATH)

    # 2) JSON이 없으면 기존 CSV 방식도 지원
    else:
        files = sorted(LABEL_DIR.glob("*.csv"))
        if not files:
            raise FileNotFoundError(
                "평가 결과가 없습니다.\n"
                f"- Label Studio JSON: {LABEL_JSON_PATH}\n"
                f"- 또는 CSV 폴더: {LABEL_DIR}"
            )

        latest = {}
        for path in files:
            with path.open("r", encoding="utf-8-sig", newline="") as f:
                for row in csv.DictReader(f):
                    evaluator = (row.get("evaluator") or "").strip()
                    case_id = (row.get("caseId") or "").strip()
                    choice = choice_map.get((row.get("choice") or "").strip().upper())
                    answered = (row.get("answeredAt") or "").strip()
                    if not evaluator or not case_id or choice not in {"A", "B", "SIMILAR"}:
                        continue
                    key = (evaluator, case_id)
                    prev = latest.get(key)
                    if prev is None or answered >= prev["answeredAt"]:
                        latest[key] = {
                            "evaluator": evaluator,
                            "caseId": case_id,
                            "choice": choice,
                            "answeredAt": answered,
                        }
        labels = list(latest.values())
        source_files.extend(files)

    return labels, source_files


def normalize(value, bins):
    for upper, norm in bins:
        if value <= upper:
            return float(norm)
    return float(bins[-1][1])


def slope_sensitivity(user):
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


def band_penalty(grade, mod_th, steep_th, mod_pen, steep_pen):
    if grade >= steep_th:
        return steep_pen
    if grade >= mod_th:
        return mod_pen
    return 0.0


def route_penalty(case, route, w, policy):
    user = case["userContext"]
    walk_time = normalize(route["walkTimeMin"], policy.WALK_TIME_BINS)
    walk_dist = normalize(route["walkDistanceM"], policy.WALK_DISTANCE_BINS)

    walk_weight = w["walk"][user["walkingDuration"]]
    transfer_weight = w["transfer"][user["transferLevel"]]
    stair_weight = w["stair"].get(user["stairLevel"], 0.0)
    aid = w["aid_multiplier"] if user["mobilityAid"] != "NOT_USED" else 1.0

    obstacle_base = (
        route["stairCount"] * w["obstacle"]["stair"]
        + route["overpassCount"] * w["obstacle"]["overpass"]
        + route["underpassCount"] * w["obstacle"]["underpass"]
    )
    if route.get("obstacleUnknown"):
        obstacle_base += w["unknown_segment_penalty"]

    weather = w["weather"][case["weatherCondition"]] * walk_dist

    up = float(route["maxUphillGradePercent"])
    down = float(route["maxDownhillGradePercent"])
    slope_base = max(
        band_penalty(
            up,
            policy.SLOPE_MODERATE_GRADE_PERCENT,
            policy.SLOPE_STEEP_GRADE_PERCENT,
            w["slope_up"][0],
            w["slope_up"][1],
        ),
        band_penalty(
            down,
            policy.SLOPE_MODERATE_GRADE_PERCENT,
            policy.SLOPE_STEEP_GRADE_PERCENT,
            w["slope_down"][0],
            w["slope_down"][1],
        ),
    )
    sensitivity = w["slope_sensitivity"][slope_sensitivity(user)]
    slope_penalty = min(policy.SLOPE_MAX_PENALTY, slope_base * sensitivity)

    return (
        walk_weight * walk_time
        + walk_weight * walk_dist
        + stair_weight * aid * obstacle_base
        + transfer_weight * route["transferCount"]
        + weather
        + slope_penalty
    )


def current_weights(policy):
    return {
        "walk": dict(policy.WALK_WEIGHT),
        "stair": dict(policy.STAIR_WEIGHT),
        "transfer": dict(policy.TRANSFER_WEIGHT),
        "weather": dict(policy.WEATHER_PENALTY),
        "aid_multiplier": float(policy.AID_MULTIPLIER),
        "obstacle": dict(policy.OBSTACLE_WEIGHT),
        "unknown_segment_penalty": float(policy.UNKNOWN_SEGMENT_PENALTY),
        "slope_up": [
            float(policy.SLOPE_UPHILL_PENALTY[1]),
            float(policy.SLOPE_UPHILL_PENALTY[2]),
        ],
        "slope_down": [
            float(policy.SLOPE_DOWNHILL_PENALTY[1]),
            float(policy.SLOPE_DOWNHILL_PENALTY[2]),
        ],
        "slope_sensitivity": {
            "LOW": 1.0,
            "MEDIUM": float(policy.SLOPE_SENSITIVITY_MULTIPLIER["MEDIUM"]),
            "HIGH": float(policy.SLOPE_SENSITIVITY_MULTIPLIER["HIGH"]),
        },
    }


def get_param(w, name):
    parts = name.split(".")
    obj = w
    for p in parts[:-1]:
        obj = obj[int(p)] if p.isdigit() else obj[p]
    last = parts[-1]
    return obj[int(last)] if last.isdigit() else obj[last]


def set_param(w, name, value):
    parts = name.split(".")
    obj = w
    for p in parts[:-1]:
        obj = obj[int(p)] if p.isdigit() else obj[p]
    last = parts[-1]
    if last.isdigit():
        obj[int(last)] = float(value)
    else:
        obj[last] = float(value)


def parameter_bounds(base, params):
    bounds = {}
    for p in params:
        b = max(get_param(base, p), 1e-6)
        lo = max(0.02, b * 0.40)
        hi = b * 2.50
        if p == "aid_multiplier":
            lo, hi = 1.0, 3.0
        elif p == "slope_sensitivity.MEDIUM":
            lo, hi = 1.0, 3.0
        elif p == "slope_sensitivity.HIGH":
            lo, hi = 1.0, 4.0
        bounds[p] = (lo, hi)
    return bounds


def clamp_param(value, bound):
    lo, hi = bound
    return min(max(value, lo), hi)


def tunable_parameters(cases):
    params = [
        "walk.WITHIN_10_MINUTES",
        "walk.WITHIN_20_MINUTES",
        "walk.OVER_30_MINUTES",
        "stair.AVAILABLE",
        "stair.SLIGHTLY_DIFFICULT",
        "transfer.AVAILABLE",
        "transfer.FEWER_PREFERRED",
        "transfer.AVOID_PREFERRED",
        "weather.RAIN",
        "weather.HEAVY_RAIN",
        "weather.SNOW",
        "weather.HEAVY_SNOW",
        "weather.HEAT",
        "weather.SEVERE_HEAT",
        "weather.COLD",
        "weather.SEVERE_COLD",
        "aid_multiplier",
        "obstacle.stair",
        "obstacle.overpass",
        "obstacle.underpass",
        "slope_up.0",
        "slope_up.1",
        "slope_down.0",
        "slope_down.1",
        "slope_sensitivity.MEDIUM",
        "slope_sensitivity.HIGH",
    ]
    weather_seen = {c["weatherCondition"] for c in cases.values()}
    params = [
        p for p in params
        if not p.startswith("weather.") or p.split(".", 1)[1] in weather_seen
    ]
    has_unknown = any(
        c[r].get("obstacleUnknown")
        for c in cases.values()
        for r in ("routeA", "routeB")
    )
    if has_unknown:
        params.append("unknown_segment_penalty")
    return params


def valid_constraints(w):
    return (
        w["walk"]["WITHIN_10_MINUTES"] >= w["walk"]["WITHIN_20_MINUTES"] >= w["walk"]["OVER_30_MINUTES"] > 0
        and w["stair"]["SLIGHTLY_DIFFICULT"] >= w["stair"]["AVAILABLE"] > 0
        and 0 < w["transfer"]["AVAILABLE"] <= w["transfer"]["FEWER_PREFERRED"] <= w["transfer"]["AVOID_PREFERRED"]
        and w["weather"]["HEAVY_RAIN"] >= w["weather"]["RAIN"] >= 0
        and w["weather"]["HEAVY_SNOW"] >= w["weather"]["SNOW"] >= 0
        and w["weather"]["SEVERE_HEAT"] >= w["weather"]["HEAT"] >= 0
        and w["weather"]["SEVERE_COLD"] >= w["weather"]["COLD"] >= 0
        and w["obstacle"]["stair"] >= w["obstacle"]["overpass"] > 0
        and w["obstacle"]["stair"] >= w["obstacle"]["underpass"] > 0
        and w["aid_multiplier"] >= 1.0
        and 0 <= w["slope_up"][0] <= w["slope_up"][1]
        and 0 <= w["slope_down"][0] <= w["slope_down"][1]
        and 1.0 <= w["slope_sensitivity"]["MEDIUM"] <= w["slope_sensitivity"]["HIGH"]
    )


def softplus(x):
    if x > 30:
        return x
    if x < -30:
        return math.exp(x)
    return math.log1p(math.exp(x))


def sample_loss(delta, choice):
    if choice == "A":
        return softplus(-delta)
    if choice == "B":
        return softplus(delta)
    return 0.55 * abs(delta)


def regularization(w, base, params):
    vals = []
    for p in params:
        x = max(get_param(w, p), 1e-6)
        b = max(get_param(base, p), 1e-6)
        vals.append(math.log(x / b) ** 2)
    return sum(vals) / max(len(vals), 1)


def objective(w, base, params, case_ids, labels_by_case, cases, policy):
    losses = []
    for cid in case_ids:
        c = cases[cid]
        pa = route_penalty(c, c["routeA"], w, policy)
        pb = route_penalty(c, c["routeB"], w, policy)
        delta = pb - pa
        for choice in labels_by_case.get(cid, []):
            losses.append(sample_loss(delta, choice))
    if not losses:
        return float("inf")
    return sum(losses) / len(losses) + REGULARIZATION * regularization(w, base, params)


def split_cases(cases, labeled_ids):
    by_type = defaultdict(list)
    for cid in sorted(labeled_ids):
        by_type[cases[cid]["tradeoffType"]].append(cid)
    train, test = [], []
    for ids in by_type.values():
        for i, cid in enumerate(ids, start=1):
            (test if i % 5 == 0 else train).append(cid)
    return train, test


def coordinate_search(base, params, train_ids, labels_by_case, cases, policy):
    rng = random.Random(SEED)
    bounds = parameter_bounds(base, params)
    rounds = [
        [0.55, 0.70, 0.85, 1.0, 1.15, 1.35, 1.65],
        [0.78, 0.88, 0.95, 1.0, 1.05, 1.12, 1.25],
        [0.90, 0.95, 0.98, 1.0, 1.02, 1.05, 1.10],
    ]

    starts = [deepcopy(base)]
    for _ in range(5):
        s = deepcopy(base)
        for p in params:
            proposal = get_param(s, p) * math.exp(rng.uniform(-0.25, 0.25))
            set_param(s, p, clamp_param(proposal, bounds[p]))
        if valid_constraints(s):
            starts.append(s)

    best_global = deepcopy(base)
    best_global_loss = objective(best_global, base, params, train_ids, labels_by_case, cases, policy)

    for start in starts:
        w = deepcopy(start)
        if not valid_constraints(w):
            continue
        for factors in rounds:
            order = list(params)
            rng.shuffle(order)
            for _ in range(3):
                changed = False
                for p in order:
                    current = get_param(w, p)
                    current_loss = objective(w, base, params, train_ids, labels_by_case, cases, policy)
                    local_best, local_loss = deepcopy(w), current_loss
                    for factor in factors:
                        cand = deepcopy(w)
                        set_param(cand, p, clamp_param(current * factor, bounds[p]))
                        if not valid_constraints(cand):
                            continue
                        loss = objective(cand, base, params, train_ids, labels_by_case, cases, policy)
                        if loss < local_loss:
                            local_best, local_loss = cand, loss
                    if local_loss + 1e-10 < current_loss:
                        w = local_best
                        changed = True
                if not changed:
                    break
        loss = objective(w, base, params, train_ids, labels_by_case, cases, policy)
        if loss < best_global_loss:
            best_global, best_global_loss = w, loss

    return best_global, best_global_loss


def majority_choice(choices):
    counts = Counter(choices)
    if not counts:
        return None
    top = counts.most_common()
    if len(top) >= 2 and top[0][1] == top[1][1]:
        return None
    return top[0][0]


def model_choice(case, w, policy):
    pa = route_penalty(case, case["routeA"], w, policy)
    pb = route_penalty(case, case["routeB"], w, policy)
    if abs(pa - pb) <= REPORT_TIE_MARGIN:
        return "SIMILAR"
    return "A" if pa < pb else "B"


def metrics(case_ids, labels_by_case, cases, w, policy):
    total = correct = pref_total = pref_correct = 0
    by_type = defaultdict(lambda: [0, 0])
    for cid in case_ids:
        human = majority_choice(labels_by_case.get(cid, []))
        if human is None:
            continue
        pred = model_choice(cases[cid], w, policy)
        total += 1
        correct += pred == human
        typ = cases[cid]["tradeoffType"]
        by_type[typ][1] += 1
        by_type[typ][0] += pred == human
        if human in {"A", "B"}:
            pref_total += 1
            pref_correct += pred == human
    return {
        "accuracy": correct / total if total else 0.0,
        "n": total,
        "preference_accuracy": pref_correct / pref_total if pref_total else 0.0,
        "preference_n": pref_total,
        "by_type": {
            k: {"accuracy": c / n if n else 0.0, "n": n}
            for k, (c, n) in sorted(by_type.items())
        },
    }


def make_policy_snippet(w):
    lines = []
    lines.append("# === Human-preference tuned weights ===\n")
    lines.append("WALK_WEIGHT = " + repr(w["walk"]) + "\n")
    lines.append("STAIR_WEIGHT = " + repr(w["stair"]) + "\n")
    lines.append("TRANSFER_WEIGHT = " + repr(w["transfer"]) + "\n")
    lines.append("WEATHER_PENALTY = " + repr(w["weather"]) + "\n")
    lines.append(f'AID_MULTIPLIER = {w["aid_multiplier"]:.4f}\n')
    lines.append("OBSTACLE_WEIGHT = " + repr(w["obstacle"]) + "\n")
    lines.append(f'UNKNOWN_SEGMENT_PENALTY = {w["unknown_segment_penalty"]:.4f}\n')
    lines.append(
        f'SLOPE_UPHILL_PENALTY = (0.0, {w["slope_up"][0]:.4f}, {w["slope_up"][1]:.4f})\n'
    )
    lines.append(
        f'SLOPE_DOWNHILL_PENALTY = (0.0, {w["slope_down"][0]:.4f}, {w["slope_down"][1]:.4f})\n'
    )
    lines.append(
        "SLOPE_SENSITIVITY_MULTIPLIER = "
        + repr(
            {
                "LOW": 1.0,
                "MEDIUM": round(w["slope_sensitivity"]["MEDIUM"], 4),
                "HIGH": round(w["slope_sensitivity"]["HIGH"], 4),
            }
        )
        + "\n"
    )
    return "\n".join(lines)


def main():
    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    policy = load_policy()
    cases = load_cases()
    labels, files = load_labels()
    labels = [x for x in labels if x["caseId"] in cases]

    # 추출된 human label을 사람이 확인할 수 있도록 별도 CSV로 저장
    with (RESULT_DIR / "human_labels.csv").open(
        "w", encoding="utf-8-sig", newline=""
    ) as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["evaluator", "caseId", "choice", "answeredAt"],
        )
        writer.writeheader()
        writer.writerows(sorted(labels, key=lambda x: x["caseId"]))

    evaluators = sorted({x["evaluator"] for x in labels})
    labels_by_case = defaultdict(list)
    for x in labels:
        labels_by_case[x["caseId"]].append(x["choice"])

    labeled_ids = sorted(labels_by_case)
    if len(labeled_ids) < 30:
        raise RuntimeError(
            f"라벨이 있는 case가 {len(labeled_ids)}개뿐입니다. 최소 30개 이상 평가 후 실행하세요."
        )

    train_ids, test_ids = split_cases(cases, labeled_ids)
    base = current_weights(policy)
    params = tunable_parameters(cases)

    tuned, train_loss = coordinate_search(
        base, params, train_ids, labels_by_case, cases, policy
    )

    base_train = metrics(train_ids, labels_by_case, cases, base, policy)
    base_test = metrics(test_ids, labels_by_case, cases, base, policy)
    tuned_train = metrics(train_ids, labels_by_case, cases, tuned, policy)
    tuned_test = metrics(test_ids, labels_by_case, cases, tuned, policy)

    comparable = unanimous = 0
    for choices in labels_by_case.values():
        if len(choices) >= 2:
            comparable += 1
            unanimous += len(set(choices)) == 1

    result = {
        "evaluation": {
            "evaluators": evaluators,
            "label_files": [p.name for p in files],
            "labeled_cases": len(labeled_ids),
            "train_cases": len(train_ids),
            "test_cases": len(test_ids),
            "unanimous_rate_on_multi_rater_cases": (
                unanimous / comparable if comparable else None
            ),
        },
        "tuning": {
            "regularization": REGULARIZATION,
            "report_tie_margin": REPORT_TIE_MARGIN,
            "tuned_parameters": params,
            "train_objective": train_loss,
        },
        "baseline_metrics": {"train": base_train, "test": base_test},
        "tuned_metrics": {"train": tuned_train, "test": tuned_test},
        "baseline_weights": base,
        "best_weights": tuned,
        "notes": [
            "CLEAR=0과 slope LOW sensitivity=1.0은 상대척도 기준점으로 고정.",
            "Hard Filter, DRT 임계값, 정규화 bins, 경사 구간 경계값은 이번 weight tuning 대상이 아님.",
            "데이터에 UNKNOWN 장애물 신호가 없으면 UNKNOWN_SEGMENT_PENALTY는 기존값 유지.",
            "최종 채택은 train이 아니라 test 성능을 우선 확인.",
        ],
    }

    with (RESULT_DIR / "best_weights.json").open("w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    (RESULT_DIR / "policy_tuned_snippet.py").write_text(
        make_policy_snippet(tuned), encoding="utf-8"
    )

    report = [
        "=== 길벗 가중치 튜닝 결과 ===",
        f"평가자: {', '.join(evaluators)}",
        f"라벨 case: {len(labeled_ids)} / {len(cases)}",
        f"train/test: {len(train_ids)} / {len(test_ids)}",
    ]
    if comparable:
        report.append(f"다중 평가 case 만장일치율: {unanimous/comparable:.1%}")
    report += [
        "",
        "[Baseline]",
        f"train agreement: {base_train['accuracy']:.1%} (A/B only {base_train['preference_accuracy']:.1%})",
        f"test  agreement: {base_test['accuracy']:.1%} (A/B only {base_test['preference_accuracy']:.1%})",
        "",
        "[Tuned]",
        f"train agreement: {tuned_train['accuracy']:.1%} (A/B only {tuned_train['preference_accuracy']:.1%})",
        f"test  agreement: {tuned_test['accuracy']:.1%} (A/B only {tuned_test['preference_accuracy']:.1%})",
        "",
        "[Tuned test by trade-off]",
    ]
    for typ, m in tuned_test["by_type"].items():
        report.append(f"{typ}: {m['accuracy']:.1%} (n={m['n']})")
    report += [
        "",
        "최종 가중치: results/policy_tuned_snippet.py",
        "상세 결과: results/best_weights.json",
        "※ tuned test 성능이 baseline test보다 좋아졌는지 확인한 뒤 policy.py에 반영.",
    ]

    text = "\n".join(report) + "\n"
    (RESULT_DIR / "tuning_report.txt").write_text(text, encoding="utf-8")
    print(text)
    print(f"결과 저장: {RESULT_DIR}")


if __name__ == "__main__":
    main()