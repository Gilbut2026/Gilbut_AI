"""DRT(똑버스) 및 교통약자 콜택시 안내 판단.

수원 관내 똑버스에는 휠체어 탑승 가능 차량이 없다(경기교통공사 확인).
따라서 휠체어 사용자에게는 똑버스 대신 교통약자 콜택시를 안내한다.
"""

from . import policy


def decide(user, weather, top_candidate, passable_count):
    """DRT 판단 결과를 반환한다.

    Returns:
        show: 똑버스를 옵션으로 표시할지
        priority: 똑버스를 1순위로 노출할지
        taxiGuide: 교통약자 콜택시를 안내할지
        reasonCodes: 판단 근거
        basedOnCandidateId: 판단 기준이 된 후보
    """
    based_on = top_candidate.get("candidateId") if top_candidate else None
    device = user.get("assistiveDevice", "NONE")
    no_route = passable_count == 0

    if device == "WHEELCHAIR":
        reasons = [policy.NO_PASSABLE_ROUTE] if no_route else [policy.ASSISTIVE_DEVICE]
        return _result(False, False, True, reasons, based_on)

    reasons = []
    priority = no_route

    if no_route:
        reasons.append(policy.NO_PASSABLE_ROUTE)

    if device != "NONE":
        reasons.append(policy.ASSISTIVE_DEVICE)

    if top_candidate:
        metrics = top_candidate.get("metrics", {})

        if metrics.get("totalWalkDistanceM", 0) >= policy.DRT_LONG_WALK_METERS:
            reasons.append(policy.LONG_WALK_DISTANCE)
            priority = True

        if metrics.get("transferCount", 0) >= policy.DRT_MANY_TRANSFERS:
            reasons.append(policy.MANY_TRANSFERS)
            priority = True

    if weather in policy.SEVERE_WEATHER:
        reasons.append(policy.SEVERE_WEATHER_REASON)
        priority = True

    show = device != "NONE" or priority
    return _result(show, priority, False, reasons, based_on)


def _result(show, priority, taxi_guide, reasons, based_on):
    return {
        "show": show,
        "priority": priority,
        "taxiGuide": taxi_guide,
        "reasonCodes": reasons,
        "basedOnCandidateId": based_on,
    }
