from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from ..util import require_str


def create_backup(source: Path, backup_dir: Path, *, now: datetime | None = None) -> Path:
    if not source.exists():
        raise ValueError("DBがありません。先に diet init を実行してください")
    now = now or datetime.now().astimezone()
    backup_dir.mkdir(parents=True, exist_ok=True)
    destination = backup_dir / f"diet-{now:%Y%m%d-%H%M%S}.db"
    with sqlite3.connect(source) as source_db, sqlite3.connect(destination) as destination_db:
        source_db.backup(destination_db)
    return destination


def cleanup_candidates(
    temporary_dir: Path, *, retention_days: int = 30, now: datetime | None = None
) -> list[dict[str, object]]:
    now = now or datetime.now().astimezone()
    cutoff = now - timedelta(days=retention_days)
    if not temporary_dir.exists():
        return []
    results: list[dict[str, object]] = []
    for path in sorted(temporary_dir.iterdir()):
        if not path.is_file():
            continue
        modified = datetime.fromtimestamp(path.stat().st_mtime, tz=now.tzinfo)
        if modified < cutoff:
            results.append({"path": str(path), "modified_at": modified.isoformat()})
    return results


def cleanup_photos(candidates: list[dict[str, object]], *, apply: bool) -> int:
    if apply:
        for candidate in candidates:
            Path(require_str(candidate, "path")).unlink(missing_ok=True)
    return len(candidates)
