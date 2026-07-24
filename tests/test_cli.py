import json
from datetime import date, timedelta
from pathlib import Path
from typing import cast

from pytest import CaptureFixture, MonkeyPatch

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


def test_daily_report_assigns_early_morning_meal_to_previous_day(
    tmp_path: Path, capsys: CaptureFixture[str]
) -> None:
    root_args = ["--root", str(tmp_path)]
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    _ = (config_dir / "profile.json").write_text(
        json.dumps({"day_start_time": "04:00"}), encoding="utf-8"
    )
    assert main([*root_args, "init"]) == 0
    _ = capsys.readouterr()
    assert (
        main(
            [
                *root_args,
                "meal",
                "add",
                "--type",
                "snack",
                "--at",
                "2026-07-23T00:08:00+09:00",
                "--calories",
                "46",
            ]
        )
        == 0
    )
    meal_output = cast(dict[str, object], cast(object, json.loads(capsys.readouterr().out)))
    meal_advice = cast(dict[str, object], meal_output["advice_after_meal"])
    evidence = cast(dict[str, object], meal_advice["evidence"])
    assert evidence["date"] == "2026-07-22"

    assert (
        main(
            [*root_args, "report", "daily", "--date", "2026-07-22", "--format", "json"]
        )
        == 0
    )
    previous_day = cast(dict[str, object], cast(object, json.loads(capsys.readouterr().out)))
    previous_meals = cast(list[dict[str, object]], previous_day["meals"])
    assert [meal["meal_type"] for meal in previous_meals] == ["snack"]

    assert (
        main(
            [*root_args, "report", "daily", "--date", "2026-07-23", "--format", "json"]
        )
        == 0
    )
    current_day = cast(dict[str, object], cast(object, json.loads(capsys.readouterr().out)))
    assert current_day["meals"] == []


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


def test_daily_report_generates_self_contained_html(
    tmp_path: Path, capsys: CaptureFixture[str]
) -> None:
    root_args = ["--root", str(tmp_path)]
    assert main([*root_args, "init"]) == 0
    _ = capsys.readouterr()
    assert (
        main(
            [
                *root_args,
                "meal",
                "add",
                "--type",
                "lunch",
                "--text",
                "<script>alert('xss')</script>",
                "--at",
                "2026-07-21T12:00:00+09:00",
                "--calories",
                "650",
                "--calories-min",
                "550",
                "--calories-max",
                "750",
            ]
        )
        == 0
    )
    _ = capsys.readouterr()

    assert (
        main(
            [
                *root_args,
                "report",
                "daily",
                "--date",
                "2026-07-21",
                "--format",
                "html",
                "--no-open",
            ]
        )
        == 0
    )
    output = cast(dict[str, object], cast(object, json.loads(capsys.readouterr().out)))
    report_path = Path(cast(str, output["path"]))
    html = report_path.read_text(encoding="utf-8")

    assert report_path == tmp_path / "reports/daily/2026-07-21.html"
    assert "<!doctype html>" in html.lower()
    assert "日次レポート" in html
    assert 'id="calorie-chart"' in html
    assert 'id="weight-chart"' in html
    assert 'id="exercise-chart"' in html
    assert "<script>alert('xss')</script>" not in html
    assert "&lt;script&gt;alert" in html
    assert 'src="http' not in html


def test_weekly_report_generates_html_with_daily_table(
    tmp_path: Path, capsys: CaptureFixture[str]
) -> None:
    root_args = ["--root", str(tmp_path)]
    assert main([*root_args, "init"]) == 0
    _ = capsys.readouterr()

    assert (
        main(
            [
                *root_args,
                "report",
                "weekly",
                "--date",
                "2026-07-21",
                "--format",
                "html",
                "--no-open",
            ]
        )
        == 0
    )
    output = cast(dict[str, object], cast(object, json.loads(capsys.readouterr().out)))
    report_path = Path(cast(str, output["path"]))
    html = report_path.read_text(encoding="utf-8")

    assert report_path == tmp_path / "reports/weekly/2026-07-21.html"
    assert "直近12週間の推移" in html
    assert "対象週の日別記録" in html
    assert html.count("<tbody>") == 1
    assert html.count("<tr>") == 8


def test_html_stdout_does_not_save_or_open_browser(
    tmp_path: Path,
    capsys: CaptureFixture[str],
    monkeypatch: MonkeyPatch,
) -> None:
    root_args = ["--root", str(tmp_path)]
    assert main([*root_args, "init"]) == 0
    _ = capsys.readouterr()
    opened_urls: list[str] = []

    def record_open(url: str) -> bool:
        opened_urls.append(url)
        return True

    monkeypatch.setattr("diet_assistant.cli.webbrowser.open", record_open)

    assert (
        main(
            [
                *root_args,
                "report",
                "daily",
                "--date",
                "2026-07-21",
                "--format",
                "html",
                "--stdout",
            ]
        )
        == 0
    )
    output = cast(dict[str, object], cast(object, json.loads(capsys.readouterr().out)))

    assert "<!doctype html>" in cast(str, output["html"]).lower()
    assert not (tmp_path / "reports/daily/2026-07-21.html").exists()
    assert opened_urls == []


def test_html_browser_failure_returns_warning(
    tmp_path: Path,
    capsys: CaptureFixture[str],
    monkeypatch: MonkeyPatch,
) -> None:
    root_args = ["--root", str(tmp_path)]
    assert main([*root_args, "init"]) == 0
    _ = capsys.readouterr()

    def fail_open(_url: str) -> bool:
        return False

    monkeypatch.setattr("diet_assistant.cli.webbrowser.open", fail_open)

    assert (
        main(
            [
                *root_args,
                "report",
                "daily",
                "--date",
                "2026-07-21",
                "--format",
                "html",
            ]
        )
        == 0
    )
    output = cast(dict[str, object], cast(object, json.loads(capsys.readouterr().out)))

    assert output["opened"] is False
    assert "warning" in output
    assert Path(cast(str, output["path"])).exists()


def test_markdown_reports_do_not_show_unrecorded_exercise_as_zero(
    tmp_path: Path, capsys: CaptureFixture[str]
) -> None:
    root_args = ["--root", str(tmp_path)]
    assert main([*root_args, "init"]) == 0
    _ = capsys.readouterr()

    assert main([*root_args, "report", "daily", "--date", "2026-07-21"]) == 0
    _ = capsys.readouterr()
    daily = (tmp_path / "reports/daily/2026-07-21.md").read_text(encoding="utf-8")
    assert "- 摂取カロリー: 記録なし" in daily
    assert "- 運動時間: 記録なし" in daily
    assert "- 体重: 記録なし" in daily

    assert main([*root_args, "report", "weekly", "--date", "2026-07-21"]) == 0
    _ = capsys.readouterr()
    weekly = (tmp_path / "reports/weekly/2026-07-21.md").read_text(encoding="utf-8")
    assert "- 平均摂取カロリー: 算出不可" in weekly
    assert "- 記録された運動時間: 記録なし" in weekly


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
