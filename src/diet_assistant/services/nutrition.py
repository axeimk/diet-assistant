"""栄養素の目安を「日本人の食事摂取基準」から導出する。

たんぱく質は体重（g/kg）ではなくエネルギー比（%E）を採る。減量目的では体重基準の目安が
目標カロリーで満たせない量になり、栄養素を満たすために食べ足す動機を生むため（ADR 0014）。

表には**原典で確認できた値だけ**を持ち、確認できていない性別・年齢区分では目安を返さない。
推測値で比較対象を作ると、根拠のない不足・過剰の指摘が出るため（ADR 0007・0014）。
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from typing import Literal, TypedDict

from ..util import age_on

DRI_2020 = "食事摂取基準2020年版"
DRI_2025 = "食事摂取基準2025年版"

_ENERGY_PER_GRAM = {"protein": 4.0, "fat": 9.0, "carbohydrates": 4.0}

# エネルギー産生栄養素バランスの目標量（%E）。2020年版の報告書本体で確認した。
# 男女で同じ値。2025年版はこの章にアルコールの記述が加わったのみとされる。
_ENERGY_PERCENT: tuple[tuple[str, int, int, float, float], ...] = (
    ("protein", 18, 49, 13.0, 20.0),
    ("protein", 50, 64, 14.0, 20.0),
    ("protein", 65, 999, 15.0, 20.0),
    ("fat", 18, 999, 20.0, 30.0),
    ("carbohydrates", 18, 999, 50.0, 65.0),
)

# 食物繊維の目標量（g/日以上）。2025年版で21gから引き上げられた区分だけを確認できている。
_FIBER_MINIMUM: tuple[tuple[str, int, int, float, str], ...] = (
    ("male", 18, 29, 20.0, DRI_2025),
    ("male", 30, 64, 22.0, DRI_2025),
)

# 食塩相当量の目標量（g/日未満）。
_SODIUM_MAXIMUM: tuple[tuple[str, int, int, float, str], ...] = (
    ("male", 18, 999, 7.5, DRI_2025),
    ("female", 18, 999, 6.5, DRI_2025),
)

_SEX_LABELS = {"male": "男性", "female": "女性"}


class NutrientTarget(TypedDict):
    minimum: float | None
    maximum: float | None
    unit: str
    basis: str


class NutrientComparison(TypedDict):
    actual: float
    minimum: float | None
    maximum: float | None
    unit: str
    basis: str
    status: Literal["below", "within", "above"]
    difference: float


def compare_nutrients(
    totals: Mapping[str, float], targets: Mapping[str, NutrientTarget]
) -> dict[str, NutrientComparison]:
    """摂取量を目安と突き合わせる。目安が無い栄養素は結果に入れない。

    差は範囲の外に出た分だけを返す（下回れば負、上回れば正、範囲内は0.0）。
    """
    comparisons: dict[str, NutrientComparison] = {}
    for name, target in targets.items():
        actual = totals.get(name)
        if actual is None:
            continue
        minimum = target["minimum"]
        maximum = target["maximum"]
        if minimum is not None and actual < minimum:
            status: Literal["below", "within", "above"] = "below"
            difference = round(actual - minimum, 1)
        elif maximum is not None and actual > maximum:
            status = "above"
            difference = round(actual - maximum, 1)
        else:
            status = "within"
            difference = 0.0
        comparisons[name] = {
            "actual": actual,
            "minimum": minimum,
            "maximum": maximum,
            "unit": target["unit"],
            "basis": target["basis"],
            "status": status,
            "difference": difference,
        }
    return comparisons


def nutrient_targets(
    profile: dict[str, object], *, target_calories: float | None, on_date: date
) -> dict[str, NutrientTarget]:
    """プロフィールと目標カロリーから栄養素の目安を導出する。

    目安が確認できない栄養素はキーを作らない。目標カロリーが無い場合も、
    食物繊維と食塩相当量は絶対量なので返す。
    """
    sex = profile.get("sex")
    birth_value = profile.get("birth_date")
    if sex not in _SEX_LABELS or not isinstance(birth_value, str):
        return {}
    age = age_on(date.fromisoformat(birth_value), on_date)
    if age < 18:
        return {}

    targets: dict[str, NutrientTarget] = {}
    if target_calories is not None:
        for name, start, end, percent_min, percent_max in _ENERGY_PERCENT:
            if not start <= age <= end:
                continue
            per_gram = _ENERGY_PER_GRAM[name]
            targets[name] = {
                "minimum": round(target_calories * percent_min / 100 / per_gram, 1),
                "maximum": round(target_calories * percent_max / 100 / per_gram, 1),
                "unit": "g",
                "basis": f"{DRI_2020} 目標量 {_percent(percent_min)}〜{_percent(percent_max)}%E"
                + f"（{_band_label(start, end)}）",
            }
    fiber = _lookup(_FIBER_MINIMUM, str(sex), age)
    if fiber is not None:
        value, edition, start, end = fiber
        targets["fiber"] = {
            "minimum": value,
            "maximum": None,
            "unit": "g",
            "basis": f"{edition} 目標量 {_number(value)} g/日以上"
            + f"（{_band_label(start, end)}・{_SEX_LABELS[str(sex)]}）",
        }
    sodium = _lookup(_SODIUM_MAXIMUM, str(sex), age)
    if sodium is not None:
        value, edition, start, end = sodium
        targets["sodium"] = {
            "minimum": None,
            "maximum": value,
            "unit": "g",
            "basis": f"{edition} 目標量 {_number(value)} g/日未満"
            + f"（{_band_label(start, end)}・{_SEX_LABELS[str(sex)]}）",
        }
    return targets


def _lookup(
    table: tuple[tuple[str, int, int, float, str], ...], sex: str, age: int
) -> tuple[float, str, int, int] | None:
    for entry_sex, start, end, value, edition in table:
        if entry_sex == sex and start <= age <= end:
            return value, edition, start, end
    return None


def _band_label(start: int, end: int) -> str:
    return f"{start}歳以上" if end >= 999 else f"{start}〜{end}歳"


def _percent(value: float) -> str:
    return _number(value)


def _number(value: float) -> str:
    return str(int(value)) if value == int(value) else str(value)
