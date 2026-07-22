-- 実行時の正本は src/diet_assistant/db.py の MIGRATIONS[4]。
-- このファイルは変更履歴を示す。適用後 PRAGMA user_version = 4。
-- 食物繊維（g）を品目にも記録できるようにする（食事全体のfiber列は初期スキーマから存在）。

ALTER TABLE meal_items ADD COLUMN fiber REAL CHECK(fiber >= 0);
