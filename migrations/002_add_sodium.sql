-- 実行時の正本は src/diet_assistant/db.py の MIGRATIONS[2]。
-- このファイルは変更履歴を示す。適用後 PRAGMA user_version = 2。
-- 食塩相当量（g）を食事とその品目に記録できるようにする。ナトリウム量ではない。

ALTER TABLE meals ADD COLUMN sodium REAL CHECK(sodium >= 0);
ALTER TABLE meal_items ADD COLUMN sodium REAL CHECK(sodium >= 0);
