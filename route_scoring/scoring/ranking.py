"""접근성 점수 계산 이후의 경로 정렬 정책.

Score Function 자체는 수정하지 않는다. ``breakdown.total``은 기존 접근성
점수 그대로 사용하고, 접근성 점수가 충분히 비슷한 후보끼리만
``totalTimeSec``가 짧은 경로를 우선한다.
"""

from __future__ import annotations


# 초기 정책값. 실제 경로 데이터 검증 후 조정할 수 있도록 score/policy 가중치와
# 분리해 ranking 모듈에만 둔다.
ACCESSIBILITY_NEAR_TIE_THRESHOLD = 0.3
SCORE_GAP_EPSILON = 1e-9


def order(entries, threshold: float = ACCESSIBILITY_NEAR_TIE_THRESHOLD):
    """접근성 score를 보존하면서 near-tie 그룹만 총 이동시간으로 정렬한다.

    아직 정렬되지 않은 후보 중 가장 높은 접근성 점수를 anchor로 잡고,
    anchor와의 score 차이가 threshold 이하인 후보만 같은 그룹으로 묶는다.
    pairwise 비교로 생길 수 있는 비추이성(A-B near, B-C near, A-C not near)을
    피하기 위한 방식이다.
    """
    if threshold < 0:
        raise ValueError("near-tie threshold must not be negative")

    remaining = sorted(
        entries,
        key=lambda entry: -entry["breakdown"].total,
    )
    ordered = []

    while remaining:
        best_score = remaining[0]["breakdown"].total
        split_index = len(remaining)

        for index, entry in enumerate(remaining):
            score_gap = best_score - entry["breakdown"].total
            if score_gap > threshold + SCORE_GAP_EPSILON:
                split_index = index
                break

        near_tie_group = remaining[:split_index]
        remaining = remaining[split_index:]

        near_tie_group.sort(key=_near_tie_sort_key)
        ordered.extend(near_tie_group)

    return ordered


def _near_tie_sort_key(entry):
    """near-tie 그룹 안에서는 totalTimeSec가 짧은 후보를 우선한다.

    totalTimeSec까지 같으면 기존 정렬의 후순위 기준을 유지한다.
    """
    candidate = entry["candidate"]
    metrics = candidate["metrics"]

    return (
        metrics["totalTimeSec"],
        entry["obstacles"].weight,
        metrics["totalWalkTimeSec"],
        metrics["transferCount"],
        candidate.get("providerRank", float("inf")),
    )
