"""기상청 초단기실황을 경로 스코어링용 날씨 condition으로 변환한다.

외부에 제공하는 계약은 다음 두 값이다.

    {
        "weatherCondition": "HEAVY_RAIN",
        "weatherLookupStatus": "SUCCESS",
    }

기상청 API 조회에 실패해도 경로 스코어링 자체는 계속할 수 있도록
``get_weather_environment``는 FAILED 상태를 반환한다.
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
    "VilageFcstInfoService_2.0/getUltraSrtNcst"
)

# 수원시 기상청 격자 좌표
NX = 60
NY = 121

KST = ZoneInfo("Asia/Seoul")

HEAVY_RAIN_MM_PER_HOUR = 15.0
HEAVY_SNOW_PRECIP_MM_PER_HOUR = 3.0

HEAT_TEMP_C = 30.0
SEVERE_HEAT_TEMP_C = 35.0

COLD_TEMP_C = -5.0
SEVERE_COLD_TEMP_C = -15.0

MIXED_PRECIP_SNOW_TEMP_C = 1.0

RAIN_PTY_CODES = {"1", "5"}
MIXED_PTY_CODES = {"2", "6"}
SNOW_PTY_CODES = {"3", "7"}


def parse_float(value: Any, default: float = 0.0) -> float:
    """기상청 숫자값과 '1mm 미만' 같은 문자열을 float로 변환한다."""
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


def get_base_datetime(now: datetime | None = None) -> tuple[str, str]:
    """초단기실황 조회 기준 시각을 KST로 계산한다.

    정시 관측값의 API 반영 지연을 고려해 매시 45분 전에는 이전 시각 자료를
    요청한다.
    """
    current = now or datetime.now(KST)

    if current.tzinfo is None:
        current = current.replace(tzinfo=KST)
    else:
        current = current.astimezone(KST)

    if current.minute < 45:
        current -= timedelta(hours=1)

    return current.strftime("%Y%m%d"), current.strftime("%H00")


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

    # PTY가 비어 있어도 RN1에 강수량이 있으면 비로 취급한다.
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


def classify_weather(observation: dict[str, Any]) -> str:
    """기상청 관측값을 하나의 weatherCondition으로 축약한다.

    강수와 기온 위험이 동시에 존재하면 route scoring에서 더 큰 페널티를
    갖는 condition 하나를 선택한다.
    """
    temperature = parse_float(observation.get("T1H"))
    rain_amount = parse_float(observation.get("RN1"))
    pty = observation.get("PTY", "0")

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
        key=lambda condition: policy.WEATHER_PENALTY[condition],
    )


def _service_key() -> str:
    service_key = os.getenv("KMA_SERVICE_KEY", "").strip()

    if not service_key:
        raise RuntimeError("KMA_SERVICE_KEY environment variable is not set")

    return service_key


def fetch_current_observation() -> dict[str, Any]:
    """수원시 기상청 초단기실황 원본 관측값을 조회한다."""
    base_date, base_time = get_base_datetime()

    params = {
        "serviceKey": _service_key(),
        "numOfRows": 100,
        "pageNo": 1,
        "dataType": "JSON",
        "base_date": base_date,
        "base_time": base_time,
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
        raise RuntimeError("기상청 API 관측 항목이 없습니다") from error

    return {
        str(item["category"]): item["obsrValue"]
        for item in items
    }


def get_weather_condition() -> str:
    """현재 수원 날씨를 score function의 condition 문자열로 반환한다."""
    return classify_weather(fetch_current_observation())


def get_weather_environment() -> dict[str, str]:
    """``score_routes``에 바로 넣을 environment 값을 반환한다.

    조회 실패 시 condition을 임의 추정하지 않고 FAILED로 표시한다.
    ``score_routes``는 FAILED 상태일 때 날씨 페널티를 적용하지 않는다.
    """
    try:
        condition = get_weather_condition()
    except (requests.RequestException, RuntimeError, KeyError, TypeError, ValueError):
        return {
            "weatherCondition": "CLEAR",
            "weatherLookupStatus": "FAILED",
        }

    return {
        "weatherCondition": condition,
        "weatherLookupStatus": "SUCCESS",
    }


if __name__ == "__main__":
    print(get_weather_environment())
