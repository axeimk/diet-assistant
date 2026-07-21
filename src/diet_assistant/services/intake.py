from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
from pathlib import Path
from typing import cast

from ..db import connect, transaction
from ..repository import add_meal, insert
from ..util import now_iso, read_json, require_int, require_str


def file_digest(*paths: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(paths):
        digest.update(path.read_bytes())
    return digest.hexdigest()


def import_file(db_path: Path, json_path: Path, temporary_dir: Path) -> dict[str, object]:
    payload = read_json(json_path)
    stem = json_path.stem
    images = [
        candidate
        for candidate in json_path.parent.glob(f"{stem}.*")
        if candidate.suffix.lower() in {".jpg", ".jpeg", ".png", ".heic"}
    ]
    external_id = str(payload.get("external_id") or file_digest(json_path, *images))
    with connect(db_path) as connection:
        existing = cast(
            sqlite3.Row | None,
            connection.execute(
                "SELECT * FROM intake_entries WHERE external_id = ?", (external_id,)
            ).fetchone(),
        )
    if existing:
        return {"duplicate": True, "entry": cast(dict[str, object], dict(existing))}
    entry_id = insert(
        db_path,
        "intake_entries",
        {
            "external_id": external_id,
            "occurred_at": payload.get("occurred_at") or payload.get("captured_at") or now_iso(),
            "type": payload.get("type", "meal"),
            "source": payload.get("source", "inbox"),
            "raw_text": payload.get("text") or payload.get("note"),
            "raw_json": json.dumps(payload, ensure_ascii=False),
            "image_paths": json.dumps([str(path) for path in images], ensure_ascii=False),
            "status": "pending",
            "error_message": None,
            "created_at": now_iso(),
            "processed_at": None,
            "result_type": None,
            "result_id": None,
        },
    )
    return process_entry(
        db_path, entry_id, json_path=json_path, images=images, temporary_dir=temporary_dir
    )


def process_entry(
    db_path: Path,
    entry_id: int,
    *,
    json_path: Path | None = None,
    images: list[Path] | None = None,
    temporary_dir: Path,
) -> dict[str, object]:
    with connect(db_path) as connection:
        row = cast(
            sqlite3.Row | None,
            connection.execute("SELECT * FROM intake_entries WHERE id = ?", (entry_id,)).fetchone(),
        )
    if not row:
        raise ValueError(f"intake_entries の id={entry_id} は見つかりません")
    entry = cast(dict[str, object], dict(row))
    payload_value = cast(object, json.loads(require_str(entry, "raw_json")))
    if not isinstance(payload_value, dict):
        raise ValueError("raw_json はオブジェクトである必要があります")
    payload = cast(dict[str, object], payload_value)
    image_values = cast(object, json.loads(require_str(entry, "image_paths")))
    if not isinstance(image_values, list):
        raise ValueError("image_paths は文字列の配列である必要があります")
    raw_image_values = cast(list[object], image_values)
    if not all(isinstance(item, str) for item in raw_image_values):
        raise ValueError("image_paths は文字列の配列である必要があります")
    stored_images = [Path(item) for item in cast(list[str], raw_image_values)]
    images = images if images is not None else stored_images
    with transaction(db_path) as connection:
        _ = connection.execute(
            "UPDATE intake_entries SET status='processing', error_message=NULL WHERE id=?",
            (entry_id,),
        )
    try:
        if entry["type"] != "meal":
            raise ValueError("MVPのinbox自動変換は meal のみ対応しています")
        temporary_dir.mkdir(parents=True, exist_ok=True)
        moved_images: list[Path] = []
        for image in images:
            if image.exists():
                destination = temporary_dir / image.name
                _ = shutil.move(str(image), destination)
                moved_images.append(destination)
        meal_payload: dict[str, object] = {
            "eaten_at": require_str(entry, "occurred_at"),
            "meal_type": payload.get("meal_type", "other"),
            "note": payload.get("text") or payload.get("note"),
            "photo_path": str(moved_images[0]) if moved_images else None,
            "source": require_str(entry, "source"),
        }
        for key in (
            "estimated_calories",
            "calories_min",
            "calories_max",
            "protein",
            "fat",
            "carbohydrates",
            "fiber",
            "estimation_confidence",
            "items",
        ):
            if key in payload:
                meal_payload[key] = payload[key]
        meal = add_meal(db_path, meal_payload)
        if json_path and json_path.exists():
            _ = json_path.unlink()
        with transaction(db_path) as connection:
            _ = connection.execute(
                "UPDATE intake_entries SET status='completed', processed_at=?, "
                + "result_type='meal', result_id=?, image_paths=? WHERE id=?",
                (
                    now_iso(),
                    require_int(meal, "id"),
                    json.dumps([str(p) for p in moved_images]),
                    entry_id,
                ),
            )
        return {"duplicate": False, "entry_id": entry_id, "meal": meal}
    except Exception as exc:
        with transaction(db_path) as connection:
            _ = connection.execute(
                "UPDATE intake_entries SET status='failed', error_message=? WHERE id=?",
                (str(exc), entry_id),
            )
        raise


def import_directory(db_path: Path, inbox: Path, temporary_dir: Path) -> list[dict[str, object]]:
    inbox.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, object]] = []
    for json_path in sorted(inbox.glob("*.json")):
        try:
            results.append(import_file(db_path, json_path, temporary_dir))
        except (ValueError, json.JSONDecodeError, sqlite3.Error) as exc:
            results.append({"file": str(json_path), "error": str(exc)})
    return results
