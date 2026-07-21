from __future__ import annotations

import json
from pathlib import Path
from typing import cast


def load_profile(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as file:
        value = cast(object, json.load(file))
    if not isinstance(value, dict):
        raise ValueError("プロフィールはJSONオブジェクトである必要があります")
    return cast(dict[str, object], value)


def validate_profile(profile: dict[str, object]) -> list[str]:
    errors: list[str] = []
    height = profile.get("height_cm")
    if height is not None and (not isinstance(height, (int, float)) or not 100 <= height <= 250):
        errors.append("height_cm は100〜250の数値にしてください")
    meals = profile.get("meals_per_day")
    if meals is not None and (not isinstance(meals, int) or not 1 <= meals <= 10):
        errors.append("meals_per_day は1〜10の整数にしてください")
    retention = profile.get("photo_retention_days")
    if retention is not None and (not isinstance(retention, int) or retention < 1):
        errors.append("photo_retention_days は1以上の整数にしてください")
    return errors
