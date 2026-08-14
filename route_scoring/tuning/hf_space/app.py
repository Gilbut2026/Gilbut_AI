from __future__ import annotations

import hashlib
import json
import os
import urllib.request
from collections import defaultdict
from copy import deepcopy
from datetime import datetime, timezone

import gradio as gr

from storage import ResponseStore

CASE_SOURCE_URL = os.getenv(
    "CASE_SOURCE_URL",
    "https://raw.githubusercontent.com/Gilbut2026/Gilbut_AI/012ece7d5b40686ae683648d761d5e68e4159ed1/"
    "route_scoring/tuning/data/cases.json",
)

NUM_EVALUATORS = int(os.getenv("NUM_EVALUATORS", "9"))
RATINGS_PER_CASE = int(os.getenv("RATINGS_PER_CASE", "3"))
ASSIGNMENT_SEED = os.getenv("ASSIGNMENT_SEED", "gilbut-slope-v2")

SLOPE_LEVELS = ("AVAILABLE", "SLIGHTLY_DIFFICULT", "DIFFICULT")
SLOPE_LABELS = {
    "AVAILABLE": "오르막길 이동 가능",
    "SLIGHTLY_DIFFICULT": "오르막길 이동이 조금 어려움",
    "DIFFICULT": "오르막길 이동이 어려움",
}
WALK_LABELS = {
    "UNABLE_TO_WALK": "보행 어려움",
    "WITHIN_10_MINUTES": "10분 이내",
    "WITHIN_20_MINUTES": "20분 이내",
    "OVER_30_MINUTES": "30분 이상",
}
STAIR_LABELS = {
    "AVAILABLE": "이용 가능",
    "SLIGHTLY_DIFFICULT": "조금 어려움",
    "DIFFICULT": "어려움",
}
TRANSFER_LABELS = {
    "AVAILABLE": "환승 가능",
    "FEWER_PREFERRED": "적은 환승 선호",
    "AVOID_PREFERRED": "환승 회피 선호",
}
AID_LABELS = {
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

STORE = ResponseStore()


def _load_source_cases():
    with urllib.request.urlopen(CASE_SOURCE_URL, timeout=15) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("cases"), list):
        raise RuntimeError("cases.json 형식이 올바르지 않습니다.")
    return payload["cases"]


def _attach_slope_levels(source_cases):
    grouped = defaultdict(list)
    for case in source_cases:
        grouped[str(case.get("tradeoffType") or "UNKNOWN")].append(case)

    assigned = []
    for group_index, tradeoff_type in enumerate(sorted(grouped)):
        group = sorted(grouped[tradeoff_type], key=lambda x: str(x.get("caseId")))
        for index, source in enumerate(group):
            case = deepcopy(source)
            source_case_id = str(case["caseId"])
            slope_level = SLOPE_LEVELS[(index + group_index) % len(SLOPE_LEVELS)]
            case["userContext"]["slopeLevel"] = slope_level
            case["sourceCaseId"] = source_case_id
            case["caseId"] = f"slv2_{source_case_id}"
            assigned.append(case)
    return sorted(assigned, key=lambda x: x["caseId"])


def _stable_rank(case_id):
    value = hashlib.sha256(f"{ASSIGNMENT_SEED}:{case_id}".encode()).hexdigest()
    return int(value, 16)


CASES = _attach_slope_levels(_load_source_cases())
CASES_BY_ID = {case["caseId"]: case for case in CASES}
ORDERED_CASE_IDS = [c["caseId"] for c in sorted(CASES, key=lambda c: _stable_rank(c["caseId"]))]


def _validate_assignment_config():
    if NUM_EVALUATORS < RATINGS_PER_CASE:
        raise RuntimeError("NUM_EVALUATORS는 RATINGS_PER_CASE보다 크거나 같아야 합니다.")
    if NUM_EVALUATORS % RATINGS_PER_CASE != 0:
        raise RuntimeError("기본 균형 배정을 위해 NUM_EVALUATORS는 RATINGS_PER_CASE의 배수여야 합니다.")


_validate_assignment_config()


def assigned_case_ids(slot_number: int):
    slot = slot_number - 1
    assigned = []
    step = NUM_EVALUATORS // RATINGS_PER_CASE
    for index, case_id in enumerate(ORDERED_CASE_IDS):
        raters = {
            (index + offset * step) % NUM_EVALUATORS
            for offset in range(RATINGS_PER_CASE)
        }
        if slot in raters:
            assigned.append(case_id)
    return assigned


def display_swapped(slot_number: int, case_id: str):
    digest = hashlib.sha256(f"{slot_number}:{case_id}:ab".encode()).hexdigest()
    return int(digest, 16) % 2 == 1


def render_user(case):
    u = case["userContext"]
    return (
        f"### 사용자 정보\n"
        f"- **한 번에 걸을 수 있는 시간:** {WALK_LABELS.get(u.get('walkingDuration'), u.get('walkingDuration'))}\n"
        f"- **계단 이용:** {STAIR_LABELS.get(u.get('stairLevel'), u.get('stairLevel'))}\n"
        f"- **오르막길 이동:** {SLOPE_LABELS[u['slopeLevel']]}\n"
        f"- **환승 선호:** {TRANSFER_LABELS.get(u.get('transferLevel'), u.get('transferLevel'))}\n"
        f"- **보조기구:** {AID_LABELS.get(u.get('mobilityAid'), u.get('mobilityAid'))}\n"
        f"- **현재 날씨:** {WEATHER_LABELS.get(case.get('weatherCondition'), case.get('weatherCondition'))}"
    )


def render_route(route, title):
    unknown = "있음" if route.get("obstacleUnknown") else "없음"
    return (
        f"### {title}\n"
        f"- 도보 시간: **{route.get('walkTimeMin')}분**\n"
        f"- 도보 거리: **{route.get('walkDistanceM')}m**\n"
        f"- 환승 횟수: **{route.get('transferCount')}회**\n"
        f"- 계단: **{route.get('stairCount')}개**\n"
        f"- 육교: **{route.get('overpassCount')}개**\n"
        f"- 지하보도: **{route.get('underpassCount')}개**\n"
        f"- 최대 오르막 경사: **{route.get('maxUphillGradePercent')}%**\n"
        f"- 최대 내리막 경사: **{route.get('maxDownhillGradePercent')}%**\n"
        f"- 장애물 조회 실패: **{unknown}**"
    )


def _progress_text(done, total):
    pct = 100 if total == 0 else round(done / total * 100)
    return f"**진행률: {done}/{total} ({pct}%)**"


def start_session(slot_value, alias):
    if slot_value is None:
        raise gr.Error("평가자 슬롯을 선택해주세요.")
    slot = int(slot_value)
    evaluator = f"slot_{slot:02d}"
    case_ids = assigned_case_ids(slot)
    completed = STORE.completed_case_ids(evaluator)
    remaining = [cid for cid in case_ids if cid not in completed]
    state = {
        "slot": slot,
        "evaluator": evaluator,
        "alias": (alias or "").strip(),
        "case_ids": case_ids,
        "remaining": remaining,
    }
    return (*render_next(state), state)


def render_next(state):
    if not state:
        return (
            "### 평가자 슬롯을 선택한 뒤 시작해주세요.",
            "",
            "",
            "",
            gr.update(interactive=False),
            gr.update(interactive=False),
            gr.update(interactive=False),
        )

    total = len(state["case_ids"])
    done = total - len(state["remaining"])
    if not state["remaining"]:
        return (
            f"## ✅ 평가 완료\n{state['evaluator']}의 배정된 {total}개 평가가 모두 저장되었습니다.",
            "",
            "",
            _progress_text(total, total),
            gr.update(interactive=False),
            gr.update(interactive=False),
            gr.update(interactive=False),
        )

    case_id = state["remaining"][0]
    case = CASES_BY_ID[case_id]
    swapped = display_swapped(state["slot"], case_id)
    left = case["routeB"] if swapped else case["routeA"]
    right = case["routeA"] if swapped else case["routeB"]
    state["current_case_id"] = case_id
    state["swapped"] = swapped

    return (
        render_user(case),
        render_route(left, "왼쪽 경로"),
        render_route(right, "오른쪽 경로"),
        _progress_text(done, total),
        gr.update(interactive=True),
        gr.update(interactive=True),
        gr.update(interactive=True),
    )


def submit_choice(display_choice, state):
    if not state or not state.get("current_case_id"):
        raise gr.Error("먼저 평가를 시작해주세요.")

    case_id = state["current_case_id"]
    case = CASES_BY_ID[case_id]
    swapped = bool(state["swapped"])

    if display_choice == "SIMILAR":
        canonical = "SIMILAR"
    elif display_choice == "LEFT":
        canonical = "B" if swapped else "A"
    elif display_choice == "RIGHT":
        canonical = "A" if swapped else "B"
    else:
        raise gr.Error("올바르지 않은 선택입니다.")

    payload = {
        "evaluator": state["evaluator"],
        "evaluatorAlias": state.get("alias") or "",
        "caseId": case_id,
        "sourceCaseId": case.get("sourceCaseId"),
        "choice": canonical,
        "displayChoice": display_choice,
        "displaySwapped": swapped,
        "slopeLevel": case["userContext"]["slopeLevel"],
        "tradeoffType": case.get("tradeoffType"),
        "answeredAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    STORE.save_response(payload)

    state["remaining"] = [cid for cid in state["remaining"] if cid != case_id]
    return (*render_next(state), state)


with gr.Blocks(title="길벗 경로 선호도 평가") as demo:
    gr.Markdown(
        """
# 길벗 경로 선호도 평가
아래 사용자의 입장에서 **더 편한 경로**를 선택해주세요.

- 본인의 개인 취향이 아니라, 화면에 제시된 사용자의 이동 조건을 기준으로 판단합니다.
- 특히 **오르막길 이동 정도(slopeLevel)** 를 반드시 함께 고려해주세요.
- 좌/우 경로 위치는 평가자·문항별로 자동 섞여 위치 편향을 줄입니다.
- 선택 결과는 즉시 저장되며, 같은 슬롯으로 다시 접속하면 완료한 문항은 건너뜁니다.
"""
    )

    with gr.Row():
        slot = gr.Dropdown(
            choices=[str(i) for i in range(1, NUM_EVALUATORS + 1)],
            label="평가자 슬롯",
            info="팀 내에서 서로 다른 슬롯을 하나씩 사용해주세요.",
        )
        alias = gr.Textbox(
            label="이름/별칭 (선택)",
            placeholder="예: 정민",
        )
        start = gr.Button("평가 시작", variant="primary")

    progress = gr.Markdown()
    user_card = gr.Markdown("### 평가자 슬롯을 선택한 뒤 시작해주세요.")

    with gr.Row():
        left_card = gr.Markdown()
        right_card = gr.Markdown()

    gr.Markdown("### 이 사용자에게 어느 경로가 더 편한가요?")
    with gr.Row():
        left_btn = gr.Button("⬅️ 왼쪽 경로", interactive=False)
        similar_btn = gr.Button("비슷함", interactive=False)
        right_btn = gr.Button("오른쪽 경로 ➡️", interactive=False)

    state = gr.State()

    start.click(
        start_session,
        inputs=[slot, alias],
        outputs=[user_card, left_card, right_card, progress, left_btn, similar_btn, right_btn, state],
    )
    left_btn.click(
        lambda s: submit_choice("LEFT", s),
        inputs=[state],
        outputs=[user_card, left_card, right_card, progress, left_btn, similar_btn, right_btn, state],
    )
    similar_btn.click(
        lambda s: submit_choice("SIMILAR", s),
        inputs=[state],
        outputs=[user_card, left_card, right_card, progress, left_btn, similar_btn, right_btn, state],
    )
    right_btn.click(
        lambda s: submit_choice("RIGHT", s),
        inputs=[state],
        outputs=[user_card, left_card, right_card, progress, left_btn, similar_btn, right_btn, state],
    )

if __name__ == "__main__":
    demo.launch()
