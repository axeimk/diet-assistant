from __future__ import annotations

import json
import sqlite3
import statistics
from collections.abc import Iterable, Mapping, Sequence
from datetime import date, time, timedelta
from pathlib import Path
from typing import Literal, NotRequired, TypedDict, cast

from ..db import connect
from ..util import day_bounds, require_str
from .finding import Finding
from .nutrition import NutrientComparison, NutrientTarget, compare_nutrients

NutrientKey = Literal["protein", "fat", "carbohydrates", "fiber", "sodium"]
NUTRIENT_KEYS: tuple[NutrientKey, ...] = ("protein", "fat", "carbohydrates", "fiber", "sodium")


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
    nutrient_targets: dict[str, NutrientTarget]
    nutrients: dict[str, NutrientComparison]
    recorded_nutrients: list[NutrientKey]
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
    recorded_exercise_days: int
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
                + "WHERE p.calculated_at <= ? AND g.started_at <= ? "
                + "ORDER BY p.calculated_at DESC, p.id DESC LIMIT 1",
                (end, day.isoformat()),
            ).fetchone(),
        )

    def numeric_total(values: Iterable[float | None]) -> float:
        return round(sum(value for value in values if value is not None), 1)

    calories = numeric_total(row["estimated_calories"] for row in meals)
    plan_record = cast(dict[str, object], dict(plan)) if plan else {}
    target_value = plan_record.get("target_daily_calories")
    target = float(target_value) if isinstance(target_value, (int, float)) else None
    metric_record = cast(MetricRecord, cast(object, dict(metric))) if metric else None
    nutrient_target_map = plan_nutrient_targets(plan_record)
    # 未記録の栄養素はゼロではなく欠損として扱い、目安との比較対象から外す（ADR 0007）。
    recorded: list[NutrientKey] = [
        name for name in NUTRIENT_KEYS if any(row[name] is not None for row in meals)
    ]
    totals: Totals = {
        "estimated_calories": calories,
        "calories_min": numeric_total(row["calories_min"] for row in meals),
        "calories_max": numeric_total(row["calories_max"] for row in meals),
        "protein": numeric_total(row["protein"] for row in meals),
        "fat": numeric_total(row["fat"] for row in meals),
        "carbohydrates": numeric_total(row["carbohydrates"] for row in meals),
        "fiber": numeric_total(row["fiber"] for row in meals),
        "sodium": numeric_total(row["sodium"] for row in meals),
        "exercise_minutes": round(sum(row["duration_minutes"] or 0 for row in exercises), 1),
    }
    return {
        "date": day.isoformat(),
        "meals": meals,
        "exercises": exercises,
        "metric": metric_record,
        "totals": totals,
        "target_daily_calories": target,
        "difference_from_target": round(calories - target, 1) if target and meals else None,
        "nutrient_targets": nutrient_target_map,
        "nutrients": compare_nutrients(_recorded_totals(totals, recorded), nutrient_target_map),
        "recorded_nutrients": recorded,
        "uncertain_meal_ids": [m["id"] for m in meals if m["estimation_confidence"] == "low"],
    }


def _recorded_totals(totals: Totals, recorded: Sequence[str]) -> dict[str, float]:
    values: dict[str, float] = {
        "protein": totals["protein"],
        "fat": totals["fat"],
        "carbohydrates": totals["carbohydrates"],
        "fiber": totals["fiber"],
        "sodium": totals["sodium"],
    }
    return {name: value for name, value in values.items() if name in recorded}


def plan_nutrient_targets(plan_record: Mapping[str, object]) -> dict[str, NutrientTarget]:
    raw = plan_record.get("nutrient_targets")
    if not isinstance(raw, str):
        return {}
    parsed = cast(object, json.loads(raw))
    return cast(dict[str, NutrientTarget], parsed) if isinstance(parsed, dict) else {}


def period_summary(
    path: Path, end_day: date, days: int = 7, *, day_start: time = time.min
) -> PeriodSummary:
    start_day = end_day - timedelta(days=days - 1)
    daily = [
        daily_summary(path, start_day + timedelta(days=i), day_start=day_start)
        for i in range(days)
    ]
    calories = [entry["totals"]["estimated_calories"] for entry in daily if entry["meals"]]
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
        "recorded_exercise_days": sum(bool(d["exercises"]) for d in daily),
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


NUTRIENT_LABELS: dict[str, NutrientKey] = {
    "たんぱく質": "protein",
    "脂質": "fat",
    "炭水化物": "carbohydrates",
    "食物繊維": "fiber",
    "食塩相当量": "sodium",
}


def nutrient_reference(comparison: NutrientComparison) -> str:
    """「目安 7.5 g未満 / +3.4」のような、目安と差の表示。MarkdownとHTMLで共用する。"""
    return f"目安 {_range_text(comparison)} / {_signed(comparison['difference'])}"


def _nutrient_line(label: str, name: NutrientKey, summary: DailySummary) -> str:
    if name not in summary["recorded_nutrients"]:
        return f"- {label}: 記録なし"
    actual = summary["totals"][name]
    comparison = summary["nutrients"].get(name)
    if comparison is None:
        return f"- {label}: {actual} g（目安未設定）"
    return f"- {label}: {actual} {comparison['unit']}（{nutrient_reference(comparison)}）"


def _range_text(comparison: NutrientComparison) -> str:
    minimum = comparison["minimum"]
    maximum = comparison["maximum"]
    unit = comparison["unit"]
    if minimum is not None and maximum is not None:
        return f"{_amount(minimum)}〜{_amount(maximum)} {unit}"
    if minimum is not None:
        return f"{_amount(minimum)} {unit}以上"
    return f"{_amount(maximum or 0)} {unit}未満"


def _amount(value: float) -> str:
    return str(int(value)) if value == int(value) else str(value)


_WRITE_HINT = "の分析結果から書いて `diet advice save` で保存する）"
_UNWRITTEN_DAILY = "（助言は未記載。`diet advice today` " + _WRITE_HINT
_UNWRITTEN_WEEKLY = "- （助言は未記載。`diet advice weekly` " + _WRITE_HINT

FINDING_LABELS = {
    "meal_records_missing": "食事記録のある日数",
    "calorie_average_recorded": "平均摂取カロリー",
    "calorie_average_above_target": "平均摂取カロリー（目標上限超過）",
    "calorie_average_below_target": "平均摂取カロリー（目標下限未満）",
    "calorie_average_within_target": "平均摂取カロリー（目標範囲内）",
    "meal_type_calorie_skew": "食事種別の偏り",
    "plan_basis_weight_stale": "計画の前提体重",
    "goal_pace_behind": "体重の変化ペース（目標より遅い）",
    "goal_pace_on_track": "体重の変化ペース（目標どおり）",
}
_NUTRIENT_STATUS_SUFFIX = {
    "_below_target": "不足",
    "_above_target": "超過",
    "_within_target": "範囲内",
}


def findings_markdown(findings: Sequence[Finding]) -> list[str]:
    """findingsを数値の箇条書きにする。助言の文面ではなく事実だけを並べる。"""
    return [_finding_line(finding) for finding in findings] or ["- 判断できる記録がありません"]


def _finding_line(finding: Finding) -> str:
    mark = "指摘" if finding["severity"] == "attention" else "参考"
    unit = finding["unit"]
    notes: list[str] = []
    reference = finding["reference"]
    if reference is not None:
        difference = round(finding["actual"] - reference, 2)
        notes.append(f"目安 {_amount(reference)} {unit} / {_signed(difference)}")
    meal_type = finding["detail"].get("meal_type")
    share = finding["detail"].get("share")
    if isinstance(meal_type, str) and isinstance(share, (int, float)):
        notes.append(f"{meal_type} が{round(float(share) * 100)}%")
    notes.append(f"{finding['sample_days']}日分")
    return (
        f"- [{mark}] {_finding_label(finding)}: "
        + f"{_amount(finding['actual'])} {unit}（{'、'.join(notes)}）"
    )


def _finding_label(finding: Finding) -> str:
    kind = finding["kind"]
    label = FINDING_LABELS.get(kind)
    if label is not None:
        return label
    for suffix, status in _NUTRIENT_STATUS_SUFFIX.items():
        if kind.endswith(suffix):
            nutrient = finding["detail"].get("label")
            name = nutrient if isinstance(nutrient, str) else kind[: -len(suffix)]
            return f"{name} {status}"
    return kind


def _signed(value: float) -> str:
    return "±0" if value == 0 else f"{'+' if value > 0 else '−'}{_amount(abs(value))}"


def daily_markdown(
    summary: DailySummary,
    advice: dict[str, object] | None = None,
    findings: Sequence[Finding] | None = None,
    goal_evaluation: dict[str, object] | None = None,
) -> str:
    totals = summary["totals"]
    metric = summary["metric"]
    weight_text = (
        f"{metric['weight']} kg"
        if metric is not None and metric["weight"] is not None
        else "記録なし"
    )
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
    advice_lines = (
        [require_str(advice, "situation") + " " + require_str(advice, "priority_action")]
        if advice
        else [_UNWRITTEN_DAILY]
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
    if summary["meals"]:
        calorie_lines = [
            f"- 摂取カロリー: {totals['estimated_calories']} kcal "
            + f"（範囲 {totals['calories_min']}〜{totals['calories_max']} kcal）",
            *(
                _nutrient_line(label, name, summary)
                for label, name in NUTRIENT_LABELS.items()
            ),
        ]
    else:
        calorie_lines = [
            "- 摂取カロリー: 記録なし",
            *(f"- {label}: 記録なし" for label in NUTRIENT_LABELS),
        ]
    exercise_text = (
        f"{totals['exercise_minutes']}分" if summary["exercises"] else "記録なし"
    )
    return "\n".join(
        [
            f"# 日次レポート {summary['date']}",
            "",
            "## 食事",
            *meal_lines,
            "",
            "## 集計",
            *calorie_lines,
            f"- 運動時間: {exercise_text}",
            f"- 体重: {weight_text}",
            f"- 目標との差: {difference_text}",
            "",
            "## 短い助言",
            *advice_lines,
            "",
            "## 分析結果",
            *findings_markdown(findings or []),
            *evaluation_lines,
            "",
            "## 不確実性の高い記録",
            f"- 食事ID: {summary['uncertain_meal_ids'] or 'なし'}",
            "",
        ]
    )


def _advice_item(advice: dict[str, object] | None, key: str) -> str:
    """保存済み助言の項目。未記載なら捏造せず、未記載と書く。"""
    if advice is None:
        return _UNWRITTEN_WEEKLY
    value = advice.get(key)
    return f"- {value}" if isinstance(value, str) and value else "- （未記載）"


def weekly_markdown(
    summary: PeriodSummary,
    advice: dict[str, object] | None = None,
    findings: Sequence[Finding] | None = None,
) -> str:
    changes = summary.get("changes")
    if changes is None:
        raise ValueError("週次集計にchangesがありません")
    average_weight = summary["average_weight"] or "算出不可"
    average_calories = (
        summary["average_calories"]
        if summary["average_calories"] is not None
        else "算出不可"
    )
    exercise_text = (
        f"{summary['exercise_minutes']}分"
        if summary["recorded_exercise_days"]
        else "記録なし"
    )
    missing: list[str] = []
    if summary["recorded_meal_days"] < 7:
        missing.append(f"食事記録 {7 - summary['recorded_meal_days']}日分")
    if summary["weight_measurements"] < 3:
        missing.append("体重（週3回未満）")
    return "\n".join(
        [
            f"# 週次レポート {summary['period_start']}〜{summary['period_end']}",
            "",
            f"- 平均摂取カロリー: {average_calories}"
            + (" kcal/日" if summary["average_calories"] is not None else ""),
            f"- 記録された運動時間: {exercise_text}",
            f"- 体重7日平均: {average_weight} kg",
            "- 前週との差（カロリー/体重）: "
            + f"{changes['average_calories']} kcal / {changes['average_weight']} kg",
            f"- 目標ペース: {summary.get('target_weekly_weight_change')} kg/週",
            f"- 実績と目標ペースの差: {summary.get('pace_difference')} kg/週",
            "",
            "## 分析結果",
            *findings_markdown(findings or []),
            "",
            "## よかった点",
            _advice_item(advice, "keep"),
            "",
            "## 調整したほうがよい点",
            _advice_item(advice, "situation"),
            "",
            "## 来週の最優先行動",
            _advice_item(advice, "priority_action"),
            "",
            "## 代替案",
            _advice_item(advice, "alternative"),
            "",
            "## 計画変更",
            _advice_item(advice, "plan_change"),
            "",
            "## データ不足",
            f"- {', '.join(missing) if missing else 'なし'}",
            "",
        ]
    )
