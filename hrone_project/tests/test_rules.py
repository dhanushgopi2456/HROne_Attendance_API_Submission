"""Database-independent tests for the attendance calculation rules."""
from datetime import date, datetime, timedelta, timezone

from app.main import (
    attendance_date,
    compute_late_minutes,
    compute_overtime_minutes,
    compute_work_hours,
    department_trend_pipeline,
    is_half_day,
    month_bounds,
    ms_to_dt,
    working_days_between,
)

IST = timezone(timedelta(hours=5, minutes=30))
UTC = timezone.utc


def at_ist(day: int, hour: int, minute: int, second: int = 0) -> int:
    return int(datetime(2026, 7, day, hour, minute, second, tzinfo=IST).timestamp() * 1000)


def test_late_grace_is_strict_and_whole_minutes_are_floored():
    employee = {"shift_start": "09:30", "shift_end": "18:30"}
    assert compute_late_minutes(ms_to_dt(at_ist(6, 9, 40)), employee, "2026-07-06") == 0
    assert compute_late_minutes(ms_to_dt(at_ist(6, 9, 40, 1)), employee, "2026-07-06") == 10


def test_overnight_date_attribution_and_next_day_overtime():
    employee = {"shift_start": "22:00", "shift_end": "06:00"}
    punch_in = ms_to_dt(at_ist(7, 4, 0))
    punch_out = ms_to_dt(at_ist(7, 6, 30))
    assert attendance_date(punch_in, employee) == "2026-07-06"
    assert compute_overtime_minutes(punch_out, employee, "2026-07-06") == 30


def test_work_hours_round_half_up_before_half_day_check():
    punch_in = ms_to_dt(at_ist(6, 9, 30))
    rounded_boundary = ms_to_dt(at_ist(6, 13, 59, 42))
    below_boundary = ms_to_dt(at_ist(6, 13, 59, 41))
    assert compute_work_hours(punch_in, rounded_boundary) == 4.5
    assert is_half_day(compute_work_hours(punch_in, rounded_boundary)) is False
    assert is_half_day(compute_work_hours(punch_in, below_boundary)) is True


def test_working_days_and_leap_month_bounds():
    assert working_days_between(date(2026, 7, 6), date(2026, 7, 10)) == 5
    assert month_bounds("2024-02") == ("2024-02-01", "2024-02-29")


def test_trend_pipeline_uses_date_values_for_densify_bounds():
    pipeline = department_trend_pipeline(
        "Research", date(2026, 7, 6), date(2026, 7, 8),
    )
    densify = next(stage["$densify"] for stage in pipeline if "$densify" in stage)
    lower, upper = densify["range"]["bounds"]
    assert densify["field"] == "date"
    assert lower == datetime(2026, 7, 6, tzinfo=UTC)
    assert upper == datetime(2026, 7, 8, 23, 59, 59, 999000, tzinfo=UTC)
    assert lower.tzinfo is not None and upper.tzinfo is not None


def test_trend_pipeline_seeds_empty_period_and_builds_moving_average():
    pipeline = department_trend_pipeline(
        "Research", date(2026, 7, 6), date(2026, 7, 8),
    )
    union = next(stage["$unionWith"] for stage in pipeline if "$unionWith" in stage)
    assert union["coll"] == "employees"
    assert any("$lookup" in stage and stage["$lookup"].get("as") == "headcount_result" for stage in pipeline)
    window = next(stage["$setWindowFields"] for stage in pipeline if "$setWindowFields" in stage)
    assert window["output"]["moving_avg_7d_raw"]["window"]["documents"] == [-6, 0]
