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
    context = cast(dict[str, object], meal_output["day_context"])
    assert context["date"] == "2026-07-22"

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


def test_meal_and_daily_report_include_goal_based_numbers(
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
    context = cast(dict[str, object], meal_output["day_context"])
    assert context["remaining_meals"] == 2
    assert context["remaining_calories"] is not None
    assert "priority_action" not in context, "CLIは食後の文面を作らない（ADR 0013）"

    assert main([*root_args, "report", "daily", "--date", today, "--format", "json"]) == 0
    report_output = cast(dict[str, object], cast(object, json.loads(capsys.readouterr().out)))
    goal_evaluation = cast(dict[str, object], report_output["goal_evaluation"])
    report_findings = cast(list[dict[str, object]], report_output["findings"])
    assert report_output["feedback"] is None, "フィードバックはエージェントが書くまで空"
    assert [f for f in report_findings if f["group"] == "calorie"]
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
    # 12週の数値表と対象週の日別表の2つ。日別表は対象週の7日分を並べる。
    assert html.count("<tbody>") == 2
    daily_table = html.split("対象週の日別記録", 1)[1]
    assert daily_table.count("<tr>") == 8
    for day, weekday in zip(range(15, 22), "水木金土日月火", strict=True):
        assert f'<th scope="row">2026-07-{day}（{weekday}）</th>' in daily_table


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


def _setup_goal_with_profile(
    tmp_path: Path, capsys: CaptureFixture[str], *, start_weight: str = "91"
) -> dict[str, object]:
    """プロフィールと有効な目標を用意し、goal addの出力を返す。"""
    root_args = ["--root", str(tmp_path)]
    config_dir = tmp_path / "config"
    config_dir.mkdir(exist_ok=True)
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
    target_date = (date.today() + timedelta(days=90)).isoformat()
    assert (
        main(
            [
                *root_args,
                "goal",
                "add",
                "--start-weight",
                start_weight,
                "--target-weight",
                "85",
                "--target-date",
                target_date,
                "--activate",
            ]
        )
        == 0
    )
    return cast(dict[str, object], cast(object, json.loads(capsys.readouterr().out)))


def test_daily_report_compares_nutrients_with_targets(
    tmp_path: Path, capsys: CaptureFixture[str]
) -> None:
    """摂取量だけでなく目安と差を出す。多いか少ないかをレポート内で判断できるようにする。"""
    root_args = ["--root", str(tmp_path)]
    goal_output = _setup_goal_with_profile(tmp_path, capsys)
    plan = cast(dict[str, object], goal_output["plan"])
    targets = cast(dict[str, dict[str, object]], plan["nutrient_targets"])
    protein_minimum = cast(float, targets["protein"]["minimum"])
    protein_maximum = cast(float, targets["protein"]["maximum"])

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
                "--protein",
                "20",
                "--fiber",
                "4",
                "--sodium",
                "10.9",
            ]
        )
        == 0
    )
    _ = capsys.readouterr()

    assert main([*root_args, "report", "daily", "--date", today, "--format", "json"]) == 0
    summary = cast(dict[str, object], cast(object, json.loads(capsys.readouterr().out)))
    nutrients = cast(dict[str, dict[str, object]], summary["nutrients"])
    assert nutrients["protein"]["status"] == "below"
    assert nutrients["sodium"]["status"] == "above"
    assert nutrients["sodium"]["difference"] == 3.4
    assert "2025年版" in cast(str, nutrients["sodium"]["basis"])

    assert main([*root_args, "report", "daily", "--date", today]) == 0
    _ = capsys.readouterr()
    report = (tmp_path / f"reports/daily/{today}.md").read_text(encoding="utf-8")
    assert f"- たんぱく質: 20.0 g（目安 {protein_minimum}〜{protein_maximum} g / " in report
    assert "- 食物繊維: 4.0 g（目安 22 g以上 / −18）" in report
    assert "- 食塩相当量: 10.9 g（目安 7.5 g未満 / +3.4）" in report
    assert "- 脂質: 記録なし" in report, "未記録の栄養素をゼロとして不足扱いにしない（ADR 0007）"
    assert "fat" not in nutrients

    assert (
        main([*root_args, "report", "daily", "--date", today, "--format", "html", "--no-open"]) == 0
    )
    _ = capsys.readouterr()
    html = (tmp_path / f"reports/daily/{today}.html").read_text(encoding="utf-8")
    assert "目安 7.5 g未満 / +3.4" in html
    assert "<dt>脂質</dt>" in html
    assert "フィードバックは未記載" in html
    assert "助言" not in html


def test_daily_report_says_target_is_unset_without_profile(
    tmp_path: Path, capsys: CaptureFixture[str]
) -> None:
    """目安が無い場合は差を捏造せず、目安未設定と書く。"""
    root_args = ["--root", str(tmp_path)]
    assert main([*root_args, "init"]) == 0
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
                "--protein",
                "20",
            ]
        )
        == 0
    )
    _ = capsys.readouterr()

    assert main([*root_args, "report", "daily", "--date", today]) == 0
    _ = capsys.readouterr()
    report = (tmp_path / f"reports/daily/{today}.md").read_text(encoding="utf-8")
    assert "- たんぱく質: 20.0 g（目安未設定）" in report


def test_feedback_returns_findings_and_report_embeds_the_saved_text(
    tmp_path: Path, capsys: CaptureFixture[str]
) -> None:
    """diet feedback はfindingsを返し、文面はエージェントが書いてsaveし、reportが埋め込む。"""
    root_args = ["--root", str(tmp_path)]
    _ = _setup_goal_with_profile(tmp_path, capsys)
    today = date.today()
    for offset in range(7):
        day = (today - timedelta(days=offset)).isoformat()
        assert (
            main(
                [
                    *root_args,
                    "meal",
                    "add",
                    "--type",
                    "dinner",
                    "--at",
                    f"{day}T19:00:00+09:00",
                    "--calories",
                    "2300",
                    "--sodium",
                    "11.0",
                ]
            )
            == 0
        )
    _ = capsys.readouterr()

    assert main([*root_args, "feedback", "weekly", "--date", today.isoformat()]) == 0
    feedback_output = cast(dict[str, object], cast(object, json.loads(capsys.readouterr().out)))
    result_findings = cast(list[dict[str, object]], feedback_output["findings"])

    assert feedback_output["saved_feedback"] is None
    assert "situation" not in feedback_output, "CLIは文面を返さない（ADR 0013）"
    kinds = [f["kind"] for f in result_findings]
    assert "calorie_average_above_target" in kinds
    assert "sodium_above_target" in kinds
    assert result_findings[0]["group"] == "calorie", "カロリーが栄養素より先に来る"

    feedback_file = tmp_path / "feedback.json"
    _ = feedback_file.write_text(
        json.dumps(
            {
                "situation": "平均が目標上限を超えている",
                "priority_action": "夕食の主菜を1品減らす",
                "keep": "記録が7日続いている",
                "alternative": "主食を減らす",
                "plan_change": "変更なし",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    assert (
        main(
            [
                *root_args,
                "feedback",
                "save",
                "--json",
                str(feedback_file),
                "--kind",
                "period",
                "--days",
                "7",
                "--date",
                today.isoformat(),
            ]
        )
        == 0
    )
    saved = cast(dict[str, object], cast(object, json.loads(capsys.readouterr().out)))
    assert cast(list[object], saved["evidence"]), "根拠はCLIが計算したfindings"

    assert main([*root_args, "report", "weekly", "--date", today.isoformat()]) == 0
    _ = capsys.readouterr()
    report = (tmp_path / f"reports/weekly/{today.isoformat()}.md").read_text(encoding="utf-8")
    assert "- 夕食の主菜を1品減らす" in report
    assert "- 記録が7日続いている" in report
    assert "[指摘] 平均摂取カロリー（目標上限超過）" in report
    assert "フィードバックは未記載" not in report
    assert "助言" not in report


def test_weekly_report_says_feedback_is_unwritten_before_it_is_saved(
    tmp_path: Path, capsys: CaptureFixture[str]
) -> None:
    root_args = ["--root", str(tmp_path)]
    assert main([*root_args, "init"]) == 0
    _ = capsys.readouterr()

    assert main([*root_args, "report", "weekly", "--date", "2026-07-21"]) == 0
    _ = capsys.readouterr()
    report = (tmp_path / "reports/weekly/2026-07-21.md").read_text(encoding="utf-8")

    assert "フィードバックは未記載" in report
    assert "助言" not in report
    assert "## 分析結果" in report


def test_feedback_save_rejects_wrong_keys(tmp_path: Path, capsys: CaptureFixture[str]) -> None:
    root_args = ["--root", str(tmp_path)]
    assert main([*root_args, "init"]) == 0
    _ = capsys.readouterr()
    feedback_file = tmp_path / "feedback.json"
    _ = feedback_file.write_text(
        json.dumps({"situation": "状況", "priority": "最優先"}, ensure_ascii=False),
        encoding="utf-8",
    )

    assert main([*root_args, "feedback", "save", "--json", str(feedback_file)]) == 2
    assert "priority" in json.loads(capsys.readouterr().err)["error"]


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


def test_goal_delete_hides_goal_but_keeps_report_basis(
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
                "--target-date",
                target_date,
                "--activate",
            ]
        )
        == 0
    )
    goal_output = cast(dict[str, object], cast(object, json.loads(capsys.readouterr().out)))
    goal_id = require_int(cast(dict[str, object], goal_output["goal"]), "id")

    assert main([*root_args, "goal", "delete", str(goal_id), "--yes"]) == 0
    deleted = cast(dict[str, object], cast(object, json.loads(capsys.readouterr().out)))
    assert deleted["deleted"] == goal_id
    assert deleted["deleted_at"] is not None

    assert main([*root_args, "goal", "list"]) == 0
    listed = cast(list[dict[str, object]], cast(object, json.loads(capsys.readouterr().out)))
    assert listed == [], "削除した目標は一覧に出さない"

    assert main([*root_args, "goal", "show", str(goal_id)]) == 2
    _ = capsys.readouterr()

    with connect(tmp_path / "data/diet.db") as connection:
        goal_row = cast(
            tuple[str, str],
            connection.execute(
                "SELECT status, deleted_at FROM goals WHERE id = ?", (goal_id,)
            ).fetchone(),
        )
        plan_count = cast(
            tuple[int],
            connection.execute(
                "SELECT COUNT(*) FROM plans WHERE goal_id = ?", (goal_id,)
            ).fetchone(),
        )[0]
    assert goal_row[0] == "inactive"
    assert goal_row[1] is not None
    assert plan_count == 1, "レポートの根拠になるplanは物理削除しない"

    assert main([*root_args, "report", "daily", "--format", "json"]) == 0
    report = cast(dict[str, object], cast(object, json.loads(capsys.readouterr().out)))
    assert report["target_daily_calories"] is not None, (
        "削除した目標のplanでも、その日のレポートの根拠として引ける"
    )
