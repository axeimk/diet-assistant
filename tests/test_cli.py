import json
from datetime import date, timedelta
from pathlib import Path
from typing import cast

from pytest import CaptureFixture

from diet_assistant.cli import main
from diet_assistant.db import connect
from diet_assistant.util import require_int


def test_cli_happy_path(tmp_path: Path, capsys: CaptureFixture[str]) -> None:
    root_args = ["--root", str(tmp_path)]
    assert main([*root_args, "init"]) == 0
    assert main([*root_args, "meal", "add", "--type", "dinner", "--text", "鮭おにぎり2個"]) == 0
    assert main([*root_args, "exercise", "add", "--type", "walking", "--minutes", "30"]) == 0
    assert main([*root_args, "metric", "add", "--weight", "70"]) == 0
    assert main([*root_args, "report", "daily", "--format", "json"]) == 0
    output = capsys.readouterr().out
    assert '"meal_type": "dinner"' in output
    with connect(tmp_path / "data/diet.db") as connection:
        rows = cast(
            list[tuple[str]],
            connection.execute("SELECT type FROM intake_entries").fetchall(),
        )
        intake_types = {row[0] for row in rows}
    assert intake_types == {"meal", "exercise", "metric"}


def test_cli_error_code(tmp_path: Path, capsys: CaptureFixture[str]) -> None:
    code = main(["--root", str(tmp_path), "meal", "list"])
    assert code == 2
    assert "diet init" in json.loads(capsys.readouterr().err)["error"]


def test_meal_and_daily_report_include_goal_based_advice(
    tmp_path: Path, capsys: CaptureFixture[str]
) -> None:
    root_args = ["--root", str(tmp_path)]
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    _ = (config_dir / "profile.json").write_text(
        json.dumps(
            {
                "height_cm": 175,
                "birth_date": "1991-03-04",
                "sex": "male",
                "activity_level": "sedentary",
                "meals_per_day": 3,
            }
        ),
        encoding="utf-8",
    )
    assert main([*root_args, "init"]) == 0
    _ = capsys.readouterr()
    target_date = (date.today() + timedelta(days=31)).isoformat()
    assert (
        main(
            [
                *root_args,
                "goal",
                "add",
                "--start-weight",
                "91",
                "--target-weight",
                "87",
                "--success-threshold-weight",
                "88",
                "--evaluation-window-days",
                "7",
                "--target-date",
                target_date,
                "--activate",
            ]
        )
        == 0
    )
    goal_output = cast(dict[str, object], cast(object, json.loads(capsys.readouterr().out)))
    plan = cast(dict[str, object], goal_output["plan"])
    energy = cast(dict[str, object], plan["energy"])
    assert energy["estimated_maintenance_calories"] == 2200

    today = date.today().isoformat()
    assert (
        main(
            [
                *root_args,
                "meal",
                "add",
                "--type",
                "lunch",
                "--at",
                f"{today}T12:00:00+09:00",
                "--calories",
                "600",
            ]
        )
        == 0
    )
    meal_output = cast(dict[str, object], cast(object, json.loads(capsys.readouterr().out)))
    meal_advice = cast(dict[str, object], meal_output["advice_after_meal"])
    meal_evidence = cast(dict[str, object], meal_advice["evidence"])
    assert meal_evidence["remaining_meals"] == 2
    assert meal_evidence["remaining_calories"] is not None

    assert main([*root_args, "report", "daily", "--date", today, "--format", "json"]) == 0
    report_output = cast(dict[str, object], cast(object, json.loads(capsys.readouterr().out)))
    daily_advice = cast(dict[str, object], report_output["advice"])
    daily_evidence = cast(dict[str, object], daily_advice["evidence"])
    goal_evaluation = cast(dict[str, object], report_output["goal_evaluation"])
    assert daily_evidence["consumed_calories"] == 600
    assert goal_evaluation["evaluation_window_days"] == 7


def test_metric_crud(tmp_path: Path, capsys: CaptureFixture[str]) -> None:
    root_args = ["--root", str(tmp_path)]
    assert main([*root_args, "init"]) == 0
    _ = capsys.readouterr()
    assert main([*root_args, "metric", "add", "--weight", "70"]) == 0
    metric_output = cast(dict[str, object], cast(object, json.loads(capsys.readouterr().out)))
    metric_id = require_int(metric_output, "id")
    update_file = tmp_path / "metric-update.json"
    _ = update_file.write_text('{"weight": 69.5}', encoding="utf-8")
    assert main([*root_args, "metric", "update", str(metric_id), "--json", str(update_file)]) == 0
    assert json.loads(capsys.readouterr().out)["weight"] == 69.5
    assert main([*root_args, "metric", "delete", str(metric_id), "--yes"]) == 0
