from datetime import date
from pathlib import Path

from diet_assistant.repository import add_meal, insert
from diet_assistant.services.html_reporting import daily_trend, weekly_trend
from diet_assistant.util import now_iso


def _add_metric(path: Path, measured_at: str, weight: float) -> None:
    _ = insert(
        path,
        "body_metrics",
        {
            "measured_at": measured_at,
            "weight": weight,
            "body_fat_percentage": None,
            "waist": None,
            "note": None,
            "created_at": now_iso(),
        },
    )


def _add_exercise(path: Path, performed_at: str, minutes: float) -> None:
    _ = insert(
        path,
        "exercises",
        {
            "performed_at": performed_at,
            "exercise_type": "walking",
            "duration_minutes": minutes,
            "distance": None,
            "sets": None,
            "repetitions": None,
            "weight": None,
            "intensity": None,
            "estimated_calories_burned": None,
            "note": None,
            "source": "manual",
            "created_at": now_iso(),
            "updated_at": now_iso(),
        },
    )


def test_daily_trend_preserves_missing_values_and_requires_three_weights(
    db_path: Path,
) -> None:
    _ = add_meal(
        db_path,
        {
            "eaten_at": "2026-07-21T12:00:00+09:00",
            "meal_type": "lunch",
            "estimated_calories": 1400,
            "calories_min": 1250,
            "calories_max": 1550,
        },
    )
    _add_metric(db_path, "2026-07-17T07:00:00+09:00", 70.0)
    _add_metric(db_path, "2026-07-19T07:00:00+09:00", 69.8)
    _add_metric(db_path, "2026-07-21T07:00:00+09:00", 69.6)

    trend = daily_trend(db_path, date(2026, 7, 21), days=7)

    assert len(trend) == 7
    assert trend[0]["calories"] is None
    assert trend[-1]["calories"] == 1400
    assert trend[-1]["calories_min"] == 1250
    assert trend[-1]["calories_max"] == 1550
    assert trend[-1]["weight_moving_average"] == 69.8
    assert trend[-3]["weight_moving_average"] is None
    assert trend[-1]["exercise_minutes"] is None


def test_weekly_trend_requires_four_meal_days_and_three_weight_measurements(
    db_path: Path,
) -> None:
    for day in (8, 10, 12):
        _ = add_meal(
            db_path,
            {
                "eaten_at": f"2026-07-{day:02d}T12:00:00+09:00",
                "meal_type": "lunch",
                "estimated_calories": 1600,
            },
        )
    for day in (15, 17, 19, 21):
        _ = add_meal(
            db_path,
            {
                "eaten_at": f"2026-07-{day:02d}T12:00:00+09:00",
                "meal_type": "lunch",
                "estimated_calories": 1500,
            },
        )
    _add_metric(db_path, "2026-07-15T07:00:00+09:00", 70.0)
    _add_metric(db_path, "2026-07-18T07:00:00+09:00", 69.8)
    _add_metric(db_path, "2026-07-21T07:00:00+09:00", 69.6)
    _add_exercise(db_path, "2026-07-20T18:00:00+09:00", 35)

    trend = weekly_trend(db_path, date(2026, 7, 21), weeks=2)

    assert trend[0]["recorded_meal_days"] == 3
    assert trend[0]["average_calories"] is None
    assert trend[1]["recorded_meal_days"] == 4
    assert trend[1]["average_calories"] == 1500
    assert trend[1]["average_weight"] == 69.8
    assert trend[1]["exercise_minutes"] == 35
