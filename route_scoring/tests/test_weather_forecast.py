"""departureDateTime 기반 기상청 단기예보 계약/분류 검증."""

import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scoring import policy
from scoring.weather_penalty import (
    KST,
    VALID_WEATHER_CONDITIONS,
    _build_environment,
    _is_qualitative_extended_period,
    _request_forecast_items,
    classify_weather,
    get_forecast_base_candidates,
    get_weather_environment,
    parse_departure_datetime,
    select_forecast_for_departure,
)


def _item(date, time, category, value):
    return {
        "fcstDate": date,
        "fcstTime": time,
        "category": category,
        "fcstValue": value,
    }


@pytest.mark.parametrize(
    "text, expected",
    [
        ("2026-08-17T10:30", datetime(2026, 8, 17, 10, 30, tzinfo=KST)),
        ("2026-08-17T10:30:00", datetime(2026, 8, 17, 10, 30, tzinfo=KST)),
        (
            "2026-08-17T10:30:00.123456",
            datetime(2026, 8, 17, 10, 30, 0, 123456, tzinfo=KST),
        ),
    ],
)
def test_parse_backend_local_datetime_iso_forms_as_kst(text, expected):
    assert parse_departure_datetime(text) == expected


def test_offset_aware_departure_is_converted_to_kst():
    parsed = parse_departure_datetime("2026-08-17T01:30:00+00:00")
    assert parsed == datetime(2026, 8, 17, 10, 30, tzinfo=KST)


def test_date_only_is_rejected():
    with pytest.raises(ValueError):
        parse_departure_datetime("2026-08-17")


def test_latest_forecast_base_uses_current_available_run():
    now = datetime(2026, 8, 16, 20, 20, tzinfo=KST)

    candidates = get_forecast_base_candidates(now)

    assert candidates[:3] == [
        datetime(2026, 8, 16, 20, 0, tzinfo=KST),
        datetime(2026, 8, 16, 17, 0, tzinfo=KST),
        datetime(2026, 8, 16, 14, 0, tzinfo=KST),
    ]


def test_before_first_daily_run_uses_previous_day_2300():
    now = datetime(2026, 8, 17, 1, 0, tzinfo=KST)
    assert get_forecast_base_candidates(now)[0] == datetime(
        2026, 8, 16, 23, 0, tzinfo=KST
    )


def test_kma_request_uses_yyyymmdd_hhmm(monkeypatch):
    captured = {}

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "response": {
                    "header": {"resultCode": "00"},
                    "body": {
                        "items": {
                            "item": [
                                _item("20260817", "1000", "TMP", "28"),
                            ]
                        }
                    },
                }
            }

    def fake_get(url, params, timeout):
        captured.update(params)
        return Response()

    monkeypatch.setenv("KMA_SERVICE_KEY", "test-key")
    monkeypatch.setattr("scoring.weather_penalty.requests.get", fake_get)

    _request_forecast_items(datetime(2026, 8, 16, 20, 0, tzinfo=KST))

    assert captured["base_date"] == "20260816"
    assert captured["base_time"] == "2000"
    assert captured["nx"] == 60
    assert captured["ny"] == 121


def test_select_forecast_closest_to_departure_time():
    items = [
        _item("20260817", "1000", "TMP", "28"),
        _item("20260817", "1000", "PCP", "강수없음"),
        _item("20260817", "1000", "PTY", "0"),
        _item("20260817", "1100", "TMP", "31"),
        _item("20260817", "1100", "PCP", "20.0mm"),
        _item("20260817", "1100", "PTY", "1"),
        _item("20260817", "1100", "SNO", "적설없음"),
    ]

    assert select_forecast_for_departure(
        items,
        "2026-08-17T10:50:00",
    ) == {
        "TMP": "31",
        "PCP": "20.0mm",
        "PTY": "1",
        "SNO": "적설없음",
    }


def test_exact_halfway_prefers_earlier_forecast():
    items = [
        _item("20260817", "1000", "TMP", "28"),
        _item("20260817", "1000", "PCP", "강수없음"),
        _item("20260817", "1000", "PTY", "0"),
        _item("20260817", "1100", "TMP", "29"),
        _item("20260817", "1100", "PCP", "강수없음"),
        _item("20260817", "1100", "PTY", "0"),
    ]

    selected = select_forecast_for_departure(
        items,
        "2026-08-17T10:30:00",
    )

    assert selected["TMP"] == "28"


@pytest.mark.parametrize(
    "forecast, expected",
    [
        ({"TMP": "25", "PCP": "강수없음", "PTY": "0"}, "CLEAR"),
        ({"TMP": "25", "PCP": "2.0mm", "PTY": "1"}, "RAIN"),
        ({"TMP": "25", "PCP": "15.0mm", "PTY": "1"}, "HEAVY_RAIN"),
        ({"TMP": "-2", "PCP": "1.0mm", "PTY": "3"}, "SNOW"),
        ({"TMP": "-2", "PCP": "3.0mm", "PTY": "3"}, "HEAVY_SNOW"),
        ({"TMP": "30", "PCP": "강수없음", "PTY": "0"}, "HEAT"),
        ({"TMP": "35", "PCP": "강수없음", "PTY": "0"}, "SEVERE_HEAT"),
        ({"TMP": "-5", "PCP": "강수없음", "PTY": "0"}, "COLD"),
        ({"TMP": "-15", "PCP": "강수없음", "PTY": "0"}, "SEVERE_COLD"),
    ],
)
def test_new_forecast_inputs_preserve_existing_weather_outputs(forecast, expected):
    assert classify_weather(forecast) == expected


def test_qualitative_extended_heavy_rain_code():
    assert classify_weather(
        {
            "TMP": "10",
            "PCP": "3",
            "PTY": "1",
            "SNO": "0",
            "_qualitative": True,
        }
    ) == "HEAVY_RAIN"


def test_qualitative_extended_snow_uses_sno_code():
    assert classify_weather(
        {
            "TMP": "-3",
            "PCP": "2",
            "PTY": "3",
            "SNO": "2",
            "_qualitative": True,
        }
    ) == "HEAVY_SNOW"


def test_extended_period_boundary_matches_kma_release_rule():
    base_1400 = datetime(2026, 8, 16, 14, 0, tzinfo=KST)
    base_2000 = datetime(2026, 8, 16, 20, 0, tzinfo=KST)

    assert _is_qualitative_extended_period(
        base_1400,
        datetime(2026, 8, 19, 3, 0, tzinfo=KST),
    )
    assert not _is_qualitative_extended_period(
        base_2000,
        datetime(2026, 8, 19, 3, 0, tzinfo=KST),
    )
    assert _is_qualitative_extended_period(
        base_2000,
        datetime(2026, 8, 20, 3, 0, tzinfo=KST),
    )


def test_weather_condition_contract_matches_score_function():
    assert VALID_WEATHER_CONDITIONS == frozenset(policy.WEATHER_PENALTY)


@pytest.mark.parametrize(
    "condition",
    [
        "CLEAR",
        "RAIN",
        "HEAVY_RAIN",
        "SNOW",
        "HEAVY_SNOW",
        "HEAT",
        "SEVERE_HEAT",
        "COLD",
        "SEVERE_COLD",
    ],
)
def test_success_environment_is_score_function_compatible(condition):
    assert _build_environment(condition, "SUCCESS") == {
        "weatherCondition": condition,
        "weatherLookupStatus": "SUCCESS",
    }


def test_unknown_weather_condition_is_rejected():
    with pytest.raises(RuntimeError):
        _build_environment("TYPHOON", "SUCCESS")


def test_missing_departure_datetime_fails_without_weather_penalty():
    assert get_weather_environment(None) == {
        "weatherCondition": "CLEAR",
        "weatherLookupStatus": "FAILED",
    }
