"""도보 구간의 장애물(계단·육교·지하보도) 집계."""

from dataclasses import dataclass

from . import policy


@dataclass(frozen=True)
class Obstacles:
    """경로 하나의 장애물 집계 결과.

    종류를 분리해서 담는다. 계단은 통행 불가로 이어지지만, 육교·지하보도는
    엘리베이터나 경사로가 있을 수 있어 판단 기준이 다르기 때문이다.

    Attributes:
        weight: 점수 계산에 사용할 가중 장애물 값. 조회 실패 구간도 포함한다.
        stair: 확인된 계단 개수.
        overpass: 확인된 육교 개수.
        underpass: 확인된 지하보도 개수.
        has_unknown: 조회 실패로 확인하지 못한 구간이 있는지.
    """

    weight: float = 0.0
    stair: int = 0
    overpass: int = 0
    underpass: int = 0
    has_unknown: bool = False

    @property
    def total(self):
        """확인된 장애물 전체 개수."""
        return self.stair + self.overpass + self.underpass


def collect(walk_segments):
    """walkSegments에서 장애물을 종류별로 집계한다.

    역 내부(STATION_INTERNAL) 구간은 제외한다. 수원 관내 지하철역 전수 조사 결과
    모든 역에 엘리베이터가 있어, 역 내부 이동은 엘리베이터로 가능하다고 가정한다.

    조회 실패(UNKNOWN)는 구간 단위로 한 번만 감점한다. 계단·육교·지하보도가
    각각 UNKNOWN인 것은 장애물이 세 개 있다는 뜻이 아니라, 그 구간 조회가
    통째로 실패했다는 뜻이기 때문이다.
    """
    counts = {"stair": 0, "overpass": 0, "underpass": 0}
    weight = 0.0
    has_unknown = False

    for segment in walk_segments or []:
        if not isinstance(segment, dict):
            continue
        if segment.get("segmentScope") == policy.STATION_INTERNAL:
            continue

        signals = segment.get("accessibilitySignals") or {}
        segment_unknown = False

        for kind in policy.OBSTACLE_KINDS:
            signal = signals.get(kind) or {}
            state = signal.get("state", policy.UNKNOWN)

            if state == policy.PRESENT:
                # PRESENT인데 count가 없거나 0이면 최소 1개로 본다.
                # 상태와 개수가 어긋난 경우, 상태를 신뢰하는 편이 안전하다.
                count = signal.get("count") or 0
                count = max(count, 1)
                counts[kind] += count
                weight += count * policy.OBSTACLE_WEIGHT[kind]
            elif state == policy.UNKNOWN:
                segment_unknown = True

        if segment_unknown:
            weight += policy.UNKNOWN_SEGMENT_PENALTY
            has_unknown = True

    return Obstacles(
        weight=weight,
        stair=counts["stair"],
        overpass=counts["overpass"],
        underpass=counts["underpass"],
        has_unknown=has_unknown,
    )
