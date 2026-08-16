"""기상청 날씨 연동 수동 검증 스크립트.

검증 항목
1) 기존 초단기실황 API(getUltraSrtNcst)가 T1H/RN1/PTY를 반환하는지
2) 새 단기예보 API(getVilageFcst)가 TMP/PCP/PTY를 반환하는지
3) Backend 형식의 departureDateTime(ISO-8601 LocalDateTime)을 KST로 파싱하는지
4) 현재 이용 가능한 최신 단기예보 발표본을 선택하는지
5) departureDateTime과 가장 가까운 예보 시각을 선택하는지
6) 실제 production get_weather_environment()가 SUCCESS와 weatherCondition을 만드는지

사용 예시
    python route_scoring/verify_weather_api.py

특정 출발시각 검증
    python route_scoring/verify_weather_api.py --departure 2026-08-17T10:30:00

API 키는 코드에 저장하지 않는다. KMA_SERVICE_KEY 환경변수가 없으면 실행 시
터미널에서 입력받는다. requests가 query parameter를 인코딩하므로 공공데이터포털의
일반 인증키(Decoding)를 사용하는 것을 권장한다.
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from datetime import datetime, timedelta
from typing import Any

import requests

from scoring.weather_penalty import (
    KST,
    NX,
    NY,
    _request_forecast_items,
    classify_weather,
    get_forecast_base_candidates,
    get_weather_environment,
    parse_departure_datetime,
    select_forecast_for_departure,
)

CURRENT_URL = (
    "https://apis.data.go.kr/1360000/"
    "VilageFcstInfoService_2.0/getUltraSrtNcst"
)

REQUIRED_CURRENT = {"T1H", "RN1", "PTY"}
REQUIRED_FORECAST = {"TMP", "PCP", "PTY"}


def _get_service_key() -> str:
    key = os.getenv("KMA_SERVICE_KEY", "").strip()
    if not key:
        key = getpass.getpass("KMA_SERVICE_KEY (Decoding key) 입력: ").strip()
    if not key:
        raise SystemExit("[FAIL] KMA_SERVICE_KEY가 비어 있습니다.")
    os.environ["KMA_SERVICE_KEY"] = key
    return key


def _check_kma_response(response: requests.Response) -> list[dict[str, Any]]:
    response.raise_for_status()
    data = response.json()
    header = data["response"]["header"]
    result_code = str(header.get("resultCode", ""))
    if result_code != "00":
        raise RuntimeError(
            f"KMA API error {result_code}: {header.get('resultMsg', '')}"
        )
    items = data["response"]["body"]["items"]["item"]
    if not isinstance(items, list) or not items:
        raise RuntimeError("KMA API item list is empty")
    return items


def _current_base_datetime(now: datetime) -> datetime:
    """기존 초단기실황 코드와 같은 45분 반영 지연 정책."""
    current = now.astimezone(KST)
    if current.minute < 45:
        current -= timedelta(hours=1)
    return current.replace(minute=0, second=0, microsecond=0)


def fetch_legacy_current_observation(
    service_key: str,
    now: datetime,
) -> tuple[datetime, dict[str, Any]]:
    base = _current_base_datetime(now)
    params = {
        "serviceKey": service_key,
        "pageNo": 1,
        "numOfRows": 1000,
        "dataType": "JSON",
        "base_date": base.strftime("%Y%m%d"),
        "base_time": base.strftime("%H%M"),
        "nx": NX,
        "ny": NY,
    }
    response = requests.get(CURRENT_URL, params=params, timeout=10)
    items = _check_kma_response(response)

    values: dict[str, Any] = {}
    for item in items:
        if isinstance(item, dict) and "category" in item:
            values[str(item["category"])] = item.get("obsrValue")

    missing = REQUIRED_CURRENT.difference(values)
    if missing:
        raise RuntimeError(f"legacy current observation missing: {sorted(missing)}")

    return base, values


def verify_forecast(
    departure: datetime,
    now: datetime,
) -> tuple[datetime, dict[str, Any]]:
    last_error: Exception | None = None

    for base in get_forecast_base_candidates(now):
        try:
            items = _request_forecast_items(base)
            selected = select_forecast_for_departure(items, departure)
            missing = REQUIRED_FORECAST.difference(selected)
            if missing:
                raise RuntimeError(
                    f"short forecast missing: {sorted(missing)}"
                )
            return base, selected
        except Exception as error:  # diagnostic script: show fallback reason
            last_error = error
            print(
                f"  - 발표본 {base.strftime('%Y-%m-%d %H:%M')} 실패, 직전 발표본 시도: {error}"
            )

    raise RuntimeError("사용 가능한 단기예보 발표본이 없습니다") from last_error


def _legacy_condition(values: dict[str, Any]) -> str:
    """기존 T1H/RN1/PTY를 현재 condition 분류기에 맞춰 비교용으로 변환."""
    return classify_weather(
        {
            "TMP": values.get("T1H"),
            "PCP": values.get("RN1"),
            "PTY": values.get("PTY"),
        }
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--departure",
        help="BE와 같은 ISO-8601 형식. 예: 2026-08-17T10:30:00",
    )
    args = parser.parse_args()

    service_key = _get_service_key()
    now = datetime.now(KST)

    if args.departure:
        departure = parse_departure_datetime(args.departure)
    else:
        # API 키만 입력해도 검증 가능하도록 기본값은 현재 + 6시간의 정시로 설정.
        departure = (now + timedelta(hours=6)).replace(
            minute=0,
            second=0,
            microsecond=0,
        )

    print("\n=== Gilbut Weather API Verification ===")
    print(f"현재 시각(KST)       : {now.isoformat()}")
    print(f"departureDateTime   : {departure.isoformat()}")
    print(f"수원 격자            : nx={NX}, ny={NY}")

    failures: list[str] = []

    print("\n[1] 기존 초단기실황 API 검증")
    try:
        current_base, current = fetch_legacy_current_observation(
            service_key,
            now,
        )
        print(f"[PASS] API 호출 성공 / base={current_base.strftime('%Y%m%d %H%M')}")
        print(f"  T1H(기온)          : {current.get('T1H')}")
        print(f"  RN1(1시간 강수량)  : {current.get('RN1')}")
        print(f"  PTY(강수형태)      : {current.get('PTY')}")
        print(f"  기존값 condition   : {_legacy_condition(current)}")
        print(f"  반환 category 전체 : {', '.join(sorted(current))}")
    except Exception as error:
        failures.append(f"legacy current observation: {error}")
        print(f"[FAIL] {error}")

    print("\n[2] 새 단기예보 API + departureDateTime 선택 검증")
    try:
        forecast_base, forecast = verify_forecast(departure, now)
        print(
            "[PASS] API 호출 및 출발시각 예보 선택 성공 / "
            f"발표본={forecast_base.strftime('%Y%m%d %H%M')}"
        )
        print(f"  TMP(기온)          : {forecast.get('TMP')}")
        print(f"  PCP(1시간 강수량)  : {forecast.get('PCP')}")
        print(f"  PTY(강수형태)      : {forecast.get('PTY')}")
        print(f"  forecast condition : {classify_weather(forecast)}")
    except Exception as error:
        failures.append(f"short forecast: {error}")
        print(f"[FAIL] {error}")

    print("\n[3] 실제 production weather environment 경로 검증")
    try:
        environment = get_weather_environment(departure)
        print(f"  result             : {environment}")
        if environment.get("weatherLookupStatus") != "SUCCESS":
            raise RuntimeError(
                "production get_weather_environment returned FAILED"
            )
        print("[PASS] production 경로 SUCCESS")
    except Exception as error:
        failures.append(f"production environment: {error}")
        print(f"[FAIL] {error}")

    print("\n=== 결과 ===")
    if failures:
        for failure in failures:
            print(f"[FAIL] {failure}")
        return 1

    print("[PASS] 기존 실황 + 새 출발시각 예보 + production 경로 모두 정상")
    return 0


if __name__ == "__main__":
    sys.exit(main())
