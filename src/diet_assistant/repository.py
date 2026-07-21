from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from pathlib import Path
from typing import cast

from .db import connect, transaction
from .util import now_iso


class NotFoundError(ValueError):
    pass


def insert(path: Path, table: str, data: dict[str, object]) -> int:
    columns = ", ".join(data)
    placeholders = ", ".join("?" for _ in data)
    with transaction(path) as connection:
        cursor = connection.execute(
            f"INSERT INTO {table} ({columns}) VALUES ({placeholders})", tuple(data.values())
        )
        if cursor.lastrowid is None:
            raise sqlite3.DatabaseError(f"{table} のIDを取得できませんでした")
        return cursor.lastrowid


def get(path: Path, table: str, record_id: int) -> dict[str, object]:
    with connect(path) as connection:
        row = cast(
            sqlite3.Row | None,
            connection.execute(f"SELECT * FROM {table} WHERE id = ?", (record_id,)).fetchone(),
        )
    if row is None:
        raise NotFoundError(f"{table} の id={record_id} は見つかりません")
    return cast(dict[str, object], dict(row))


def list_rows(
    path: Path,
    table: str,
    *,
    order_by: str = "id DESC",
    where: str = "1=1",
    params: Iterable[object] = (),
    limit: int = 100,
) -> list[dict[str, object]]:
    with connect(path) as connection:
        rows = cast(
            list[sqlite3.Row],
            connection.execute(
                f"SELECT * FROM {table} WHERE {where} ORDER BY {order_by} LIMIT ?",
                (*params, limit),
            ).fetchall(),
        )
    return [cast(dict[str, object], dict(row)) for row in rows]


def update(path: Path, table: str, record_id: int, data: dict[str, object]) -> dict[str, object]:
    if not data:
        return get(path, table, record_id)
    assignments = ", ".join(f"{column} = ?" for column in data)
    with transaction(path) as connection:
        cursor = connection.execute(
            f"UPDATE {table} SET {assignments} WHERE id = ?", (*data.values(), record_id)
        )
        if cursor.rowcount == 0:
            raise NotFoundError(f"{table} の id={record_id} は見つかりません")
    return get(path, table, record_id)


def delete(path: Path, table: str, record_id: int) -> None:
    with transaction(path) as connection:
        cursor = connection.execute(f"DELETE FROM {table} WHERE id = ?", (record_id,))
        if cursor.rowcount == 0:
            raise NotFoundError(f"{table} の id={record_id} は見つかりません")


def add_meal(path: Path, data: dict[str, object]) -> dict[str, object]:
    timestamp = now_iso()
    items = data.pop("items", [])
    if not isinstance(items, list):
        raise ValueError("items は配列である必要があります")
    item_list = cast(list[object], items)
    values = {
        "eaten_at": data.pop("eaten_at", timestamp),
        "meal_type": data.pop("meal_type", data.pop("type", "other")),
        "note": data.pop("note", data.pop("text", None)),
        "photo_path": data.pop("photo_path", None),
        "estimated_calories": data.pop("estimated_calories", None),
        "calories_min": data.pop("calories_min", None),
        "calories_max": data.pop("calories_max", None),
        "protein": data.pop("protein", None),
        "fat": data.pop("fat", None),
        "carbohydrates": data.pop("carbohydrates", None),
        "fiber": data.pop("fiber", None),
        "estimation_confidence": data.pop("estimation_confidence", None),
        "source": data.pop("source", "manual"),
        "created_at": timestamp,
        "updated_at": timestamp,
    }
    if data:
        raise ValueError(f"未対応の食事フィールド: {', '.join(sorted(data))}")
    with transaction(path) as connection:
        columns = ", ".join(values)
        cursor = connection.execute(
            f"INSERT INTO meals ({columns}) VALUES ({', '.join('?' for _ in values)})",
            tuple(values.values()),
        )
        if cursor.lastrowid is None:
            raise sqlite3.DatabaseError("meals のIDを取得できませんでした")
        meal_id = cursor.lastrowid
        allowed = {
            "name",
            "amount_text",
            "estimated_grams",
            "estimated_calories",
            "calories_min",
            "calories_max",
            "protein",
            "fat",
            "carbohydrates",
            "confidence",
            "note",
        }
        for raw_item in item_list:
            if not isinstance(raw_item, dict):
                raise ValueError("各品目はオブジェクトである必要があります")
            item = cast(dict[str, object], raw_item)
            unknown = set(item) - allowed
            if unknown:
                raise ValueError(f"未対応の品目フィールド: {', '.join(sorted(unknown))}")
            item_values = {"meal_id": meal_id, **item}
            item_columns = ", ".join(item_values)
            _ = connection.execute(
                f"INSERT INTO meal_items ({item_columns}) VALUES "
                + f"({', '.join('?' for _ in item_values)})",
                tuple(item_values.values()),
            )
    return get_meal(path, meal_id)


def get_meal(path: Path, meal_id: int) -> dict[str, object]:
    meal = get(path, "meals", meal_id)
    with connect(path) as connection:
        rows = cast(
            list[sqlite3.Row],
            connection.execute(
                "SELECT * FROM meal_items WHERE meal_id = ? ORDER BY id", (meal_id,)
            ).fetchall(),
        )
    meal["items"] = [cast(dict[str, object], dict(row)) for row in rows]
    return meal


def activate_goal(path: Path, goal_id: int) -> dict[str, object]:
    _ = get(path, "goals", goal_id)
    with transaction(path) as connection:
        _ = connection.execute("UPDATE goals SET status = 'inactive' WHERE status = 'active'")
        _ = connection.execute("UPDATE goals SET status = 'active' WHERE id = ?", (goal_id,))
    return get(path, "goals", goal_id)
