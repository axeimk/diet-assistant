from __future__ import annotations

import json
import sqlite3
from datetime import date, timedelta
from pathlib import Path
from typing import cast

from ..db import connect
from ..repository import insert
from ..util import now_iso, optional_number, require_int, require_str
from .reporting import period_summary


def generate_advice(
    path: Path, end_day: date, days: int = 7, *, save: bool = True
) -> dict[str, object]:
    summary = period_summary(path, end_day, days)
    with connect(path) as connection:
        plan = cast(
            sqlite3.Row | None,
            connection.execute(
                "SELECT p.* FROM plans p JOIN goals g ON p.goal_id=g.id "
                + "WHERE p.status='active' AND g.status='active' ORDER BY p.id DESC LIMIT 1"
            ).fetchone(),
        )
    plan_record = cast(dict[str, object], dict(plan)) if plan else {}
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
        _ = insert(
            path,
            "advice_history",
            {
                "generated_at": now_iso(),
                "advice_type": f"{days}day",
                "period_start": require_str(summary, "period_start"),
                "period_end": require_str(summary, "period_end"),
                "summary": situation,
                "details": json.dumps(result, ensure_ascii=False),
                "evidence": json.dumps(result["evidence"], ensure_ascii=False),
                "priority": "normal",
                "status": "active",
            },
        )
    return result
