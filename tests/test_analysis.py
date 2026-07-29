"""findingsの計算と順位付けのテスト。

カロリー管理が最優先で、栄養素の不足がカロリー超過を押しのけないこと、
そして余裕がないときに増量方向のフィードバック材料を出さないこと（ADR 0014）を
不変条件として固定する。
"""

from datetime import date, timedelta
from pathlib import Path

from diet_assistant.repository import add_meal, insert
from diet_assistant.services.analysis import findings
from diet_assistant.services.finding import Finding
from diet_assistant.services.planning import save_plan
from diet_assistant.util import now_iso

PROFILE: dict[str, object] = {
    "height_cm": 175,
    "birth_date": "1991-03-04",
    "sex": "male",
    "activity_level": "sedentary",
}
END_DAY = date(2026, 7, 25)


def _goal(path: Path, *, start_weight: float = 91.0, target_weight: float = 85.0) -> int:
    goal_id = insert(
        path,
        "goals",
        {
            "started_at": "2026-07-01",
            "target_date": "2026-10-23",
            "start_weight": start_weight,
            "target_weight": target_weight,
            "target_type": "weight_loss",
            "status": "active",
            "note": None,
            "created_at": now_iso(),
        },
    )
    _ = save_plan(path, goal_id, profile=PROFILE, today=date(2026, 7, 19))
    return goal_id


def _meal(
    path: Path,
    day: int,
    *,
    meal_type: str = "dinner",
    calories: float = 600.0,
    protein: float | None = None,
    fiber: float | None = None,
    sodium: float | None = None,
    hour: int = 19,
) -> None:
    _ = add_meal(
        path,
        {
            "eaten_at": f"2026-07-{day:02d}T{hour:02d}:00:00+09:00",
            "meal_type": meal_type,
            "estimated_calories": calories,
            "protein": protein,
            "fiber": fiber,
            "sodium": sodium,
        },
    )


def _kinds(result: list[Finding]) -> list[str]:
    return [finding["kind"] for finding in result]


def _by_kind(result: list[Finding], kind: str) -> Finding:
    matches = [finding for finding in result if finding["kind"] == kind]
    assert matches, f"{kind} のfindingがない: {_kinds(result)}"
    return matches[0]


def test_insufficient_records_is_reported_before_anything_else(db_path: Path) -> None:
    _ = _goal(db_path)
    _meal(db_path, 25, calories=2500)

    result = findings(db_path, END_DAY, 7, profile=PROFILE)

    assert result[0]["kind"] == "meal_records_missing"
    assert result[0]["resolution"] == "record"
    assert result[0]["sample_days"] == 1


def test_calorie_excess_is_measured_against_the_target_range(db_path: Path) -> None:
    _ = _goal(db_path)
    for day in range(19, 26):
        _meal(db_path, day, calories=2200)

    result = findings(db_path, END_DAY, 7, profile=PROFILE)
    finding = _by_kind(result, "calorie_average_above_target")

    assert finding["group"] == "calorie"
    assert finding["severity"] == "attention"
    assert finding["actual"] == 2200
    assert finding["reference"] is not None
    assert finding["resolution"] == "reduce"
    assert finding["sample_days"] == 7


def test_meal_type_skew_reports_the_actual_share(db_path: Path) -> None:
    """夕食偏重のような実際の配分を、固定文ではなく数値で出す。"""
    _ = _goal(db_path)
    for day in range(19, 26):
        _meal(db_path, day, meal_type="breakfast", calories=200, hour=8)
        _meal(db_path, day, meal_type="dinner", calories=900)

    result = findings(db_path, END_DAY, 7, profile=PROFILE)
    finding = _by_kind(result, "meal_type_calorie_skew")

    assert finding["detail"]["meal_type"] == "dinner"
    assert finding["actual"] == 900
    assert finding["detail"]["share"] == 0.82


def test_snack_finding_only_exists_when_snacks_are_recorded(db_path: Path) -> None:
    """間食に言及する材料は、間食の記録があるときだけ生まれる。"""
    _ = _goal(db_path)
    for day in range(19, 26):
        _meal(db_path, day, meal_type="dinner", calories=1600)

    result = findings(db_path, END_DAY, 7, profile=PROFILE)

    assert not [f for f in result if f["detail"].get("meal_type") == "snack"]


def test_nutrient_shortfall_uses_substitution_when_there_is_no_calorie_room(
    db_path: Path,
) -> None:
    """カロリーに余裕がないとき、栄養素不足は置き換えでしか解消させない。"""
    _ = _goal(db_path)
    for day in range(19, 26):
        _meal(db_path, day, calories=2200, protein=30, fiber=5)

    result = findings(db_path, END_DAY, 7, profile=PROFILE)
    protein = _by_kind(result, "protein_below_target")

    assert protein["calorie_headroom"] is not None and protein["calorie_headroom"] < 0
    assert protein["resolution"] == "substitute"
    assert not [f for f in result if f["resolution"] == "increase"], (
        "カロリー超過中に増量方向の材料を出さない"
    )


def test_calorie_findings_outrank_nutrition_findings(db_path: Path) -> None:
    _ = _goal(db_path)
    for day in range(19, 26):
        _meal(db_path, day, calories=2200, protein=30, fiber=5, sodium=11.0)

    result = findings(db_path, END_DAY, 7, profile=PROFILE)
    attention = [f for f in result if f["severity"] == "attention"]

    assert attention[0]["group"] == "calorie"
    assert "nutrition" in [f["group"] for f in attention], "栄養素の指摘自体は残す"


def test_small_calorie_headroom_is_not_room_to_eat_more(db_path: Path) -> None:
    """目標レンジの幅（±100 kcal）に収まる程度の余裕では増量を許さない。"""
    _ = _goal(db_path)
    for day in range(19, 26):
        _meal(db_path, day, calories=1720, protein=30, fiber=5)

    result = findings(db_path, END_DAY, 7, profile=PROFILE)
    fiber = _by_kind(result, "fiber_below_target")

    headroom = fiber["calorie_headroom"]
    assert headroom is not None and 0 < headroom < 100
    assert fiber["resolution"] == "substitute"
    assert not [f for f in result if f["resolution"] == "increase"]


def test_nutrient_shortfall_may_increase_when_calories_are_low(db_path: Path) -> None:
    """摂取が目標を下回っているときだけ、増量方向を許す。"""
    _ = _goal(db_path)
    for day in range(19, 26):
        _meal(db_path, day, calories=1200, protein=30, fiber=5)

    result = findings(db_path, END_DAY, 7, profile=PROFILE)
    protein = _by_kind(result, "protein_below_target")

    assert protein["calorie_headroom"] is not None and protein["calorie_headroom"] > 0
    assert protein["resolution"] == "increase"


def test_sodium_excess_is_reduced_not_substituted(db_path: Path) -> None:
    _ = _goal(db_path)
    for day in range(19, 26):
        _meal(db_path, day, calories=1600, sodium=11.0)

    result = findings(db_path, END_DAY, 7, profile=PROFILE)
    sodium = _by_kind(result, "sodium_above_target")

    assert sodium["resolution"] == "reduce"
    assert sodium["reference"] == 7.5
    assert sodium["reference_basis"] is not None


def test_unrecorded_nutrients_produce_no_finding(db_path: Path) -> None:
    """未記録の栄養素をゼロとみなして不足を指摘しない（ADR 0007）。"""
    _ = _goal(db_path)
    for day in range(19, 26):
        _meal(db_path, day, calories=1600)

    result = findings(db_path, END_DAY, 7, profile=PROFILE)

    assert "protein_below_target" not in _kinds(result)
    assert "fiber_below_target" not in _kinds(result)


def test_every_finding_with_a_reference_carries_its_basis(db_path: Path) -> None:
    _ = _goal(db_path)
    for day in range(19, 26):
        _meal(db_path, day, calories=2200, protein=30, fiber=5, sodium=11.0)
        _ = insert(
            db_path,
            "body_metrics",
            {
                "measured_at": f"2026-07-{day:02d}T07:00:00+09:00",
                "weight": 90.0 - (day - 19) * 0.1,
                "body_fat_percentage": None,
                "waist": None,
                "note": None,
                "created_at": now_iso(),
            },
        )

    result = findings(db_path, END_DAY, 7, profile=PROFILE)

    for finding in result:
        if finding["reference"] is not None:
            assert finding["reference_basis"], f"{finding['kind']} に参照根拠がない"


def test_stale_plan_basis_weight_is_reported(db_path: Path) -> None:
    """計画の前提体重が現在体重から離れたら、維持カロリーの前提が古いと知らせる。"""
    _ = _goal(db_path)
    for day in range(19, 26):
        _meal(db_path, day, calories=1600)
    _ = insert(
        db_path,
        "body_metrics",
        {
            "measured_at": "2026-07-25T07:00:00+09:00",
            "weight": 88.1,
            "body_fat_percentage": None,
            "waist": None,
            "note": None,
            "created_at": now_iso(),
        },
    )

    result = findings(db_path, END_DAY, 7, profile=PROFILE)
    stale = _by_kind(result, "plan_basis_weight_stale")

    assert stale["group"] == "goal"
    assert stale["actual"] == 88.1
    assert stale["reference"] == 91.0
    assert stale["detail"]["suggested_command"] == "diet goal recalculate"


def _weight(path: Path, day: date, weight: float) -> None:
    _ = insert(
        path,
        "body_metrics",
        {
            "measured_at": f"{day.isoformat()}T07:00:00+09:00",
            "weight": weight,
            "body_fat_percentage": None,
            "waist": None,
            "note": None,
            "created_at": now_iso(),
        },
    )


def test_pace_behind_target_is_reported_against_the_plan(db_path: Path) -> None:
    """目標ペースより遅ければ指摘する。前週と当週の体重平均から週変化を出す。"""
    _ = _goal(db_path)
    for day in range(19, 26):
        _meal(db_path, day, calories=1600)
    for offset in range(7):
        _weight(db_path, date(2026, 7, 12) + timedelta(days=offset), 89.0)
        _weight(db_path, date(2026, 7, 19) + timedelta(days=offset), 88.95)

    result = findings(db_path, END_DAY, 7, profile=PROFILE)
    pace = _by_kind(result, "goal_pace_behind")

    assert pace["group"] == "goal"
    assert pace["severity"] == "attention"
    assert pace["actual"] == -0.05
    assert pace["reference"] is not None and pace["reference"] < 0
    assert pace["reference_basis"] == "当初目標ペース"


def test_daily_finding_uses_seven_day_actual_pace_not_previous_day_change(
    db_path: Path,
) -> None:
    """日次分析でも体重ペースは7日平均同士を比べ、前日差を7倍しない。"""
    _ = _goal(db_path)
    for day in range(19, 26):
        _meal(db_path, day, calories=1600)
    for offset in range(7):
        _weight(db_path, date(2026, 7, 12) + timedelta(days=offset), 89.0)
        _weight(db_path, date(2026, 7, 19) + timedelta(days=offset), 88.95)

    result = findings(db_path, END_DAY, 1, profile=PROFILE)
    pace = _by_kind(result, "goal_pace_behind")

    assert pace["actual"] == -0.05
    assert pace["reference_basis"] == "当初目標ペース"
    assert pace["period_days"] == 7
    assert pace["sample_days"] == 7
    assert pace["detail"]["previous_weight_measurements"] == 7


def test_pace_on_track_is_info_not_a_complaint(db_path: Path) -> None:
    _ = _goal(db_path)
    for day in range(19, 26):
        _meal(db_path, day, calories=1600)
    for offset in range(7):
        _weight(db_path, date(2026, 7, 12) + timedelta(days=offset), 89.0)
        _weight(db_path, date(2026, 7, 19) + timedelta(days=offset), 88.2)

    result = findings(db_path, END_DAY, 7, profile=PROFILE)
    pace = _by_kind(result, "goal_pace_on_track")

    assert pace["severity"] == "info"


def test_pace_is_not_judged_without_previous_period_weights(db_path: Path) -> None:
    _ = _goal(db_path)
    for day in range(19, 26):
        _meal(db_path, day, calories=1600)
        _weight(db_path, date(2026, 7, day), 88.5)

    result = findings(db_path, END_DAY, 7, profile=PROFILE)

    assert "goal_pace_behind" not in _kinds(result)
    assert "goal_pace_on_track" not in _kinds(result)


def test_nutrient_targets_come_from_profile_without_an_active_plan(db_path: Path) -> None:
    """目標が無くても、食塩相当量のような絶対量の目安は使える。"""
    for day in range(19, 26):
        _meal(db_path, day, calories=1600, sodium=11.0)

    result = findings(db_path, END_DAY, 7, profile=PROFILE)
    sodium = _by_kind(result, "sodium_above_target")

    assert sodium["reference"] == 7.5
    assert sodium["calorie_headroom"] is None
    assert sodium["resolution"] == "reduce"


def test_findings_are_json_serializable(db_path: Path) -> None:
    import json

    _ = _goal(db_path)
    for day in range(19, 26):
        _meal(db_path, day, calories=2200, protein=30, sodium=11.0)

    result = findings(db_path, END_DAY, 7, profile=PROFILE)

    assert json.loads(json.dumps(result, ensure_ascii=False)) == result
