"""フィードバックの保存と読み出し。

フィードバックの文面はエージェントが書く。CLIは文面を生成せず、保存と、根拠（findings）の
添付だけを行う（ADR 0013）。根拠は保存時にこちらで計算するので、書き手が数値を
差し替えることはできない。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Literal, cast

from ..config import profile_day_start_time
from ..db import connect
from ..repository import upsert
from ..util import now_iso, optional_number, reporting_date, require_int, require_str
from .analysis import findings
from .planning import active_plan
from .reporting import daily_summary

Kind = Literal["daily", "period", "after_meal"]

# エージェントが書いてよい項目。取り違えを黙って捨てないため、これ以外は受け付けない。
TEXT_KEYS = (
    "situation",
    "priority_action",
    "keep",
    "alternative",
    "plan_change",
    "next_review_date",
    "note",
)
REQUIRED_TEXT_KEYS = ("situation", "priority_action")


def save_advice(
    path: Path,
    text: dict[str, object],
    *,
    kind: Kind,
    day: date,
    days: int = 1,
    meal_id: int | None = None,
    profile: dict[str, object] | None = None,
) -> dict[str, object]:
    """エージェントが書いたフィードバックを保存する。根拠はここで計算して付ける。"""
    _validate_text(text)
    day_start = profile_day_start_time(profile or {})
    advice_type = _advice_type(kind, days)
    period_start = day - timedelta(days=days - 1) if kind == "period" else day
    evidence: object
    if kind == "after_meal":
        if meal_id is None:
            raise ValueError("食後のフィードバックには meal_id が必要です")
        evidence = _meal_evidence(path, meal_id, day, profile or {}, day_start=day_start)
    else:
        evidence = findings(
            path,
            day,
            days if kind == "period" else 1,
            profile=profile,
            day_start=day_start,
        )
    result: dict[str, object] = {**text, "evidence": evidence}
    _ = upsert(
        path,
        "advice_history",
        {
            "generated_at": now_iso(),
            "advice_type": advice_type,
            "meal_id": meal_id,
            "period_start": period_start.isoformat(),
            "period_end": day.isoformat(),
            "summary": require_str(text, "situation"),
            "details": json.dumps(result, ensure_ascii=False),
            "evidence": json.dumps(evidence, ensure_ascii=False),
            "priority": "normal",
            "written_by": "agent",
        },
        conflict=("advice_type", "period_start", "period_end", "COALESCE(meal_id, 0)"),
    )
    return result


def latest_advice(
    path: Path,
    *,
    kind: Kind,
    day: date,
    days: int = 1,
    meal_id: int | None = None,
) -> dict[str, object] | None:
    """保存済みのフィードバック。レポートはこれを埋め込み、無ければfindingsの事実だけを載せる。"""
    advice_type = _advice_type(kind, days)
    period_start = day - timedelta(days=days - 1) if kind == "period" else day
    with connect(path) as connection:
        row = cast(
            sqlite3.Row | None,
            connection.execute(
                "SELECT details FROM advice_history WHERE advice_type = ? AND period_start = ? "
                + "AND period_end = ? AND COALESCE(meal_id, 0) = ? AND written_by = 'agent'",
                (advice_type, period_start.isoformat(), day.isoformat(), meal_id or 0),
            ).fetchone(),
        )
    if row is None:
        return None
    parsed = cast(object, json.loads(cast(str, row["details"])))
    return cast(dict[str, object], parsed) if isinstance(parsed, dict) else None


def meal_day_context(
    path: Path, meal: dict[str, object], profile: dict[str, object]
) -> dict[str, object]:
    """食事を記録した日の残りカロリーと残り食数。文面は返さない。"""
    eaten_at = datetime.fromisoformat(require_str(meal, "eaten_at"))
    day_start = profile_day_start_time(profile)
    day = reporting_date(eaten_at, starts_at=day_start)
    return _meal_evidence(path, require_int(meal, "id"), day, profile, day_start=day_start)


def _meal_evidence(
    path: Path, meal_id: int, day: date, profile: dict[str, object], *, day_start: time
) -> dict[str, object]:
    summary = daily_summary(path, day, day_start=day_start)
    plan = active_plan(path)
    target = optional_number(plan, "target_daily_calories")
    consumed = summary["totals"]["estimated_calories"]
    meals_per_day_value = profile.get("meals_per_day", 3)
    meals_per_day = meals_per_day_value if isinstance(meals_per_day_value, int) else 3
    remaining_meals = max(meals_per_day - len(summary["meals"]), 0)
    remaining = round(target - consumed) if target is not None else None
    return {
        "meal_id": meal_id,
        "date": day.isoformat(),
        "consumed_calories": consumed,
        "target_daily_calories": target,
        "target_calorie_range_min": optional_number(plan, "target_calorie_range_min"),
        "target_calorie_range_max": optional_number(plan, "target_calorie_range_max"),
        "remaining_calories": remaining,
        "remaining_meals": remaining_meals,
        "suggested_calories_per_remaining_meal": (
            round(max(remaining, 0) / remaining_meals)
            if remaining is not None and remaining_meals
            else None
        ),
        "nutrients": summary["nutrients"],
        "recorded_nutrients": summary["recorded_nutrients"],
    }


def _advice_type(kind: Kind, days: int) -> str:
    if kind == "daily":
        return "daily"
    if kind == "after_meal":
        return "after_meal"
    return f"{days}day"


def _validate_text(text: dict[str, object]) -> None:
    unknown = sorted(key for key in text if key not in TEXT_KEYS)
    if unknown:
        invalid_keys = ", ".join(unknown)
        valid_keys = ", ".join(TEXT_KEYS)
        raise ValueError(
            f"フィードバックに使えない項目です: {invalid_keys}（使える項目: {valid_keys}）"
        )
    missing = [key for key in REQUIRED_TEXT_KEYS if not text.get(key)]
    if missing:
        raise ValueError(f"フィードバックには {', '.join(missing)} が必要です")
