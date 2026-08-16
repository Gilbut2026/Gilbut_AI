"""출발 예정 시각의 기상청 단기예보를 경로 스코어링용 날씨 condition으로 변환한다.

Backend는 ``LocalDateTime departureDateTime``을 JSON ISO-8601 문자열로 전달한다.
offset이 없는 값은 서비스 기준 시간대인 KST로 해석한다.

기존 초단기실황 기반 분류 의미는 그대로 유지한다.

    T1H(기온)      -> TMP(1시간 기온)
    RN1(강수량)    -> PCP(1시간 강수량)
    PTY(강수형태)  -> PTY(강수형태)

기상청 단기예보의 일반 구간은 1시간 간격이지만 연장기간은 3시간 간격이며,
연장기간 PCP는 실제 mm/h가 아닌 정성 코드값(0~3)을 사용한다. 실제 반환 시각
간격이 3시간인 구간을 감지해 출발시각 선택 허용범위와 PCP 해석을 전환한다.

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

# 일반 구간은 1시간, 연장기간은 3시간 간격이다. departureDateTime은 가장
# 가까운 예보를 사용하므로 각각 간격의 절반(30분/90분)까지 허용한다.
STANDARD_FORECAST_INTERVAL = timedelta(hours=1)
EXTENDED_FORECAST_INTERVAL = timedelta(hours=3)
STANDARD_MAX_FORECAST_TIME_DELTA = timedelta(minutes=30)
EXTENDED_MAX_FORECAST_TIME_DELTA = timedelta(minutes=90)

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

# 연장기간 PCP 정성 코드: 0=강수없음, 1=<3mm/h, 2=3~15mm/h, 3=>=15mm/h.
# 기존 임계값 분류와 호환되도록 실제 강수량을 복원하지 않고 분류 가능한
# 대표 하한값으로 변환한다. 코드 1은 강수가 있음을 보존하기 위한 양수 sentinel이다.
QUALITATIVE_PCP_CLASSIFICATION_MM = {
    "0": 0.0,
    "1": 0.1,
    "2": 3.0,
    "3": 15.0,
}

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
    """현재 시점에서 이용 가능한 최신 단기예보 발표 시각 후보를 반환한다."""
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


def _parse_qualitative_pcp(value: Any) -> float:
    """연장기간 PCP 정성 코드(0~3)를 기존 강수량 임계 분류용 값으로 변환한다."""
    if value is None or isinstance(value, bool):
        raise RuntimeError("기상청 연장기간 PCP 예보값이 없습니다")

    try:
        numeric = float(str(value).strip())
    except ValueError as error:
        raise RuntimeError("기상청 연장기간 PCP 코드 형식이 예상과 다릅니다") from error

    if not numeric.is_integer():
        raise RuntimeError("기상청 연장기간 PCP 코드는 정수 0~3이어야 합니다")

    code = str(int(numeric))
    if code not in QUALITATIVE_PCP_CLASSIFICATION_MM:
        raise RuntimeError(f"지원하지 않는 기상청 연장기간 PCP 코드입니다: {code}")

    return QUALITATIVE_PCP_CLASSIFICATION_MM[code]


def _parse_pcp(value: Any, *, qualitative: bool = False) -> float:
    """PCP를 기존 RN1 분류기와 호환되는 강수량 값으로 변환한다."""
    if qualitative:
        return _parse_qualitative_pcp(value)

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
    rain_amount = _parse_pcp(
        forecast.get("PCP"),
        qualitative=bool(forecast.get("_qualitative_pcp")),
    )
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
    """지정된 발표본의 수원시 기상청 단기예보 항목을 조회한다."""
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


def _forecast_interval_for_timestamp(
    forecast_times: list[datetime],
    selected_at: datetime,
) -> timedelta:
    """실제 반환 시각의 인접 간격으로 일반(1h)/연장(3h) 구간을 판별한다."""
    try:
        index = forecast_times.index(selected_at)
    except ValueError as error:
        raise RuntimeError("선택된 예보 시각을 찾을 수 없습니다") from error

    adjacent: list[timedelta] = []
    if index > 0:
        adjacent.append(selected_at - forecast_times[index - 1])
    if index + 1 < len(forecast_times):
        adjacent.append(forecast_times[index + 1] - selected_at)

    if EXTENDED_FORECAST_INTERVAL in adjacent:
        return EXTENDED_FORECAST_INTERVAL

    return STANDARD_FORECAST_INTERVAL


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

    forecast_times = sorted(complete)
    selected_at = min(
        forecast_times,
        key=lambda forecast_at: (
            abs(forecast_at - target),
            forecast_at,
        ),
    )

    interval = _forecast_interval_for_timestamp(forecast_times, selected_at)
    max_delta = (
        EXTENDED_MAX_FORECAST_TIME_DELTA
        if interval == EXTENDED_FORECAST_INTERVAL
        else STANDARD_MAX_FORECAST_TIME_DELTA
    )

    if abs(selected_at - target) > max_delta:
        raise RuntimeError("departureDateTime이 단기예보 제공 범위를 벗어났습니다")

    forecast = dict(complete[selected_at])
    if interval == EXTENDED_FORECAST_INTERVAL:
        forecast["_qualitative_pcp"] = True

    return selected_at, forecast


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