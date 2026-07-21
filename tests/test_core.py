import json
import os
import sqlite3
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import cast

import pytest

from diet_assistant.db import SCHEMA_VERSION, connect, initialize, migrate, schema_version
from diet_assistant.repository import add_meal, get, insert
from diet_assistant.services.intake import import_file
from diet_assistant.services.maintenance import cleanup_candidates, create_backup
from diet_assistant.services.planning import (
    calculate_energy_targets,
    calculate_plan,
    evaluate_goal,
    save_plan,
)
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
    assert version == SCHEMA_VERSION


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


def test_sodium_recorded_and_totalled(db_path: Path) -> None:
    meal = add_meal(
        db_path,
        {
            "eaten_at": "2026-07-20T14:00:00+09:00",
            "meal_type": "lunch",
            "estimated_calories": 304,
            "sodium": 5.2,
            "items": [{"name": "ラーメン", "sodium": 5.2, "confidence": "high"}],
        },
    )
    assert meal["sodium"] == 5.2
    items = cast(list[dict[str, object]], meal["items"])
    assert items[0]["sodium"] == 5.2
    assert daily_summary(db_path, date(2026, 7, 20))["totals"]["sodium"] == 5.2


def test_negative_sodium_rejected(db_path: Path) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        _ = add_meal(db_path, {"meal_type": "lunch", "sodium": -1})


def _initialize_v1(path: Path) -> None:
    """マイグレーション検証用に、sodium列を持たないv1スキーマを作る。"""
    with connect(path) as connection:
        _ = connection.executescript(
            "CREATE TABLE meals (id INTEGER PRIMARY KEY, eaten_at TEXT, protein REAL);"
            + "CREATE TABLE meal_items (id INTEGER PRIMARY KEY, meal_id INTEGER, name TEXT);"
            + "CREATE TABLE goals (id INTEGER PRIMARY KEY, started_at TEXT NOT NULL, "
            + "target_date TEXT NOT NULL, start_weight REAL NOT NULL, target_weight REAL NOT NULL, "
            + "target_type TEXT NOT NULL, status TEXT NOT NULL, note TEXT, "
            + "created_at TEXT NOT NULL);"
            + "CREATE TABLE plans (id INTEGER PRIMARY KEY, goal_id INTEGER NOT NULL, "
            + "calculated_at TEXT NOT NULL, target_daily_calories INTEGER, "
            + "target_calorie_range_min INTEGER, target_calorie_range_max INTEGER, "
            + "target_weekly_exercise_minutes INTEGER, target_weekly_weight_change REAL NOT NULL, "
            + "protein_target REAL, step_target INTEGER, assumptions TEXT NOT NULL, "
            + "weekly_actions TEXT NOT NULL, safety_note TEXT, status TEXT NOT NULL);"
            + "INSERT INTO meals (eaten_at, protein) VALUES ('2026-07-20T12:00:00+09:00', 10);"
        )
        _ = connection.execute("PRAGMA user_version = 1")
        connection.commit()


def test_migration_adds_sodium_and_keeps_rows(tmp_path: Path) -> None:
    path = tmp_path / "data/diet.db"
    _initialize_v1(path)

    applied = migrate(path)

    assert applied == [2, 3]
    assert schema_version(path) == SCHEMA_VERSION
    with connect(path) as connection:
        row = tuple(
            cast(sqlite3.Row, connection.execute("SELECT protein, sodium FROM meals").fetchone())
        )
        item_columns = {
            cast(tuple[int, str], info)[1]
            for info in cast(
                list[tuple[object, ...]],
                connection.execute("PRAGMA table_info(meal_items)").fetchall(),
            )
        }
    assert row == (10, None), "既存の行は保持され、sodiumはNULLで埋まる"
    assert "sodium" in item_columns
    with connect(path) as connection:
        goal_columns = {
            cast(tuple[int, str], info)[1]
            for info in cast(
                list[tuple[object, ...]], connection.execute("PRAGMA table_info(goals)").fetchall()
            )
        }
    assert {"success_threshold_weight", "evaluation_window_days"} <= goal_columns


def test_migration_is_not_reapplied(tmp_path: Path) -> None:
    path = tmp_path / "data/diet.db"
    _initialize_v1(path)
    _ = migrate(path)

    assert migrate(path) == [], "適用済みのマイグレーションは再実行しない"


def test_initialize_migrates_existing_db(tmp_path: Path) -> None:
    path = tmp_path / "data/diet.db"
    _initialize_v1(path)

    assert initialize(path) == [2, 3]
    assert schema_version(path) == SCHEMA_VERSION


def test_initialize_skips_migrations_for_new_db(tmp_path: Path) -> None:
    assert initialize(tmp_path / "data/diet.db") == []


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


def test_energy_targets_from_profile_are_capped() -> None:
    energy = calculate_energy_targets(
        {
            "height_cm": 175,
            "birth_date": "1991-03-04",
            "sex": "male",
            "activity_level": "sedentary",
        },
        weight=91,
        theoretical_daily_deficit=994,
        on_date=date(2026, 7, 21),
        days_remaining=31,
    )
    assert energy["estimated_maintenance_calories"] == 2200
    assert energy["planned_daily_deficit"] == 550
    assert energy["target_daily_calories"] == 1650
    assert energy["deficit_was_capped"] is True
    assert energy["calorie_plan_supports_theoretical_pace"] is False
    assert energy["projected_weight_at_target_date"] == 88.79


def test_goal_evaluation_uses_seven_day_average(db_path: Path) -> None:
    goal_id = insert(
        db_path,
        "goals",
        {
            "started_at": "2026-07-21",
            "target_date": "2026-08-21",
            "start_weight": 91,
            "target_weight": 87,
            "success_threshold_weight": 88,
            "evaluation_window_days": 7,
            "target_type": "weight_loss",
            "status": "inactive",
            "note": None,
            "created_at": now_iso(),
        },
    )
    for index, weight in enumerate((87.5, 87.8, 87.9, 87.8)):
        _ = insert(
            db_path,
            "body_metrics",
            {
                "measured_at": f"2026-08-{18 + index:02d}T07:00:00+09:00",
                "weight": weight,
                "body_fat_percentage": None,
                "waist": None,
                "note": None,
                "created_at": now_iso(),
            },
        )
    evaluation = evaluate_goal(db_path, goal_id, evaluation_date=date(2026, 8, 21))
    assert evaluation["average_weight"] == 87.75
    assert evaluation["challenge_achieved"] is False
    assert evaluation["success_threshold_achieved"] is True
    assert evaluation["outcome"] == "success_threshold_achieved"


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
