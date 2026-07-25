"""分析結果（finding）の型。

計算する`analysis`と、表示する`reporting`の両方から参照するため、型だけを独立させている。
"""

from __future__ import annotations

from typing import Literal, TypedDict

Group = Literal["goal", "calorie", "nutrition"]
Resolution = Literal["reduce", "increase", "substitute", "record", "none"]


class Finding(TypedDict):
    group: Group
    kind: str
    severity: Literal["info", "attention"]
    actual: float
    reference: float | None
    reference_basis: str | None
    unit: str
    period_days: int
    sample_days: int
    calorie_headroom: float | None
    resolution: Resolution
    detail: dict[str, object]
