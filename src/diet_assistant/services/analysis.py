"""記録から分析結果（findings）を計算する。

フィードバックの文面はエージェントが書き、CLIはここで計算した数値だけを正本として持つ（ADR 0013）。
各findingは実測値・参照値・参照根拠・根拠にした日数を必ず持ち、
裏付けのない主張がフィードバックに混ざらないようにする。

順位付けと`resolution`にはカロリー優先の不変条件が入っている（ADR 0014）。
栄養素の不足がカロリー超過を押しのけることはなく、カロリーに余裕がないときの不足は
置き換え（substitute）にしかならない。
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from datetime import date, time
from pathlib import Path
from typing import Literal, cast

from ..util import optional_number
from .finding import Finding, Group, Resolution
from .nutrition import NutrientTarget, compare_nutrients, nutrient_targets
from .planning import CALORIE_RANGE_MARGIN, active_plan, latest_weight
from .reporting import (
    NUTRIENT_KEYS,
    DailySummary,
    NutrientKey,
    goal_progress,
    period_summary,
    plan_nutrient_targets,
)

# 順位付けはこの順を守る。カロリー管理が栄養素より先に来る。
_GROUP_RANK: dict[Group, int] = {"goal": 0, "calorie": 1, "nutrition": 2}

# 食事種別の偏りを指摘する下限。1種別だけの記録は偏りではなく記録漏れなので対象外。
_SKEW_SHARE = 0.4

# 増量方向を許す最小の余裕（kcal/日）。計画は目標±100 kcalを範囲内とみなすので、
# それに収まる程度の余裕は「足す余地」とは数えない（ADR 0014）。
_INCREASE_HEADROOM = CALORIE_RANGE_MARGIN

# 計画の前提体重と現在体重の許容差（kg）。
_STALE_WEIGHT_DIFFERENCE = 1.0

_NUTRIENT_LABELS: dict[NutrientKey, str] = {
    "protein": "たんぱく質",
    "fat": "脂質",
    "carbohydrates": "炭水化物",
    "fiber": "食物繊維",
    "sodium": "食塩相当量",
}


def findings(
    path: Path,
    end_day: date,
    days: int = 7,
    *,
    profile: dict[str, object] | None = None,
    day_start: time = time.min,
) -> list[Finding]:
    """期間の記録から findings を計算し、優先順位の高い順に返す。"""
    summary = period_summary(path, end_day, days, day_start=day_start)
    daily = summary["daily"]
    plan = active_plan(path)
    target = optional_number(plan, "target_daily_calories")
    target_min = optional_number(plan, "target_calorie_range_min")
    target_max = optional_number(plan, "target_calorie_range_max")
    average = summary["average_calories"]
    recorded_meal_days = summary["recorded_meal_days"]
    headroom = (
        round(target - average, 1) if target is not None and average is not None else None
    )
    enough_data = recorded_meal_days >= _required_meal_days(days)

    result: list[Finding] = []
    result.extend(
        _goal_findings(path, plan, end_day=end_day, days=days, day_start=day_start)
    )
    if not enough_data:
        result.append(
            _finding(
                group="calorie",
                kind="meal_records_missing",
                severity="attention",
                actual=float(recorded_meal_days),
                reference=float(_required_meal_days(days)),
                reference_basis=f"判断に必要な記録日数（{days}日中）",
                unit="日",
                period_days=days,
                sample_days=recorded_meal_days,
                headroom=headroom,
                resolution="record",
                detail={"missing_days": days - recorded_meal_days},
            )
        )
        return _ranked(result)

    result.extend(
        _calorie_findings(
            daily,
            days=days,
            average=average,
            recorded_meal_days=recorded_meal_days,
            target=target,
            target_min=target_min,
            target_max=target_max,
            headroom=headroom,
        )
    )
    # 計画に目安が無くても、食物繊維と食塩相当量は絶対量なのでプロフィールから導ける。
    targets = plan_nutrient_targets(plan) or nutrient_targets(
        profile or {}, target_calories=target, on_date=end_day
    )
    result.extend(_nutrition_findings(daily, targets=targets, days=days, headroom=headroom))
    return _ranked(result)


def _goal_findings(
    path: Path,
    plan: dict[str, object],
    *,
    end_day: date,
    days: int,
    day_start: time,
) -> list[Finding]:
    result: list[Finding] = []
    basis_weight = optional_number(plan, "basis_weight")
    current_weight = latest_weight(path)
    if (
        basis_weight is not None
        and current_weight is not None
        and abs(current_weight - basis_weight) >= _STALE_WEIGHT_DIFFERENCE
    ):
        result.append(
            _finding(
                group="goal",
                kind="plan_basis_weight_stale",
                severity="attention",
                actual=current_weight,
                reference=basis_weight,
                reference_basis="計画が維持カロリーの前提にした体重",
                unit="kg",
                period_days=days,
                sample_days=1,
                headroom=None,
                resolution="record",
                detail={
                    "difference": round(current_weight - basis_weight, 2),
                    "suggested_command": "diet goal recalculate",
                },
            )
        )
    pace = _pace_finding(path, end_day=end_day, day_start=day_start)
    if pace is not None:
        result.append(pace)
    return result


def _pace_finding(
    path: Path, *, end_day: date, day_start: time
) -> Finding | None:
    """直近7日とその前の7日の実績ペースを、当初目標ペースと比べる。"""
    progress = goal_progress(path, end_day, day_start=day_start)
    if progress is None:
        return None
    target_pace = progress["initial_target_weekly_weight_change"]
    actual_pace = progress["actual_weekly_weight_change"]
    status = progress["status"]
    if actual_pace is None or status is None:
        return None
    behind = status == "behind"
    return _finding(
        group="goal",
        kind="goal_pace_behind" if behind else "goal_pace_on_track",
        severity="attention" if behind else "info",
        actual=actual_pace,
        reference=target_pace,
        reference_basis="当初目標ペース",
        unit="kg/週",
        period_days=7,
        sample_days=progress["current_weight_measurements"],
        headroom=None,
        resolution="none",
        detail={
            "difference": round(actual_pace - target_pace, 3),
            "current_required_weekly_weight_change": (
                progress["current_required_weekly_weight_change"]
            ),
            "previous_weight_measurements": progress["previous_weight_measurements"],
        },
    )


def _calorie_findings(
    daily: list[DailySummary],
    *,
    days: int,
    average: float | None,
    recorded_meal_days: int,
    target: float | None,
    target_min: float | None,
    target_max: float | None,
    headroom: float | None,
) -> list[Finding]:
    result: list[Finding] = []
    if average is not None and (target is None or target_min is None):
        # 目標が無いときは比較しない。平均は事実として出す。
        result.append(
            _finding(
                group="calorie",
                kind="calorie_average_recorded",
                severity="info",
                actual=average,
                reference=None,
                reference_basis=None,
                unit="kcal/日",
                period_days=days,
                sample_days=recorded_meal_days,
                headroom=headroom,
                resolution="none",
                detail={"target_daily_calories": None},
            )
        )
    elif average is not None and target is not None and target_min is not None:
        if target_max is not None and average > target_max:
            kind, severity, reference, resolution = (
                "calorie_average_above_target",
                "attention",
                target_max,
                "reduce",
            )
        elif average < target_min:
            kind, severity, reference, resolution = (
                "calorie_average_below_target",
                "attention",
                target_min,
                "increase",
            )
        else:
            kind, severity, reference, resolution = (
                "calorie_average_within_target",
                "info",
                target,
                "none",
            )
        result.append(
            _finding(
                group="calorie",
                kind=kind,
                severity=severity,
                actual=average,
                reference=reference,
                reference_basis="計画の摂取目標（プロフィールの維持カロリーから算出）",
                unit="kcal/日",
                period_days=days,
                sample_days=recorded_meal_days,
                headroom=headroom,
                resolution=cast(Resolution, resolution),
                detail={
                    "target_daily_calories": target,
                    "target_calorie_range_min": target_min,
                    "target_calorie_range_max": target_max,
                },
            )
        )
    skew = _skew_finding(daily, days=days, recorded_meal_days=recorded_meal_days, headroom=headroom)
    if skew is not None:
        result.append(skew)
    return result


def _skew_finding(
    daily: list[DailySummary], *, days: int, recorded_meal_days: int, headroom: float | None
) -> Finding | None:
    """食事種別ごとの1日あたり平均から、配分の偏りを出す。"""
    totals: defaultdict[str, float] = defaultdict(float)
    for entry in daily:
        for meal in entry["meals"]:
            if meal["estimated_calories"] is not None:
                totals[meal["meal_type"]] += meal["estimated_calories"]
    if len(totals) < 2 or not recorded_meal_days:
        return None
    overall = sum(totals.values())
    if not overall:
        return None
    meal_type, total = max(totals.items(), key=lambda item: item[1])
    share = round(total / overall, 2)
    if share < _SKEW_SHARE:
        return None
    return _finding(
        group="calorie",
        kind="meal_type_calorie_skew",
        severity="attention" if share >= 0.5 else "info",
        actual=round(total / recorded_meal_days, 1),
        reference=None,
        reference_basis=None,
        unit="kcal/日",
        period_days=days,
        sample_days=recorded_meal_days,
        headroom=headroom,
        resolution="reduce",
        detail={
            "meal_type": meal_type,
            "share": share,
            "averages_by_meal_type": {
                name: round(value / recorded_meal_days, 1) for name, value in sorted(totals.items())
            },
        },
    )


def _nutrition_findings(
    daily: list[DailySummary],
    *,
    targets: dict[str, NutrientTarget],
    days: int,
    headroom: float | None,
) -> list[Finding]:
    """記録がある日だけを平均し、目安と比べる。未記録の栄養素は判定しない（ADR 0007）。

    目安は有効な計画のものを使う。findingsは「いまどうするか」の材料なので、
    期間中に計画が変わった場合も現在の目安で評価する（レポートは当時の計画で表示する）。
    """
    result: list[Finding] = []
    required_days = _required_nutrient_days(days)
    for name in NUTRIENT_KEYS:
        values = [
            entry["totals"][name] for entry in daily if name in entry["recorded_nutrients"]
        ]
        if len(values) < required_days or name not in targets:
            continue
        average = round(statistics.fmean(values), 1)
        comparison = compare_nutrients({name: average}, {name: targets[name]}).get(name)
        if comparison is None:
            continue
        label = _NUTRIENT_LABELS[name]
        if comparison["status"] == "below":
            resolution: Resolution = (
                "increase"
                if headroom is not None and headroom >= _INCREASE_HEADROOM
                else "substitute"
            )
            result.append(
                _finding(
                    group="nutrition",
                    kind=f"{name}_below_target",
                    severity="attention",
                    actual=average,
                    reference=comparison["minimum"],
                    reference_basis=comparison["basis"],
                    unit=comparison["unit"],
                    period_days=days,
                    sample_days=len(values),
                    headroom=headroom,
                    resolution=resolution,
                    detail={"label": label, "difference": comparison["difference"]},
                )
            )
        elif comparison["status"] == "above":
            result.append(
                _finding(
                    group="nutrition",
                    kind=f"{name}_above_target",
                    severity="attention",
                    actual=average,
                    reference=comparison["maximum"],
                    reference_basis=comparison["basis"],
                    unit=comparison["unit"],
                    period_days=days,
                    sample_days=len(values),
                    headroom=headroom,
                    resolution="reduce",
                    detail={"label": label, "difference": comparison["difference"]},
                )
            )
        else:
            result.append(
                _finding(
                    group="nutrition",
                    kind=f"{name}_within_target",
                    severity="info",
                    actual=average,
                    reference=comparison["minimum"] or comparison["maximum"],
                    reference_basis=comparison["basis"],
                    unit=comparison["unit"],
                    period_days=days,
                    sample_days=len(values),
                    headroom=headroom,
                    resolution="none",
                    detail={"label": label},
                )
            )
    return result


def _required_meal_days(days: int) -> int:
    """判断に必要な食事記録の日数。1日レポートでは1日でよい。"""
    return 1 if days == 1 else max(3, days // 2)


def _required_nutrient_days(days: int) -> int:
    """栄養素は単日で判断せず、傾向で見る（3日以上）。"""
    return max(3, days // 2)


def _finding(
    *,
    group: Group,
    kind: str,
    severity: Literal["info", "attention"],
    actual: float,
    reference: float | None,
    reference_basis: str | None,
    unit: str,
    period_days: int,
    sample_days: int,
    headroom: float | None,
    resolution: Resolution,
    detail: dict[str, object],
) -> Finding:
    if resolution == "increase" and headroom is not None and headroom < _INCREASE_HEADROOM:
        # カロリーに余裕がないのに増量方向の材料を作らない（ADR 0014）。
        resolution = "substitute"
    return {
        "group": group,
        "kind": kind,
        "severity": severity,
        "actual": actual,
        "reference": reference,
        "reference_basis": reference_basis,
        "unit": unit,
        "period_days": period_days,
        "sample_days": sample_days,
        "calorie_headroom": headroom,
        "resolution": resolution,
        "detail": detail,
    }


def _ranked(result: list[Finding]) -> list[Finding]:
    """指摘（attention）を先に、記録の欠けを最優先に、あとはgroup順と乖離の大きさで並べる。"""

    def key(finding: Finding) -> tuple[int, int, int, float]:
        severity = 0 if finding["severity"] == "attention" else 1
        missing_data = 0 if finding["resolution"] == "record" else 1
        reference = finding["reference"]
        deviation = (
            abs(finding["actual"] - reference) / abs(reference)
            if reference not in (None, 0)
            else 0.0
        )
        return (severity, missing_data, _GROUP_RANK[finding["group"]], -deviation)

    return sorted(result, key=key)
