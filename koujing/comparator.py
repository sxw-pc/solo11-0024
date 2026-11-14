"""Deterministic pairwise comparison of calculated profiles."""

from __future__ import annotations

import math
from typing import Any, NamedTuple

from .models import (
    CalculationResult,
    ComparisonResult,
    DimensionAlignment,
    MatchedGroup,
    Number,
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


class _Projection(NamedTuple):
    source_groups: int
    recovered_groups: int
    unmapped_groups: int


def _project_groups(
    result: CalculationResult, common: tuple[str, ...]
) -> tuple[dict[tuple[str, ...], Number], _Projection]:
    """Re-aggregate one side's groups onto the shared dimensions.

    Groups are folded in sorted-key order so the merged sums are deterministic.
    A group whose key is too short to provide every common-dimension value is
    counted as unmapped instead of being silently dropped; a group whose key
    arity differs from the declared dimensions (e.g. a row missing a trailing
    dimension value) but that still covers every common dimension is counted
    as recovered and kept in the projection.
    """
    positions = tuple(result.dimensions.index(dimension) for dimension in common)
    needed = max(positions) + 1
    projected: dict[tuple[str, ...], Number] = {}
    recovered = 0
    unmapped = 0
    ordered = sorted(result.groups, key=lambda group: _sort_key(group.key))
    for group in ordered:
        key = tuple(str(part) for part in group.key)
        if len(key) < needed:
            unmapped += 1
            continue
        if len(key) != len(result.dimensions):
            recovered += 1
        projected_key = tuple(key[position] for position in positions)
        merged = projected.get(projected_key)
        projected[projected_key] = (
            group.value if merged is None else normalize_number(merged + group.value)
        )
    return projected, _Projection(
        source_groups=len(result.groups),
        recovered_groups=recovered,
        unmapped_groups=unmapped,
    )


def _match_groups(
    base_map: dict[tuple[str, ...], Number],
    other_map: dict[tuple[str, ...], Number],
    abs_tolerance: float,
    rel_tolerance: float,
) -> tuple[tuple[MatchedGroup, ...], tuple[SideGroup, ...], tuple[SideGroup, ...]]:
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
    return (
        tuple(matched),
        tuple(SideGroup(key=key, value=base_map[key]) for key in only_base),
        tuple(SideGroup(key=key, value=other_map[key]) for key in only_other),
    )


def compare_calculations(
    base: CalculationResult,
    other: CalculationResult,
    tolerance_abs: float = 0.0,
    tolerance_rel: float = 0.0,
    joint: bool = False,
) -> ComparisonResult:
    """Align two results by dimension key and classify each group.

    A group is within tolerance when either the absolute difference or the
    absolute relative difference is within its configured threshold.  If the
    base value is zero and the difference is non-zero, the relative rate is
    reported as ``None`` with ``base_zero=True`` instead of infinity.

    By default both sides must declare identical dimension lists, otherwise a
    ``dimension_mismatch`` error is raised.  With ``joint=True`` the comparison
    instead falls back to the shared dimensions: both sides are re-aggregated
    onto the common dimensions (order taken from the base side) and matched
    there, while the group-count effects of that re-aggregation — collapsed,
    recovered and unmapped groups — are preserved in
    ``ComparisonResult.alignment``.  Dimension lists with no intersection are
    still rejected with ``dimension_mismatch`` even in joint mode.
    """
    if base.dimensions != other.dimensions and not joint:
        raise ComparisonError(
            "两个口径的 dimensions 必须完全一致: "
            f"{list(base.dimensions)} != {list(other.dimensions)}",
            "dimension_mismatch",
        )
    abs_tolerance = _tolerance("tolerance_abs", tolerance_abs)
    rel_tolerance = _tolerance("tolerance_rel", tolerance_rel)

    if base.dimensions == other.dimensions:
        alignment = None
        if joint:
            alignment = DimensionAlignment(
                mode="exact",
                common_dimensions=base.dimensions,
                base_source_groups=len(base.groups),
                compare_source_groups=len(other.groups),
            )
        matched, only_base, only_compare = _match_groups(
            base.group_map(), other.group_map(), abs_tolerance, rel_tolerance
        )
        return ComparisonResult(
            base_profile=base.profile_id,
            compare_profile=other.profile_id,
            dimensions=base.dimensions,
            tolerance_abs=abs_tolerance,
            tolerance_rel=rel_tolerance,
            matched=matched,
            only_base=only_base,
            only_compare=only_compare,
            alignment=alignment,
        )

    common = tuple(
        dimension for dimension in base.dimensions if dimension in other.dimensions
    )
    if not common:
        raise ComparisonError(
            "两个口径没有公共维度，无法联合比对: "
            f"{list(base.dimensions)} 与 {list(other.dimensions)}",
            "dimension_mismatch",
        )
    base_map, base_projection = _project_groups(base, common)
    other_map, other_projection = _project_groups(other, common)
    matched, only_base, only_compare = _match_groups(
        base_map, other_map, abs_tolerance, rel_tolerance
    )
    alignment = DimensionAlignment(
        mode="common",
        common_dimensions=common,
        base_extra_dimensions=tuple(
            dimension for dimension in base.dimensions if dimension not in common
        ),
        compare_extra_dimensions=tuple(
            dimension for dimension in other.dimensions if dimension not in common
        ),
        base_source_groups=base_projection.source_groups,
        compare_source_groups=other_projection.source_groups,
        base_collapsed_groups=(
            base_projection.source_groups - base_projection.unmapped_groups
        )
        - len(base_map),
        compare_collapsed_groups=(
            other_projection.source_groups - other_projection.unmapped_groups
        )
        - len(other_map),
        base_recovered_groups=base_projection.recovered_groups,
        compare_recovered_groups=other_projection.recovered_groups,
        base_unmapped_groups=base_projection.unmapped_groups,
        compare_unmapped_groups=other_projection.unmapped_groups,
    )
    return ComparisonResult(
        base_profile=base.profile_id,
        compare_profile=other.profile_id,
        dimensions=common,
        tolerance_abs=abs_tolerance,
        tolerance_rel=rel_tolerance,
        matched=matched,
        only_base=only_base,
        only_compare=only_compare,
        alignment=alignment,
    )
