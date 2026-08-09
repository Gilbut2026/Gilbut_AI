"""점수 계산.

    score = -(도보시간 + 도보거리 + 장애물 + 환승 + 날씨 페널티)

점수가 높을수록(0에 가까울수록) 편한 경로다.
항목별 페널티를 함께 반환하여, 왜 이 순위인지 설명할 수 있게 한다.
"""

from dataclasses import asdict, dataclass

from . import policy


@dataclass(frozen=True)
class Breakdown:
    """항목별 페널티와 최종 점수."""

    walk_time: float
    walk_distance: float
    obstacle: float
    transfer: float
    weather: float

    @property
    def total(self):
        return -(self.walk_time + self.walk_distance + self.obstacle
                 + self.transfer + self.weather)

    def as_dict(self):
        return {
            "walkTimePenalty": round(self.walk_time, 2),
            "walkDistancePenalty": round(self.walk_distance, 2),
            "obstaclePenalty": round(self.obstacle, 2),
            "transferPenalty": round(self.transfer, 2),
            "weatherPenalty": round(self.weather, 2),
        }


def calculate(candidate, obstacles, user, weather):
    """후보 경로 하나의 항목별 페널티를 계산한다."""
    metrics = candidate["metrics"]

    walk_time = normalize(metrics["totalWalkTimeSec"] / 60, policy.WALK_TIME_BINS)
    walk_distance = normalize(metrics["totalWalkDistanceM"], policy.WALK_DISTANCE_BINS)

    walk_weight = policy.WALK_WEIGHT.get(user.get("walkingDuration"), 1.5)
    stair_weight = policy.STAIR_WEIGHT.get(user.get("stairLevel"), 0.1)
    transfer_weight = policy.TRANSFER_WEIGHT.get(user.get("transferLevel"), 1.0)
    weather_weight = policy.WEATHER_PENALTY.get(weather, 0.0)

    aid = policy.AID_MULTIPLIER if user.get("mobilityAid", "NOT_USED") != "NOT_USED" else 1.0

    return Breakdown(
        walk_time=walk_weight * walk_time,
        walk_distance=walk_weight * walk_distance,
        obstacle=stair_weight * aid * obstacles.weight,
        transfer=transfer_weight * metrics["transferCount"],
        # 날씨는 도보 거리와 함께 작용한다. 밖에 오래 있을수록 영향이 크다.
        weather=weather_weight * walk_distance,
    )


def normalize(value, bins):
    """구간별 계단식 정규화.

    선형 정규화는 체감의 비선형성을 반영하지 못한다. 도보 5분과 10분의 차이는
    크지만 40분과 45분의 차이는 작은데, 선형은 이를 동일하게 취급한다.
    구간 경계는 온보딩 선택지와 일치시켜 근거를 확보했다.
    """
    for upper, normalized in bins:
        if value <= upper:
            return normalized
    return bins[-1][1]
