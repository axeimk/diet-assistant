from __future__ import annotations

import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import cast

SCHEMA_VERSION = 10


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    _ = connection.execute("PRAGMA foreign_keys = ON")
    _ = connection.execute("PRAGMA busy_timeout = 5000")
    return connection


@contextmanager
def transaction(path: Path) -> Generator[sqlite3.Connection]:
    connection = connect(path)
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def initialize(path: Path) -> list[int]:
    """スキーマを作成し、既存DBには未適用のマイグレーションを適用する。

    戻り値は適用したマイグレーションのバージョン一覧（新規作成時は空）。
    既存DBではマイグレーションを先に適用する。`SCHEMA_SQL`は最新スキーマを前提にした
    索引を含むため、列の追加より先に流すと失敗する。
    """
    with connect(path) as connection:
        fresh = (
            connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='meals'"
            ).fetchone()
            is None
        )
    applied = [] if fresh else migrate(path)
    with transaction(path) as connection:
        _ = connection.executescript(SCHEMA_SQL)
        if fresh:
            _ = connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    return applied


def schema_version(path: Path) -> int:
    with connect(path) as connection:
        row = cast(tuple[int], connection.execute("PRAGMA user_version").fetchone())
    return row[0]


def migrate(path: Path) -> list[int]:
    """`PRAGMA user_version`より新しいマイグレーションを順に適用する。"""
    version = schema_version(path)
    applied: list[int] = []
    for target in sorted(MIGRATIONS):
        if target <= version:
            continue
        with transaction(path) as connection:
            _ = connection.executescript(MIGRATIONS[target])
            _ = connection.execute(f"PRAGMA user_version = {target}")
        applied.append(target)
    return applied


# 各マイグレーションは冪等ではない。適用済みかどうかは PRAGMA user_version だけで判定する。
MIGRATIONS: dict[int, str] = {
    2: """
ALTER TABLE meals ADD COLUMN sodium REAL CHECK(sodium >= 0);
ALTER TABLE meal_items ADD COLUMN sodium REAL CHECK(sodium >= 0);
""",
    3: """
ALTER TABLE goals ADD COLUMN success_threshold_weight REAL CHECK(success_threshold_weight > 0);
ALTER TABLE goals ADD COLUMN evaluation_window_days INTEGER NOT NULL DEFAULT 1
    CHECK(evaluation_window_days BETWEEN 1 AND 28);
ALTER TABLE plans ADD COLUMN estimated_maintenance_calories INTEGER;
ALTER TABLE plans ADD COLUMN planned_daily_deficit INTEGER;
""",
    4: """
ALTER TABLE meal_items ADD COLUMN fiber REAL CHECK(fiber >= 0);
""",
    5: """
ALTER TABLE goals ADD COLUMN deleted_at TEXT;
""",
    # フィードバック履歴を「種別・期間ごとに最新1件」へ作り直す。
    # 重複していた過去の行は最新だけを残し、食事が既に削除されている食後分は捨てる。
    6: """
CREATE TABLE advice_history_rebuilt (
    id INTEGER PRIMARY KEY,
    generated_at TEXT NOT NULL,
    advice_type TEXT NOT NULL,
    meal_id INTEGER REFERENCES meals(id) ON DELETE CASCADE,
    period_start TEXT NOT NULL,
    period_end TEXT NOT NULL,
    summary TEXT NOT NULL,
    details TEXT NOT NULL,
    evidence TEXT NOT NULL,
    priority TEXT NOT NULL
);
INSERT INTO advice_history_rebuilt
    (id, generated_at, advice_type, meal_id, period_start, period_end,
     summary, details, evidence, priority)
SELECT h.id, h.generated_at, h.advice_type,
       CAST(json_extract(h.details, '$.meal_id') AS INTEGER),
       h.period_start, h.period_end, h.summary, h.details, h.evidence, h.priority
FROM advice_history h
WHERE h.id = (
        SELECT o.id FROM advice_history o
        WHERE o.advice_type = h.advice_type
          AND o.period_start = h.period_start
          AND o.period_end = h.period_end
          AND COALESCE(CAST(json_extract(o.details, '$.meal_id') AS INTEGER), 0)
              = COALESCE(CAST(json_extract(h.details, '$.meal_id') AS INTEGER), 0)
        ORDER BY o.generated_at DESC, o.id DESC
        LIMIT 1
    )
  AND (
        json_extract(h.details, '$.meal_id') IS NULL
        OR EXISTS (
            SELECT 1 FROM meals m
            WHERE m.id = CAST(json_extract(h.details, '$.meal_id') AS INTEGER)
        )
    );
DROP TABLE advice_history;
ALTER TABLE advice_history_rebuilt RENAME TO advice_history;
CREATE UNIQUE INDEX IF NOT EXISTS advice_history_key
    ON advice_history(advice_type, period_start, period_end, COALESCE(meal_id, 0));
""",
    # v1から書き込みも読み出しもされないまま残っていた列を落とす。
    7: """
ALTER TABLE plans DROP COLUMN protein_target;
""",
    # 栄養素の目安（JSON）と、維持カロリーの前提にした体重をplanへ残す。
    # 目安は読み出すコード（reporting・analysis）と同時に追加している。
    8: """
ALTER TABLE plans ADD COLUMN nutrient_targets TEXT;
ALTER TABLE plans ADD COLUMN basis_weight REAL CHECK(basis_weight > 0);
""",
    # フィードバックの書き手を区別する。旧実装がCLIで生成した定型文は履歴として残すが、
    # レポートには埋め込まない（ADR 0013）。
    9: """
ALTER TABLE advice_history ADD COLUMN written_by TEXT NOT NULL DEFAULT 'agent'
    CHECK(written_by IN ('cli','agent'));
UPDATE advice_history SET written_by = 'cli';
""",
    # 正規用語に合わせてフィードバック履歴のテーブル・列・索引名を変更する。
    10: """
ALTER TABLE advice_history RENAME TO feedback_history;
ALTER TABLE feedback_history RENAME COLUMN advice_type TO feedback_type;
DROP INDEX advice_history_key;
CREATE UNIQUE INDEX feedback_history_key
    ON feedback_history(feedback_type, period_start, period_end, COALESCE(meal_id, 0));
""",
}


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS goals (
    id INTEGER PRIMARY KEY,
    started_at TEXT NOT NULL,
    target_date TEXT NOT NULL,
    start_weight REAL NOT NULL CHECK(start_weight > 0),
    target_weight REAL NOT NULL CHECK(target_weight > 0),
    success_threshold_weight REAL CHECK(success_threshold_weight > 0),
    evaluation_window_days INTEGER NOT NULL DEFAULT 1
        CHECK(evaluation_window_days BETWEEN 1 AND 28),
    target_type TEXT NOT NULL DEFAULT 'weight_loss',
    status TEXT NOT NULL DEFAULT 'inactive'
        CHECK(status IN ('active','inactive','completed','cancelled')),
    note TEXT,
    created_at TEXT NOT NULL,
    deleted_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS one_active_goal ON goals(status) WHERE status = 'active';

CREATE TABLE IF NOT EXISTS plans (
    id INTEGER PRIMARY KEY,
    goal_id INTEGER NOT NULL REFERENCES goals(id) ON DELETE CASCADE,
    calculated_at TEXT NOT NULL,
    target_daily_calories INTEGER,
    target_calorie_range_min INTEGER,
    target_calorie_range_max INTEGER,
    estimated_maintenance_calories INTEGER,
    planned_daily_deficit INTEGER,
    nutrient_targets TEXT,
    basis_weight REAL CHECK(basis_weight > 0),
    target_weekly_exercise_minutes INTEGER,
    target_weekly_weight_change REAL NOT NULL,
    step_target INTEGER,
    assumptions TEXT NOT NULL,
    weekly_actions TEXT NOT NULL DEFAULT '[]',
    safety_note TEXT,
    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','superseded'))
);
CREATE INDEX IF NOT EXISTS plans_goal_id ON plans(goal_id);

CREATE TABLE IF NOT EXISTS meals (
    id INTEGER PRIMARY KEY,
    eaten_at TEXT NOT NULL,
    meal_type TEXT NOT NULL CHECK(meal_type IN ('breakfast','lunch','dinner','snack','other')),
    note TEXT,
    photo_path TEXT,
    estimated_calories REAL CHECK(estimated_calories >= 0),
    calories_min REAL CHECK(calories_min >= 0),
    calories_max REAL CHECK(calories_max >= 0),
    protein REAL CHECK(protein >= 0),
    fat REAL CHECK(fat >= 0),
    carbohydrates REAL CHECK(carbohydrates >= 0),
    fiber REAL CHECK(fiber >= 0),
    sodium REAL CHECK(sodium >= 0),
    estimation_confidence TEXT CHECK(estimation_confidence IN ('low','medium','high')),
    source TEXT NOT NULL DEFAULT 'manual',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK(calories_min IS NULL OR calories_max IS NULL OR calories_min <= calories_max),
    CHECK(estimated_calories IS NULL OR calories_min IS NULL OR estimated_calories >= calories_min),
    CHECK(estimated_calories IS NULL OR calories_max IS NULL OR estimated_calories <= calories_max)
);
CREATE INDEX IF NOT EXISTS meals_eaten_at ON meals(eaten_at);

CREATE TABLE IF NOT EXISTS meal_items (
    id INTEGER PRIMARY KEY,
    meal_id INTEGER NOT NULL REFERENCES meals(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    amount_text TEXT,
    estimated_grams REAL CHECK(estimated_grams >= 0),
    estimated_calories REAL CHECK(estimated_calories >= 0),
    calories_min REAL CHECK(calories_min >= 0),
    calories_max REAL CHECK(calories_max >= 0),
    protein REAL CHECK(protein >= 0),
    fat REAL CHECK(fat >= 0),
    carbohydrates REAL CHECK(carbohydrates >= 0),
    fiber REAL CHECK(fiber >= 0),
    sodium REAL CHECK(sodium >= 0),
    confidence TEXT CHECK(confidence IN ('low','medium','high')),
    note TEXT
);

CREATE TABLE IF NOT EXISTS exercises (
    id INTEGER PRIMARY KEY,
    performed_at TEXT NOT NULL,
    exercise_type TEXT NOT NULL,
    duration_minutes REAL CHECK(duration_minutes >= 0),
    distance REAL CHECK(distance >= 0),
    sets INTEGER CHECK(sets >= 0),
    repetitions INTEGER CHECK(repetitions >= 0),
    weight REAL CHECK(weight >= 0),
    intensity TEXT,
    estimated_calories_burned REAL CHECK(estimated_calories_burned >= 0),
    note TEXT,
    source TEXT NOT NULL DEFAULT 'manual',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS exercises_performed_at ON exercises(performed_at);

CREATE TABLE IF NOT EXISTS body_metrics (
    id INTEGER PRIMARY KEY,
    measured_at TEXT NOT NULL,
    weight REAL CHECK(weight > 0),
    body_fat_percentage REAL CHECK(body_fat_percentage BETWEEN 0 AND 100),
    waist REAL CHECK(waist > 0),
    note TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS metrics_measured_at ON body_metrics(measured_at);

CREATE TABLE IF NOT EXISTS intake_entries (
    id INTEGER PRIMARY KEY,
    external_id TEXT NOT NULL UNIQUE,
    occurred_at TEXT NOT NULL,
    type TEXT NOT NULL CHECK(type IN ('meal','exercise','metric')),
    source TEXT NOT NULL,
    raw_text TEXT,
    raw_json TEXT NOT NULL,
    image_paths TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL
        CHECK(status IN ('pending','processing','completed','needs_review','failed')),
    error_message TEXT,
    created_at TEXT NOT NULL,
    processed_at TEXT,
    result_type TEXT,
    result_id INTEGER
);
CREATE INDEX IF NOT EXISTS intake_status ON intake_entries(status);

CREATE TABLE IF NOT EXISTS feedback_history (
    id INTEGER PRIMARY KEY,
    generated_at TEXT NOT NULL,
    feedback_type TEXT NOT NULL,
    meal_id INTEGER REFERENCES meals(id) ON DELETE CASCADE,
    period_start TEXT NOT NULL,
    period_end TEXT NOT NULL,
    summary TEXT NOT NULL,
    details TEXT NOT NULL,
    evidence TEXT NOT NULL,
    priority TEXT NOT NULL,
    -- 文面の書き手。CLIが生成した過去の定型文（cli）はレポートへ埋め込まない。
    written_by TEXT NOT NULL DEFAULT 'agent' CHECK(written_by IN ('cli','agent'))
);
-- フィードバックは種別・期間（食後は対象の食事）ごとに最新の1件だけを保持する。
CREATE UNIQUE INDEX IF NOT EXISTS feedback_history_key
    ON feedback_history(feedback_type, period_start, period_end, COALESCE(meal_id, 0));
"""
