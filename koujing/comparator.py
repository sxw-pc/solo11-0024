"""Deterministic pairwise comparison of calculated profiles."""

from __future__ import annotations

import math
from typing import Any

from .models import (
    CalculationResult,
    ComparisonResult,
    MatchedGroup,
    SideGroup,
    normalize_number,
)


class ComparisonError(Exception):
    def __init__(self, message: str, code: str = "comparison_error"):
        self.message = message
        self.code = code
        super().__init__(message)

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}


def _tolerance(name: str, value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ComparisonError(f"{name} 必须是非负数", "invalid_tolerance")
    if not math.isfinite(float(value)) or value < 0:
        raise ComparisonError(f"{name} 必须是非负有限数", "invalid_tolerance")
    return float(value)


def _sort_key(key: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(str(part) for part in key)


def compare_calculations(
    base: CalculationResult,
    other: CalculationResult,
    tolerance_abs: float = 0.0,
    tolerance_rel: float = 0.0,
) -> ComparisonResult:
    """Align two results by dimension key and classify each group.

    A group is within tolerance when either the absolute difference or the
    absolute relative difference is within its configured threshold.  If the
    base value is zero and the difference is non-zero, the relative rate is
    reported as ``None`` with ``base_zero=True`` instead of infinity.
    """
    if base.dimensions != other.dimensions:
        raise ComparisonError(
            "两个口径的 dimensions 必须完全一致: "
            f"{list(base.dimensions)} != {list(other.dimensions)}",
            "dimension_mismatch",
        )
    abs_tolerance = _tolerance("tolerance_abs", tolerance_abs)
    rel_tolerance = _tolerance("tolerance_rel", tolerance_rel)
    base_map = base.group_map()
    other_map = other.group_map()
    common = sorted(set(base_map) & set(other_map), key=_sort_key)
    only_base = sorted(set(base_map) - set(other_map), key=_sort_key)
    only_other = sorted(set(other_map) - set(base_map), key=_sort_key)

    matched: list[MatchedGroup] = []
    for key in common:
        base_value = base_map[key]
        compare_value = other_map[key]
        diff = normalize_number(compare_value - base_value)
        base_zero = base_value == 0
        if base_zero:
            rate = 0.0 if diff == 0 else None
        else:
            rate = float(normalize_number(diff / abs(base_value)))
        absolute_ok = abs(diff) <= abs_tolerance
        relative_ok = rate is not None and abs(rate) <= rel_tolerance
        matched.append(
            MatchedGroup(
                key=key,
                base_value=base_value,
                compare_value=compare_value,
                diff=diff,
                diff_rate=rate,
                base_zero=base_zero,
                within_tolerance=absolute_ok or relative_ok,
            )
        )
    return ComparisonResult(
        base_profile=base.profile_id,
        compare_profile=other.profile_id,
        dimensions=base.dimensions,
        tolerance_abs=abs_tolerance,
        tolerance_rel=rel_tolerance,
        matched=tuple(matched),
        only_base=tuple(SideGroup(key=key, value=base_map[key]) for key in only_base),
        only_compare=tuple(SideGroup(key=key, value=other_map[key]) for key in only_other),
    )
