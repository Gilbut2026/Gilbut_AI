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


def _snapshot(date, time, tmp="10", pcp="강수없음", pty="0"):
    return [
        _item(date, time, "TMP", tmp),
        _item(date, time, "PCP", pcp),
        _item(date, time, "PTY", pty),
    ]


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
    assert get_forecast_base_candidates(now)[:3] == [
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
    items = (
        _snapshot("20260817", "1000", "28", "강수없음", "0")
        + _snapshot("20260817", "1100", "31", "20.0mm", "1")
    )

    assert select_forecast_for_departure(
        items,
        "2026-08-17T10:50:00",
    ) == {
        "TMP": "31",
        "PCP": "20.0mm",
        "PTY": "1",
    }


def test_exact_halfway_prefers_earlier_forecast():
    items = (
        _snapshot("20260817", "1000", "28")
        + _snapshot("20260817", "1100", "29")
    )

    selected = select_forecast_for_departure(
        items,
        "2026-08-17T10:30:00",
    )
    assert selected["TMP"] == "28"


def test_standard_hourly_period_does_not_substitute_more_than_30_minutes():
    items = (
        _snapshot("20260817", "1000", "28")
        + _snapshot("20260817", "1100", "29")
    )

    with pytest.raises(RuntimeError):
        select_forecast_for_departure(items, "2026-08-17T09:29:00")


def test_extended_three_hour_period_accepts_nearest_forecast_within_90_minutes():
    items = (
        _snapshot("20260819", "2300", "20", "강수없음", "0")
        + _snapshot("20260820", "0000", "20", "1", "1")
        + _snapshot("20260820", "0300", "19", "3", "1")
        + _snapshot("20260820", "0600", "18", "0", "0")
    )

    selected = select_forecast_for_departure(
        items,
        "2026-08-20T01:00:00",
    )

    assert selected["TMP"] == "20"
    assert selected["PCP"] == "1"
    assert selected["_qualitative_pcp"] is True


def test_extended_three_hour_halfway_prefers_earlier_forecast():
    items = (
        _snapshot("20260820", "0000", "20", "1", "1")
        + _snapshot("20260820", "0300", "19", "3", "1")
        + _snapshot("20260820", "0600", "18", "0", "0")
    )

    selected = select_forecast_for_departure(
        items,
        "2026-08-20T01:30:00",
    )

    assert selected["TMP"] == "20"
    assert selected["PCP"] == "1"


def test_irregular_two_hour_gap_is_not_mistaken_for_extended_period():
    items = (
        _snapshot("20260817", "1000", "20")
        + _snapshot("20260817", "1200", "21")
    )

    with pytest.raises(RuntimeError):
        select_forecast_for_departure(items, "2026-08-17T11:00:00")


@pytest.mark.parametrize(
    "forecast, expected",
    [
        ({"TMP": "25", "PCP": "강수없음", "PTY": "0"}, "CLEAR"),
        ({"TMP": "25", "PCP": "1.0mm 미만", "PTY": "1"}, "RAIN"),
        ({"TMP": "25", "PCP": "2.0mm", "PTY": "1"}, "RAIN"),
        ({"TMP": "25", "PCP": "15.0mm", "PTY": "1"}, "HEAVY_RAIN"),
        ({"TMP": "25", "PCP": "30.0~50.0mm", "PTY": "1"}, "HEAVY_RAIN"),
        ({"TMP": "-2", "PCP": "1.0mm", "PTY": "3"}, "SNOW"),
        ({"TMP": "-2", "PCP": "3.0mm", "PTY": "3"}, "HEAVY_SNOW"),
        ({"TMP": "25", "PCP": "1.0mm", "PTY": "4"}, "RAIN"),
        ({"TMP": "30", "PCP": "강수없음", "PTY": "0"}, "HEAT"),
        ({"TMP": "35", "PCP": "강수없음", "PTY": "0"}, "SEVERE_HEAT"),
        ({"TMP": "-5", "PCP": "강수없음", "PTY": "0"}, "COLD"),
        ({"TMP": "-15", "PCP": "강수없음", "PTY": "0"}, "SEVERE_COLD"),
    ],
)
def test_new_forecast_inputs_preserve_existing_weather_outputs(forecast, expected):
    assert classify_weather(forecast) == expected


@pytest.mark.parametrize(
    "pcp_code, expected",
    [
        ("0", "CLEAR"),
        ("1", "RAIN"),
        ("2", "RAIN"),
        ("3", "HEAVY_RAIN"),
    ],
)
def test_extended_qualitative_pcp_codes_preserve_rain_thresholds(pcp_code, expected):
    forecast = {
        "TMP": "20",
        "PCP": pcp_code,
        "PTY": "0" if pcp_code == "0" else "1",
        "_qualitative_pcp": True,
    }
    assert classify_weather(forecast) == expected


def test_extended_qualitative_pcp_code_2_preserves_existing_heavy_snow_threshold():
    assert classify_weather(
        {
            "TMP": "-2",
            "PCP": "2",
            "PTY": "3",
            "_qualitative_pcp": True,
        }
    ) == "HEAVY_SNOW"


def test_extended_qualitative_pcp_rejects_unknown_code():
    with pytest.raises(RuntimeError):
        classify_weather(
            {
                "TMP": "20",
                "PCP": "4",
                "PTY": "1",
                "_qualitative_pcp": True,
            }
        )


def test_unexpected_pty_code_is_not_silently_treated_as_clear():
    with pytest.raises(RuntimeError):
        classify_weather({"TMP": "25", "PCP": "강수없음", "PTY": "9"})


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
