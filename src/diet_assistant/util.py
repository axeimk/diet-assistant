from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import cast


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def parse_datetime(value: str | None, *, default: datetime | None = None) -> datetime:
    if value is None:
        return default or datetime.now().astimezone()
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.astimezone()
    return parsed


def day_bounds(day: date, *, starts_at: time = time.min) -> tuple[str, str]:
    zone = datetime.now().astimezone().tzinfo
    start = datetime.combine(day, starts_at, zone)
    end = datetime.combine(day + timedelta(days=1), starts_at, zone) - timedelta(microseconds=1)
    return start.isoformat(), end.isoformat()


def reporting_date(moment: datetime, *, starts_at: time = time.min) -> date:
    day = moment.date()
    return day - timedelta(days=1) if moment.timetz().replace(tzinfo=None) < starts_at else day


_WEEKDAY_LABELS = ("月", "火", "水", "木", "金", "土", "日")


def with_weekday(value: str) -> str:
    """ISO 8601の日付に曜日を添える。'2026-07-26' → '2026-07-26（日）'"""
    return f"{value}（{_WEEKDAY_LABELS[date.fromisoformat(value).weekday()]}）"


def age_on(birth_date: date, on_date: date) -> int:
    before_birthday = (on_date.month, on_date.day) < (birth_date.month, birth_date.day)
    return on_date.year - birth_date.year - before_birthday


def json_dump(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, default=str)


def read_json(path: Path) -> dict[str, object]:
    with path.open(encoding="utf-8") as file:
        value = cast(object, json.load(file))
    if not isinstance(value, dict):
        raise ValueError("入力JSONはオブジェクトである必要があります")
    return cast(dict[str, object], value)


def require_str(record: Mapping[str, object], key: str) -> str:
    value = record.get(key)
    if not isinstance(value, str):
        raise ValueError(f"{key} は文字列である必要があります")
    return value


def require_int(record: Mapping[str, object], key: str) -> int:
    value = record.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{key} は整数である必要があります")
    return value


def require_number(record: Mapping[str, object], key: str) -> float:
    value = record.get(key)
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"{key} は数値である必要があります")
    return float(value)


def optional_number(record: Mapping[str, object], key: str) -> float | None:
    value = record.get(key)
    if value is None:
        return None
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"{key} は数値またはnullである必要があります")
    return float(value)
