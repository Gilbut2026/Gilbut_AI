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

        route_id = candidate.get("routeId")

        if not route_id or not isinstance(route_id, str):
            return f"routeId is required at index {index}"

        if route_id in seen:
            return f"duplicate routeId: {route_id}"

        seen.add(route_id)

        error = _validate_metrics(candidate, route_id)
        if error:
            return error

    return None


def _validate_metrics(candidate, route_id):
    metrics = candidate.get("metrics")

    if not isinstance(metrics, dict):
        return f"metrics is required: {route_id}"

    for field in REQUIRED_METRICS:
        if field not in metrics:
            return f"{field} is required: {route_id}"

        value = metrics[field]

        # bool은 int의 하위 타입이라 별도로 걸러야 한다
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return f"{field} must be a number: {route_id}"

        if not math.isfinite(value):
            return f"{field} must be a finite number: {route_id}"

        if value < 0:
            return f"{field} must not be negative: {route_id}"

        if field in INTEGER_METRICS and not isinstance(value, int):
            return f"{field} must be an integer: {route_id}"

    return None
