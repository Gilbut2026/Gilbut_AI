"""스코어링 정책 상수.

가중치 튜닝은 이 파일만 수정하면 된다.
외부 입력 enum은 Backend 온보딩 계약을 그대로 사용한다.
"""

SCORING_VERSION = "accessibility-score-v1"


# --- 장애물 상태 ---
PRESENT = "PRESENT"
ABSENT = "ABSENT"
UNKNOWN = "UNKNOWN"

# --- 도보 구간 범위 ---
EXTERNAL_WALK = "EXTERNAL_WALK"
STATION_INTERNAL = "STATION_INTERNAL"

# --- 결과 상태 ---
SCORED = "SCORED"
FILTERED = "FILTERED"

# --- 필터 사유 ---
STAIR_DIFFICULT_WITH_EXTERNAL_STAIR = "STAIR_DIFFICULT_WITH_EXTERNAL_STAIR"
WHEELCHAIR_WITH_EXTERNAL_STAIR = "WHEELCHAIR_WITH_EXTERNAL_STAIR"
WALK_TIME_EXCEEDED = "WALK_TIME_EXCEEDED"

# --- DRT 판단 사유 ---
ASSISTIVE_DEVICE = "ASSISTIVE_DEVICE"
LONG_WALK_DISTANCE = "LONG_WALK_DISTANCE"
MANY_TRANSFERS = "MANY_TRANSFERS"
SEVERE_WEATHER_REASON = "SEVERE_WEATHER"
NO_PASSABLE_ROUTE = "NO_PASSABLE_ROUTE"

OBSTACLE_KINDS = ("stair", "overpass", "underpass")


# ---------------------------------------------------------------------------
# 가중치 (임의값 — 실제 경로 데이터로 검증 후 조정 예정)
# ---------------------------------------------------------------------------

# Backend 온보딩: 보행 불가 / 10분 이내 / 20분 / 30분 이상
# UNABLE_TO_WALK은 Hard Filter에서 처리하므로 실제 점수 계산에는 사용되지 않는다.
WALK_WEIGHT = {
    "UNABLE_TO_WALK": 2.5,
    "WITHIN_10_MINUTES": 2.5,
    "WITHIN_20_MINUTES": 1.5,
    "OVER_30_MINUTES": 1.0,
}

# DIFFICULT은 Hard Filter에서 처리하므로 여기 없음.
# SLIGHTLY_DIFFICULT은 계단 경로를 허용하되 강하게 감점한다.
STAIR_WEIGHT = {
    "AVAILABLE": 0.1,
    "SLIGHTLY_DIFFICULT": 2.0,
}

# Backend 온보딩의 선호 의미를 그대로 사용한다.
# AVOID_PREFERRED도 Hard Filter가 아닌 강한 감점으로 처리해 후보 소실을 방지한다.
TRANSFER_WEIGHT = {
    "AVAILABLE": 1.0,
    "FEWER_PREFERRED": 2.0,
    "AVOID_PREFERRED": 5.0,
}

WEATHER_PENALTY = {
    "CLEAR": 0.0,
    "RAIN": 2.0,
    "HEAVY_RAIN": 3.0,
    "SNOW": 3.0,
    "HEAVY_SNOW": 4.0,
    "HEAT": 1.5,
    "SEVERE_HEAT": 2.5,
    "COLD": 1.5,
    "SEVERE_COLD": 2.5,
}

SEVERE_WEATHER = frozenset({"HEAVY_RAIN", "HEAVY_SNOW", "SEVERE_HEAT", "SEVERE_COLD"})

# 보조기구 사용 시 장애물 페널티 증폭
AID_MULTIPLIER = 1.5

# Backend 온보딩 보행 가능 시간(분). None은 상한 없음.
WALK_TOLERANCE_MINUTES = {
    "UNABLE_TO_WALK": 0,
    "WITHIN_10_MINUTES": 10,
    "WITHIN_20_MINUTES": 20,
    "OVER_30_MINUTES": None,
}

# 보행 가능 시간의 N배를 초과하면 Hard Filter로 제외
WALK_TOLERANCE_MULTIPLIER = 2

# 장애물 종류별 가중치.
# 육교·지하보도는 엘리베이터나 경사로가 있을 수 있어 계단보다 낮게 잡았다.
# 접근 가능 여부를 구분할 수 있게 되면 이 값 대신 실제 신호를 사용해야 한다.
OBSTACLE_WEIGHT = {
    "stair": 1.0,
    "overpass": 0.7,
    "underpass": 0.7,
}

# 조회 실패(UNKNOWN) 구간에 부여할 페널티.
# 계단·육교·지하보도가 각각 UNKNOWN인 것은 장애물이 세 개라는 뜻이 아니라
# 그 구간 조회가 통째로 실패했다는 뜻이므로, 구간 단위로 한 번만 적용한다.
UNKNOWN_SEGMENT_PENALTY = 1.0

# DRT 우선 노출 임계값
DRT_LONG_WALK_METERS = 800
DRT_MANY_TRANSFERS = 2


# ---------------------------------------------------------------------------
# 정규화 구간 — 경계값은 온보딩 선택지와 일치시킴
# ---------------------------------------------------------------------------

# (상한값, 정규화 결과). 마지막 항목의 상한은 무한대
WALK_TIME_BINS = ((10, 0.0), (20, 0.3), (30, 0.8), (float("inf"), 1.0))
WALK_DISTANCE_BINS = ((400, 0.0), (800, 0.3), (1200, 0.8), (float("inf"), 1.0))
