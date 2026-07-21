import json
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
