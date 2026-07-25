from datetime import date
from pathlib import Path
from typing import Literal

from diet_assistant.repository import add_meal, insert
from diet_assistant.services.html_reporting import daily_html, daily_trend, weekly_trend
from diet_assistant.services.nutrition import NutrientComparison
from diet_assistant.services.reporting import daily_summary
from diet_assistant.util import now_iso


def _comparison(
    actual: float,
    *,
    minimum: float | None,
    maximum: float | None,
    status: Literal["below", "within", "above"],
    difference: float,
) -> NutrientComparison:
    return {
        "actual": actual,
        "minimum": minimum,
        "maximum": maximum,
        "unit": "g",
        "basis": "テスト",
        "status": status,
        "difference": difference,
    }


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


def test_daily_html_marks_each_nutrient_status(db_path: Path) -> None:
    _ = add_meal(
        db_path,
        {
            "eaten_at": "2026-07-21T12:00:00+09:00",
            "meal_type": "lunch",
            "estimated_calories": 1500,
            "protein": 52.0,
            "fiber": 21.0,
            "sodium": 9.2,
        },
    )
    summary = daily_summary(db_path, date(2026, 7, 21))
    summary["recorded_nutrients"] = ["protein", "fiber", "sodium"]
    summary["nutrients"] = {
        "protein": _comparison(52.0, minimum=60.0, maximum=90.0, status="below", difference=-8.0),
        "fiber": _comparison(21.0, minimum=20.0, maximum=None, status="within", difference=0.0),
        "sodium": _comparison(9.2, minimum=None, maximum=7.5, status="above", difference=1.7),
    }

    html = daily_html(summary, None, [], None, [])

    nutrition = html.split("栄養集計", 1)[1]
    assert 'class="nutrient nutrient--below"' in nutrition
    assert 'class="nutrient nutrient--within"' in nutrition
    assert 'class="nutrient nutrient--above"' in nutrition
    assert "不足" in nutrition
    assert "範囲内" in nutrition
    assert "超過" in nutrition


def test_daily_html_omits_nutrient_status_without_reference(db_path: Path) -> None:
    _ = add_meal(
        db_path,
        {
            "eaten_at": "2026-07-21T12:00:00+09:00",
            "meal_type": "lunch",
            "estimated_calories": 1500,
            "protein": 52.0,
        },
    )
    summary = daily_summary(db_path, date(2026, 7, 21))
    summary["recorded_nutrients"] = ["protein"]

    html = daily_html(summary, None, [], None, [])

    nutrition = html.split("栄養集計", 1)[1]
    assert "nutrient--" not in nutrition
    assert "目安未設定" in nutrition


def test_daily_trend_drops_leading_days_without_records(db_path: Path) -> None:
    for day in (16, 17, 18, 19, 20, 21):
        _ = add_meal(
            db_path,
            {
                "eaten_at": f"2026-07-{day:02d}T12:00:00+09:00",
                "meal_type": "lunch",
                "estimated_calories": 1500,
            },
        )

    trend = daily_trend(db_path, date(2026, 7, 21), days=28)

    assert [point["date"] for point in trend] == [
        f"2026-07-{day:02d}" for day in range(15, 22)
    ]


def test_daily_trend_keeps_seven_days_when_records_are_fewer(db_path: Path) -> None:
    _add_metric(db_path, "2026-07-21T07:00:00+09:00", 69.6)

    trend = daily_trend(db_path, date(2026, 7, 21), days=28)

    assert len(trend) == 7
    assert trend[0]["date"] == "2026-07-15"


def test_daily_trend_keeps_full_window_when_records_span_it(db_path: Path) -> None:
    for day in (24, 30):
        _add_metric(db_path, f"2026-06-{day:02d}T07:00:00+09:00", 70.0)

    trend = daily_trend(db_path, date(2026, 7, 21), days=28)

    assert len(trend) == 28
    assert trend[0]["date"] == "2026-06-24"


def test_daily_trend_moving_average_uses_days_outside_the_visible_range(
    db_path: Path,
) -> None:
    for day in (15, 17, 19):
        _add_metric(db_path, f"2026-07-{day:02d}T07:00:00+09:00", 70.0)
    _ = add_meal(
        db_path,
        {
            "eaten_at": "2026-07-21T12:00:00+09:00",
            "meal_type": "lunch",
            "estimated_calories": 1500,
        },
    )

    trend = daily_trend(db_path, date(2026, 7, 21), days=28)

    assert trend[0]["date"] == "2026-07-15"
    assert trend[-1]["weight_moving_average"] == 70.0


def test_weekly_trend_drops_leading_weeks_without_records(db_path: Path) -> None:
    for day in (15, 17, 19, 21):
        _ = add_meal(
            db_path,
            {
                "eaten_at": f"2026-07-{day:02d}T12:00:00+09:00",
                "meal_type": "lunch",
                "estimated_calories": 1500,
            },
        )

    trend = weekly_trend(db_path, date(2026, 7, 21), weeks=12)

    assert len(trend) == 4
    assert trend[-1]["period_end"] == "2026-07-21"
    assert trend[0]["period_end"] == "2026-06-30"


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
