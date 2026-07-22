from __future__ import annotations

import json
import re
from datetime import date, time
from importlib import resources
from pathlib import Path
from typing import cast

SCHEMA_RESOURCE = "profile.schema.json"

_TYPE_LABELS = {
    "string": "文字列",
    "number": "数値",
    "integer": "整数",
    "boolean": "真偽値",
    "array": "配列",
    "object": "オブジェクト",
    "null": "null",
}


def load_profile(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as file:
        value = cast(object, json.load(file))
    if not isinstance(value, dict):
        raise ValueError("プロフィールはJSONオブジェクトである必要があります")
    return cast(dict[str, object], value)


def load_schema() -> dict[str, object]:
    text = resources.files(__package__ or "diet_assistant").joinpath(SCHEMA_RESOURCE).read_text(
        encoding="utf-8"
    )
    return cast(dict[str, object], json.loads(text))


def validate_profile(
    profile: dict[str, object], schema: dict[str, object] | None = None
) -> list[str]:
    return _validate(profile, schema if schema is not None else load_schema(), "プロフィール")


def profile_day_start_time(profile: dict[str, object]) -> time:
    value = profile.get("day_start_time", "00:00")
    if not isinstance(value, str):
        raise ValueError("day_start_time は HH:MM 形式の時刻にしてください")
    try:
        parsed = time.fromisoformat(value)
    except ValueError as error:
        raise ValueError("day_start_time は HH:MM 形式の時刻にしてください") from error
    if len(value) != 5 or parsed.second or parsed.microsecond or parsed.tzinfo is not None:
        raise ValueError("day_start_time は HH:MM 形式の時刻にしてください")
    return parsed


def _validate(value: object, schema: dict[str, object], path: str) -> list[str]:
    errors: list[str] = []
    expected = schema.get("type")
    if isinstance(expected, str) and not _matches_type(value, expected):
        return [f"{path} は{_TYPE_LABELS.get(expected, expected)}にしてください"]

    allowed = schema.get("enum")
    if isinstance(allowed, list) and value not in cast(list[object], allowed):
        joined = "、".join(str(item) for item in cast(list[object], allowed))
        errors.append(f"{path} は {joined} のいずれかにしてください")

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        minimum = schema.get("minimum")
        if isinstance(minimum, (int, float)) and value < minimum:
            errors.append(f"{path} は {minimum} 以上にしてください")
        maximum = schema.get("maximum")
        if isinstance(maximum, (int, float)) and value > maximum:
            errors.append(f"{path} は {maximum} 以下にしてください")

    if isinstance(value, str) and schema.get("format") == "date":
        try:
            _ = date.fromisoformat(value)
        except ValueError:
            errors.append(f"{path} は YYYY-MM-DD 形式の日付にしてください")
    pattern = schema.get("pattern")
    if isinstance(value, str) and isinstance(pattern, str) and re.search(pattern, value) is None:
        errors.append(f"{path} は指定された形式にしてください")

    if isinstance(value, dict):
        errors.extend(_validate_object(cast(dict[str, object], value), schema, path))
    elif isinstance(value, list):
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(cast(list[object], value)):
                errors.extend(
                    _validate(item, cast(dict[str, object], item_schema), f"{path}[{index}]")
                )
    return errors


def _validate_object(value: dict[str, object], schema: dict[str, object], path: str) -> list[str]:
    errors: list[str] = []
    raw_properties = schema.get("properties")
    properties = cast(dict[str, object], raw_properties) if isinstance(raw_properties, dict) else {}

    required = schema.get("required")
    if isinstance(required, list):
        for key in cast(list[object], required):
            if isinstance(key, str) and key not in value:
                errors.append(f"{_label(path, key)} は必須です")

    if schema.get("additionalProperties") is False:
        for key in value:
            if key not in properties:
                errors.append(f"{_label(path, key)} は未知のキーです")

    for key, item in value.items():
        item_schema = properties.get(key)
        if isinstance(item_schema, dict):
            errors.extend(
                _validate(item, cast(dict[str, object], item_schema), _label(path, key))
            )
    return errors


def _label(path: str, key: str) -> str:
    return key if path == "プロフィール" else f"{path}.{key}"


def _matches_type(value: object, type_name: str) -> bool:
    if type_name == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if type_name == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if type_name == "string":
        return isinstance(value, str)
    if type_name == "boolean":
        return isinstance(value, bool)
    if type_name == "array":
        return isinstance(value, list)
    if type_name == "object":
        return isinstance(value, dict)
    if type_name == "null":
        return value is None
    return True
