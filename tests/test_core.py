import json
import os
import sqlite3
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import cast

import pytest

from diet_assistant.db import connect
from diet_assistant.repository import add_meal, get, insert
from diet_assistant.services.intake import import_file
from diet_assistant.services.maintenance import cleanup_candidates, create_backup
from diet_assistant.services.planning import calculate_plan, save_plan
from diet_assistant.services.reporting import daily_summary, period_summary, weekly_summary
from diet_assistant.util import now_iso, require_str


def test_db_initialization(db_path: Path) -> None:
    with connect(db_path) as connection:
        table_rows = cast(
            list[tuple[str]],
            connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall(),
        )
        tables = {row[0] for row in table_rows}
        version = cast(tuple[int], connection.execute("PRAGMA user_version").fetchone())[0]
    assert {"meals", "exercises", "body_metrics", "goals", "plans", "intake_entries"} <= tables
    assert version == 1


def test_add_meal_with_items(db_path: Path) -> None:
    meal = add_meal(
        db_path,
        {
            "eaten_at": "2026-07-21T12:00:00+09:00",
            "meal_type": "lunch",
            "text": "おにぎり",
            "estimated_calories": 200,
            "calories_min": 180,
            "calories_max": 230,
            "estimation_confidence": "medium",
            "items": [{"name": "鮭おにぎり", "estimated_calories": 200}],
        },
    )
    assert meal["estimated_calories"] == 200
    items = cast(list[dict[str, object]], meal["items"])
    assert items[0]["name"] == "鮭おにぎり"


def test_invalid_calorie_range(db_path: Path) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        _ = add_meal(db_path, {"meal_type": "lunch", "calories_min": 500, "calories_max": 300})


def test_add_exercise_and_metric(db_path: Path) -> None:
    exercise_id = insert(
        db_path,
        "exercises",
        {
            "performed_at": now_iso(),
            "exercise_type": "walking",
            "duration_minutes": 30,
            "distance": None,
            "sets": None,
            "repetitions": None,
            "weight": None,
            "intensity": "moderate",
            "estimated_calories_burned": 100,
            "note": None,
            "source": "manual",
            "created_at": now_iso(),
            "updated_at": now_iso(),
        },
    )
    metric_id = insert(
        db_path,
        "body_metrics",
        {
            "measured_at": now_iso(),
            "weight": 70,
            "body_fat_percentage": 20,
            "waist": 80,
            "note": None,
            "created_at": now_iso(),
        },
    )
    assert get(db_path, "exercises", exercise_id)["duration_minutes"] == 30
    assert get(db_path, "body_metrics", metric_id)["weight"] == 70


def _goal(db_path: Path, target_date: str = "2026-10-13") -> int:
    return insert(
        db_path,
        "goals",
        {
            "started_at": "2026-07-21",
            "target_date": target_date,
            "start_weight": 80,
            "target_weight": 74,
            "target_type": "weight_loss",
            "status": "inactive",
            "note": None,
            "created_at": now_iso(),
        },
    )


def test_goal_pace_and_plan_history(db_path: Path) -> None:
    goal_id = _goal(db_path)
    calculation = calculate_plan(get(db_path, "goals", goal_id), today=date(2026, 7, 21))
    assert calculation["days_remaining"] == 84
    assert calculation["target_weekly_weight_change"] == -0.5
    first = save_plan(db_path, goal_id, today=date(2026, 7, 21))
    second = save_plan(db_path, goal_id, today=date(2026, 7, 28))
    with connect(db_path) as connection:
        status_rows = cast(
            list[tuple[str]],
            connection.execute("SELECT status FROM plans ORDER BY id").fetchall(),
        )
        statuses = [row[0] for row in status_rows]
    assert first["plan_id"] != second["plan_id"]
    assert statuses == ["superseded", "active"]


def test_daily_and_moving_averages(db_path: Path) -> None:
    for index in range(7):
        _ = add_meal(
            db_path,
            {
                "eaten_at": f"2026-07-{15 + index:02d}T12:00:00+09:00",
                "meal_type": "lunch",
                "estimated_calories": 1000 + index * 100,
                "calories_min": 900,
                "calories_max": 1800,
                "estimation_confidence": "medium",
            },
        )
    daily = daily_summary(db_path, date(2026, 7, 21))
    moving = period_summary(db_path, date(2026, 7, 21), 7)
    weekly = weekly_summary(db_path, date(2026, 7, 21))
    assert daily["totals"]["estimated_calories"] == 1600
    assert moving["average_calories"] == 1300
    assert weekly["recorded_meal_days"] == 7


def test_pending_import_and_duplicate(db_path: Path, tmp_path: Path) -> None:
    inbox = tmp_path / "inbox"
    temporary = tmp_path / "photos/temporary"
    inbox.mkdir()
    source = inbox / "20260721-123500.json"
    _ = source.write_text(
        json.dumps(
            {"captured_at": "2026-07-21T12:35:00+09:00", "meal_type": "lunch", "note": "唐揚げ"}
        ),
        encoding="utf-8",
    )
    duplicate_copy = tmp_path / "duplicate.json"
    _ = duplicate_copy.write_bytes(source.read_bytes())
    result = import_file(db_path, source, temporary)
    duplicate = import_file(db_path, duplicate_copy, temporary)
    imported_meal = cast(dict[str, object], result["meal"])
    assert imported_meal["note"] == "唐揚げ"
    assert duplicate["duplicate"] is True
    with connect(db_path) as connection:
        assert connection.execute("SELECT count(*) FROM meals").fetchone()[0] == 1


def test_backup(db_path: Path, tmp_path: Path) -> None:
    backup = create_backup(db_path, tmp_path / "backups", now=datetime(2026, 7, 21, 3))
    assert backup.name == "diet-20260721-030000.db"
    with connect(backup) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_photo_cleanup_candidates(tmp_path: Path) -> None:
    temporary = tmp_path / "photos"
    temporary.mkdir()
    old = temporary / "old.jpg"
    _ = old.write_bytes(b"x")
    now = datetime(2026, 7, 21, tzinfo=UTC)
    old_timestamp = (now - timedelta(days=31)).timestamp()
    _ = os.utime(old, (old_timestamp, old_timestamp))
    candidates = cleanup_candidates(temporary, retention_days=30, now=now)
    assert [Path(require_str(item, "path")).name for item in candidates] == ["old.jpg"]
    assert old.exists()
