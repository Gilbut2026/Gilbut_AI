"""기상청 기존/신규 API의 원본 출력 비교용 스크립트.

Score Function이나 weatherCondition 계산은 하지 않는다.

실행:
    python route_scoring/verify_weather_api.py

BE 출발시간 형식으로 지정:
    python route_scoring/verify_weather_api.py --departure 2026-08-17T10:00:00
"""

from __future__ import annotations

import argparse
import os
from datetime import datetime, timedelta
from pathlib import Path

import requests
from dotenv import load_dotenv

from scoring.weather_penalty import (
    KST,
    NX,
    NY,
    URL as FORECAST_URL,
    get_forecast_base_candidates,
    parse_departure_datetime,
)


CURRENT_URL = (
    "https://apis.data.go.kr/1360000/"
    "VilageFcstInfoService_2.0/getUltraSrtNcst"
)

load_dotenv(Path(__file__).resolve().parent.parent / "api" / ".env")


def _service_key() -> str:
    key = os.getenv("KMA_SERVICE_KEY", "").strip()
    if not key:
        raise RuntimeError("api/.env에 KMA_SERVICE_KEY를 입력해주세요.")
    return key


def _items(response: requests.Response) -> list[dict]:
    response.raise_for_status()
    data = response.json()

    header = data["response"]["header"]
    if str(header.get("resultCode")) != "00":
        raise RuntimeError(
            f"KMA API error {header.get('resultCode')}: "
            f"{header.get('resultMsg', '')}"
        )

    items = data["response"]["body"]["items"]["item"]
    if not isinstance(items, list) or not items:
        raise RuntimeError("KMA API item list is empty")

    return items


def _current_base(now: datetime) -> datetime:
    current = now.astimezone(KST)
    if current.minute < 45:
        current -= timedelta(hours=1)
    return current.replace(minute=0, second=0, microsecond=0)


def print_current_observation(now: datetime) -> None:
    """기존 getUltraSrtNcst가 반환하는 category/obsrValue를 출력한다."""
    base = _current_base(now)
    response = requests.get(
        CURRENT_URL,
        params={
            "serviceKey": _service_key(),
            "pageNo": 1,
            "numOfRows": 100,
            "dataType": "JSON",
            "base_date": base.strftime("%Y%m%d"),
            "base_time": base.strftime("%H%M"),
            "nx": NX,
            "ny": NY,
        },
        timeout=10,
    )

    print("\n" + "=" * 70)
    print("기존 API - 초단기실황 getUltraSrtNcst")
    print("=" * 70)
    print("base_date :", base.strftime("%Y%m%d"))
    print("base_time :", base.strftime("%H%M"))
    print("\n[원본 category -> obsrValue]")

    for item in _items(response):
        print(f"{item.get('category', ''):>4} -> {item.get('obsrValue')}")


def _forecast_groups(items: list[dict]) -> dict[datetime, dict[str, object]]:
    grouped: dict[datetime, dict[str, object]] = {}

    for item in items:
        try:
            at = datetime.strptime(
                f"{item['fcstDate']}{item['fcstTime']}",
                "%Y%m%d%H%M",
            ).replace(tzinfo=KST)
        except (KeyError, TypeError, ValueError):
            continue

        grouped.setdefault(at, {})[str(item.get("category"))] = item.get(
            "fcstValue"
        )

    return grouped


def print_departure_forecast(
    departure: datetime,
    now: datetime,
) -> None:
    """새 getVilageFcst의 출발시각과 가장 가까운 원본 값을 출력한다."""
    last_error = None

    for base in get_forecast_base_candidates(now):
        try:
            response = requests.get(
                FORECAST_URL,
                params={
                    "serviceKey": _service_key(),
                    "pageNo": 1,
                    "numOfRows": 1000,
                    "dataType": "JSON",
                    "base_date": base.strftime("%Y%m%d"),
                    "base_time": base.strftime("%H%M"),
                    "nx": NX,
                    "ny": NY,
                },
                timeout=10,
            )
            groups = _forecast_groups(_items(response))
            if not groups:
                raise RuntimeError("forecast groups are empty")

            selected_at = min(
                groups,
                key=lambda at: (abs(at - departure), at),
            )

            print("\n" + "=" * 70)
            print("새 API - 단기예보 getVilageFcst")
            print("=" * 70)
            print("BE departureDateTime :", departure.isoformat())
            print("사용한 발표일자       :", base.strftime("%Y%m%d"))
            print("사용한 발표시각       :", base.strftime("%H%M"))
            print("선택된 예보시각       :", selected_at.isoformat())
            print("\n[선택된 시간의 원본 category -> fcstValue]")

            for category, value in sorted(groups[selected_at].items()):
                print(f"{category:>4} -> {value}")

            return
        except Exception as error:
            last_error = error

    raise RuntimeError("단기예보 API 조회 실패") from last_error


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--departure",
        help="BE LocalDateTime 형식. 예: 2026-08-17T10:00:00",
    )
    args = parser.parse_args()

    now = datetime.now(KST)
    departure = (
        parse_departure_datetime(args.departure)
        if args.departure
        else (now + timedelta(hours=6)).replace(
            minute=0,
            second=0,
            microsecond=0,
        )
    )

    print_current_observation(now)
    print_departure_forecast(departure, now)


if __name__ == "__main__":
    main()
