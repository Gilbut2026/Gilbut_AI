"""출발 예정 시각의 기상청 단기예보를 경로 스코어링용 날씨 condition으로 변환한다.

Backend는 ``LocalDateTime departureDateTime``을 JSON ISO-8601 문자열로 전달한다.
예: ``2026-08-17T10:00:00``. offset이 없는 값은 서비스 기준 시간대인 KST로
해석한다.

기상청 단기예보(getVilageFcst)의 최신 이용 가능한 발표본을 조회하고,
departureDateTime과 가장 가까운 예보 시각의 값을 기존 weatherCondition으로
변환한다.

기존 초단기실황 분류 입력과의 대응은 다음과 같다.

    T1H(기온)      -> TMP(1시간 기온)
    RN1(강수량)    -> PCP(1시간 강수량)
    PTY(강수형태)  -> PTY(강수형태)

연장 예보기간은 3시간 간격이며 PCP/SNO가 정성 코드로 제공될 수 있으므로
해당 구간은 기상청 공식 코드 범위에 맞춰 해석한다.

외부에 제공하는 계약은 다음 두 값이다.

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

# 일반 구간은 1시간, 연장 구간은 3시간 간격이므로 어느 쪽에서도
# 가장 가까운 예보점을 선택할 수 있도록 최대 90분 차이를 허용한다.
MAX_FORECAST_TIME_DELTA = timedelta(minutes=90)

HEAVY_RAIN_MM_PER_HOUR = 15.0
HEAVY_SNOW_PRECIP_MM_PER_HOUR = 3.0

HEAT_TEMP_C = 30.0
SEVERE_HEAT_TEMP_C = 35.0

COLD_TEMP_C = -5.0
SEVERE_COLD_TEMP_C = -15.0

MIXED_PRECIP_SNOW_TEMP_C = 1.0

# getVilageFcst 단기예보 PTY 코드
RAIN_PTY_CODES = {"1", "4"}  # 비, 소나기
MIXED_PTY_CODES = {"2"}      # 비/눈
SNOW_PTY_CODES = {"3"}       # 눈

# 동시에 여러 위험이 잡혀도 weatherCondition은 하나만 제공한다.
# Score Function의 WEATHER_PENALTY key와 반드시 동일한 집합이어야 한다.
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
OPTIONAL_FORECAST_CATEGORIES = frozenset({"SNO"})
FORECAST_CATEGORIES = REQUIRED_FORECAST_CATEGORIES | OPTIONAL_FORECAST_CATEGORIES

NO_PRECIPITATION_VALUES = frozenset({
    "",
    "강수없음",
    "적설없음",
    "없음",
    "-",
    "null",
    "none",
})

# 2024-11-28 이후 단기예보 연장구간의 정성 강수량 코드.
# 1: 3mm/h 미만, 2: 3~15mm/h 미만, 3: 15mm/h 이상
QUALITATIVE_PCP_LOWER_BOUND = {
    "0": 0.0,
    "1": 0.0,
    "2": 3.0,
    "3": 15.0,
}
QUALITATIVE_PCP_TEXT_LOWER_BOUND = {
    "약한비": 0.0,
    "약함 비": 0.0,
    "비": 3.0,
    "보통비": 3.0,
    "(보통) 비": 3.0,
    "강한비": 15.0,
    "강한 비": 15.0,
}

# 연장구간 SNO 정성 코드: 1은 1cm/h 미만, 2는 1cm/h 이상.
# 기상청 표출 의미상 2는 "많은눈"으로 취급한다.
QUALITATIVE_SNO_HEAVY_CODES = frozenset({"2"})
QUALITATIVE_SNO_LIGHT_CODES = frozenset({"0", "1"})


def parse_float(value: Any, default: float = 0.0) -> float:
    """일반 숫자값 또는 숫자를 포함한 문자열을 float로 변환한다."""
    if value is None or isinstance(value, bool):
        return default

    if isinstance(value, (int, float)):
        return float(value)

    text = str(value).strip()

    if text.lower() in NO_PRECIPITATION_VALUES:
        return default

    match = re.search(r"-?\d+(?:\.\d+)?", text)

    if match is None:
        return default

    return float(match.group())


def _parse_required_number(value: Any, field: str) -> float:
    """TMP처럼 반드시 숫자여야 하는 예보값을 엄격하게 변환한다."""
    if value is None or isinstance(value, bool):
        raise RuntimeError(f"기상청 {field} 예보값이 숫자가 아닙니다")

    if isinstance(value, (int, float)):
        return float(value)

    text = str(value).strip()
    match = re.search(r"-?\d+(?:\.\d+)?", text)

    if match is None:
        raise RuntimeError(f"기상청 {field} 예보값 형식이 예상과 다릅니다")

    return float(match.group())


def _parse_pcp(value: Any, qualitative: bool) -> float:
    """PCP를 기존 RN1 기반 분류기에 넣을 mm/h 형태로 변환한다."""
    if value is None or isinstance(value, bool):
        raise RuntimeError("기상청 PCP 예보값이 없습니다")

    if isinstance(value, (int, float)):
        text = str(value)
    else:
        text = str(value).strip()

    if text.lower() in NO_PRECIPITATION_VALUES:
        return 0.0

    if qualitative:
        if text in QUALITATIVE_PCP_LOWER_BOUND:
            return QUALITATIVE_PCP_LOWER_BOUND[text]

        if text in QUALITATIVE_PCP_TEXT_LOWER_BOUND:
            return QUALITATIVE_PCP_TEXT_LOWER_BOUND[text]

    # 일반 1시간 단기예보는 "20.0mm", "1mm 미만"처럼 정량값을 준다.
    match = re.search(r"-?\d+(?:\.\d+)?", text)

    if match is None:
        raise RuntimeError("기상청 PCP 예보값 형식이 예상과 다릅니다")

    return float(match.group())


def _qualitative_snow_is_heavy(value: Any) -> bool | None:
    """연장구간 SNO 정성 코드를 HEAVY_SNOW 판정 보조값으로 변환한다."""
    if value is None:
        return None

    text = str(value).strip()

    if text.lower() in NO_PRECIPITATION_VALUES:
        return False

    if text in QUALITATIVE_SNO_HEAVY_CODES or text in {"많은눈", "많은 눈"}:
        return True

    if text in QUALITATIVE_SNO_LIGHT_CODES or text in {"눈", "보통눈", "(보통) 눈"}:
        return False

    # 예상하지 못한 정성값은 임의 추정하지 않는다.
    raise RuntimeError("기상청 SNO 정성 예보값 형식이 예상과 다릅니다")


def parse_departure_datetime(value: str | datetime) -> datetime:
    """Backend LocalDateTime/ISO-8601 값을 KST-aware datetime으로 변환한다."""
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        text = value.strip()

        # LocalDateTime은 날짜만이 아니라 시간까지 포함해야 한다.
        if "T" not in text and " " not in text:
            raise ValueError(
                "departureDateTime must include date and time"
            )

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

    # Backend의 java.time.LocalDateTime은 offset이 없으므로 KST로 해석한다.
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
    current = _as_kst(now)
    day = current.date()

    candidates: list[datetime] = []

    # 오늘과 전날을 훑으면 최신 3개 발표본을 충분히 확보할 수 있다.
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


def _is_qualitative_extended_period(
    base_datetime: datetime,
    forecast_at: datetime,
) -> bool:
    """PCP/SNO가 정성 코드로 제공되는 연장 예보기간인지 판정한다."""
    base = _as_kst(base_datetime)
    forecast = _as_kst(forecast_at)

    # 02~14시 발표는 글피(+3일), 17~23시 발표는 그글피(+4일)가 연장구간.
    extended_day_offset = 3 if base.hour <= 14 else 4
    extended_date = base.date() + timedelta(days=extended_day_offset)

    return forecast.date() >= extended_date


def classify_precipitation(
    pty: Any,
    rain_amount: float,
    temperature: float,
    heavy_snow_override: bool | None = None,
) -> str | None:
    """강수형태와 강수량을 기존 weatherCondition으로 변환한다."""
    pty_code = str(pty or "0").strip()

    if pty_code in RAIN_PTY_CODES:
        return (
            "HEAVY_RAIN"
            if rain_amount >= HEAVY_RAIN_MM_PER_HOUR
            else "RAIN"
        )

    if pty_code in SNOW_PTY_CODES:
        is_heavy_snow = (
            heavy_snow_override
            if heavy_snow_override is not None
            else rain_amount >= HEAVY_SNOW_PRECIP_MM_PER_HOUR
        )
        return "HEAVY_SNOW" if is_heavy_snow else "SNOW"

    if pty_code in MIXED_PTY_CODES:
        if temperature <= MIXED_PRECIP_SNOW_TEMP_C:
            is_heavy_snow = (
                heavy_snow_override
                if heavy_snow_override is not None
                else rain_amount >= HEAVY_SNOW_PRECIP_MM_PER_HOUR
            )
            return "HEAVY_SNOW" if is_heavy_snow else "SNOW"

        return (
            "HEAVY_RAIN"
            if rain_amount >= HEAVY_RAIN_MM_PER_HOUR
            else "RAIN"
        )

    # PTY가 없음이어도 PCP에 강수량이 있으면 기존 fallback처럼 비로 취급한다.
    if rain_amount > 0:
        return (
            "HEAVY_RAIN"
            if rain_amount >= HEAVY_RAIN_MM_PER_HOUR
            else "RAIN"
        )

    return None


def classify_temperature(temperature: float) -> str | None:
    """기온을 기존 고온/저온 weatherCondition으로 변환한다."""
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
    """기상청 단기예보 한 시점의 값을 기존 weatherCondition으로 축약한다."""
    qualitative = bool(forecast.get("_qualitative", False))
    temperature = _parse_required_number(forecast.get("TMP"), "TMP")
    rain_amount = _parse_pcp(forecast.get("PCP"), qualitative)
    pty = forecast.get("PTY", "0")

    heavy_snow_override = None
    if qualitative and str(pty or "0").strip() in (
        SNOW_PTY_CODES | MIXED_PTY_CODES
    ):
        heavy_snow_override = _qualitative_snow_is_heavy(
            forecast.get("SNO")
        )

    conditions = [
        condition
        for condition in (
            classify_precipitation(
                pty,
                rain_amount,
                temperature,
                heavy_snow_override=heavy_snow_override,
            ),
            classify_temperature(temperature),
        )
        if condition is not None
    ]

    if not conditions:
        condition = "CLEAR"
    else:
        condition = max(
            conditions,
            key=lambda item: CONDITION_PRIORITY[item],
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


def _request_forecast_items(
    base_datetime: datetime,
) -> list[dict[str, Any]]:
    """지정된 발표본의 수원시 단기예보 항목을 조회한다."""
    base = _as_kst(base_datetime)

    params = {
        "serviceKey": _service_key(),
        "numOfRows": 1000,
        "pageNo": 1,
        "dataType": "JSON",
        # 기상청 getVilageFcst 요청 형식
        "base_date": base.strftime("%Y%m%d"),
        "base_time": base.strftime("%H%M"),
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
    """KMA fcstDate(YYYYMMDD)+fcstTime(HHMM)을 KST datetime으로 변환한다."""
    try:
        return datetime.strptime(
            f"{item['fcstDate']}{item['fcstTime']}",
            "%Y%m%d%H%M",
        ).replace(tzinfo=KST)
    except (KeyError, TypeError, ValueError) as error:
        raise RuntimeError(
            "기상청 API 예보 시각 형식이 예상과 다릅니다"
        ) from error


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
        if category not in FORECAST_CATEGORIES:
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

    # 정확히 중간 시각인 경우 과거 쪽 예보를 선택해 미래값으로 치우치지 않게 한다.
    selected_at = min(
        complete,
        key=lambda forecast_at: (
            abs(forecast_at - target),
            forecast_at,
        ),
    )

    if abs(selected_at - target) > MAX_FORECAST_TIME_DELTA:
        raise RuntimeError(
            "departureDateTime이 단기예보 제공 범위를 벗어났습니다"
        )

    return selected_at, complete[selected_at]


def select_forecast_for_departure(
    items: list[dict[str, Any]],
    departure_datetime: str | datetime,
) -> dict[str, Any]:
    """departureDateTime과 가장 가까운 TMP/PCP/PTY(+SNO) 예보를 선택한다."""
    _, values = _select_forecast_snapshot(
        items,
        departure_datetime,
    )
    return values


def fetch_forecast_for_departure(
    departure_datetime: str | datetime,
    now: datetime | None = None,
) -> dict[str, Any]:
    """현재 이용 가능한 최신 발표본에서 출발시각 예보를 조회한다.

    최신 발표본이 아직 API에 반영되지 않은 경우 직전 발표본으로 fallback한다.
    """
    target = parse_departure_datetime(departure_datetime)
    last_error: Exception | None = None

    for base_datetime in get_forecast_base_candidates(now):
        try:
            items = _request_forecast_items(base_datetime)
            selected_at, forecast = _select_forecast_snapshot(
                items,
                target,
            )

            # 분류기에 PCP/SNO 정성 코드 여부만 내부 메타데이터로 전달한다.
            return {
                **forecast,
                "_qualitative": _is_qualitative_extended_period(
                    base_datetime,
                    selected_at,
                ),
            }
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
    """출발 예정 시각의 수원 단기예보를 Score Function condition으로 반환한다."""
    forecast = fetch_forecast_for_departure(
        departure_datetime,
        now=now,
    )
    return classify_weather(forecast)


def _build_environment(
    condition: str,
    status: str,
) -> dict[str, str]:
    """Score Function에 넘기기 전에 weather output 계약을 검증한다."""
    if condition not in VALID_WEATHER_CONDITIONS:
        raise RuntimeError(
            f"unsupported weatherCondition for score function: {condition}"
        )

    if status not in VALID_LOOKUP_STATUSES:
        raise RuntimeError(
            f"unsupported weatherLookupStatus: {status}"
        )

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
        return _build_environment("CLEAR", "FAILED")

    return _build_environment(condition, "SUCCESS")


if __name__ == "__main__":
    sample_departure = os.getenv("DEPARTURE_DATETIME")

    if not sample_departure:
        raise SystemExit(
            "DEPARTURE_DATETIME 환경변수에 ISO-8601 출발시각을 지정하세요."
        )

    print(get_weather_environment(sample_departure))
