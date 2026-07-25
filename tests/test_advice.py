import json
import sqlite3
from datetime import date
from pathlib import Path
from typing import cast

from diet_assistant.db import connect
from diet_assistant.repository import add_meal, list_rows
from diet_assistant.services.advice import (
    generate_advice,
    generate_daily_advice,
    generate_meal_advice,
)


def _advice_rows(path: Path) -> list[dict[str, object]]:
    return list_rows(path, "advice_history", order_by="id")


def _meal(path: Path, eaten_at: str, calories: float) -> dict[str, object]:
    return add_meal(
        path,
        {
            "eaten_at": eaten_at,
            "meal_type": "lunch",
            "estimated_calories": calories,
        },
    )


def test_daily_advice_keeps_one_row_per_day(db_path: Path) -> None:
    day = date(2026, 7, 21)
    _ = generate_daily_advice(db_path, day)
    _ = _meal(db_path, "2026-07-21T12:00:00+09:00", 600)
    latest = generate_daily_advice(db_path, day)

    rows = _advice_rows(db_path)

    assert len(rows) == 1, "同じ日の日次助言は追記せず上書きする"
    assert rows[0]["summary"] == latest["situation"]
    assert rows[0]["advice_type"] == "daily"


def test_daily_advice_is_separate_per_day(db_path: Path) -> None:
    _ = generate_daily_advice(db_path, date(2026, 7, 21))
    _ = generate_daily_advice(db_path, date(2026, 7, 22))

    assert len(_advice_rows(db_path)) == 2


def test_period_advice_keeps_one_row_per_period(db_path: Path) -> None:
    day = date(2026, 7, 21)
    _ = generate_advice(db_path, day, 7)
    _ = generate_advice(db_path, day, 7)
    _ = generate_advice(db_path, day, 14)

    rows = _advice_rows(db_path)

    assert [row["advice_type"] for row in rows] == ["7day", "14day"]


def test_period_advice_does_not_collide_with_daily_advice(db_path: Path) -> None:
    day = date(2026, 7, 21)
    _ = generate_daily_advice(db_path, day)
    _ = generate_advice(db_path, day, 1)

    assert len(_advice_rows(db_path)) == 2, "日次助言と1日期間の助言は別種として残る"


def test_meal_advice_is_kept_per_meal(db_path: Path) -> None:
    profile: dict[str, object] = {"meals_per_day": 3}
    first = _meal(db_path, "2026-07-21T08:00:00+09:00", 400)
    second = _meal(db_path, "2026-07-21T12:00:00+09:00", 600)
    _ = generate_meal_advice(db_path, first, profile)
    _ = generate_meal_advice(db_path, second, profile)
    latest = generate_meal_advice(db_path, first, profile)

    rows = _advice_rows(db_path)

    assert len(rows) == 2, "食事ごとに1行、同じ食事の再生成は上書きする"
    assert [row["meal_id"] for row in rows] == [first["id"], second["id"]]
    assert rows[0]["summary"] == latest["situation"]


def test_meal_advice_is_removed_with_its_meal(db_path: Path) -> None:
    meal = _meal(db_path, "2026-07-21T08:00:00+09:00", 400)
    _ = generate_meal_advice(db_path, meal, {"meals_per_day": 3})

    with connect(db_path) as connection:
        with connection:
            _ = connection.execute("DELETE FROM meals WHERE id = ?", (meal["id"],))

    assert _advice_rows(db_path) == [], "食事を削除したらその食後助言も残さない"


def test_saved_advice_details_are_reloadable(db_path: Path) -> None:
    day = date(2026, 7, 21)
    result = generate_daily_advice(db_path, day)

    row = _advice_rows(db_path)[0]
    details = cast(dict[str, object], json.loads(cast(str, row["details"])))

    assert details == result
    assert row["period_start"] == day.isoformat()
    assert row["period_end"] == day.isoformat()


def test_advice_history_rejects_duplicate_key(db_path: Path) -> None:
    _ = generate_daily_advice(db_path, date(2026, 7, 21))
    row = _advice_rows(db_path)[0]

    with connect(db_path) as connection:
        try:
            with connection:
                _ = connection.execute(
                    "INSERT INTO advice_history "
                    + "(generated_at, advice_type, period_start, period_end, "
                    + "summary, details, evidence, priority) "
                    + "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        row["generated_at"],
                        row["advice_type"],
                        row["period_start"],
                        row["period_end"],
                        row["summary"],
                        row["details"],
                        row["evidence"],
                        row["priority"],
                    ),
                )
        except sqlite3.IntegrityError:
            return
    raise AssertionError("同じ種別・期間の助言を二重に挿入できてはいけない")
