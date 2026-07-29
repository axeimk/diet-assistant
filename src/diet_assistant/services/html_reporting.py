from __future__ import annotations

import statistics
from collections.abc import Callable, Sequence
from datetime import date, time, timedelta
from functools import cache
from importlib.resources import files
from pathlib import Path
from typing import Literal, TypedDict

from jinja2 import Environment, PackageLoader, select_autoescape

from ..util import with_weekday
from .finding import Finding
from .nutrition import NutrientComparison
from .reporting import (
    NUTRIENT_LABELS,
    NUTRIENT_STATUS_LABELS,
    PACE_TOLERANCE,
    DailySummary,
    GoalProgress,
    PeriodSummary,
    findings_lines,
    nutrient_reference,
    period_summary,
)

# 記録が始まる前の空白でグラフが潰れないよう、記録の無い先頭を落として表示する。
# 最初の記録の1つ前まで残して線が左端に張り付くのを避け、点が1つだけの図にはしない。
TREND_MARGIN_POINTS = 1
MIN_TREND_POINTS = 3


class StatusBadge(TypedDict):
    """状態の表示。色だけに意味を持たせないよう、必ず記号（mark）と語（label）を伴う。"""

    tone: Literal["good", "warn", "muted"]
    mark: Literal["check", "up", "down", "alert", "dash"]
    label: str


# 目標の達成判定。データ不足は良し悪しではないので色を付けない。
_GOAL_OUTCOME_BADGES: dict[str, StatusBadge] = {
    "challenge_achieved": {"tone": "good", "mark": "check", "label": "挑戦目標達成"},
    "success_threshold_achieved": {
        "tone": "good",
        "mark": "check",
        "label": "達成最低ライン達成",
    },
    "not_achieved": {"tone": "warn", "mark": "alert", "label": "未達"},
    "insufficient_data": {"tone": "muted", "mark": "dash", "label": "データ不足"},
}

def nutrient_badge(comparison: NutrientComparison) -> StatusBadge:
    status = comparison["status"]
    label = NUTRIENT_STATUS_LABELS[status]
    if status == "within":
        return {"tone": "good", "mark": "check", "label": label}
    return {"tone": "warn", "mark": "down" if status == "below" else "up", "label": label}


def calorie_badge(summary: DailySummary) -> StatusBadge | None:
    """摂取カロリーを計画の目標範囲と突き合わせる。範囲が無ければ判定しない。

    下回りも「不足」として指摘する。無条件に達成と見せて過度な制限を促さない。
    """
    minimum = summary["target_calorie_range_min"]
    maximum = summary["target_calorie_range_max"]
    if not summary["meals"] or minimum is None or maximum is None:
        return None
    actual = summary["totals"]["estimated_calories"]
    if actual > maximum:
        return {"tone": "warn", "mark": "up", "label": "超過"}
    if actual < minimum:
        return {"tone": "warn", "mark": "down", "label": "不足"}
    return {"tone": "good", "mark": "check", "label": "範囲内"}


def goal_badge(goal_evaluation: dict[str, object] | None) -> StatusBadge | None:
    outcome = goal_evaluation.get("outcome") if goal_evaluation else None
    return _GOAL_OUTCOME_BADGES.get(outcome) if isinstance(outcome, str) else None


def goal_progress_badge(progress: GoalProgress | None) -> StatusBadge | None:
    if progress is None or progress["status"] is None:
        return None
    if progress["status"] == "on_track":
        return {"tone": "good", "mark": "check", "label": "順調"}
    return {"tone": "warn", "mark": "alert", "label": "遅れ"}


def pace_badge(summary: PeriodSummary) -> StatusBadge | None:
    """週の体重変化を計画のペースと比べる。目標ペースが無ければ判定しない。"""
    target = summary.get("target_weekly_weight_change")
    changes = summary.get("changes")
    actual = changes["average_weight"] if changes else None
    if target is None or actual is None:
        return None
    if actual > target + PACE_TOLERANCE:
        return {"tone": "warn", "mark": "alert", "label": "計画より遅い"}
    return {"tone": "good", "mark": "check", "label": "計画どおり"}


class DailyTrendPoint(TypedDict):
    date: str
    calories: float | None
    calories_min: float | None
    calories_max: float | None
    target_calories: float | None
    weight: float | None
    weight_moving_average: float | None
    exercise_minutes: float | None


class WeeklyTrendPoint(TypedDict):
    period_start: str
    period_end: str
    average_calories: float | None
    average_weight: float | None
    exercise_minutes: float | None
    recorded_meal_days: int
    weight_measurements: int
    recorded_exercise_days: int


def daily_trend(
    path: Path,
    end_day: date,
    *,
    days: int = 28,
    day_start: time = time.min,
) -> list[DailyTrendPoint]:
    summaries = period_summary(path, end_day, days, day_start=day_start)["daily"]
    result: list[DailyTrendPoint] = []
    for index, summary in enumerate(summaries):
        window = summaries[max(0, index - 6) : index + 1]
        weights = [
            metric["weight"]
            for entry in window
            if (metric := entry["metric"]) is not None and metric["weight"] is not None
        ]
        has_calories = any(meal["estimated_calories"] is not None for meal in summary["meals"])
        has_minimum = any(meal["calories_min"] is not None for meal in summary["meals"])
        has_maximum = any(meal["calories_max"] is not None for meal in summary["meals"])
        has_exercise_minutes = any(
            exercise["duration_minutes"] is not None for exercise in summary["exercises"]
        )
        metric = summary["metric"]
        result.append(
            {
                "date": summary["date"],
                "calories": (
                    summary["totals"]["estimated_calories"] if has_calories else None
                ),
                "calories_min": summary["totals"]["calories_min"] if has_minimum else None,
                "calories_max": summary["totals"]["calories_max"] if has_maximum else None,
                "target_calories": summary["target_daily_calories"],
                "weight": metric["weight"] if metric else None,
                "weight_moving_average": (
                    round(statistics.fmean(weights), 2) if len(weights) >= 3 else None
                ),
                "exercise_minutes": (
                    summary["totals"]["exercise_minutes"] if has_exercise_minutes else None
                ),
            }
        )
    # 移動平均は範囲外の日も使うので、絞り込みは全期間を組み立てた後に行う。
    return _trim_leading_gap(result, _has_daily_record)


def _has_daily_record(point: DailyTrendPoint) -> bool:
    # target_caloriesは記録ではなく計画なので、記録の有無には数えない。
    return any(
        point[key] is not None for key in ("calories", "weight", "exercise_minutes")
    )


def _has_weekly_record(point: WeeklyTrendPoint) -> bool:
    return any(
        point[key] > 0
        for key in ("recorded_meal_days", "weight_measurements", "recorded_exercise_days")
    )


def _trim_leading_gap[T](points: list[T], has_record: Callable[[T], bool]) -> list[T]:
    first = next((index for index, point in enumerate(points) if has_record(point)), None)
    if first is None:
        return points
    start = min(first - TREND_MARGIN_POINTS, len(points) - MIN_TREND_POINTS)
    return points[max(0, start) :]


def weekly_trend(
    path: Path,
    end_day: date,
    *,
    weeks: int = 12,
    day_start: time = time.min,
) -> list[WeeklyTrendPoint]:
    result: list[WeeklyTrendPoint] = []
    for weeks_ago in range(weeks - 1, -1, -1):
        week_end = end_day - timedelta(days=weeks_ago * 7)
        summary = period_summary(path, week_end, 7, day_start=day_start)
        result.append(
            {
                "period_start": summary["period_start"],
                "period_end": summary["period_end"],
                "average_calories": (
                    summary["average_calories"]
                    if summary["recorded_meal_days"] >= 4
                    else None
                ),
                "average_weight": (
                    summary["average_weight"] if summary["weight_measurements"] >= 3 else None
                ),
                "exercise_minutes": (
                    summary["exercise_minutes"]
                    if summary["recorded_exercise_days"] > 0
                    else None
                ),
                "recorded_meal_days": summary["recorded_meal_days"],
                "weight_measurements": summary["weight_measurements"],
                "recorded_exercise_days": summary["recorded_exercise_days"],
            }
        )
    return _trim_leading_gap(result, _has_weekly_record)


def daily_html(
    summary: DailySummary,
    feedback: dict[str, object] | None,
    findings: Sequence[Finding],
    goal_evaluation: dict[str, object] | None,
    trend: list[DailyTrendPoint],
    *,
    goal_progress: GoalProgress | None = None,
) -> str:
    return _render(
        "daily.html",
        title=f"日次レポート {summary['date']}",
        heading="日次レポート",
        period_label=with_weekday(summary["date"]),
        report_kind="daily",
        summary=summary,
        feedback=feedback,
        goal_evaluation=goal_evaluation,
        goal_progress=goal_progress,
        trend=trend,
        finding_lines=findings_lines(findings),
        nutrient_labels=list(NUTRIENT_LABELS.items()),
        nutrient_reference=nutrient_reference,
        nutrient_badge=nutrient_badge,
        calorie_badge=calorie_badge(summary),
        goal_badge=goal_badge(goal_evaluation),
        goal_progress_badge=goal_progress_badge(goal_progress),
    )


def weekly_html(
    summary: PeriodSummary,
    feedback: dict[str, object] | None,
    findings: Sequence[Finding],
    trend: list[WeeklyTrendPoint],
) -> str:
    progress = summary.get("goal_progress")
    return _render(
        "weekly.html",
        title=f"週次レポート {summary['period_start']}〜{summary['period_end']}",
        heading="週次レポート",
        period_label=f"{with_weekday(summary['period_start'])} — "
        + with_weekday(summary["period_end"]),
        report_kind="weekly",
        summary=summary,
        feedback=feedback,
        goal_progress=progress,
        finding_lines=findings_lines(findings),
        pace_badge=pace_badge(summary),
        goal_progress_badge=goal_progress_badge(progress),
        trend=trend,
    )


def _render(template_name: str, **context: object) -> str:
    template = _environment().get_template(template_name)
    return template.render(
        **context,
        chart_js=_asset_text("chart.umd.min.js"),
        report_css=_asset_text("report.css"),
        report_js=_asset_text("report.js"),
    )


@cache
def _environment() -> Environment:
    environment = Environment(
        loader=PackageLoader("diet_assistant", "templates"),
        autoescape=select_autoescape(("html",)),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    environment.filters["with_weekday"] = with_weekday
    return environment


@cache
def _asset_text(name: str) -> str:
    return files("diet_assistant").joinpath("assets", name).read_text(encoding="utf-8")
