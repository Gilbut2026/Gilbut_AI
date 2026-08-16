"""출발 예정 시각의 기상청 단기예보를 경로 스코어링용 condition으로 변환한다.

Backend는 ISO-8601 LocalDateTime 형태의 ``departureDateTime``을 전달한다.
이 모듈은 해당 값을 KST datetime으로 변환하고, 현재 사용할 수 있는 최신
기상청 단기예보 발표본을 조회한 뒤 departureDateTime과 가장 가까운 시간의
예보를 선택한다.

외부에 제공하는 계약은 다음 두 값이다.

    {
        "weatherCondition": "HEAVY_RAIN",
        "weatherLookupStatus": "SUCCESS",
    }

예보 조회/파싱에 실패하거나 departureDateTime이 예보 범위를 벗어나면
임의 추정하지 않고 FAILED를 반환한다. score_routes는 FAILED 상태에서
날씨 페널티를 적용하지 않는다.
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import requests


URL = (
    "https://apis.data.go.kr/1360000/"
    "VilageFcstInfoService_2.0/getVilageFcst"
)

# 수원시 기상청 격자 좌표
NX = 60
NY = 121

KST = ZoneInfo("Asia/Seoul")

# 기상청 단기예보 발표 시각: 02, 05, 08, 11, 14, 17, 20, 23시
FORECAST_BASE_HOURS = (2, 5, 8, 11, 14, 17, 20, 23)
FORECAST_BASE_CANDIDATE_COUNT = 3
MAX_FORECAST_TIME_DELTA = timedelta(hours=1)

HEAVY_RAIN_MM_PER_HOUR = 15.0
HEAVY_SNOW_PRECIP_MM_PER_HOUR = 3.0

HEAT_TEMP_C = 30.0
SEVERE_HEAT_TEMP_C = 35.0

COLD_TEMP_C = -5.0
SEVERE_COLD_TEMP_C = -15.0

MIXED_PRECIP_SNOW_TEMP_C = 1.0

# 단기예보 PTY: 1 비, 2 비/눈, 3 눈, 4 소나기.
# 5~7 코드는 기존 초단기 계열 응답과의 호환을 위해 유지한다.
RAIN_PTY_CODES = {"1", "4", "5"}
MIXED_PTY_CODES = {"2", "6"}
SNOW_PTY_CODES = {"3", "7"}

# 동시에 여러 위험이 잡혀도 weatherCondition은 하나만 제공한다.
# 이 우선순위는 condition 생성 규칙이며 score 가중치와 분리해 유지한다.
CONDITION_PRIORITY = {
    "CLEAR": 0,
    "HEAT": 1,
    "COLD": 1,
    "RAIN": 2,
    "SEVERE_HEAT": 3,
    "SEVERE_COLD": 3,
    "HEAVY_RAIN": 4,
    "SNOW": 4,
    "HEAVY_SNOW": 5,
}

REQUIRED_FORECAST_CATEGORIES = frozenset({"TMP", "PCP", "PTY"})


def parse_float(value: Any, default: float = 0.0) -> float:
    """기상청 숫자값과 '1mm 미만', '강수없음' 같은 문자열을 float로 변환한다."""
    if value is None:
        return default

    if isinstance(value, bool):
        return default

    if isinstance(value, (int, float)):
        return float(value)

    text = str(value).strip()

    if not text or text in {"강수없음", "없음", "-", "null"}:
        return default

    match = re.search(r"-?\d+(?:\.\d+)?", text)

    if match is None:
        return default

    return float(match.group())


def parse_departure_datetime(value: str | datetime) -> datetime:
    """Backend LocalDateTime 문자열을 KST-aware datetime으로 변환한다."""
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        text = value.strip()

        # 혹시 offset-aware ISO 문자열이 들어와도 함께 처리한다.
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"

        try:
            parsed = datetime.fromisoformat(text)
        except ValueError as error:
            raise ValueError(
                "departureDateTime must be ISO-8601 datetime"
            ) from error
    else:
        raise ValueError("departureDateTime is required")

    # Backend의 LocalDateTime은 offset이 없으므로 서비스 기준 시간대(KST)로 해석한다.
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=KST)

    return parsed.astimezone(KST)


def _as_kst(value: datetime | None = None) -> datetime:
    current = value or datetime.now(KST)

    if current.tzinfo is None:
        return current.replace(tzinfo=KST)

    return current.astimezone(KST)


def get_forecast_base_candidates(
    now: datetime | None = None,
    count: int = FORECAST_BASE_CANDIDATE_COUNT,
) -> list[datetime]:
    """현재 시점에서 사용할 수 있는 최신 단기예보 발표 시각 후보를 반환한다.

    가장 최신 발표본이 API에 아직 반영되지 않았을 수 있으므로 직전 발표본도
    함께 반환한다. 미래 발표본은 절대 선택하지 않는다.
    """
    current = _as_kst(now)
    day = current.date()

    candidates: list[datetime] = []

    # 오늘과 전날까지 훑으면 최신 3개 발표본을 충분히 만들 수 있다.
    for day_offset in (0, 1):
        candidate_day = day - timedelta(days=day_offset)

        for hour in reversed(FORECAST_BASE_HOURS):
            candidate = datetime(
                candidate_day.year,
                candidate_day.month,
                candidate_day.day,
                hour,
                tzinfo=KST,
            )

            if candidate <= current:
                candidates.append(candidate)

                if len(candidates) >= count:
                    return candidates

    return candidates


def classify_precipitation(
    pty: Any,
    rain_amount: float,
    temperature: float,
) -> str | None:
    """강수형태와 강수량을 score function의 condition으로 변환한다."""
    pty_code = str(pty or "0").strip()

    if pty_code in RAIN_PTY_CODES:
        return (
            "HEAVY_RAIN"
            if rain_amount >= HEAVY_RAIN_MM_PER_HOUR
            else "RAIN"
        )

    if pty_code in SNOW_PTY_CODES:
        return (
            "HEAVY_SNOW"
            if rain_amount >= HEAVY_SNOW_PRECIP_MM_PER_HOUR
            else "SNOW"
        )

    if pty_code in MIXED_PTY_CODES:
        if temperature <= MIXED_PRECIP_SNOW_TEMP_C:
            return (
                "HEAVY_SNOW"
                if rain_amount >= HEAVY_SNOW_PRECIP_MM_PER_HOUR
                else "SNOW"
            )

        return (
            "HEAVY_RAIN"
            if rain_amount >= HEAVY_RAIN_MM_PER_HOUR
            else "RAIN"
        )

    # PTY가 맑음이어도 PCP에 강수량이 있으면 비로 취급한다.
    if rain_amount > 0:
        return (
            "HEAVY_RAIN"
            if rain_amount >= HEAVY_RAIN_MM_PER_HOUR
            else "RAIN"
        )

    return None


def classify_temperature(temperature: float) -> str | None:
    """기온을 score function의 고온/저온 condition으로 변환한다."""
    if temperature >= SEVERE_HEAT_TEMP_C:
        return "SEVERE_HEAT"

    if temperature >= HEAT_TEMP_C:
        return "HEAT"

    if temperature <= SEVERE_COLD_TEMP_C:
        return "SEVERE_COLD"

    if temperature <= COLD_TEMP_C:
        return "COLD"

    return None


def classify_weather(forecast: dict[str, Any]) -> str:
    """기상청 단기예보 한 시점의 값을 weatherCondition으로 축약한다."""
    temperature = parse_float(forecast.get("TMP"))
    rain_amount = parse_float(forecast.get("PCP"))
    pty = forecast.get("PTY", "0")

    conditions = [
        condition
        for condition in (
            classify_precipitation(pty, rain_amount, temperature),
            classify_temperature(temperature),
        )
        if condition is not None
    ]

    if not conditions:
        return "CLEAR"

    return max(
        conditions,
        key=lambda condition: CONDITION_PRIORITY[condition],
    )


def _service_key() -> str:
    service_key = os.getenv("KMA_SERVICE_KEY", "").strip()

    if not service_key:
        raise RuntimeError("KMA_SERVICE_KEY environment variable is not set")

    return service_key


def _request_forecast_items(
    base_datetime: datetime,
) -> list[dict[str, Any]]:
    """지정된 발표본의 수원시 단기예보 항목을 조회한다."""
    params = {
        "serviceKey": _service_key(),
        "numOfRows": 1000,
        "pageNo": 1,
        "dataType": "JSON",
        "base_date": base_datetime.strftime("%Y%m%d"),
        "base_time": base_datetime.strftime("%H%M"),
        "nx": NX,
        "ny": NY,
    }

    response = requests.get(
        URL,
        params=params,
        timeout=10,
    )
    response.raise_for_status()

    try:
        data = response.json()
    except ValueError as error:
        raise RuntimeError(
            "기상청 API가 JSON이 아닌 응답을 반환했습니다"
        ) from error

    try:
        header = data["response"]["header"]
    except (KeyError, TypeError) as error:
        raise RuntimeError(
            "기상청 API 응답 구조가 예상과 다릅니다"
        ) from error

    result_code = str(header.get("resultCode", ""))

    if result_code != "00":
        message = header.get("resultMsg", "기상청 API 오류")
        raise RuntimeError(
            f"기상청 API 오류 ({result_code}): {message}"
        )

    try:
        items = data["response"]["body"]["items"]["item"]
    except (KeyError, TypeError) as error:
        raise RuntimeError(
            "기상청 API 예보 항목이 없습니다"
        ) from error

    if not isinstance(items, list) or not items:
        raise RuntimeError("기상청 API 예보 항목이 비어 있습니다")

    return items


def _forecast_datetime(item: dict[str, Any]) -> datetime:
    try:
        return datetime.strptime(
            f"{item['fcstDate']}{item['fcstTime']}",
            "%Y%m%d%H%M",
        ).replace(tzinfo=KST)
    except (KeyError, TypeError, ValueError) as error:
        raise RuntimeError(
            "기상청 API 예보 시각 형식이 예상과 다릅니다"
        ) from error


def select_forecast_for_departure(
    items: list[dict[str, Any]],
    departure_datetime: str | datetime,
) -> dict[str, Any]:
    """departureDateTime과 가장 가까운 시간의 TMP/PCP/PTY 예보를 선택한다."""
    target = parse_departure_datetime(departure_datetime)
    grouped: dict[datetime, dict[str, Any]] = {}

    for item in items:
        if not isinstance(item, dict):
            continue

        category = item.get("category")
        if category not in REQUIRED_FORECAST_CATEGORIES:
            continue

        forecast_at = _forecast_datetime(item)
        grouped.setdefault(forecast_at, {})[str(category)] = item.get(
            "fcstValue"
        )

    complete = {
        forecast_at: values
        for forecast_at, values in grouped.items()
        if REQUIRED_FORECAST_CATEGORIES.issubset(values)
    }

    if not complete:
        raise RuntimeError(
            "기상청 API 필수 예보값(TMP/PCP/PTY)이 없습니다"
        )

    selected_at = min(
        complete,
        key=lambda forecast_at: abs(forecast_at - target),
    )

    # 단기예보는 1시간 단위이므로 1시간보다 멀리 떨어진 값은 대신 쓰지 않는다.
    if abs(selected_at - target) > MAX_FORECAST_TIME_DELTA:
        raise RuntimeError(
            "departureDateTime이 단기예보 제공 범위를 벗어났습니다"
        )

    return complete[selected_at]


def fetch_forecast_for_departure(
    departure_datetime: str | datetime,
    now: datetime | None = None,
) -> dict[str, Any]:
    """현재 이용 가능한 최신 발표본에서 출발시각 예보를 조회한다.

    최신 발표본이 아직 API에 반영되지 않은 경우 직전 발표본으로 자동 fallback한다.
    """
    target = parse_departure_datetime(departure_datetime)
    last_error: Exception | None = None

    for base_datetime in get_forecast_base_candidates(now):
        try:
            items = _request_forecast_items(base_datetime)
            return select_forecast_for_departure(
                items,
                target,
            )
        except (
            requests.RequestException,
            RuntimeError,
            KeyError,
            TypeError,
            ValueError,
        ) as error:
            last_error = error

    if last_error is not None:
        raise RuntimeError(
            "사용 가능한 단기예보를 조회하지 못했습니다"
        ) from last_error

    raise RuntimeError(
        "사용 가능한 단기예보 발표 시각을 계산하지 못했습니다"
    )


def get_weather_condition(
    departure_datetime: str | datetime,
    now: datetime | None = None,
) -> str:
    """출발 예정 시각의 수원 단기예보를 condition 문자열로 반환한다."""
    forecast = fetch_forecast_for_departure(
        departure_datetime,
        now=now,
    )
    return classify_weather(forecast)


def get_weather_environment(
    departure_datetime: str | datetime | None,
    now: datetime | None = None,
) -> dict[str, str]:
    """``score_routes``에 바로 넣을 출발시각 기준 environment 값을 반환한다."""
    if departure_datetime is None:
        return {
            "weatherCondition": "CLEAR",
            "weatherLookupStatus": "FAILED",
        }

    try:
        condition = get_weather_condition(
            departure_datetime,
            now=now,
        )
    except (
        requests.RequestException,
        RuntimeError,
        KeyError,
        TypeError,
        ValueError,
    ):
        return {
            "weatherCondition": "CLEAR",
            "weatherLookupStatus": "FAILED",
        }

    return {
        "weatherCondition": condition,
        "weatherLookupStatus": "SUCCESS",
    }


if __name__ == "__main__":
    sample_departure = os.getenv("DEPARTURE_DATETIME")

    if not sample_departure:
        raise SystemExit(
            "DEPARTURE_DATETIME 환경변수에 ISO-8601 출발시각을 지정하세요."
        )

    print(get_weather_environment(sample_departure))
