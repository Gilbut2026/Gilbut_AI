"""Hard Filter — 점수를 매기기 전에 통행이 불가능한 후보를 제외한다.

감점으로 처리하지 않고 제외하는 이유는, 계단 이용이 어려운 사용자에게
계단이 있는 경로는 "불편"이 아니라 "통행 불가"에 가깝기 때문이다. 감점만 하면
다른 조건이 좋을 때 통행 불가 경로가 1순위가 될 수 있다.
"""

from . import policy


def check(candidate, obstacles, user):
    """제외 사유 코드 목록을 반환한다. 통과하면 빈 리스트."""
    return _check_stairs(obstacles, user) + _check_walk_time(candidate, user)


def _check_stairs(obstacles, user):
    """계단으로 인한 제외 여부.

    육교·지하보도는 제외 대상이 아니다. 엘리베이터나 경사로가 있을 수 있는데
    현재 데이터로는 구분할 수 없어, 일괄 제외하면 갈 수 있는 경로까지 잃는다.
    대신 점수에서 감점한다.

    휠체어 사용자는 조회 실패(UNKNOWN)도 제외 대상이다. 계단이 있으면 물리적으로
    통행이 불가능하고 되돌릴 수 없어, 확실하지 않을 때는 안전하게 판단한다.

    계단 이용 어려움 사용자(휠체어 제외)는 확인된 계단만 제외 대상이다.
    조회 실패만으로 후보를 잃지 않도록 통과시키고, 점수에서 불리하게 처리한다.
    """
    if user.get("mobilityAid") == "WHEELCHAIR":
        if obstacles.stair > 0 or obstacles.has_unknown:
            return [policy.WHEELCHAIR_WITH_EXTERNAL_STAIR]

    elif user.get("stairLevel") == "DIFFICULT":
        if obstacles.stair > 0:
            return [policy.STAIR_DIFFICULT_WITH_EXTERNAL_STAIR]

    return []


def _check_walk_time(candidate, user):
    """보행 불가이거나 보행 가능 시간을 크게 초과하는 경로 제외.

    정규화는 30분 초과를 모두 같은 값으로 취급하므로, 감점만으로는
    "10분만 가능한 사용자에게 90분 경로" 같은 극단적인 경우를 걸러내지 못한다.
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
