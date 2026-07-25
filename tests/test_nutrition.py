from datetime import date

import pytest

from diet_assistant.services.nutrition import (
    NutrientTarget,
    compare_nutrients,
    nutrient_targets,
)

PROFILE_MALE_35: dict[str, object] = {
    "sex": "male",
    "birth_date": "1991-03-04",
}
ON_DATE = date(2026, 7, 25)


def test_protein_target_comes_from_energy_percentage() -> None:
    """たんぱく質は体重ではなく目標カロリーの13〜20%から導く（30〜49歳男性）。"""
    targets = nutrient_targets(PROFILE_MALE_35, target_calories=1650, on_date=ON_DATE)
    protein = targets["protein"]
    assert protein["minimum"] == pytest.approx(1650 * 0.13 / 4, abs=0.05)
    assert protein["maximum"] == pytest.approx(1650 * 0.20 / 4, abs=0.05)
    assert protein["unit"] == "g"
    assert "13〜20%E" in (protein["basis"] or "")


def test_protein_target_follows_target_calories() -> None:
    """目標カロリーを絞れば目安も下がる。体重基準ではないことの確認。"""
    low = nutrient_targets(PROFILE_MALE_35, target_calories=1400, on_date=ON_DATE)
    high = nutrient_targets(PROFILE_MALE_35, target_calories=2200, on_date=ON_DATE)
    low_max = low["protein"]["maximum"]
    high_max = high["protein"]["maximum"]
    assert low_max is not None and high_max is not None
    assert low_max < high_max


def test_protein_lower_bound_rises_from_fifty() -> None:
    """50〜64歳は目標量の下限が14%Eになる。"""
    older: dict[str, object] = {"sex": "male", "birth_date": "1970-01-01"}
    targets = nutrient_targets(older, target_calories=1650, on_date=ON_DATE)
    assert targets["protein"]["minimum"] == pytest.approx(1650 * 0.14 / 4, abs=0.05)


def test_fat_and_carbohydrate_targets_are_energy_ranges() -> None:
    targets = nutrient_targets(PROFILE_MALE_35, target_calories=1650, on_date=ON_DATE)
    assert targets["fat"]["minimum"] == pytest.approx(1650 * 0.20 / 9, abs=0.05)
    assert targets["fat"]["maximum"] == pytest.approx(1650 * 0.30 / 9, abs=0.05)
    assert targets["carbohydrates"]["minimum"] == pytest.approx(1650 * 0.50 / 4, abs=0.1)
    assert targets["carbohydrates"]["maximum"] == pytest.approx(1650 * 0.65 / 4, abs=0.1)


def test_fiber_target_is_a_lower_bound_by_age_band() -> None:
    """食物繊維は2025年版で30〜64歳男性が22g以上。上限は設けない。"""
    targets = nutrient_targets(PROFILE_MALE_35, target_calories=1650, on_date=ON_DATE)
    fiber = targets["fiber"]
    assert fiber["minimum"] == 22.0
    assert fiber["maximum"] is None
    assert "2025年版" in (fiber["basis"] or "")


def test_fiber_target_is_lower_for_younger_adults() -> None:
    younger: dict[str, object] = {"sex": "male", "birth_date": "2001-01-01"}
    targets = nutrient_targets(younger, target_calories=1650, on_date=ON_DATE)
    assert targets["fiber"]["minimum"] == 20.0


def test_sodium_target_is_an_upper_bound_in_salt_equivalent() -> None:
    """sodiumは食塩相当量（g）。男性は7.5g/日未満。"""
    targets = nutrient_targets(PROFILE_MALE_35, target_calories=1650, on_date=ON_DATE)
    sodium = targets["sodium"]
    assert sodium["minimum"] is None
    assert sodium["maximum"] == 7.5
    assert sodium["unit"] == "g"


def test_female_sodium_target_is_lower() -> None:
    female: dict[str, object] = {"sex": "female", "birth_date": "1991-03-04"}
    targets = nutrient_targets(female, target_calories=1650, on_date=ON_DATE)
    assert targets["sodium"]["maximum"] == 6.5


def test_unconfirmed_bands_get_no_target_instead_of_a_guess() -> None:
    """原典で確認できていない区分では目安を作らない。

    食物繊維は2025年版で区分ごとに改定されており、確認できたのは男性18〜64歳だけ。
    女性と65歳以上は値を持たず、比較対象なしとして扱う（推測値で不足を指摘しない）。
    """
    female: dict[str, object] = {"sex": "female", "birth_date": "1991-03-04"}
    older: dict[str, object] = {"sex": "male", "birth_date": "1950-01-01"}
    assert "fiber" not in nutrient_targets(female, target_calories=1650, on_date=ON_DATE)
    assert "fiber" not in nutrient_targets(older, target_calories=1650, on_date=ON_DATE)


def test_no_targets_without_sex_or_birth_date() -> None:
    """性別・生年月日が無ければ目安を作らない。推測で埋めない。"""
    assert nutrient_targets({}, target_calories=1650, on_date=ON_DATE) == {}
    assert (
        nutrient_targets(
            {"sex": "unspecified", "birth_date": "1991-03-04"},
            target_calories=1650,
            on_date=ON_DATE,
        )
        == {}
    )


def _range_target(minimum: float | None, maximum: float | None) -> NutrientTarget:
    return {"minimum": minimum, "maximum": maximum, "unit": "g", "basis": "テスト"}


def test_comparison_marks_shortfall_against_the_lower_bound() -> None:
    comparison = compare_nutrients(
        {"protein": 42.6}, {"protein": _range_target(53.6, 82.5)}
    )["protein"]
    assert comparison["status"] == "below"
    assert comparison["difference"] == pytest.approx(-11.0, abs=0.05)


def test_comparison_marks_excess_against_the_upper_bound() -> None:
    comparison = compare_nutrients({"sodium": 10.9}, {"sodium": _range_target(None, 7.5)})["sodium"]
    assert comparison["status"] == "above"
    assert comparison["difference"] == pytest.approx(3.4, abs=0.05)


def test_comparison_inside_the_range_has_no_difference() -> None:
    comparison = compare_nutrients(
        {"protein": 66.1}, {"protein": _range_target(53.6, 82.5)}
    )["protein"]
    assert comparison["status"] == "within"
    assert comparison["difference"] == 0.0


def test_comparison_skips_nutrients_without_a_target() -> None:
    """目安が無い栄養素は比較しない。表示は集計側で「目安未設定」にする。"""
    assert compare_nutrients({"protein": 52.0, "fiber": 10.5}, {}) == {}


def test_calorie_independent_targets_survive_without_target_calories() -> None:
    """目標カロリーが無くても、食物繊維と食塩相当量は絶対量なので出せる。"""
    targets = nutrient_targets(PROFILE_MALE_35, target_calories=None, on_date=ON_DATE)
    assert sorted(targets) == ["fiber", "sodium"]
    assert targets["fiber"]["minimum"] == 22.0
