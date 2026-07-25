"""助言の保存と読み出しのテスト。

助言の文面はエージェントが書き、CLIは保存とfindingsの添付だけを行う（ADR 0013）。
種別・期間ごとに最新1件だけを保持する規則（ADR 0010）は変わらない。
"""

import json
import sqlite3
from datetime import date
from pathlib import Path
from typing import cast

import pytest

from diet_assistant.db import connect
from diet_assistant.repository import add_meal, list_rows
from diet_assistant.services.advice import latest_advice, meal_day_context, save_advice

PROFILE: dict[str, object] = {
    "height_cm": 175,
    "birth_date": "1991-03-04",
    "sex": "male",
    "activity_level": "sedentary",
    "meals_per_day": 3,
}
TEXT: dict[str, object] = {
    "situation": "夕食が1日の43%を占めている",
    "priority_action": "夕食の主菜を昼に寄せる",
}


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
    _ = save_advice(db_path, TEXT, kind="daily", day=day, profile=PROFILE)
    _ = _meal(db_path, "2026-07-21T12:00:00+09:00", 600)
    latest = save_advice(
        db_path, {**TEXT, "situation": "書き直した"}, kind="daily", day=day, profile=PROFILE
    )

    rows = _advice_rows(db_path)

    assert len(rows) == 1, "同じ日の日次助言は追記せず上書きする"
    assert rows[0]["summary"] == latest["situation"] == "書き直した"
    assert rows[0]["advice_type"] == "daily"


def test_daily_advice_is_separate_per_day(db_path: Path) -> None:
    _ = save_advice(db_path, TEXT, kind="daily", day=date(2026, 7, 21), profile=PROFILE)
    _ = save_advice(db_path, TEXT, kind="daily", day=date(2026, 7, 22), profile=PROFILE)

    assert len(_advice_rows(db_path)) == 2


def test_period_advice_keeps_one_row_per_period(db_path: Path) -> None:
    day = date(2026, 7, 21)
    _ = save_advice(db_path, TEXT, kind="period", day=day, days=7, profile=PROFILE)
    _ = save_advice(db_path, TEXT, kind="period", day=day, days=7, profile=PROFILE)
    _ = save_advice(db_path, TEXT, kind="period", day=day, days=14, profile=PROFILE)

    rows = _advice_rows(db_path)

    assert [row["advice_type"] for row in rows] == ["7day", "14day"]
    assert rows[0]["period_start"] == "2026-07-15"
    assert rows[0]["period_end"] == "2026-07-21"


def test_period_advice_does_not_collide_with_daily_advice(db_path: Path) -> None:
    day = date(2026, 7, 21)
    _ = save_advice(db_path, TEXT, kind="daily", day=day, profile=PROFILE)
    _ = save_advice(db_path, TEXT, kind="period", day=day, days=1, profile=PROFILE)

    assert len(_advice_rows(db_path)) == 2, "日次助言と1日期間の助言は別種として残る"


def test_meal_advice_is_kept_per_meal(db_path: Path) -> None:
    first = _meal(db_path, "2026-07-21T08:00:00+09:00", 400)
    second = _meal(db_path, "2026-07-21T12:00:00+09:00", 600)
    day = date(2026, 7, 21)
    first_id = cast(int, first["id"])
    second_id = cast(int, second["id"])
    _ = save_advice(db_path, TEXT, kind="after_meal", day=day, meal_id=first_id, profile=PROFILE)
    _ = save_advice(db_path, TEXT, kind="after_meal", day=day, meal_id=second_id, profile=PROFILE)
    latest = save_advice(
        db_path,
        {**TEXT, "situation": "上書き"},
        kind="after_meal",
        day=day,
        meal_id=first_id,
        profile=PROFILE,
    )

    rows = _advice_rows(db_path)

    assert len(rows) == 2, "食事ごとに1行、同じ食事の再生成は上書きする"
    assert [row["meal_id"] for row in rows] == [first_id, second_id]
    assert rows[0]["summary"] == latest["situation"]


def test_meal_advice_is_removed_with_its_meal(db_path: Path) -> None:
    meal = _meal(db_path, "2026-07-21T08:00:00+09:00", 400)
    _ = save_advice(
        db_path,
        TEXT,
        kind="after_meal",
        day=date(2026, 7, 21),
        meal_id=cast(int, meal["id"]),
        profile=PROFILE,
    )

    with connect(db_path) as connection:
        with connection:
            _ = connection.execute("DELETE FROM meals WHERE id = ?", (meal["id"],))

    assert _advice_rows(db_path) == [], "食事を削除したらその食後助言も残さない"


def test_evidence_is_computed_by_the_cli_not_supplied_by_the_writer(db_path: Path) -> None:
    """根拠は保存側が計算する。書き手が数値を書き込むことはできない。"""
    for day in range(15, 22):
        _ = _meal(db_path, f"2026-07-{day:02d}T12:00:00+09:00", 2500)

    with pytest.raises(ValueError, match="evidence"):
        _ = save_advice(
            db_path,
            {**TEXT, "evidence": "捏造した根拠"},
            kind="period",
            day=date(2026, 7, 21),
            days=7,
            profile=PROFILE,
        )

    result = save_advice(
        db_path, TEXT, kind="period", day=date(2026, 7, 21), days=7, profile=PROFILE
    )

    row = _advice_rows(db_path)[0]
    evidence = cast(list[dict[str, object]], json.loads(cast(str, row["evidence"])))
    assert [f["kind"] for f in evidence], "findingsが根拠として保存される"
    assert result["evidence"] == evidence


def test_required_text_keys_are_enforced(db_path: Path) -> None:
    with pytest.raises(ValueError, match="priority_action"):
        _ = save_advice(
            db_path, {"situation": "状況だけ"}, kind="daily", day=date(2026, 7, 21), profile=PROFILE
        )


def test_unknown_text_keys_are_rejected(db_path: Path) -> None:
    """項目名の取り違えを黙って捨てない。"""
    with pytest.raises(ValueError, match="priority"):
        _ = save_advice(
            db_path,
            {**TEXT, "priority": "最優先"},
            kind="daily",
            day=date(2026, 7, 21),
            profile=PROFILE,
        )


def test_latest_advice_reads_back_what_was_written(db_path: Path) -> None:
    day = date(2026, 7, 21)
    text: dict[str, object] = {**TEXT, "alternative": "主食を少し減らす", "plan_change": "変更なし"}
    _ = save_advice(db_path, text, kind="period", day=day, days=7, profile=PROFILE)

    stored = latest_advice(db_path, kind="period", day=day, days=7)

    assert stored is not None
    assert stored["situation"] == text["situation"]
    assert stored["alternative"] == "主食を少し減らす"
    assert latest_advice(db_path, kind="daily", day=day) is None


def test_cli_written_advice_is_not_read_back_as_advice(db_path: Path) -> None:
    """旧実装がCLIで生成した文面は、助言としてレポートへ埋め込まない（ADR 0013）。"""
    day = date(2026, 7, 21)
    with connect(db_path) as connection:
        with connection:
            _ = connection.execute(
                "INSERT INTO advice_history (generated_at, advice_type, period_start, "
                + "period_end, summary, details, evidence, priority, written_by) "
                + "VALUES (?, 'daily', ?, ?, '旧定型文', ?, '{}', 'normal', 'cli')",
                (
                    "2026-07-21T21:00:00+09:00",
                    day.isoformat(),
                    day.isoformat(),
                    json.dumps({"situation": "旧定型文"}, ensure_ascii=False),
                ),
            )

    assert latest_advice(db_path, kind="daily", day=day) is None

    _ = save_advice(db_path, TEXT, kind="daily", day=day, profile=PROFILE)
    stored = latest_advice(db_path, kind="daily", day=day)

    assert stored is not None and stored["situation"] == TEXT["situation"]
    assert len(_advice_rows(db_path)) == 1, "同じ期間の行は上書きされる"


def test_meal_day_context_reports_the_remaining_budget(db_path: Path) -> None:
    """食後は、その日の残りカロリーと残り食数という数値だけを返す（文面は返さない）。"""
    meal = _meal(db_path, "2026-07-21T12:00:00+09:00", 600)

    context = meal_day_context(db_path, meal, PROFILE)

    assert context["date"] == "2026-07-21"
    assert context["consumed_calories"] == 600
    assert context["remaining_meals"] == 2
    assert "situation" not in context
    assert "priority_action" not in context


def test_advice_history_rejects_duplicate_key(db_path: Path) -> None:
    _ = save_advice(db_path, TEXT, kind="daily", day=date(2026, 7, 21), profile=PROFILE)
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
