"""출발 예정 시각의 기상청 단기예보를 경로 스코어링용 날씨 condition으로 변환한다.

Backend는 ``LocalDateTime departureDateTime``을 JSON ISO-8601 문자열로 전달한다.
offset이 없는 값은 서비스 기준 시간대인 KST로 해석한다.

기존 초단기실황 기반 분류 의미는 그대로 유지한다.

    T1H(기온)      -> TMP(1시간 기온)
    RN1(강수량)    -> PCP(1시간 강수량)
    PTY(강수형태)  -> PTY(강수형태)

단기예보 API는 SNO(1시간 신적설)도 제공하지만, 기존 HEAVY_SNOW 기준은
강수량(mm/h) 기준이므로 새로운 적설량 임계값을 임의로 만들지 않고 PCP를 사용한다.

외부 계약은 다음 두 값이다.

    {
        "weatherCondition": "HEAVY_RAIN",
        "weatherLookupStatus": "SUCCESS",
    }

예보 조회/파싱에 실패하거나 departureDateTime이 제공 범위를 벗어나면
임의 추정하지 않고 FAILED를 반환한다. Score Function은 FAILED 상태에서
날씨 페널티를 적용하지 않는다.
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import requests

from . import policy


URL = (
    "https://apis.data.go.kr/1360000/"
    "VilageFcstInfoService_2.0/getVilageFcst"
)

# 수원시 기상청 격자 좌표
NX = 60
NY = 121

KST = ZoneInfo("Asia/Seoul")

# 기상청 단기예보 발표 시각(KST): 매일 02, 05, 08, 11, 14, 17, 20, 23시
FORECAST_BASE_HOURS = (2, 5, 8, 11, 14, 17, 20, 23)
FORECAST_BASE_CANDIDATE_COUNT = 3

# 단기예보는 1시간 간격이다. 분 단위 departureDateTime은 가장 가까운
# 정시 예보를 사용하되 30분을 넘겨 다른 시각을 대신 쓰지 않는다.
MAX_FORECAST_TIME_DELTA = timedelta(minutes=30)

HEAVY_RAIN_MM_PER_HOUR = 15.0
HEAVY_SNOW_PRECIP_MM_PER_HOUR = 3.0

HEAT_TEMP_C = 30.0
SEVERE_HEAT_TEMP_C = 35.0

COLD_TEMP_C = -5.0
SEVERE_COLD_TEMP_C = -15.0

MIXED_PRECIP_SNOW_TEMP_C = 1.0

# getVilageFcst PTY: 0 없음, 1 비, 2 비/눈, 3 눈, 4 소나기
NO_PRECIP_PTY_CODE = "0"
RAIN_PTY_CODES = frozenset({"1", "4"})
MIXED_PTY_CODES = frozenset({"2"})
SNOW_PTY_CODES = frozenset({"3"})
VALID_PTY_CODES = frozenset({NO_PRECIP_PTY_CODE}) | RAIN_PTY_CODES | MIXED_PTY_CODES | SNOW_PTY_CODES

# 동시에 여러 위험이 잡혀도 weatherCondition은 하나만 제공한다.
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

VALID_WEATHER_CONDITIONS = frozenset(policy.WEATHER_PENALTY)
VALID_LOOKUP_STATUSES = frozenset({"SUCCESS", "FAILED"})
REQUIRED_FORECAST_CATEGORIES = frozenset({"TMP", "PCP", "PTY"})

NO_PRECIPITATION_VALUES = frozenset({
    "",
    "강수없음",
    "없음",
    "-",
    "null",
    "none",
})


def parse_departure_datetime(value: str | datetime) -> datetime:
    """Backend LocalDateTime/ISO-8601 값을 KST-aware datetime으로 변환한다."""
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        text = value.strip()

        if "T" not in text and " " not in text:
            raise ValueError("departureDateTime must include date and time")

        # 방어적으로 offset-aware ISO 문자열도 허용한다.
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

    # Backend java.time.LocalDateTime은 offset이 없으므로 KST로 해석한다.
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
    """현재 시점에서 이용 가능한 최신 단기예보 발표 시각 후보를 반환한다.

    최신 발표본이 API에 아직 반영되지 않았을 수 있으므로 직전 발표본도
    함께 반환한다. 미래 발표본은 선택하지 않는다.
    """
    if count <= 0:
        return []

    current = _as_kst(now)
    day = current.date()
    candidates: list[datetime] = []

    for day_offset in (0, 1):
        candidate_day = day - timedelta(days=day_offset)

        for hour in reversed(FORECAST_BASE_HOURS):
            candidate = datetime(
                candidate_day.year,
                candidate_day.month,
                candidate_day.day,
                hour,
                0,
                0,
                tzinfo=KST,
            )

            if candidate <= current:
                candidates.append(candidate)
                if len(candidates) >= count:
                    return candidates

    return candidates


def _parse_required_number(value: Any, field: str) -> float:
    """TMP처럼 반드시 숫자여야 하는 값을 엄격하게 변환한다."""
    if value is None or isinstance(value, bool):
        raise RuntimeError(f"기상청 {field} 예보값이 숫자가 아닙니다")

    if isinstance(value, (int, float)):
        return float(value)

    text = str(value).strip()
    match = re.search(r"-?\d+(?:\.\d+)?", text)

    if match is None:
        raise RuntimeError(f"기상청 {field} 예보값 형식이 예상과 다릅니다")

    return float(match.group())


def _parse_pcp(value: Any) -> float:
    """PCP 값을 기존 RN1 분류기와 같은 mm/h 숫자로 변환한다.

    실제 API의 ``강수없음``, ``1.0mm 미만``, ``30.0~50.0mm`` 같은 문자열도
    분류 임계값 비교가 가능하도록 첫 수치를 사용한다.
    """
    if value is None or isinstance(value, bool):
        raise RuntimeError("기상청 PCP 예보값이 없습니다")

    text = str(value).strip()

    if text.lower() in NO_PRECIPITATION_VALUES:
        return 0.0

    match = re.search(r"-?\d+(?:\.\d+)?", text)
    if match is None:
        raise RuntimeError("기상청 PCP 예보값 형식이 예상과 다릅니다")

    return float(match.group())


def _normalize_pty(value: Any) -> str:
    if value is None or isinstance(value, bool):
        raise RuntimeError("기상청 PTY 예보값이 없습니다")

    text = str(value).strip()
    if text not in VALID_PTY_CODES:
        raise RuntimeError(f"지원하지 않는 기상청 PTY 코드입니다: {text}")

    return text


def classify_precipitation(
    pty: Any,
    rain_amount: float,
    temperature: float,
) -> str | None:
    """강수형태와 PCP 강수량을 기존 weatherCondition으로 변환한다."""
    pty_code = _normalize_pty(pty)

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

    # 기존 로직과 동일하게 PTY=0이어도 PCP에 강수량이 있으면 비로 취급한다.
    if rain_amount > 0:
        return (
            "HEAVY_RAIN"
            if rain_amount >= HEAVY_RAIN_MM_PER_HOUR
            else "RAIN"
        )

    return None


def classify_temperature(temperature: float) -> str | None:
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
    """TMP/PCP/PTY 한 시점 예보를 기존 weatherCondition으로 축약한다."""
    temperature = _parse_required_number(forecast.get("TMP"), "TMP")
    rain_amount = _parse_pcp(forecast.get("PCP"))
    pty = forecast.get("PTY")

    conditions = [
        condition
        for condition in (
            classify_precipitation(pty, rain_amount, temperature),
            classify_temperature(temperature),
        )
        if condition is not None
    ]

    condition = (
        "CLEAR"
        if not conditions
        else max(conditions, key=lambda item: CONDITION_PRIORITY[item])
    )

    if condition not in VALID_WEATHER_CONDITIONS:
        raise RuntimeError(
            "weatherCondition이 Score Function WEATHER_PENALTY 계약과 다릅니다"
        )

    return condition


def _service_key() -> str:
    service_key = os.getenv("KMA_SERVICE_KEY", "").strip()
    if not service_key:
        raise RuntimeError("KMA_SERVICE_KEY environment variable is not set")
    return service_key


def _request_forecast_items(base_datetime: datetime) -> list[dict[str, Any]]:
    """지정된 발표본의 수원시 단기예보 항목을 조회한다."""
    base = _as_kst(base_datetime)

    params = {
        "serviceKey": _service_key(),
        "numOfRows": 1000,
        "pageNo": 1,
        "dataType": "JSON",
        "base_date": base.strftime("%Y%m%d"),
        "base_time": base.strftime("%H%M"),
        "nx": NX,
        "ny": NY,
    }

    response = requests.get(URL, params=params, timeout=10)
    response.raise_for_status()

    try:
        data = response.json()
    except ValueError as error:
        raise RuntimeError("기상청 API가 JSON이 아닌 응답을 반환했습니다") from error

    try:
        header = data["response"]["header"]
    except (KeyError, TypeError) as error:
        raise RuntimeError("기상청 API 응답 구조가 예상과 다릅니다") from error

    result_code = str(header.get("resultCode", ""))
    if result_code != "00":
        message = header.get("resultMsg", "기상청 API 오류")
        raise RuntimeError(f"기상청 API 오류 ({result_code}): {message}")

    try:
        items = data["response"]["body"]["items"]["item"]
    except (KeyError, TypeError) as error:
        raise RuntimeError("기상청 API 예보 항목이 없습니다") from error

    if not isinstance(items, list) or not items:
        raise RuntimeError("기상청 API 예보 항목이 비어 있습니다")

    return items


def _forecast_datetime(item: dict[str, Any]) -> datetime:
    """KMA fcstDate(YYYYMMDD)+fcstTime(HHMM)을 KST datetime으로 변환한다."""
    try:
        return datetime.strptime(
            f"{item['fcstDate']}{item['fcstTime']}",
            "%Y%m%d%H%M",
        ).replace(tzinfo=KST)
    except (KeyError, TypeError, ValueError) as error:
        raise RuntimeError("기상청 API 예보 시각 형식이 예상과 다릅니다") from error


def _select_forecast_snapshot(
    items: list[dict[str, Any]],
    departure_datetime: str | datetime,
) -> tuple[datetime, dict[str, Any]]:
    target = parse_departure_datetime(departure_datetime)
    grouped: dict[datetime, dict[str, Any]] = {}

    for item in items:
        if not isinstance(item, dict):
            continue

        category = item.get("category")
        if category not in REQUIRED_FORECAST_CATEGORIES:
            continue

        forecast_at = _forecast_datetime(item)
        grouped.setdefault(forecast_at, {})[str(category)] = item.get("fcstValue")

    complete = {
        forecast_at: values
        for forecast_at, values in grouped.items()
        if REQUIRED_FORECAST_CATEGORIES.issubset(values)
    }

    if not complete:
        raise RuntimeError("기상청 API 필수 예보값(TMP/PCP/PTY)이 없습니다")

    selected_at = min(
        complete,
        key=lambda forecast_at: (
            abs(forecast_at - target),
            forecast_at,
        ),
    )

    if abs(selected_at - target) > MAX_FORECAST_TIME_DELTA:
        raise RuntimeError("departureDateTime이 단기예보 제공 범위를 벗어났습니다")

    return selected_at, complete[selected_at]


def select_forecast_for_departure(
    items: list[dict[str, Any]],
    departure_datetime: str | datetime,
) -> dict[str, Any]:
    """departureDateTime과 가장 가까운 TMP/PCP/PTY 예보를 선택한다."""
    _, values = _select_forecast_snapshot(items, departure_datetime)
    return values


def fetch_forecast_for_departure(
    departure_datetime: str | datetime,
    now: datetime | None = None,
) -> dict[str, Any]:
    """현재 이용 가능한 최신 발표본에서 출발시각 예보를 조회한다."""
    target = parse_departure_datetime(departure_datetime)
    last_error: Exception | None = None

    for base_datetime in get_forecast_base_candidates(now):
        try:
            items = _request_forecast_items(base_datetime)
            _, forecast = _select_forecast_snapshot(items, target)
            return forecast
        except (
            requests.RequestException,
            RuntimeError,
            KeyError,
            TypeError,
            ValueError,
        ) as error:
            last_error = error

    if last_error is not None:
        raise RuntimeError("사용 가능한 단기예보를 조회하지 못했습니다") from last_error

    raise RuntimeError("사용 가능한 단기예보 발표 시각을 계산하지 못했습니다")


def get_weather_condition(
    departure_datetime: str | datetime,
    now: datetime | None = None,
) -> str:
    forecast = fetch_forecast_for_departure(departure_datetime, now=now)
    return classify_weather(forecast)


def _build_environment(condition: str, status: str) -> dict[str, str]:
    """Score Function에 넘기기 전에 weather output 계약을 검증한다."""
    if condition not in VALID_WEATHER_CONDITIONS:
        raise RuntimeError(
            f"unsupported weatherCondition for score function: {condition}"
        )

    if status not in VALID_LOOKUP_STATUSES:
        raise RuntimeError(f"unsupported weatherLookupStatus: {status}")

    return {
        "weatherCondition": condition,
        "weatherLookupStatus": status,
    }


def get_weather_environment(
    departure_datetime: str | datetime | None,
    now: datetime | None = None,
) -> dict[str, str]:
    """``score_routes``에 바로 넣을 출발시각 기준 environment 값을 반환한다."""
    if departure_datetime is None:
        return _build_environment("CLEAR", "FAILED")

    try:
        condition = get_weather_condition(departure_datetime, now=now)
    except (
        requests.RequestException,
        RuntimeError,
        KeyError,
        TypeError,
        ValueError,
    ):
        return _build_environment("CLEAR", "FAILED")

    return _build_environment(condition, "SUCCESS")


if __name__ == "__main__":
    sample_departure = os.getenv("DEPARTURE_DATETIME")
    if not sample_departure:
        raise SystemExit(
            "DEPARTURE_DATETIME 환경변수에 ISO-8601 출발시각을 지정하세요."
        )

    print(get_weather_environment(sample_departure))
