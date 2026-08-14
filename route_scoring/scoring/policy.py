"""스코어링 정책 상수.

비경사 항목은 Human Preference 120건 기반 prototype tuning 값을 사용한다.
경사 관련 값은 기존 라벨 데이터에 slopeLevel이 포함되지 않았으므로 baseline을 유지한다.
"""

SCORING_VERSION = "accessibility-score-v2"


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

# --- 경사 분석 상태 ---
SLOPE_NOT_REQUESTED = "NOT_REQUESTED"
SLOPE_SUCCESS = "SUCCESS"
SLOPE_PARTIAL = "PARTIAL"
SLOPE_FAILED = "FAILED"


# ---------------------------------------------------------------------------
# Prototype tuned weights
# Human Preference 평가 120건, train 96 / test 24
# 현재 라벨은 단일 evaluator 기반이므로 실제 사용자 검증 전까지 prototype 값으로 취급한다.
# ---------------------------------------------------------------------------

WALK_WEIGHT = {
    "WITHIN_10_MINUTES": 6.25,
    "WITHIN_20_MINUTES": 3.36,
    "OVER_30_MINUTES": 0.57,
}

# DIFFICULT은 Hard Filter에서 처리하므로 여기 없음
STAIR_WEIGHT = {
    "AVAILABLE": 0.21,
    "SLIGHTLY_DIFFICULT": 2.93,
}

# AVOID_PREFERRED도 필터가 아닌 감점으로 처리
TRANSFER_WEIGHT = {
    "AVAILABLE": 0.40,
    "FEWER_PREFERRED": 2.04,
    "AVOID_PREFERRED": 2.68,
}

WEATHER_PENALTY = {
    "CLEAR": 0.0,
    "RAIN": 2.73,
    "HEAVY_RAIN": 4.03,
    "SNOW": 6.43,
    "HEAVY_SNOW": 10.0,
    "HEAT": 0.60,
    "SEVERE_HEAT": 6.25,
    "COLD": 2.33,
    "SEVERE_COLD": 6.25,
}

SEVERE_WEATHER = frozenset(
    {"HEAVY_RAIN", "HEAVY_SNOW", "SEVERE_HEAT", "SEVERE_COLD"}
)

# 보조기구 사용 시 장애물 페널티 증폭
AID_MULTIPLIER = 1.35

# 온보딩 보행 가능 시간(분). None은 상한 없음
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
OBSTACLE_WEIGHT = {
    "stair": 2.50,
    "overpass": 0.28,
    "underpass": 0.37,
}

# 이번 튜닝 데이터에는 UNKNOWN 사례가 없어 기존값을 유지한다.
UNKNOWN_SEGMENT_PENALTY = 1.0

# ---------------------------------------------------------------------------
# 경사 정책
# 기존 120건 라벨링 시 사용자 정보에 slopeLevel이 제시되지 않았으므로
# 해당 데이터로 slope 가중치를 재해석하지 않는다. 실제 서비스는 main의
# slopeLevel -> LOW/MEDIUM/HIGH 매핑을 사용하고 아래 baseline 값을 적용한다.
# ---------------------------------------------------------------------------

SLOPE_MODERATE_GRADE_PERCENT = 4.0
SLOPE_STEEP_GRADE_PERCENT = 7.0
SLOPE_UPHILL_PENALTY = (0.0, 0.5, 1.5)
SLOPE_DOWNHILL_PENALTY = (0.0, 0.25, 1.0)
SLOPE_SENSITIVITY_MULTIPLIER = {
    "LOW": 1.0,
    "MEDIUM": 1.5,
    "HIGH": 2.0,
}
SLOPE_MAX_PENALTY = 3.0

# DRT 우선 노출 임계값
DRT_LONG_WALK_METERS = 800
DRT_MANY_TRANSFERS = 2


# ---------------------------------------------------------------------------
# 정규화 구간 — 경계값은 온보딩 선택지와 일치시킴
# ---------------------------------------------------------------------------

WALK_TIME_BINS = (
    (10, 0.0),
    (20, 0.3),
    (30, 0.8),
    (float("inf"), 1.0),
)

WALK_DISTANCE_BINS = (
    (400, 0.0),
    (800, 0.3),
    (1200, 0.8),
    (float("inf"), 1.0),
)
