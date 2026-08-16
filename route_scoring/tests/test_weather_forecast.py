"""departureDateTime 기반 기상청 단기예보 선택 로직 검증."""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scoring.weather_penalty import (
    KST,
    classify_weather,
    get_forecast_base_candidates,
    get_weather_environment,
    parse_departure_datetime,
    select_forecast_for_departure,
)


def test_parse_backend_local_datetime_as_kst():
    parsed = parse_departure_datetime("2026-08-17T10:30:00")

    assert parsed == datetime(2026, 8, 17, 10, 30, tzinfo=KST)


def test_latest_forecast_base_uses_current_available_run():
    now = datetime(2026, 8, 16, 20, 20, tzinfo=KST)

    candidates = get_forecast_base_candidates(now)

    assert candidates[0] == datetime(2026, 8, 16, 20, 0, tzinfo=KST)
    assert candidates[1] == datetime(2026, 8, 16, 17, 0, tzinfo=KST)
    assert candidates[2] == datetime(2026, 8, 16, 14, 0, tzinfo=KST)


def test_before_first_daily_run_uses_previous_day_2300():
    now = datetime(2026, 8, 17, 1, 0, tzinfo=KST)

    candidates = get_forecast_base_candidates(now)

    assert candidates[0] == datetime(2026, 8, 16, 23, 0, tzinfo=KST)


def test_select_forecast_closest_to_departure_time():
    items = [
        {
            "fcstDate": "20260817",
            "fcstTime": "1000",
            "category": "TMP",
            "fcstValue": "28",
        },
        {
            "fcstDate": "20260817",
            "fcstTime": "1000",
            "category": "PCP",
            "fcstValue": "강수없음",
        },
        {
            "fcstDate": "20260817",
            "fcstTime": "1000",
            "category": "PTY",
            "fcstValue": "0",
        },
        {
            "fcstDate": "20260817",
            "fcstTime": "1100",
            "category": "TMP",
            "fcstValue": "31",
        },
        {
            "fcstDate": "20260817",
            "fcstTime": "1100",
            "category": "PCP",
            "fcstValue": "20.0mm",
        },
        {
            "fcstDate": "20260817",
            "fcstTime": "1100",
            "category": "PTY",
            "fcstValue": "1",
        },
    ]

    selected = select_forecast_for_departure(
        items,
        "2026-08-17T10:50:00",
    )

    assert selected == {
        "TMP": "31",
        "PCP": "20.0mm",
        "PTY": "1",
    }


def test_short_forecast_categories_feed_existing_weather_condition():
    condition = classify_weather(
        {
            "TMP": "31",
            "PCP": "20.0mm",
            "PTY": "1",
        }
    )

    assert condition == "HEAVY_RAIN"


def test_missing_departure_datetime_fails_without_weather_penalty():
    environment = get_weather_environment(None)

    assert environment == {
        "weatherCondition": "CLEAR",
        "weatherLookupStatus": "FAILED",
    }
