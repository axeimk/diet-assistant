from datetime import date
from pathlib import Path
from typing import Literal

from diet_assistant.repository import add_meal, insert
from diet_assistant.services.finding import Finding
from diet_assistant.services.html_reporting import (
    daily_html,
    daily_trend,
    weekly_html,
    weekly_trend,
)
from diet_assistant.services.nutrition import NutrientComparison
from diet_assistant.services.reporting import (
    DailySummary,
    GoalProgress,
    daily_markdown,
    daily_summary,
    weekly_summary,
)
from diet_assistant.util import now_iso


def _finding(kind: str, severity: Literal["info", "attention"]) -> Finding:
    return {
        "group": "calorie",
        "kind": kind,
        "severity": severity,
        "actual": 2300.0,
        "reference": 2100.0,
        "reference_basis": "計画の摂取目標",
        "unit": "kcal/日",
        "period_days": 1,
        "sample_days": 1,
        "calorie_headroom": -200.0,
        "resolution": "reduce",
        "detail": {},
    }


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


def _figures(html: str) -> str:
    """冒頭のサマリー部分だけを取り出す。CSSに含まれるクラス名を拾わないため。"""
    return html.split('<section class="figures"', 1)[1].split("</section>", 1)[0]


def _lunch_summary(db_path: Path, calories: float) -> DailySummary:
    _ = add_meal(
        db_path,
        {
            "eaten_at": "2026-07-21T12:00:00+09:00",
            "meal_type": "lunch",
            "estimated_calories": calories,
        },
    )
    return daily_summary(db_path, date(2026, 7, 21))


def test_daily_html_marks_calorie_difference_against_the_plan_range(
    db_path: Path,
) -> None:
    summary = _lunch_summary(db_path, 2300)
    summary["target_daily_calories"] = 2000
    summary["target_calorie_range_min"] = 1900
    summary["target_calorie_range_max"] = 2100
    summary["difference_from_target"] = 300

    figures = _figures(daily_html(summary, None, [], None, []))

    assert 'class="status status--warn status--up">超過' in figures


def test_daily_html_marks_calorie_difference_within_the_plan_range(
    db_path: Path,
) -> None:
    summary = _lunch_summary(db_path, 2000)
    summary["target_daily_calories"] = 2000
    summary["target_calorie_range_min"] = 1900
    summary["target_calorie_range_max"] = 2100
    summary["difference_from_target"] = 0

    figures = _figures(daily_html(summary, None, [], None, []))

    assert 'class="status status--good status--check">範囲内' in figures


def test_daily_html_marks_calorie_shortfall_as_attention(db_path: Path) -> None:
    summary = _lunch_summary(db_path, 1200)
    summary["target_daily_calories"] = 2000
    summary["target_calorie_range_min"] = 1900
    summary["target_calorie_range_max"] = 2100
    summary["difference_from_target"] = -800

    figures = _figures(daily_html(summary, None, [], None, []))

    # 目標を大きく下回るのも指摘。無条件に「達成」と見せて過度な制限を促さない。
    assert 'class="status status--warn status--down">不足' in figures


def test_daily_html_leaves_calorie_difference_unmarked_without_a_range(
    db_path: Path,
) -> None:
    summary = _lunch_summary(db_path, 2300)
    summary["target_daily_calories"] = 2000
    summary["difference_from_target"] = 300

    figures = _figures(daily_html(summary, None, [], None, []))

    assert 'class="status' not in figures


def test_daily_html_marks_goal_outcome(db_path: Path) -> None:
    summary = _lunch_summary(db_path, 2000)

    def verdict(outcome: str) -> str:
        evaluation: dict[str, object] = {
            "outcome": outcome,
            "period_start": "2026-07-21",
            "period_end": "2026-07-21",
        }
        html = daily_html(summary, None, [], evaluation, [])
        return html.split("目標の達成判定", 1)[1].split("</section>", 1)[0]

    assert 'class="status status--good status--check">挑戦目標達成' in verdict(
        "challenge_achieved"
    )
    assert 'class="status status--warn status--alert">未達' in verdict("not_achieved")
    assert 'class="status status--muted status--dash">データ不足' in verdict(
        "insufficient_data"
    )


def test_daily_html_marks_low_confidence_meals_in_the_entry_list(
    db_path: Path,
) -> None:
    _ = add_meal(
        db_path,
        {
            "eaten_at": "2026-07-21T12:00:00+09:00",
            "meal_type": "lunch",
            "estimated_calories": 900,
            "estimation_confidence": "low",
        },
    )
    summary = daily_summary(db_path, date(2026, 7, 21))

    html = daily_html(summary, None, [], None, [])

    entries = html.split("<ol class=\"entries\">", 1)[1]
    assert "確信度低" in entries


def test_daily_html_separates_attention_findings_from_reference_ones(
    db_path: Path,
) -> None:
    summary = _lunch_summary(db_path, 2000)
    findings: list[Finding] = [
        _finding("calorie_average_above_target", "attention"),
        _finding("calorie_average_recorded", "info"),
    ]

    html = daily_html(summary, None, findings, None, [])

    assert 'class="findings__item findings__item--attention"' in html
    assert 'class="findings__item findings__item--info"' in html
    assert "指摘" in html
    assert "参考" in html


def test_weekly_html_marks_the_pace_against_the_plan(db_path: Path) -> None:
    summary = weekly_summary(db_path, date(2026, 7, 21))
    summary["target_weekly_weight_change"] = -0.5

    def pace(weekly_change: float) -> str:
        summary["changes"] = {"average_calories": None, "average_weight": weekly_change}
        return _figures(weekly_html(summary, None, [], []))

    assert 'class="status status--good status--check">計画どおり' in pace(-0.6)
    # findings と同じ +0.1 kg/週 の許容。境界はまだ「遅い」としない。
    assert 'class="status status--good status--check">計画どおり' in pace(-0.4)
    assert 'class="status status--warn status--alert">計画より遅い' in pace(-0.2)


def test_daily_reports_show_all_three_paces_and_on_track_status(db_path: Path) -> None:
    summary = _lunch_summary(db_path, 1600)
    progress: GoalProgress = {
        "initial_target_weekly_weight_change": -0.9,
        "current_required_weekly_weight_change": -0.3,
        "actual_weekly_weight_change": -1.1,
        "status": "on_track",
        "current_weight_measurements": 7,
        "previous_weight_measurements": 5,
    }

    markdown = daily_markdown(summary, goal_progress=progress)
    html = daily_html(summary, None, [], None, [], goal_progress=progress)

    for text in ("当初目標ペース", "現在必要ペース（参考）", "実績ペース（参考）", "順調"):
        assert text in markdown
        assert text in html
    assert "-0.9 kg/週" in markdown
    assert "-0.3 kg/週" in markdown
    assert "-1.1 kg/週" in markdown


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

    # 7日中、最初の記録（07-17）の1日前から。
    assert [point["date"] for point in trend] == [
        f"2026-07-{day:02d}" for day in range(16, 22)
    ]
    assert trend[0]["calories"] is None
    assert trend[-1]["calories"] == 1400
    assert trend[-1]["calories_min"] == 1250
    assert trend[-1]["calories_max"] == 1550
    assert trend[-1]["weight_moving_average"] == 69.8
    assert trend[-3]["weight_moving_average"] is None
    assert trend[-1]["exercise_minutes"] is None


def test_daily_html_shows_weekdays_outside_the_charts(db_path: Path) -> None:
    summary = _lunch_summary(db_path, 2000)
    trend = daily_trend(db_path, date(2026, 7, 21), days=7)

    evaluation: dict[str, object] = {
        "outcome": "not_achieved",
        "period_start": "2026-07-20",
        "period_end": "2026-07-26",
    }

    html = daily_html(summary, None, [], evaluation, trend)

    assert '<p class="masthead__date">2026-07-21（火）</p>' in html
    assert '<th scope="row">2026-07-21（火）</th>' in html
    verdict = html.split("目標の達成判定", 1)[1].split("</section>", 1)[0]
    assert "2026-07-20（月） — 2026-07-26（日）" in verdict
    # グラフのラベルは素の日付のまま。曜日は入れない。
    chart_data = html.split('id="report-data"', 1)[1].split("</script>", 1)[0]
    assert '"date": "2026-07-21"' in chart_data
    assert "（火）" not in chart_data


def test_weekly_html_shows_weekdays_in_the_daily_table(db_path: Path) -> None:
    summary = weekly_summary(db_path, date(2026, 7, 26))

    html = weekly_html(summary, None, [], [])

    assert '<p class="masthead__date">2026-07-20（月） — 2026-07-26（日）</p>' in html
    assert '<th scope="row">2026-07-20（月）</th>' in html
    assert '<th scope="row">2026-07-26（日）</th>' in html


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
    assert 'class="status status--warn status--down">不足' in nutrition
    assert 'class="status status--good status--check">範囲内' in nutrition
    assert 'class="status status--warn status--up">超過' in nutrition


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
    assert 'class="status' not in nutrition
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

    # 最初の記録の1日前まで残し、記録が線の左端に張り付かないようにする。
    assert [point["date"] for point in trend] == [
        f"2026-07-{day:02d}" for day in range(15, 22)
    ]


def test_daily_trend_keeps_three_days_when_records_are_fewer(db_path: Path) -> None:
    _add_metric(db_path, "2026-07-21T07:00:00+09:00", 69.6)

    trend = daily_trend(db_path, date(2026, 7, 21), days=28)

    assert [point["date"] for point in trend] == ["2026-07-19", "2026-07-20", "2026-07-21"]


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

    assert trend[0]["date"] == "2026-07-14"
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

    assert len(trend) == 3
    assert trend[-1]["period_end"] == "2026-07-21"
    assert trend[0]["period_end"] == "2026-07-07"


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
