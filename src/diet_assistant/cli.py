from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import uuid
from datetime import date, datetime, time
from pathlib import Path

from .config import load_profile, profile_day_start_time, validate_profile
from .db import SCHEMA_VERSION, initialize, schema_version
from .repository import (
    NotFoundError,
    activate_goal,
    add_meal,
    delete,
    get,
    get_meal,
    insert,
    list_rows,
    update,
)
from .services.advice import generate_advice, generate_daily_advice, generate_meal_advice
from .services.intake import import_directory, process_entry
from .services.maintenance import cleanup_candidates, cleanup_photos, create_backup
from .services.planning import evaluate_active_goal, evaluate_goal, save_plan
from .services.reporting import daily_markdown, daily_summary, weekly_markdown, weekly_summary
from .util import (
    json_dump,
    now_iso,
    optional_number,
    parse_datetime,
    read_json,
    reporting_date,
    require_int,
    require_number,
    require_str,
)

ROOT = Path.cwd()


class CliArgs(argparse.Namespace):
    root: str = ""
    command: str = ""
    action: str | None = None
    at: str | None = None
    note: str | None = None
    type: str | None = None
    minutes: float | None = None
    distance: float | None = None
    sets: int | None = None
    repetitions: int | None = None
    weight: float | None = None
    intensity: str | None = None
    calories: float | None = None
    body_fat: float | None = None
    waist: float | None = None
    start_weight: float = 0
    target_weight: float = 0
    success_threshold_weight: float | None = None
    evaluation_window_days: int = 1
    target_date: str = ""
    started_at: str | None = None
    activate: bool = False
    id: int = 0
    json: Path | None = None
    text: str | None = None
    photo: str | None = None
    calories_min: float | None = None
    calories_max: float | None = None
    protein: float | None = None
    fat: float | None = None
    carbs: float | None = None
    fiber: float | None = None
    sodium: float | None = None
    confidence: str | None = None
    source: str = "manual"
    limit: int = 100
    yes: bool = False
    status: str | None = None
    date: str | None = None
    format: str = "markdown"
    stdout: bool = False
    days: int | None = None
    apply: bool = False


def paths(args: CliArgs) -> dict[str, Path]:
    root = Path(args.root).resolve()
    return {
        "root": root,
        "db": root / "data/diet.db",
        "profile": root / "config/profile.json",
        "inbox": root / "inbox",
        "temporary": root / "photos/temporary",
        "backup": root / "backups",
        "daily": root / "reports/daily",
        "weekly": root / "reports/weekly",
    }


def emit(value: object) -> None:
    print(json_dump(value))


def _add_common_record_args(parser: argparse.ArgumentParser, kind: str) -> None:
    _ = parser.add_argument("--at", help="ISO 8601日時")
    _ = parser.add_argument("--note")
    if kind == "exercise":
        _ = parser.add_argument("--type", required=True)
        _ = parser.add_argument("--minutes", type=float)
        _ = parser.add_argument("--distance", type=float)
        _ = parser.add_argument("--sets", type=int)
        _ = parser.add_argument("--repetitions", type=int)
        _ = parser.add_argument("--weight", type=float)
        _ = parser.add_argument("--intensity")
        _ = parser.add_argument("--calories", type=float)
    else:
        _ = parser.add_argument("--weight", type=float)
        _ = parser.add_argument("--body-fat", type=float)
        _ = parser.add_argument("--waist", type=float)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="diet", description="個人向けダイエット記録CLI")
    _ = parser.add_argument("--root", default=str(ROOT), help="リポジトリのルート")
    commands = parser.add_subparsers(dest="command", required=True)
    _ = commands.add_parser("init")
    _ = commands.add_parser("doctor")

    profile = commands.add_parser("profile").add_subparsers(dest="action", required=True)
    _ = profile.add_parser("show")
    _ = profile.add_parser("validate")

    goal = commands.add_parser("goal").add_subparsers(dest="action", required=True)
    goal_add = goal.add_parser("add")
    _ = goal_add.add_argument("--start-weight", type=float, required=True)
    _ = goal_add.add_argument("--target-weight", type=float, required=True)
    _ = goal_add.add_argument("--success-threshold-weight", type=float)
    _ = goal_add.add_argument("--evaluation-window-days", type=int, default=1)
    _ = goal_add.add_argument("--target-date", required=True)
    _ = goal_add.add_argument("--started-at")
    _ = goal_add.add_argument("--note")
    _ = goal_add.add_argument("--activate", action="store_true")
    _ = goal.add_parser("list")
    for action in ("show", "activate", "recalculate"):
        p = goal.add_parser(action)
        _ = p.add_argument("id", type=int)
    goal_evaluate = goal.add_parser("evaluate")
    _ = goal_evaluate.add_argument("id", type=int)
    _ = goal_evaluate.add_argument("--date")
    goal_update = goal.add_parser("update")
    _ = goal_update.add_argument("id", type=int)
    _ = goal_update.add_argument("--json", type=Path, required=True)
    goal_delete = goal.add_parser("delete")
    _ = goal_delete.add_argument("id", type=int)
    _ = goal_delete.add_argument("--yes", action="store_true")

    meal = commands.add_parser("meal").add_subparsers(dest="action", required=True)
    meal_add = meal.add_parser("add")
    _ = meal_add.add_argument("--json", type=Path)
    _ = meal_add.add_argument("--type", choices=["breakfast", "lunch", "dinner", "snack", "other"])
    _ = meal_add.add_argument("--text")
    _ = meal_add.add_argument("--at")
    _ = meal_add.add_argument("--photo")
    _ = meal_add.add_argument("--calories", type=float)
    _ = meal_add.add_argument("--calories-min", type=float)
    _ = meal_add.add_argument("--calories-max", type=float)
    _ = meal_add.add_argument("--protein", type=float)
    _ = meal_add.add_argument("--fat", type=float)
    _ = meal_add.add_argument("--carbs", type=float)
    _ = meal_add.add_argument("--fiber", type=float)
    _ = meal_add.add_argument("--sodium", type=float, help="食塩相当量（g）")
    _ = meal_add.add_argument("--confidence", choices=["low", "medium", "high"])
    _ = meal_add.add_argument("--source", default="manual")
    meal_list = meal.add_parser("list")
    _ = meal_list.add_argument("--limit", type=int, default=100)
    meal_show = meal.add_parser("show")
    _ = meal_show.add_argument("id", type=int)
    meal_update = meal.add_parser("update")
    _ = meal_update.add_argument("id", type=int)
    _ = meal_update.add_argument("--json", type=Path, required=True)
    meal_delete = meal.add_parser("delete")
    _ = meal_delete.add_argument("id", type=int)
    _ = meal_delete.add_argument("--yes", action="store_true")

    exercise = commands.add_parser("exercise").add_subparsers(dest="action", required=True)
    ex_add = exercise.add_parser("add")
    _add_common_record_args(ex_add, "exercise")
    ex_list = exercise.add_parser("list")
    _ = ex_list.add_argument("--limit", type=int, default=100)
    ex_show = exercise.add_parser("show")
    _ = ex_show.add_argument("id", type=int)
    ex_update = exercise.add_parser("update")
    _ = ex_update.add_argument("id", type=int)
    _ = ex_update.add_argument("--json", type=Path, required=True)
    ex_delete = exercise.add_parser("delete")
    _ = ex_delete.add_argument("id", type=int)
    _ = ex_delete.add_argument("--yes", action="store_true")

    metric = commands.add_parser("metric").add_subparsers(dest="action", required=True)
    metric_add = metric.add_parser("add")
    _add_common_record_args(metric_add, "metric")
    metric_list = metric.add_parser("list")
    _ = metric_list.add_argument("--limit", type=int, default=100)
    metric_show = metric.add_parser("show")
    _ = metric_show.add_argument("id", type=int)
    metric_update = metric.add_parser("update")
    _ = metric_update.add_argument("id", type=int)
    _ = metric_update.add_argument("--json", type=Path, required=True)
    metric_delete = metric.add_parser("delete")
    _ = metric_delete.add_argument("id", type=int)
    _ = metric_delete.add_argument("--yes", action="store_true")

    inbox = commands.add_parser("inbox").add_subparsers(dest="action", required=True)
    _ = inbox.add_parser("import")
    inbox_list = inbox.add_parser("list")
    _ = inbox_list.add_argument("--status")
    _ = inbox_list.add_argument("--limit", type=int, default=100)
    retry = inbox.add_parser("retry")
    _ = retry.add_argument("id", type=int)

    report = commands.add_parser("report").add_subparsers(dest="action", required=True)
    for action in ("daily", "weekly"):
        p = report.add_parser(action)
        _ = p.add_argument("--date")
        _ = p.add_argument("--format", choices=["markdown", "json"], default="markdown")
        _ = p.add_argument("--stdout", action="store_true")
    advice = commands.add_parser("advice").add_subparsers(dest="action", required=True)
    for action in ("today", "weekly"):
        p = advice.add_parser(action)
        _ = p.add_argument("--date")
    backup = commands.add_parser("backup").add_subparsers(dest="action", required=True)
    _ = backup.add_parser("create")
    _ = backup.add_parser("list")
    photo = commands.add_parser("photo").add_subparsers(dest="action", required=True)
    cleanup = photo.add_parser("cleanup")
    _ = cleanup.add_argument("--days", type=int)
    _ = cleanup.add_argument("--apply", action="store_true")
    return parser


def run(args: CliArgs) -> object:
    p = paths(args)
    command = args.command
    action = getattr(args, "action", None)
    if command == "init":
        applied = initialize(p["db"])
        for key in ("inbox", "temporary", "backup", "daily", "weekly"):
            p[key].mkdir(parents=True, exist_ok=True)
        return {
            "database": str(p["db"]),
            "schema_version": SCHEMA_VERSION,
            "migrations_applied": applied,
        }
    if command == "doctor":
        exists = p["db"].exists()
        version = schema_version(p["db"]) if exists else None
        checks: dict[str, object] = {
            "python": sys.version.split()[0],
            "database_exists": exists,
            "profile_exists": p["profile"].exists(),
        }
        if version is not None:
            checks["schema_version"] = version
        checks["ok"] = exists and version == SCHEMA_VERSION
        if exists and version != SCHEMA_VERSION:
            checks["hint"] = (
                f"スキーマが古いか未知です（期待 {SCHEMA_VERSION}）。"
                + "diet backup create のあと diet init で移行してください"
            )
        return checks
    if command == "profile":
        profile = load_profile(p["profile"])
        return (
            profile
            if action == "show"
            else {"valid": not (errors := validate_profile(profile)), "errors": errors}
        )
    _require_db(p["db"])
    if command == "goal":
        return _goal(args, p["db"], load_profile(p["profile"]))
    if command == "meal":
        return _meal(args, p["db"], load_profile(p["profile"]))
    if command == "exercise":
        return _exercise(args, p["db"])
    if command == "metric":
        return _metric(args, p["db"])
    if command == "inbox":
        if action == "import":
            return import_directory(p["db"], p["inbox"], p["temporary"])
        if action == "list":
            return list_rows(
                p["db"],
                "intake_entries",
                where="status = ?" if args.status else "1=1",
                params=(args.status,) if args.status else (),
                limit=args.limit,
            )
        return process_entry(p["db"], args.id, temporary_dir=p["temporary"])
    if command == "report":
        return _report(args, p)
    if command == "advice":
        profile = load_profile(p["profile"])
        day_start = profile_day_start_time(profile)
        day = _report_date(args.date, day_start)
        return (
            generate_daily_advice(p["db"], day, day_start=day_start)
            if action == "today"
            else generate_advice(p["db"], day, 7, day_start=day_start)
        )
    if command == "backup":
        if action == "create":
            return {"path": str(create_backup(p["db"], p["backup"]))}
        return [
            {"path": str(path), "size": path.stat().st_size}
            for path in sorted(p["backup"].glob("*.db"), reverse=True)
        ]
    if command == "photo":
        profile = load_profile(p["profile"])
        configured_days = profile.get("photo_retention_days", 30)
        if not isinstance(configured_days, int):
            raise ValueError("photo_retention_days は整数である必要があります")
        days = args.days or configured_days
        candidates = cleanup_candidates(p["temporary"], retention_days=days)
        count = cleanup_photos(candidates, apply=args.apply)
        return {"dry_run": not args.apply, "count": count, "candidates": candidates}
    raise ValueError("未対応のコマンドです")


def _validate_goal_threshold(
    start_weight: float, target_weight: float, threshold_weight: float | None
) -> None:
    if threshold_weight is None:
        return
    if target_weight < start_weight and not target_weight <= threshold_weight < start_weight:
        raise ValueError("減量の達成最低ラインは挑戦目標以上、開始体重未満にしてください")
    if target_weight > start_weight and not start_weight < threshold_weight <= target_weight:
        raise ValueError("増量の達成最低ラインは開始体重より大きく、挑戦目標以下にしてください")


def _goal(args: CliArgs, db: Path, profile: dict[str, object]) -> object:
    if args.action == "add":
        if date.fromisoformat(args.target_date) <= date.today():
            raise ValueError("目標日は今日より後にしてください")
        if not 1 <= args.evaluation_window_days <= 28:
            raise ValueError("評価期間は1〜28日にしてください")
        _validate_goal_threshold(
            args.start_weight, args.target_weight, args.success_threshold_weight
        )
        goal_id = insert(
            db,
            "goals",
            {
                "started_at": args.started_at or date.today().isoformat(),
                "target_date": args.target_date,
                "start_weight": args.start_weight,
                "target_weight": args.target_weight,
                "success_threshold_weight": args.success_threshold_weight,
                "evaluation_window_days": args.evaluation_window_days,
                "target_type": "weight_loss"
                if args.target_weight < args.start_weight
                else "weight_change",
                "status": "inactive",
                "note": args.note,
                "created_at": now_iso(),
            },
        )
        goal = activate_goal(db, goal_id) if args.activate else get(db, "goals", goal_id)
        return {"goal": goal, "plan": save_plan(db, goal_id, profile=profile)}
    if args.action == "list":
        return list_rows(db, "goals")
    if args.action == "show":
        goal = get(db, "goals", args.id)
        goal["plans"] = list_rows(db, "plans", where="goal_id = ?", params=(args.id,))
        return goal
    if args.action == "activate":
        return activate_goal(db, args.id)
    if args.action == "evaluate":
        return evaluate_goal(
            db, args.id, evaluation_date=date.fromisoformat(args.date) if args.date else None
        )
    if args.action == "update":
        if args.json is None:
            raise ValueError("--json が必要です")
        data = read_json(args.json)
        if (
            "target_date" in data
            and date.fromisoformat(require_str(data, "target_date")[:10]) <= date.today()
        ):
            raise ValueError("目標日は今日より後にしてください")
        current = get(db, "goals", args.id)
        candidate = {**current, **data}
        start_weight = require_number(candidate, "start_weight")
        target_weight = require_number(candidate, "target_weight")
        threshold_weight = optional_number(candidate, "success_threshold_weight")
        _validate_goal_threshold(start_weight, target_weight, threshold_weight)
        window_value = data.get("evaluation_window_days", current.get("evaluation_window_days", 1))
        if not isinstance(window_value, int) or not 1 <= window_value <= 28:
            raise ValueError("評価期間は1〜28日にしてください")
        return update(db, "goals", args.id, data)
    if args.action == "delete":
        if not args.yes:
            raise ValueError("削除には --yes が必要です")
        delete(db, "goals", args.id)
        return {"deleted": args.id}
    return save_plan(db, args.id, profile=profile)


def _meal(args: CliArgs, db: Path, profile: dict[str, object]) -> object:
    if args.action == "add":
        data: dict[str, object] = (
            read_json(args.json)
            if args.json
            else {
                "meal_type": args.type,
                "text": args.text,
                "eaten_at": parse_datetime(args.at).isoformat(),
                "photo_path": args.photo,
                "estimated_calories": args.calories,
                "calories_min": args.calories_min,
                "calories_max": args.calories_max,
                "protein": args.protein,
                "fat": args.fat,
                "carbohydrates": args.carbs,
                "fiber": args.fiber,
                "sodium": args.sodium,
                "estimation_confidence": args.confidence,
                "source": args.source,
            }
        )
        if not data.get("meal_type") and not data.get("type"):
            raise ValueError("--type またはJSONの meal_type が必要です")
        intake_payload = dict(data)
        meal = add_meal(db, data)
        _record_completed_intake(
            db,
            entry_type="meal",
            occurred_at=require_str(meal, "eaten_at"),
            source=require_str(meal, "source"),
            payload=intake_payload,
            result_id=require_int(meal, "id"),
        )
        meal["advice_after_meal"] = generate_meal_advice(db, meal, profile)
        return meal
    if args.action == "list":
        return list_rows(db, "meals", order_by="eaten_at DESC", limit=args.limit)
    if args.action == "show":
        return get_meal(db, args.id)
    if args.action == "update":
        if args.json is None:
            raise ValueError("--json が必要です")
        data = read_json(args.json)
        data["updated_at"] = now_iso()
        return update(db, "meals", args.id, data)
    if not args.yes:
        raise ValueError("削除には --yes が必要です")
    delete(db, "meals", args.id)
    return {"deleted": args.id}


def _exercise(args: CliArgs, db: Path) -> object:
    if args.action == "add":
        record_id = insert(
            db,
            "exercises",
            {
                "performed_at": parse_datetime(args.at).isoformat(),
                "exercise_type": args.type,
                "duration_minutes": args.minutes,
                "distance": args.distance,
                "sets": args.sets,
                "repetitions": args.repetitions,
                "weight": args.weight,
                "intensity": args.intensity,
                "estimated_calories_burned": args.calories,
                "note": args.note,
                "source": "manual",
                "created_at": now_iso(),
                "updated_at": now_iso(),
            },
        )
        exercise = get(db, "exercises", record_id)
        _record_completed_intake(
            db,
            entry_type="exercise",
            occurred_at=require_str(exercise, "performed_at"),
            source=require_str(exercise, "source"),
            payload=exercise,
            result_id=record_id,
        )
        return exercise
    if args.action == "list":
        return list_rows(db, "exercises", order_by="performed_at DESC", limit=args.limit)
    if args.action == "show":
        return get(db, "exercises", args.id)
    if args.action == "update":
        if args.json is None:
            raise ValueError("--json が必要です")
        data = read_json(args.json)
        data["updated_at"] = now_iso()
        return update(db, "exercises", args.id, data)
    if not args.yes:
        raise ValueError("削除には --yes が必要です")
    delete(db, "exercises", args.id)
    return {"deleted": args.id}


def _metric(args: CliArgs, db: Path) -> object:
    if args.action == "list":
        return list_rows(db, "body_metrics", order_by="measured_at DESC", limit=args.limit)
    if args.action == "show":
        return get(db, "body_metrics", args.id)
    if args.action == "update":
        if args.json is None:
            raise ValueError("--json が必要です")
        return update(db, "body_metrics", args.id, read_json(args.json))
    if args.action == "delete":
        if not args.yes:
            raise ValueError("削除には --yes が必要です")
        delete(db, "body_metrics", args.id)
        return {"deleted": args.id}
    record_id = insert(
        db,
        "body_metrics",
        {
            "measured_at": parse_datetime(args.at).isoformat(),
            "weight": args.weight,
            "body_fat_percentage": args.body_fat,
            "waist": args.waist,
            "note": args.note,
            "created_at": now_iso(),
        },
    )
    metric = get(db, "body_metrics", record_id)
    _record_completed_intake(
        db,
        entry_type="metric",
        occurred_at=require_str(metric, "measured_at"),
        source="manual",
        payload=metric,
        result_id=record_id,
    )
    return metric


def _report(args: CliArgs, p: dict[str, Path]) -> object:
    profile = load_profile(p["profile"])
    day_start = profile_day_start_time(profile)
    day = _report_date(args.date, day_start)
    if args.action == "daily":
        summary = daily_summary(p["db"], day, day_start=day_start)
        advice = generate_daily_advice(p["db"], day, day_start=day_start)
        goal_evaluation = evaluate_active_goal(
            p["db"], evaluation_date=day, day_start=day_start
        )
        if args.format == "json":
            return {**summary, "advice": advice, "goal_evaluation": goal_evaluation}
        content = daily_markdown(summary, advice, goal_evaluation)
        output = p["daily"] / f"{day}.md"
    else:
        summary = weekly_summary(p["db"], day, day_start=day_start)
        advice = generate_advice(p["db"], day, 7, day_start=day_start)
        if args.format == "json":
            return {"summary": summary, "advice": advice}
        content = weekly_markdown(summary, advice)
        output = p["weekly"] / f"{day}.md"
    if args.stdout:
        return {"markdown": content}
    output.parent.mkdir(parents=True, exist_ok=True)
    _ = output.write_text(content, encoding="utf-8")
    return {"path": str(output)}


def _report_date(value: str | None, day_start: time) -> date:
    if value:
        return date.fromisoformat(value)
    return reporting_date(datetime.now().astimezone(), starts_at=day_start)


def _require_db(path: Path) -> None:
    if not path.exists():
        raise ValueError("DBがありません。先に diet init を実行してください")


def _record_completed_intake(
    db: Path,
    *,
    entry_type: str,
    occurred_at: str,
    source: str,
    payload: dict[str, object],
    result_id: int,
) -> None:
    _ = insert(
        db,
        "intake_entries",
        {
            "external_id": f"cli:{uuid.uuid4()}",
            "occurred_at": occurred_at,
            "type": entry_type,
            "source": source,
            "raw_text": payload.get("text") or payload.get("note"),
            "raw_json": json.dumps(payload, ensure_ascii=False, default=str),
            "image_paths": json.dumps([payload["photo_path"]] if payload.get("photo_path") else []),
            "status": "completed",
            "error_message": None,
            "created_at": now_iso(),
            "processed_at": now_iso(),
            "result_type": entry_type,
            "result_id": result_id,
        },
    )


def main(argv: list[str] | None = None) -> int:
    try:
        args = build_parser().parse_args(argv, namespace=CliArgs())
        emit(run(args))
        return 0
    except (ValueError, NotFoundError, sqlite3.Error, json.JSONDecodeError) as exc:
        print(json_dump({"error": str(exc)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
