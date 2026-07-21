from __future__ import annotations

import json
import math
import sqlite3
from datetime import date, timedelta
from pathlib import Path
from typing import cast

from ..db import connect, transaction
from ..repository import get, insert
from ..util import day_bounds, now_iso, optional_number, require_int, require_number, require_str

KCAL_PER_KG = 7700
MAX_DEFICIT_RATIO = 0.25
CALORIE_RANGE_MARGIN = 100

ACTIVITY_FACTORS = {
    "sedentary": 1.2,
    "light": 1.375,
    "moderate": 1.55,
    "active": 1.725,
    "very_active": 1.9,
}


def calculate_plan(goal: dict[str, object], *, today: date | None = None) -> dict[str, object]:
    today = today or date.today()
    start = max(date.fromisoformat(require_str(goal, "started_at")[:10]), today)
    target = date.fromisoformat(require_str(goal, "target_date")[:10])
    days = (target - start).days
    if days <= 0:
        raise ValueError("目標日は計算日より後にしてください")
    start_weight = require_number(goal, "start_weight")
    change = require_number(goal, "target_weight") - start_weight
    weekly_change = change / (days / 7)
    daily_deficit = -change * KCAL_PER_KG / days
    if change >= 0:
        feasibility = "review"
        safety = "増量・維持目標の摂取量はプロフィールと専門家の助言を踏まえて調整してください。"
    elif abs(weekly_change) <= start_weight * 0.01:
        feasibility = "reasonable"
        safety = "一般的な目安の範囲ですが、体調を優先し医療判断には使用しないでください。"
    else:
        feasibility = "aggressive"
        safety = "週1%を超える減量ペースです。期限または目標値を見直してください。"
    return {
        "days_remaining": days,
        "weeks_remaining": round(days / 7, 2),
        "target_weekly_weight_change": round(weekly_change, 3),
        "estimated_daily_deficit": round(daily_deficit),
        "feasibility": feasibility,
        "target_weekly_exercise_minutes": 150,
        "step_target": 8000,
        "weekly_actions": ["食事と体重を記録する", "週150分を目安に無理のない運動を行う"],
        "safety_note": safety,
        "assumptions": {
            "energy_per_kg_kcal": KCAL_PER_KG,
            "note": (
                "必要赤字は体重差から求めた理論的概算。"
                "維持カロリーは別途プロフィールから計算する。"
            ),
        },
    }


def calculate_energy_targets(
    profile: dict[str, object],
    *,
    weight: float,
    theoretical_daily_deficit: float,
    on_date: date,
    days_remaining: int | None = None,
) -> dict[str, object]:
    """プロフィールから暫定維持カロリーと安全側の摂取目標を計算する。"""
    missing = [
        key
        for key in ("height_cm", "birth_date", "sex", "activity_level")
        if profile.get(key) is None
    ]
    sex = profile.get("sex")
    if sex not in ("female", "male") and "sex" not in missing:
        missing.append("sex")
    activity_level = profile.get("activity_level")
    if activity_level not in ACTIVITY_FACTORS and "activity_level" not in missing:
        missing.append("activity_level")
    if missing:
        return {
            "available": False,
            "missing_profile_fields": missing,
            "method": "Mifflin-St Jeor",
        }

    height_value = profile["height_cm"]
    birth_value = profile["birth_date"]
    if not isinstance(height_value, (int, float)) or isinstance(height_value, bool):
        raise ValueError("height_cm は数値である必要があります")
    if not isinstance(birth_value, str):
        raise ValueError("birth_date は日付文字列である必要があります")
    birth_date = date.fromisoformat(birth_value)
    before_birthday = (on_date.month, on_date.day) < (birth_date.month, birth_date.day)
    age = on_date.year - birth_date.year - before_birthday
    sex_adjustment = 5 if sex == "male" else -161
    basal_metabolic_rate = 10 * weight + 6.25 * float(height_value) - 5 * age + sex_adjustment
    activity_factor = ACTIVITY_FACTORS[cast(str, activity_level)]
    maintenance = round(basal_metabolic_rate * activity_factor)
    max_deficit = round(maintenance * MAX_DEFICIT_RATIO)
    applied_deficit = round(min(max(theoretical_daily_deficit, 0), max_deficit))
    target = maintenance - applied_deficit
    projected_weight_change = (
        -applied_deficit * days_remaining / KCAL_PER_KG if days_remaining is not None else None
    )
    return {
        "available": True,
        "method": "Mifflin-St Jeor",
        "age": age,
        "activity_factor": activity_factor,
        "basal_metabolic_rate": round(basal_metabolic_rate),
        "estimated_maintenance_calories": maintenance,
        "theoretical_daily_deficit": round(theoretical_daily_deficit),
        "planned_daily_deficit": applied_deficit,
        "deficit_was_capped": theoretical_daily_deficit > max_deficit,
        "calorie_plan_supports_theoretical_pace": theoretical_daily_deficit <= max_deficit,
        "max_deficit_ratio": MAX_DEFICIT_RATIO,
        "target_daily_calories": target,
        "target_calorie_range_min": target - CALORIE_RANGE_MARGIN,
        "target_calorie_range_max": target + CALORIE_RANGE_MARGIN,
        "projected_weight_change_at_target_date": (
            round(projected_weight_change, 2) if projected_weight_change is not None else None
        ),
        "projected_weight_at_target_date": (
            round(weight + projected_weight_change, 2)
            if projected_weight_change is not None
            else None
        ),
        "missing_profile_fields": [],
    }


def save_plan(
    path: Path,
    goal_id: int,
    *,
    profile: dict[str, object] | None = None,
    today: date | None = None,
) -> dict[str, object]:
    goal = get(path, "goals", goal_id)
    calculation = calculate_plan(goal, today=today)
    calculation_day = today or date.today()
    energy = calculate_energy_targets(
        profile or {},
        weight=require_number(goal, "start_weight"),
        theoretical_daily_deficit=require_number(calculation, "estimated_daily_deficit"),
        on_date=calculation_day,
        days_remaining=require_int(calculation, "days_remaining"),
    )
    with transaction(path) as connection:
        _ = connection.execute(
            "UPDATE plans SET status = 'superseded' WHERE goal_id = ? AND status = 'active'",
            (goal_id,),
        )
    plan_id = insert(
        path,
        "plans",
        {
            "goal_id": goal_id,
            "calculated_at": now_iso(),
            "target_daily_calories": optional_number(energy, "target_daily_calories"),
            "target_calorie_range_min": optional_number(energy, "target_calorie_range_min"),
            "target_calorie_range_max": optional_number(energy, "target_calorie_range_max"),
            "estimated_maintenance_calories": optional_number(
                energy, "estimated_maintenance_calories"
            ),
            "planned_daily_deficit": optional_number(energy, "planned_daily_deficit"),
            "target_weekly_exercise_minutes": calculation["target_weekly_exercise_minutes"],
            "target_weekly_weight_change": calculation["target_weekly_weight_change"],
            "protein_target": None,
            "step_target": calculation["step_target"],
            "assumptions": json.dumps(
                {**cast(dict[str, object], calculation["assumptions"]), "energy": energy},
                ensure_ascii=False,
            ),
            "weekly_actions": json.dumps(calculation["weekly_actions"], ensure_ascii=False),
            "safety_note": calculation["safety_note"],
            "status": "active",
        },
    )
    return {"plan_id": plan_id, **calculation, "energy": energy}


def evaluate_goal(
    path: Path, goal_id: int, *, evaluation_date: date | None = None
) -> dict[str, object]:
    """評価期間の体重平均で、挑戦目標と達成最低ラインを判定する。"""
    goal = get(path, "goals", goal_id)
    target_date = date.fromisoformat(require_str(goal, "target_date")[:10])
    end_day = evaluation_date or min(date.today(), target_date)
    raw_window = goal.get("evaluation_window_days", 1)
    window_days = raw_window if isinstance(raw_window, int) else 1
    start_day = end_day - timedelta(days=window_days - 1)
    weights: list[float] = []
    with connect(path) as connection:
        for offset in range(window_days):
            day = start_day + timedelta(days=offset)
            start, end = day_bounds(day)
            row = cast(
                sqlite3.Row | None,
                connection.execute(
                    "SELECT weight FROM body_metrics WHERE measured_at BETWEEN ? AND ? "
                    + "AND weight IS NOT NULL ORDER BY measured_at DESC LIMIT 1",
                    (start, end),
                ).fetchone(),
            )
            if row is not None:
                weight_value = cast(object, row["weight"])
                if isinstance(weight_value, (int, float)) and not isinstance(weight_value, bool):
                    weights.append(float(weight_value))
    required_measurements = max(1, math.ceil(window_days / 2))
    enough_data = len(weights) >= required_measurements
    average_weight = round(sum(weights) / len(weights), 2) if weights else None
    target_weight = require_number(goal, "target_weight")
    threshold_weight = optional_number(goal, "success_threshold_weight") or target_weight
    is_weight_loss = target_weight < require_number(goal, "start_weight")
    challenge_achieved = (
        enough_data
        and average_weight is not None
        and (average_weight <= target_weight if is_weight_loss else average_weight >= target_weight)
    )
    threshold_achieved = (
        enough_data
        and average_weight is not None
        and (
            average_weight <= threshold_weight
            if is_weight_loss
            else average_weight >= threshold_weight
        )
    )
    is_final = end_day >= target_date
    if not enough_data:
        outcome = "insufficient_data"
    elif challenge_achieved:
        outcome = "challenge_achieved"
    elif threshold_achieved:
        outcome = "success_threshold_achieved"
    else:
        outcome = "not_achieved"
    return {
        "goal_id": goal_id,
        "evaluation_date": end_day.isoformat(),
        "target_date": target_date.isoformat(),
        "is_final": is_final,
        "period_start": start_day.isoformat(),
        "period_end": end_day.isoformat(),
        "evaluation_window_days": window_days,
        "weight_measurements": len(weights),
        "required_measurements": required_measurements,
        "average_weight": average_weight,
        "challenge_target_weight": target_weight,
        "success_threshold_weight": threshold_weight,
        "challenge_achieved": challenge_achieved,
        "success_threshold_achieved": threshold_achieved,
        "outcome": outcome,
    }


def evaluate_active_goal(path: Path, *, evaluation_date: date) -> dict[str, object] | None:
    with connect(path) as connection:
        row = cast(
            sqlite3.Row | None,
            connection.execute(
                "SELECT id FROM goals WHERE status='active' ORDER BY id DESC LIMIT 1"
            ).fetchone(),
        )
    if row is None:
        return None
    goal_id_value = cast(object, row["id"])
    if not isinstance(goal_id_value, int):
        raise ValueError("有効な目標IDを取得できませんでした")
    return evaluate_goal(path, goal_id_value, evaluation_date=evaluation_date)
