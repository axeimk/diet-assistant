from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import cast

from ..config import profile_day_start_time
from ..db import connect
from ..repository import insert
from ..util import now_iso, optional_number, reporting_date, require_int, require_str
from .reporting import daily_summary, period_summary


def _active_plan(path: Path) -> dict[str, object]:
    with connect(path) as connection:
        plan = cast(
            sqlite3.Row | None,
            connection.execute(
                "SELECT p.* FROM plans p JOIN goals g ON p.goal_id=g.id "
                + "WHERE p.status='active' AND g.status='active' ORDER BY p.id DESC LIMIT 1"
            ).fetchone(),
        )
    return cast(dict[str, object], dict(plan)) if plan else {}


def generate_daily_advice(
    path: Path, day: date, *, day_start: time = time.min, save: bool = True
) -> dict[str, object]:
    summary = daily_summary(path, day, day_start=day_start)
    plan = _active_plan(path)
    target = optional_number(plan, "target_daily_calories")
    target_min = optional_number(plan, "target_calorie_range_min")
    target_max = optional_number(plan, "target_calorie_range_max")
    consumed = summary["totals"]["estimated_calories"]
    if not summary["meals"]:
        situation = "食事記録がないため、今日の摂取状況は判断できません。"
        priority = "食べた内容とおおよその量を記録する"
        remaining = None
    elif target is None or target_min is None or target_max is None:
        situation = "プロフィールから摂取目標を計算できていません。"
        priority = "プロフィールの身長・生年月日・性別・活動量を確認する"
        remaining = None
    else:
        remaining = round(target - consumed)
        if consumed > target_max:
            situation = f"今日は摂取目標上限を約{round(consumed - target_max)} kcal上回っています。"
            priority = "翌日に極端な制限をせず、7日平均で調整する"
        elif consumed < target_min:
            situation = f"今日は目標範囲まであと約{round(target_min - consumed)} kcalです。"
            priority = "空腹と体調に合わせ、たんぱく質や野菜を含む食事を選ぶ"
        else:
            situation = "今日は摂取目標の範囲内です。"
            priority = "現在の食事パターンを継続する"
    result: dict[str, object] = {
        "situation": situation,
        "priority_action": priority,
        "keep": "単日の増減で極端に調整せず、7日以上の傾向で判断すること",
        "evidence": {
            "date": day.isoformat(),
            "consumed_calories": consumed,
            "target_daily_calories": target,
            "target_calorie_range_min": target_min,
            "target_calorie_range_max": target_max,
            "remaining_calories": remaining,
        },
    }
    if save:
        _save_advice(path, "daily", day, day, result)
    return result


def generate_meal_advice(
    path: Path,
    meal: dict[str, object],
    profile: dict[str, object],
    *,
    save: bool = True,
) -> dict[str, object]:
    eaten_at = datetime.fromisoformat(require_str(meal, "eaten_at"))
    day_start = profile_day_start_time(profile)
    day = reporting_date(eaten_at, starts_at=day_start)
    summary = daily_summary(path, day, day_start=day_start)
    plan = _active_plan(path)
    target = optional_number(plan, "target_daily_calories")
    target_min = optional_number(plan, "target_calorie_range_min")
    target_max = optional_number(plan, "target_calorie_range_max")
    consumed = summary["totals"]["estimated_calories"]
    meals_per_day_value = profile.get("meals_per_day", 3)
    meals_per_day = meals_per_day_value if isinstance(meals_per_day_value, int) else 3
    remaining_meals = max(meals_per_day - len(summary["meals"]), 0)
    if target is None or target_min is None or target_max is None:
        situation = "摂取目標が未計算のため、残りカロリーは算出できません。"
        priority = "食事内容を記録し、プロフィールを確認する"
        remaining = None
        next_meal_budget = None
    else:
        remaining = round(target - consumed)
        next_meal_budget = round(max(remaining, 0) / remaining_meals) if remaining_meals else None
        if consumed > target_max:
            excess = round(consumed - target_max)
            situation = f"今日の摂取量は目標上限を約{excess} kcal超えています。"
            priority = "次の食事を抜かず、空腹に応じて軽めにし、翌日へ極端に繰り越さない"
        elif remaining_meals:
            situation = f"今日の目安は残り約{max(remaining, 0)} kcalです。"
            priority = (
                f"残り{remaining_meals}食を1食あたり約{next_meal_budget} kcalの目安で配分する"
            )
        else:
            situation = (
                "今日の摂取目標の範囲内です。"
                if target_min <= consumed <= target_max
                else f"目標中心値との差は{abs(remaining)} kcalです。"
            )
            priority = "単日の差は翌日に極端な制限で相殺せず、7日平均で確認する"
    result: dict[str, object] = {
        "meal_id": require_int(meal, "id"),
        "situation": situation,
        "priority_action": priority,
        "evidence": {
            "date": day.isoformat(),
            "consumed_calories": consumed,
            "target_daily_calories": target,
            "target_calorie_range_min": target_min,
            "target_calorie_range_max": target_max,
            "remaining_calories": remaining,
            "remaining_meals": remaining_meals,
            "suggested_calories_per_remaining_meal": next_meal_budget,
        },
    }
    if save:
        _save_advice(path, "after_meal", day, day, result)
    return result


def generate_advice(
    path: Path,
    end_day: date,
    days: int = 7,
    *,
    day_start: time = time.min,
    save: bool = True,
) -> dict[str, object]:
    summary = period_summary(path, end_day, days, day_start=day_start)
    plan_record = _active_plan(path)
    target = optional_number(plan_record, "target_daily_calories")
    average = optional_number(summary, "average_calories")
    recorded_meal_days = require_int(summary, "recorded_meal_days")
    enough_data = recorded_meal_days >= max(3, days // 2)
    if not enough_data:
        situation = "食事記録が不足しているため、摂取傾向をまだ判断できません。"
        priority = "まず食事を記録する日を増やす"
        alternative = "写真なしでも品名とおおよその量だけ記録する"
        plan_change = "データがそろうまで変更しません。"
    elif target and average and average > target + 150:
        excess = round(average - target)
        situation = f"直近{days}日間は目標より平均約{excess} kcal/日多い状態です。"
        priority = "最も頻度の高い間食を1日200 kcal以内にする"
        alternative = "主食を週4回だけ少量（約50g）減らす"
        plan_change = "2週間継続して傾向を再評価します。"
    else:
        situation = f"直近{days}日間は大きな調整を急ぐ根拠がありません。"
        priority = "現在の記録と無理のない習慣を継続する"
        alternative = "週150分を目安に軽い有酸素運動を分散して行う"
        plan_change = "現時点では不要です。"
    result: dict[str, object] = {
        "situation": situation,
        "evidence": {
            "days": days,
            "average_calories": average,
            "recorded_meal_days": recorded_meal_days,
            "target_daily_calories": target,
        },
        "priority_action": priority,
        "alternative": alternative,
        "keep": "単日の増減で極端に調整せず、記録を継続すること",
        "next_review_date": (end_day + timedelta(days=7)).isoformat(),
        "plan_change": plan_change,
    }
    if save:
        _save_advice(
            path,
            f"{days}day",
            date.fromisoformat(require_str(summary, "period_start")),
            date.fromisoformat(require_str(summary, "period_end")),
            result,
        )
    return result


def _save_advice(
    path: Path,
    advice_type: str,
    period_start: date,
    period_end: date,
    result: dict[str, object],
) -> None:
    _ = insert(
        path,
        "advice_history",
        {
            "generated_at": now_iso(),
            "advice_type": advice_type,
            "period_start": period_start.isoformat(),
            "period_end": period_end.isoformat(),
            "summary": require_str(result, "situation"),
            "details": json.dumps(result, ensure_ascii=False),
            "evidence": json.dumps(result["evidence"], ensure_ascii=False),
            "priority": "normal",
            "status": "active",
        },
    )
