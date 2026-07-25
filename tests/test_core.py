import json
import os
import sqlite3
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import cast

import pytest

from diet_assistant.db import SCHEMA_VERSION, connect, initialize, migrate, schema_version
from diet_assistant.repository import (
    NotFoundError,
    activate_goal,
    add_meal,
    get,
    get_goal,
    insert,
    list_rows,
    soft_delete_goal,
)
from diet_assistant.services.intake import import_file
from diet_assistant.services.maintenance import cleanup_candidates, create_backup
from diet_assistant.services.planning import (
    calculate_energy_targets,
    calculate_plan,
    evaluate_active_goal,
    evaluate_goal,
    save_plan,
)
from diet_assistant.services.reporting import daily_summary, period_summary, weekly_summary
from diet_assistant.util import now_iso, reporting_date, require_str, with_weekday


def test_with_weekday_labels_each_day_in_japanese() -> None:
    assert with_weekday("2026-07-20") == "2026-07-20（月）"
    assert with_weekday("2026-07-25") == "2026-07-25（土）"
    assert with_weekday("2026-07-26") == "2026-07-26（日）"


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


def test_fiber_recorded_on_meal_and_items(db_path: Path) -> None:
    meal = add_meal(
        db_path,
        {
            "eaten_at": "2026-07-20T14:00:00+09:00",
            "meal_type": "lunch",
            "estimated_calories": 500,
            "fiber": 4.2,
            "items": [
                {"name": "ご飯", "fiber": 0.5, "confidence": "high"},
                {"name": "サラダ", "fiber": 3.7, "confidence": "medium"},
            ],
        },
    )
    assert meal["fiber"] == 4.2
    items = cast(list[dict[str, object]], meal["items"])
    assert [item["fiber"] for item in items] == [0.5, 3.7]
    assert daily_summary(db_path, date(2026, 7, 20))["totals"]["fiber"] == 4.2


def test_negative_sodium_rejected(db_path: Path) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        _ = add_meal(db_path, {"meal_type": "lunch", "sodium": -1})


SCHEMA_V1_SQL = Path(__file__).parent / "data" / "schema_v1.sql"


def _initialize_v1(path: Path) -> None:
    """本番DBが辿った経路を再現するため、first commit時点のv1スキーマを作る。"""
    with connect(path) as connection:
        _ = connection.executescript(SCHEMA_V1_SQL.read_text(encoding="utf-8"))
        _ = connection.execute(
            "INSERT INTO meals (eaten_at, meal_type, protein, created_at, updated_at) "
            + "VALUES ('2026-07-20T12:00:00+09:00', 'lunch', 10, "
            + "'2026-07-20T12:00:00+09:00', '2026-07-20T12:00:00+09:00')"
        )
        connection.commit()


def _table_schema(path: Path) -> dict[str, object]:
    """列の定義（型・NOT NULL・既定値・主キー）と索引を、比較できる形で取り出す。"""
    schema: dict[str, object] = {}
    with connect(path) as connection:
        names = [
            cast(tuple[str], row)[0]
            for row in cast(
                list[tuple[object, ...]],
                connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' "
                    + "AND name NOT LIKE 'sqlite_%' ORDER BY name"
                ).fetchall(),
            )
        ]
        for table in names:
            schema[table] = {
                cast(tuple[object, str, str, int, object, int], info)[1]: cast(
                    tuple[object, str, str, int, object, int], info
                )[2:]
                for info in cast(
                    list[tuple[object, ...]],
                    connection.execute(f"PRAGMA table_info({table})").fetchall(),
                )
            }
        schema["#indexes"] = {
            cast(tuple[str, object], row)[0]: cast(tuple[str, object], row)[1]
            for row in cast(
                list[tuple[object, ...]],
                connection.execute(
                    "SELECT name, sql FROM sqlite_master WHERE type='index' ORDER BY name"
                ).fetchall(),
            )
        }
    return schema


def test_migrated_schema_matches_fresh_schema(tmp_path: Path) -> None:
    """SCHEMA_SQLとMIGRATIONSは別々に書かれた2つの正本で、同じ形に着地しないといけない。

    片方だけ直すと「新規DBと移行済みDBでスキーマが違う」状態になり、本番DBだけで
    再現する不具合を生む。列・型・NOT NULL・既定値・主キー・索引まで突き合わせる。
    """
    migrated = tmp_path / "migrated/data/diet.db"
    fresh = tmp_path / "fresh/data/diet.db"
    _initialize_v1(migrated)

    _ = initialize(migrated)
    _ = initialize(fresh)

    assert _table_schema(migrated) == _table_schema(fresh)


def test_migration_adds_sodium_and_keeps_rows(tmp_path: Path) -> None:
    path = tmp_path / "data/diet.db"
    _initialize_v1(path)

    applied = migrate(path)

    assert applied == [2, 3, 4, 5, 6, 7, 8, 9, 10]
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
    assert {"sodium", "fiber"} <= item_columns
    with connect(path) as connection:
        goal_columns = {
            cast(tuple[int, str], info)[1]
            for info in cast(
                list[tuple[object, ...]], connection.execute("PRAGMA table_info(goals)").fetchall()
            )
        }
    assert {"success_threshold_weight", "evaluation_window_days", "deleted_at"} <= goal_columns


def test_migration_drops_unused_protein_target(tmp_path: Path) -> None:
    """読み書きされないまま残っていた plans.protein_target を落とす。"""
    path = tmp_path / "data/diet.db"
    _initialize_v1(path)
    with connect(path) as connection:
        with connection:
            _ = connection.execute(
                "INSERT INTO goals (id, started_at, target_date, start_weight, target_weight, "
                + "target_type, status, created_at) VALUES (1, '2026-07-20', '2026-10-20', "
                + "80, 74, 'weight_loss', 'active', '2026-07-20T09:00:00+09:00')"
            )
            _ = connection.execute(
                "INSERT INTO plans (goal_id, calculated_at, target_weekly_weight_change, "
                + "protein_target, step_target, assumptions, weekly_actions, status) "
                + "VALUES (1, '2026-07-20T09:00:00+09:00', -0.5, NULL, 8000, '{}', '[]', 'active')"
            )

    _ = migrate(path)

    with connect(path) as connection:
        plan_columns = {
            cast(tuple[int, str], info)[1]
            for info in cast(
                list[tuple[object, ...]], connection.execute("PRAGMA table_info(plans)").fetchall()
            )
        }
        row = cast(
            sqlite3.Row, connection.execute("SELECT step_target, status FROM plans").fetchone()
        )
    assert "protein_target" not in plan_columns
    assert tuple(row) == (8000, "active"), "既存の計画行は保持される"


def test_migration_adds_nutrient_targets_and_basis_weight(tmp_path: Path) -> None:
    """栄養素の目安と、維持カロリーの前提体重をplanに持たせる。"""
    path = tmp_path / "data/diet.db"
    _initialize_v1(path)

    _ = migrate(path)

    with connect(path) as connection:
        plan_columns = {
            cast(tuple[int, str], info)[1]
            for info in cast(
                list[tuple[object, ...]], connection.execute("PRAGMA table_info(plans)").fetchall()
            )
        }
    assert {"nutrient_targets", "basis_weight"} <= plan_columns


def test_migration_marks_existing_feedback_as_cli_written(tmp_path: Path) -> None:
    """旧実装がCLIで生成したフィードバックは消さず、書き手を`cli`として残す。"""
    path = tmp_path / "data/diet.db"
    _initialize_v1(path)
    _insert_v1_legacy_feedback(
        path, "7day", "2026-07-20", "2026-07-20T21:00:00+09:00", "旧定型文", None
    )

    _ = migrate(path)

    with connect(path) as connection:
        rows = [
            tuple(row)
            for row in cast(
                list[sqlite3.Row],
                connection.execute("SELECT summary, written_by FROM feedback_history").fetchall(),
            )
        ]
    assert rows == [("旧定型文", "cli")]


def _insert_v1_legacy_feedback(
    path: Path,
    legacy_feedback_type: str,
    day: str,
    generated_at: str,
    summary: str,
    meal_id: int | None,
) -> None:
    details = json.dumps(
        {"situation": summary, **({"meal_id": meal_id} if meal_id is not None else {})},
        ensure_ascii=False,
    )
    with connect(path) as connection:
        with connection:
            _ = connection.execute(
                "INSERT INTO advice_history (generated_at, advice_type, period_start, "
                + "period_end, summary, details, evidence, priority, status) "
                + "VALUES (?, ?, ?, ?, ?, ?, '{}', 'normal', 'active')",
                (generated_at, legacy_feedback_type, day, day, summary, details),
            )


def test_migration_keeps_only_latest_feedback_per_period(tmp_path: Path) -> None:
    path = tmp_path / "data/diet.db"
    _initialize_v1(path)
    day = "2026-07-20"
    _insert_v1_legacy_feedback(
        path, "daily", day, "2026-07-20T09:00:00+09:00", "朝のフィードバック", None
    )
    _insert_v1_legacy_feedback(
        path, "daily", day, "2026-07-20T21:00:00+09:00", "夜のフィードバック", None
    )
    _insert_v1_legacy_feedback(
        path, "7day", day, "2026-07-20T21:00:00+09:00", "週のフィードバック", None
    )
    _insert_v1_legacy_feedback(
        path,
        "after_meal",
        day,
        "2026-07-20T12:30:00+09:00",
        "食後のフィードバック",
        1,
    )
    _insert_v1_legacy_feedback(
        path, "after_meal", day, "2026-07-20T13:00:00+09:00", "消えた食事", 999
    )

    _ = migrate(path)

    with connect(path) as connection:
        rows = [
            (
                cast(str, row["feedback_type"]),
                cast(str, row["summary"]),
                cast(int | None, row["meal_id"]),
            )
            for row in cast(
                list[sqlite3.Row],
                connection.execute("SELECT * FROM feedback_history ORDER BY id").fetchall(),
            )
        ]

    assert rows == [
        ("daily", "夜のフィードバック", None),
        ("7day", "週のフィードバック", None),
        ("after_meal", "食後のフィードバック", 1),
    ], "期間ごとに最新1件だけを残し、食事が消えた食後フィードバックは捨てる"
    with connect(path) as connection:
        old_table = cast(
            sqlite3.Row | None,
            connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'advice_history'"
            ).fetchone(),
        )
    assert old_table is None


def test_migration_is_not_reapplied(tmp_path: Path) -> None:
    path = tmp_path / "data/diet.db"
    _initialize_v1(path)
    _ = migrate(path)

    assert migrate(path) == [], "適用済みのマイグレーションは再実行しない"


def test_initialize_migrates_existing_db(tmp_path: Path) -> None:
    path = tmp_path / "data/diet.db"
    _initialize_v1(path)

    assert initialize(path) == [2, 3, 4, 5, 6, 7, 8, 9, 10]
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


def test_goal_delete_is_logical_and_keeps_plans(db_path: Path) -> None:
    goal_id = _goal(db_path)
    _ = save_plan(db_path, goal_id, today=date(2026, 7, 21))
    _ = activate_goal(db_path, goal_id)

    deleted = soft_delete_goal(db_path, goal_id)

    assert deleted["deleted_at"] is not None
    assert deleted["status"] == "inactive", "削除した目標をactiveのまま残さない"
    assert list_rows(db_path, "plans", where="goal_id = ?", params=(goal_id,)), (
        "過去のレポートの根拠になるplanを消さない"
    )


def test_deleted_goal_is_hidden_from_goal_operations(db_path: Path) -> None:
    goal_id = _goal(db_path)
    _ = save_plan(db_path, goal_id, today=date(2026, 7, 21))
    _ = soft_delete_goal(db_path, goal_id)

    with pytest.raises(NotFoundError):
        _ = get_goal(db_path, goal_id)
    with pytest.raises(NotFoundError):
        _ = activate_goal(db_path, goal_id)
    with pytest.raises(NotFoundError):
        _ = save_plan(db_path, goal_id, today=date(2026, 7, 22))
    with pytest.raises(NotFoundError):
        _ = soft_delete_goal(db_path, goal_id)


def test_deleted_goal_is_not_evaluated_as_active(db_path: Path) -> None:
    goal_id = _goal(db_path)
    _ = activate_goal(db_path, goal_id)
    _ = soft_delete_goal(db_path, goal_id)

    assert evaluate_active_goal(db_path, evaluation_date=date(2026, 7, 22)) is None


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


_PROFILE: dict[str, object] = {
    "height_cm": 175,
    "birth_date": "1991-03-04",
    "sex": "male",
    "activity_level": "sedentary",
}


def _weight(db_path: Path, measured_at: str, weight: float) -> None:
    _ = insert(
        db_path,
        "body_metrics",
        {
            "measured_at": measured_at,
            "weight": weight,
            "body_fat_percentage": None,
            "waist": None,
            "note": None,
            "created_at": now_iso(),
        },
    )


def test_plan_uses_latest_weight_not_start_weight(db_path: Path) -> None:
    """維持カロリーは目標登録時の体重ではなく直近の実測体重から計算する。"""
    goal_id = insert(
        db_path,
        "goals",
        {
            "started_at": "2026-07-21",
            "target_date": "2026-10-13",
            "start_weight": 91,
            "target_weight": 85,
            "target_type": "weight_loss",
            "status": "inactive",
            "note": None,
            "created_at": now_iso(),
        },
    )
    _weight(db_path, "2026-07-24T07:00:00+09:00", 88.5)
    _weight(db_path, "2026-07-25T07:00:00+09:00", 88.1)

    plan = save_plan(db_path, goal_id, profile=_PROFILE, today=date(2026, 7, 25))

    energy = cast(dict[str, object], plan["energy"])
    assert plan["basis_weight"] == 88.1
    assert energy["estimated_maintenance_calories"] == 2166, "88.1kg基準（91kg基準なら2200）"
    with connect(db_path) as connection:
        row = cast(
            sqlite3.Row,
            connection.execute(
                "SELECT basis_weight FROM plans WHERE id = ?", (plan["plan_id"],)
            ).fetchone(),
        )
    assert row["basis_weight"] == 88.1, "前提体重をplanに残し、古さを判定できるようにする"


def test_plan_falls_back_to_start_weight_without_measurements(db_path: Path) -> None:
    goal_id = _goal(db_path)

    plan = save_plan(db_path, goal_id, profile=_PROFILE, today=date(2026, 7, 21))

    assert plan["basis_weight"] == 80


def test_plan_stores_nutrient_targets(db_path: Path) -> None:
    """目標カロリーと同じ経路で、栄養素の目安もplanに保存する。"""
    goal_id = _goal(db_path)

    plan = save_plan(db_path, goal_id, profile=_PROFILE, today=date(2026, 7, 21))

    with connect(db_path) as connection:
        row = cast(
            sqlite3.Row,
            connection.execute(
                "SELECT nutrient_targets FROM plans WHERE id = ?", (plan["plan_id"],)
            ).fetchone(),
        )
    stored = cast(dict[str, object], json.loads(cast(str, row["nutrient_targets"])))
    assert sorted(stored) == ["carbohydrates", "fat", "fiber", "protein", "sodium"]
    protein = cast(dict[str, object], stored["protein"])
    energy = cast(dict[str, object], plan["energy"])
    target = cast(float, energy["target_daily_calories"])
    assert protein["minimum"] == pytest.approx(target * 0.13 / 4, abs=0.1)
    assert "13〜20%E" in cast(str, protein["basis"])


def test_plan_without_profile_stores_no_nutrient_targets(db_path: Path) -> None:
    goal_id = _goal(db_path)

    plan = save_plan(db_path, goal_id, today=date(2026, 7, 21))

    with connect(db_path) as connection:
        row = cast(
            sqlite3.Row,
            connection.execute(
                "SELECT nutrient_targets FROM plans WHERE id = ?", (plan["plan_id"],)
            ).fetchone(),
        )
    assert row["nutrient_targets"] is None


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


def test_period_average_calories_excludes_unrecorded_days(db_path: Path) -> None:
    _ = add_meal(
        db_path,
        {
            "eaten_at": "2026-07-21T12:00:00+09:00",
            "meal_type": "lunch",
            "estimated_calories": 1400,
        },
    )

    summary = period_summary(db_path, date(2026, 7, 21), 7)

    assert summary["average_calories"] == 1400
    assert summary["recorded_meal_days"] == 1


def test_daily_summary_uses_target_that_applied_on_report_date(db_path: Path) -> None:
    goal_id = insert(
        db_path,
        "goals",
        {
            "started_at": "2026-07-01",
            "target_date": "2026-10-01",
            "start_weight": 80,
            "target_weight": 74,
            "target_type": "weight_loss",
            "status": "active",
            "created_at": "2026-07-01T08:00:00+09:00",
        },
    )
    for calculated_at, target, status in (
        ("2026-07-01T08:00:00+09:00", 1800, "superseded"),
        ("2026-07-20T08:00:00+09:00", 1600, "active"),
    ):
        _ = insert(
            db_path,
            "plans",
            {
                "goal_id": goal_id,
                "calculated_at": calculated_at,
                "target_daily_calories": target,
                "target_weekly_weight_change": -0.5,
                "assumptions": "{}",
                "weekly_actions": "[]",
                "status": status,
            },
        )

    before_change = daily_summary(db_path, date(2026, 7, 15))
    after_change = daily_summary(db_path, date(2026, 7, 21))

    assert before_change["target_daily_calories"] == 1800
    assert after_change["target_daily_calories"] == 1600


def test_daily_summary_uses_configured_day_start(db_path: Path) -> None:
    _ = add_meal(
        db_path,
        {
            "eaten_at": "2026-07-23T00:08:00+09:00",
            "meal_type": "snack",
            "estimated_calories": 46,
        },
    )
    _ = add_meal(
        db_path,
        {
            "eaten_at": "2026-07-23T04:00:00+09:00",
            "meal_type": "breakfast",
            "estimated_calories": 200,
        },
    )

    previous_day = daily_summary(db_path, date(2026, 7, 22), day_start=time(4))
    current_day = daily_summary(db_path, date(2026, 7, 23), day_start=time(4))

    assert [meal["meal_type"] for meal in previous_day["meals"]] == ["snack"]
    assert previous_day["totals"]["estimated_calories"] == 46
    assert [meal["meal_type"] for meal in current_day["meals"]] == ["breakfast"]


def test_reporting_date_uses_previous_date_before_day_start() -> None:
    assert reporting_date(datetime(2026, 7, 23, 0, 8), starts_at=time(4)) == date(2026, 7, 22)
    assert reporting_date(datetime(2026, 7, 23, 4, 0), starts_at=time(4)) == date(2026, 7, 23)


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
