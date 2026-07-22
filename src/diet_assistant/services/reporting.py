from __future__ import annotations

import sqlite3
import statistics
from collections.abc import Iterable
from datetime import date, time, timedelta
from pathlib import Path
from typing import NotRequired, TypedDict, cast

from ..db import connect
from ..util import day_bounds, require_str


class MealRecord(TypedDict):
    id: int
    eaten_at: str
    meal_type: str
    note: str | None
    estimated_calories: float | None
    calories_min: float | None
    calories_max: float | None
    protein: float | None
    fat: float | None
    carbohydrates: float | None
    fiber: float | None
    sodium: float | None
    estimation_confidence: str | None


class ExerciseRecord(TypedDict):
    duration_minutes: float | None


class MetricRecord(TypedDict):
    weight: float | None


class Totals(TypedDict):
    estimated_calories: float
    calories_min: float
    calories_max: float
    protein: float
    fat: float
    carbohydrates: float
    fiber: float
    sodium: float
    exercise_minutes: float


class DailySummary(TypedDict):
    date: str
    meals: list[MealRecord]
    exercises: list[ExerciseRecord]
    metric: MetricRecord | None
    totals: Totals
    target_daily_calories: float | None
    difference_from_target: float | None
    uncertain_meal_ids: list[int]


class Changes(TypedDict):
    average_calories: float | None
    average_weight: float | None


class PeriodSummary(TypedDict):
    period_start: str
    period_end: str
    days: int
    average_calories: float | None
    exercise_minutes: float
    average_weight: float | None
    weight_measurements: int
    recorded_meal_days: int
    daily: list[DailySummary]
    previous_week: NotRequired[dict[str, float | None]]
    changes: NotRequired[Changes]
    target_weekly_weight_change: NotRequired[float | None]
    pace_difference: NotRequired[float | None]


def daily_summary(path: Path, day: date, *, day_start: time = time.min) -> DailySummary:
    start, end = day_bounds(day, starts_at=day_start)
    with connect(path) as connection:
        meal_rows = cast(
            list[sqlite3.Row],
            connection.execute(
                "SELECT * FROM meals WHERE eaten_at BETWEEN ? AND ? ORDER BY eaten_at", (start, end)
            ).fetchall(),
        )
        meals = [cast(MealRecord, cast(object, dict(row))) for row in meal_rows]
        exercise_rows = cast(
            list[sqlite3.Row],
            connection.execute(
                "SELECT * FROM exercises WHERE performed_at BETWEEN ? AND ? ORDER BY performed_at",
                (start, end),
            ).fetchall(),
        )
        exercises = [cast(ExerciseRecord, cast(object, dict(row))) for row in exercise_rows]
        metric = cast(
            sqlite3.Row | None,
            connection.execute(
                "SELECT * FROM body_metrics WHERE measured_at BETWEEN ? AND ? "
                + "ORDER BY measured_at DESC LIMIT 1",
                (start, end),
            ).fetchone(),
        )
        plan = cast(
            sqlite3.Row | None,
            connection.execute(
                "SELECT p.* FROM plans p JOIN goals g ON g.id=p.goal_id "
                + "WHERE g.status='active' AND p.status='active' ORDER BY p.id DESC LIMIT 1"
            ).fetchone(),
        )

    def numeric_total(values: Iterable[float | None]) -> float:
        return round(sum(value for value in values if value is not None), 1)

    calories = numeric_total(row["estimated_calories"] for row in meals)
    plan_record = cast(dict[str, object], dict(plan)) if plan else {}
    target_value = plan_record.get("target_daily_calories")
    target = float(target_value) if isinstance(target_value, (int, float)) else None
    metric_record = cast(MetricRecord, cast(object, dict(metric))) if metric else None
    return {
        "date": day.isoformat(),
        "meals": meals,
        "exercises": exercises,
        "metric": metric_record,
        "totals": {
            "estimated_calories": calories,
            "calories_min": numeric_total(row["calories_min"] for row in meals),
            "calories_max": numeric_total(row["calories_max"] for row in meals),
            "protein": numeric_total(row["protein"] for row in meals),
            "fat": numeric_total(row["fat"] for row in meals),
            "carbohydrates": numeric_total(row["carbohydrates"] for row in meals),
            "fiber": numeric_total(row["fiber"] for row in meals),
            "sodium": numeric_total(row["sodium"] for row in meals),
            "exercise_minutes": round(sum(row["duration_minutes"] or 0 for row in exercises), 1),
        },
        "target_daily_calories": target,
        "difference_from_target": round(calories - target, 1) if target and meals else None,
        "uncertain_meal_ids": [m["id"] for m in meals if m["estimation_confidence"] == "low"],
    }


def period_summary(
    path: Path, end_day: date, days: int = 7, *, day_start: time = time.min
) -> PeriodSummary:
    start_day = end_day - timedelta(days=days - 1)
    daily = [
        daily_summary(path, start_day + timedelta(days=i), day_start=day_start)
        for i in range(days)
    ]
    calories = [entry["totals"]["estimated_calories"] for entry in daily]
    weights = [
        entry["metric"]["weight"]
        for entry in daily
        if entry["metric"] and entry["metric"]["weight"]
    ]
    return {
        "period_start": start_day.isoformat(),
        "period_end": end_day.isoformat(),
        "days": days,
        "average_calories": round(statistics.fmean(calories), 1) if calories else None,
        "exercise_minutes": round(sum(d["totals"]["exercise_minutes"] for d in daily), 1),
        "average_weight": round(statistics.fmean(weights), 2) if weights else None,
        "weight_measurements": len(weights),
        "recorded_meal_days": sum(bool(d["meals"]) for d in daily),
        "daily": daily,
    }


def weekly_summary(path: Path, end_day: date, *, day_start: time = time.min) -> PeriodSummary:
    current = period_summary(path, end_day, 7, day_start=day_start)
    previous = period_summary(path, end_day - timedelta(days=7), 7, day_start=day_start)
    current["previous_week"] = {
        "average_calories": previous["average_calories"],
        "average_weight": previous["average_weight"],
    }
    changes: Changes = {
        "average_calories": _difference(current["average_calories"], previous["average_calories"]),
        "average_weight": _difference(current["average_weight"], previous["average_weight"]),
    }
    current["changes"] = changes
    with connect(path) as connection:
        plan = cast(
            sqlite3.Row | None,
            connection.execute(
                "SELECT p.target_weekly_weight_change FROM plans p "
                + "JOIN goals g ON g.id=p.goal_id "
                + "WHERE g.status='active' AND p.status='active' ORDER BY p.id DESC LIMIT 1"
            ).fetchone(),
        )
    target_value = plan["target_weekly_weight_change"] if plan else None
    target_change = float(target_value) if isinstance(target_value, (int, float)) else None
    actual_change = changes["average_weight"]
    current["target_weekly_weight_change"] = target_change
    current["pace_difference"] = _difference(actual_change, target_change)
    return current


def _difference(current: float | None, previous: float | None) -> float | None:
    return round(current - previous, 2) if current is not None and previous is not None else None


def daily_markdown(
    summary: DailySummary,
    advice: dict[str, object] | None = None,
    goal_evaluation: dict[str, object] | None = None,
) -> str:
    totals = summary["totals"]
    weight = summary["metric"]["weight"] if summary["metric"] else "記録なし"
    difference = summary["difference_from_target"]
    difference_text: float | str
    if difference is not None:
        difference_text = difference
    elif not summary["meals"]:
        difference_text = "食事記録なし"
    else:
        difference_text = "目標未設定"
    meal_lines = [
        f"- {m['eaten_at'][11:16]} {m['meal_type']}: {m['note'] or '（メモなし）'} "
        + f"({m['estimated_calories'] if m['estimated_calories'] is not None else '?'} kcal)"
        for m in summary["meals"]
    ] or ["- 記録なし"]
    advice_text = (
        require_str(advice, "situation")
        + " "
        + require_str(advice, "priority_action")
        if advice
        else "通常どおり記録を続け、単日の値ではなく7日以上の傾向で判断しましょう。"
    )
    outcome_labels = {
        "insufficient_data": "データ不足",
        "challenge_achieved": "挑戦目標達成",
        "success_threshold_achieved": "達成最低ライン達成",
        "not_achieved": "未達",
    }
    outcome = goal_evaluation.get("outcome") if goal_evaluation else None
    outcome_label = outcome_labels.get(str(outcome), str(outcome))
    evaluation_lines = (
        [
            "",
            "## 目標の達成判定",
            f"- 判定: {outcome_label}",
            f"- 評価期間: {goal_evaluation['period_start']}〜{goal_evaluation['period_end']}",
            f"- 体重平均: {goal_evaluation['average_weight']} kg",
            f"- 測定数: {goal_evaluation['weight_measurements']}/"
            + f"{goal_evaluation['evaluation_window_days']}日",
            f"- 最終判定: {'はい' if goal_evaluation['is_final'] else 'いいえ（途中経過）'}",
        ]
        if goal_evaluation
        else []
    )
    return "\n".join(
        [
            f"# 日次レポート {summary['date']}",
            "",
            "## 食事",
            *meal_lines,
            "",
            "## 集計",
            f"- 摂取カロリー: {totals['estimated_calories']} kcal "
            + f"（範囲 {totals['calories_min']}〜{totals['calories_max']} kcal）",
            f"- P/F/C/食物繊維: {totals['protein']}/{totals['fat']}/"
            + f"{totals['carbohydrates']}/{totals['fiber']} g",
            f"- 食塩相当量: {totals['sodium']} g",
            f"- 運動時間: {totals['exercise_minutes']}分",
            f"- 体重: {weight} kg",
            f"- 目標との差: {difference_text}",
            "",
            "## 短い助言",
            advice_text,
            *evaluation_lines,
            "",
            "## 不確実性の高い記録",
            f"- 食事ID: {summary['uncertain_meal_ids'] or 'なし'}",
            "",
        ]
    )


def weekly_markdown(summary: PeriodSummary, advice: dict[str, object]) -> str:
    changes = summary.get("changes")
    if changes is None:
        raise ValueError("週次集計にchangesがありません")
    average_weight = summary["average_weight"] or "算出不可"
    missing: list[str] = []
    if summary["recorded_meal_days"] < 7:
        missing.append(f"食事記録 {7 - summary['recorded_meal_days']}日分")
    if summary["weight_measurements"] < 3:
        missing.append("体重（週3回未満）")
    return "\n".join(
        [
            f"# 週次レポート {summary['period_start']}〜{summary['period_end']}",
            "",
            f"- 平均摂取カロリー: {summary['average_calories']} kcal/日",
            f"- 総運動時間: {summary['exercise_minutes']}分",
            f"- 体重7日平均: {average_weight} kg",
            "- 前週との差（カロリー/体重）: "
            + f"{changes['average_calories']} kcal / {changes['average_weight']} kg",
            f"- 目標ペース: {summary.get('target_weekly_weight_change')} kg/週",
            f"- 実績と目標ペースの差: {summary.get('pace_difference')} kg/週",
            "",
            "## よかった点",
            f"- {require_str(advice, 'keep')}",
            "",
            "## 調整したほうがよい点",
            f"- {require_str(advice, 'situation')}",
            "",
            "## 来週の最優先行動",
            f"- {require_str(advice, 'priority_action')}",
            "",
            "## 代替案",
            f"- {require_str(advice, 'alternative')}",
            "",
            "## 計画変更",
            f"- {require_str(advice, 'plan_change')}",
            "",
            "## データ不足",
            f"- {', '.join(missing) if missing else 'なし'}",
            "",
        ]
    )
