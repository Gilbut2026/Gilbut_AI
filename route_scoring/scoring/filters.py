"""Hard Filter — 점수를 매기기 전에 통행이 불가능한 후보를 제외한다.

감점으로 처리하지 않고 제외하는 이유는, 실제로 이용하기 어려운 경로가
다른 조건이 좋다는 이유로 상위에 노출되는 것을 막기 위해서다.
"""

from . import policy


def check(candidate, obstacles, user):
    """제외 사유 코드 목록을 반환한다. 통과하면 빈 리스트."""
    return _check_stairs(obstacles, user) + _check_walk_time(candidate, user)


def _check_stairs(obstacles, user):
    """계단으로 인한 제외 여부.

    Backend 온보딩 기준:
    - AVAILABLE: 계단 이용 가능
    - SLIGHTLY_DIFFICULT: 계단 이용 가능하지만 불편 → 점수에서 감점
    - DIFFICULT: 계단 이용 어려움 → 확인된 외부 계단 경로 제외

    육교·지하보도는 엘리베이터나 경사로가 있을 수 있어 Hard Filter하지 않고
    점수에서 감점한다.

    휠체어 사용자는 조회 실패(UNKNOWN)도 제외 대상이다. 계단이 있으면 물리적으로
    통행이 불가능하고 되돌릴 수 없어, 확실하지 않을 때는 안전하게 판단한다.
    """
    if user.get("mobilityAid") == "WHEELCHAIR":
        if obstacles.stair > 0 or obstacles.has_unknown:
            return [policy.WHEELCHAIR_WITH_EXTERNAL_STAIR]

    elif user.get("stairLevel") == "DIFFICULT":
        if obstacles.stair > 0:
            return [policy.STAIR_DIFFICULT_WITH_EXTERNAL_STAIR]

    return []


def _check_walk_time(candidate, user):
    """보행 가능 시간을 크게 초과하거나 보행 불가인 경우 경로를 제외한다.

    UNABLE_TO_WALK은 일반 경로 후보를 모두 제외해 이후 DRT/콜택시 판단으로
    넘긴다. 나머지는 기존 정책대로 사용자가 설정한 보행 가능 시간의 2배를
    초과할 때 제외한다.
    """
    walking_duration = user.get("walkingDuration")

    if walking_duration == "UNABLE_TO_WALK":
        return [policy.WALK_TIME_EXCEEDED]

    limit = policy.WALK_TOLERANCE_MINUTES.get(walking_duration)
    if limit is None:
        return []

    walk_minutes = candidate["metrics"]["totalWalkTimeSec"] / 60
    if walk_minutes > limit * policy.WALK_TOLERANCE_MULTIPLIER:
        return [policy.WALK_TIME_EXCEEDED]

    return []
