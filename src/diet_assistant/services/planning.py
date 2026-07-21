from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from ..db import transaction
from ..repository import get, insert
from ..util import now_iso, require_number, require_str

KCAL_PER_KG = 7700


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
            "note": "基礎代謝・維持カロリー未計算。必要赤字は理論的概算。",
        },
    }


def save_plan(path: Path, goal_id: int, *, today: date | None = None) -> dict[str, object]:
    goal = get(path, "goals", goal_id)
    calculation = calculate_plan(goal, today=today)
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
            "target_daily_calories": None,
            "target_calorie_range_min": None,
            "target_calorie_range_max": None,
            "target_weekly_exercise_minutes": calculation["target_weekly_exercise_minutes"],
            "target_weekly_weight_change": calculation["target_weekly_weight_change"],
            "protein_target": None,
            "step_target": calculation["step_target"],
            "assumptions": json.dumps(calculation["assumptions"], ensure_ascii=False),
            "weekly_actions": json.dumps(calculation["weekly_actions"], ensure_ascii=False),
            "safety_note": calculation["safety_note"],
            "status": "active",
        },
    )
    return {"plan_id": plan_id, **calculation}
