import json
from pathlib import Path

from diet_assistant.config import load_profile, load_schema, validate_profile

EXAMPLE = Path(__file__).resolve().parents[1] / "config/profile.example.json"


def test_schema_loads_from_package() -> None:
    schema = load_schema()
    assert schema["type"] == "object"
    assert "photo_retention_days" in json.dumps(schema)


def test_example_profile_is_valid() -> None:
    assert validate_profile(load_profile(EXAMPLE)) == []


def test_empty_profile_is_valid() -> None:
    assert validate_profile({}) == []


def test_out_of_range_values_are_reported() -> None:
    errors = validate_profile({"height_cm": 300, "meals_per_day": 0, "photo_retention_days": 0})
    assert len(errors) == 3
    assert any("height_cm" in message for message in errors)
    assert any("meals_per_day" in message for message in errors)
    assert any("photo_retention_days" in message for message in errors)


def test_wrong_types_are_reported() -> None:
    errors = validate_profile({"height_cm": "170", "meals_per_day": 3.5, "allergies": "そば"})
    assert len(errors) == 3


def test_enum_and_date_format_are_reported() -> None:
    errors = validate_profile({"sex": "unknown", "birth_date": "1990/01/01"})
    assert len(errors) == 2


def test_unknown_key_is_reported() -> None:
    errors = validate_profile({"heigth_cm": 170})
    assert errors == ["heigth_cm は未知のキーです"]


def test_schema_key_is_allowed() -> None:
    assert validate_profile({"$schema": "../src/diet_assistant/profile.schema.json"}) == []


def test_array_items_are_checked() -> None:
    errors = validate_profile({"allergies": ["そば", 1]})
    assert errors == ["allergies[1] は文字列にしてください"]


def test_boolean_is_not_accepted_as_number() -> None:
    assert validate_profile({"height_cm": True}) != []


def test_routine_accepts_known_steps_in_any_order() -> None:
    steps = ["weight", "breakfast", "lunch", "snack", "dinner", "report"]
    assert validate_profile({"routine": steps}) == []


def test_routine_rejects_unknown_step() -> None:
    errors = validate_profile({"routine": ["breakfast", "朝食"]})
    assert len(errors) == 1
    assert "routine[1]" in errors[0]
