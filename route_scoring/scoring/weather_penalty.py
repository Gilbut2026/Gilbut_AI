import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests


# 기상청 서비스키
SERVICE_KEY = "0f1a31a23df06c6460f6f1643fed7b9070e54cf0a177a67712f52d3d81f83243"

URL = (
    "https://apis.data.go.kr/1360000/"
    "VilageFcstInfoService_2.0/getUltraSrtNcst"
)

# 수원시 격자 좌표
NX = 60
NY = 121


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


def parse_float(value, default=0.0):
    if value is None:
        return default

    text = str(value).strip()

    if not text or text in {"강수없음", "없음", "-", "null"}:
        return default

    match = re.search(r"-?\d+(?:\.\d+)?", text)

    if match is None:
        return default

    return float(match.group())


def get_base_datetime():
    now = datetime.now(ZoneInfo("Asia/Seoul")) - timedelta(hours=1)

    return now.strftime("%Y%m%d"), now.strftime("%H00")


def classify_precipitation(pty, rain_amount, temperature):
    pty = str(pty or "0")

    if pty in RAIN_PTY_CODES:
        if rain_amount >= HEAVY_RAIN_MM_PER_HOUR:
            return "HEAVY_RAIN"

        return "RAIN"

    if pty in SNOW_PTY_CODES:
        if rain_amount >= HEAVY_SNOW_PRECIP_MM_PER_HOUR:
            return "HEAVY_SNOW"

        return "SNOW"

    if pty in MIXED_PTY_CODES:
        if temperature <= MIXED_PRECIP_SNOW_TEMP_C:
            if rain_amount >= HEAVY_SNOW_PRECIP_MM_PER_HOUR:
                return "HEAVY_SNOW"

            return "SNOW"

        if rain_amount >= HEAVY_RAIN_MM_PER_HOUR:
            return "HEAVY_RAIN"

        return "RAIN"

    if rain_amount > 0:
        if rain_amount >= HEAVY_RAIN_MM_PER_HOUR:
            return "HEAVY_RAIN"

        return "RAIN"

    return None


def classify_temperature(temperature):
    if temperature >= SEVERE_HEAT_TEMP_C:
        return "SEVERE_HEAT"

    if temperature >= HEAT_TEMP_C:
        return "HEAT"

    if temperature <= SEVERE_COLD_TEMP_C:
        return "SEVERE_COLD"

    if temperature <= COLD_TEMP_C:
        return "COLD"

    return None


def classify_weather(result):
    temperature = parse_float(result.get("T1H"))
    rain_amount = parse_float(result.get("RN1"))
    pty = str(result.get("PTY", "0"))

    conditions = []

    precipitation = classify_precipitation(
        pty=pty,
        rain_amount=rain_amount,
        temperature=temperature,
    )

    temperature_condition = classify_temperature(temperature)

    if precipitation is not None:
        conditions.append(precipitation)

    if temperature_condition is not None:
        conditions.append(temperature_condition)

    if not conditions:
        return "CLEAR"

    return max(
        conditions,
        key=lambda condition: WEATHER_PENALTY[condition],
    )


def fetch_current_weather():
    base_date, base_time = get_base_datetime()

    params = {
        "serviceKey": SERVICE_KEY,
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

    data = response.json()

    header = data.get("response", {}).get("header", {})

    if header.get("resultCode") != "00":
        raise RuntimeError(header.get("resultMsg", "기상청 API 오류"))

    items = data["response"]["body"]["items"]["item"]

    result = {
        item["category"]: item["obsrValue"]
        for item in items
    }

    return classify_weather(result)


if __name__ == "__main__":
    weather_code = fetch_current_weather()
    print(weather_code)