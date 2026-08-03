"""입력 검증.

값이 누락된 후보를 그대로 계산하면 0으로 처리되어, 오히려 가장 편한 경로로
1위가 되는 문제가 생긴다. 계산 전에 필수 필드와 값의 형태를 확인한다.
"""

import math

REQUIRED_METRICS = ("totalWalkTimeSec", "totalWalkDistanceM", "transferCount")
INTEGER_METRICS = ("transferCount",)


def validate(request):
    """오류 메시지를 반환한다. 문제가 없으면 None."""
    if not isinstance(request, dict):
        return "request must be an object"

    candidates = request.get("candidates")
    if not isinstance(candidates, list):
        return "candidates must be a list"

    seen = set()

    for index, candidate in enumerate(candidates):
        if not isinstance(candidate, dict):
            return f"candidate must be an object at index {index}"

        candidate_id = candidate.get("candidateId")

        if not candidate_id or not isinstance(candidate_id, str):
            return f"candidateId is required at index {index}"

        if candidate_id in seen:
            return f"duplicate candidateId: {candidate_id}"

        seen.add(candidate_id)

        error = _validate_metrics(candidate, candidate_id)
        if error:
            return error

    return None


def _validate_metrics(candidate, candidate_id):
    metrics = candidate.get("metrics")

    if not isinstance(metrics, dict):
        return f"metrics is required: {candidate_id}"

    for field in REQUIRED_METRICS:
        if field not in metrics:
            return f"{field} is required: {candidate_id}"

        value = metrics[field]

        # bool은 int의 하위 타입이라 별도로 걸러야 한다
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return f"{field} must be a number: {candidate_id}"

        if not math.isfinite(value):
            return f"{field} must be a finite number: {candidate_id}"

        if value < 0:
            return f"{field} must not be negative: {candidate_id}"

        if field in INTEGER_METRICS and not isinstance(value, int):
            return f"{field} must be an integer: {candidate_id}"

    return None
