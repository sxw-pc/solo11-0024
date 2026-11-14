"""Typed records shared by the calibration pipeline and its reporters."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Union

Number = Union[int, float]


def normalize_number(value: Number) -> Number:
    """Round computed values to a stable, JSON-friendly representation."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("a numeric value is required")
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("NaN and infinity are not supported")
        rounded = round(value, 6)
        if rounded.is_integer():
            return int(rounded)
        return rounded
    return value


def group_label(dimensions: Iterable[str], key: Iterable[str]) -> str:
    parts = [f"{name}={value}" for name, value in zip(dimensions, key)]
    return "; ".join(parts) if parts else "(总计)"


@dataclass(frozen=True)
class Profile:
    id: str
    name: str
    source: str
    grain: str
    filter: str
    metric: str
    dimensions: tuple[str, ...]
    exclusions: tuple[str, ...]
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "source": self.source,
            "grain": self.grain,
            "filter": self.filter,
            "metric": self.metric,
            "dimensions": list(self.dimensions),
            "exclusions": list(self.exclusions),
            "notes": self.notes,
        }


@dataclass(frozen=True)
class CalcStats:
    rows_read: int
    rows_filtered: int
    rows_excluded: int
    rows_aggregated: int
    groups: int
    unparsable_rows: int

    def to_dict(self) -> dict[str, int]:
        return {
            "rows_read": self.rows_read,
            "rows_filtered": self.rows_filtered,
            "rows_excluded": self.rows_excluded,
            "rows_aggregated": self.rows_aggregated,
            "groups": self.groups,
            "unparsable_rows": self.unparsable_rows,
        }


@dataclass(frozen=True)
class GroupValue:
    key: tuple[str, ...]
    value: Number


@dataclass(frozen=True)
class CalculationResult:
    profile_id: str
    profile_name: str
    grain: str
    dimensions: tuple[str, ...]
    stats: CalcStats
    groups: tuple[GroupValue, ...]

    def group_map(self) -> dict[tuple[str, ...], Number]:
        return {group.key: group.value for group in self.groups}

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile_id": self.profile_id,
            "profile_name": self.profile_name,
            "grain": self.grain,
            "dimensions": list(self.dimensions),
            "stats": self.stats.to_dict(),
            "groups": [
                {
                    "key": list(group.key),
                    "label": group_label(self.dimensions, group.key),
                    "value": group.value,
                }
                for group in self.groups
            ],
        }


@dataclass(frozen=True)
class MatchedGroup:
    key: tuple[str, ...]
    base_value: Number
    compare_value: Number
    diff: Number
    diff_rate: float | None
    base_zero: bool
    within_tolerance: bool

    @property
    def status(self) -> str:
        return "within_tolerance" if self.within_tolerance else "over_tolerance"


@dataclass(frozen=True)
class SideGroup:
    key: tuple[str, ...]
    value: Number


@dataclass(frozen=True)
class DimensionAlignment:
    """How two differing dimension lists were reconciled for a joint comparison.

    ``mode`` is ``"exact"`` when both sides declared identical dimensions and
    ``"common"`` when the comparison fell back to the shared dimensions.  The
    per-side counters keep the group-count effects of that fallback visible
    instead of silently dropping them:

    - ``*_collapsed_groups``: groups merged away by re-aggregation onto the
      common dimensions (the refinement signal, e.g. region+channel → region).
    - ``*_recovered_groups``: groups whose key arity differed from the declared
      dimensions (e.g. rows missing a dimension value) but that still provided
      every common-dimension value and were therefore kept.
    - ``*_unmapped_groups``: groups missing at least one common-dimension
      value; excluded from matching but still counted here.
    """

    mode: str
    common_dimensions: tuple[str, ...]
    base_extra_dimensions: tuple[str, ...] = ()
    compare_extra_dimensions: tuple[str, ...] = ()
    base_source_groups: int = 0
    compare_source_groups: int = 0
    base_collapsed_groups: int = 0
    compare_collapsed_groups: int = 0
    base_recovered_groups: int = 0
    compare_recovered_groups: int = 0
    base_unmapped_groups: int = 0
    compare_unmapped_groups: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "common_dimensions": list(self.common_dimensions),
            "base_extra_dimensions": list(self.base_extra_dimensions),
            "compare_extra_dimensions": list(self.compare_extra_dimensions),
            "base_source_groups": self.base_source_groups,
            "compare_source_groups": self.compare_source_groups,
            "base_collapsed_groups": self.base_collapsed_groups,
            "compare_collapsed_groups": self.compare_collapsed_groups,
            "base_recovered_groups": self.base_recovered_groups,
            "compare_recovered_groups": self.compare_recovered_groups,
            "base_unmapped_groups": self.base_unmapped_groups,
            "compare_unmapped_groups": self.compare_unmapped_groups,
        }


@dataclass(frozen=True)
class ComparisonResult:
    base_profile: str
    compare_profile: str
    dimensions: tuple[str, ...]
    tolerance_abs: float
    tolerance_rel: float
    matched: tuple[MatchedGroup, ...]
    only_base: tuple[SideGroup, ...]
    only_compare: tuple[SideGroup, ...]
    alignment: DimensionAlignment | None = None

    @property
    def within_count(self) -> int:
        return sum(1 for item in self.matched if item.within_tolerance)

    @property
    def over_count(self) -> int:
        return sum(1 for item in self.matched if not item.within_tolerance)

    def summary(self) -> dict[str, int]:
        summary = {
            "matched_groups": len(self.matched),
            "within_tolerance_groups": self.within_count,
            "over_tolerance_groups": self.over_count,
            "only_base_groups": len(self.only_base),
            "only_compare_groups": len(self.only_compare),
        }
        if self.alignment is not None:
            alignment = self.alignment
            summary.update(
                {
                    "base_source_groups": alignment.base_source_groups,
                    "compare_source_groups": alignment.compare_source_groups,
                    "base_collapsed_groups": alignment.base_collapsed_groups,
                    "compare_collapsed_groups": alignment.compare_collapsed_groups,
                    "base_recovered_groups": alignment.base_recovered_groups,
                    "compare_recovered_groups": alignment.compare_recovered_groups,
                    "base_unmapped_groups": alignment.base_unmapped_groups,
                    "compare_unmapped_groups": alignment.compare_unmapped_groups,
                }
            )
        return summary

    def to_dict(self) -> dict[str, Any]:
        matched = [
            {
                "key": list(item.key),
                "label": group_label(self.dimensions, item.key),
                "base_value": item.base_value,
                "compare_value": item.compare_value,
                "diff": item.diff,
                "diff_rate": item.diff_rate,
                "base_zero": item.base_zero,
                "within_tolerance": item.within_tolerance,
                "status": item.status,
            }
            for item in self.matched
        ]
        only_base = [
            {
                "key": list(item.key),
                "label": group_label(self.dimensions, item.key),
                "value": item.value,
            }
            for item in self.only_base
        ]
        only_compare = [
            {
                "key": list(item.key),
                "label": group_label(self.dimensions, item.key),
                "value": item.value,
            }
            for item in self.only_compare
        ]
        payload = {
            "base_profile": self.base_profile,
            "compare_profile": self.compare_profile,
            "dimensions": list(self.dimensions),
            "tolerance": {
                "absolute": self.tolerance_abs,
                "relative": self.tolerance_rel,
            },
            "summary": self.summary(),
            "matched": matched,
            "only_base": only_base,
            "only_compare": only_compare,
        }
        if self.alignment is not None:
            payload["alignment"] = self.alignment.to_dict()
        return payload


@dataclass(frozen=True)
class AttributionClue:
    clause: str
    base: str
    compare: str
    explanation: str
    affected_groups: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "clause": self.clause,
            "base": self.base,
            "compare": self.compare,
            "explanation": self.explanation,
            "affected_groups": list(self.affected_groups),
        }


@dataclass(frozen=True)
class Attribution:
    base_profile: str
    compare_profile: str
    group_key: tuple[str, ...]
    group_label: str
    clues: tuple[AttributionClue, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "base_profile": self.base_profile,
            "compare_profile": self.compare_profile,
            "group_key": list(self.group_key),
            "group_label": self.group_label,
            "clues": [clue.to_dict() for clue in self.clues],
        }


@dataclass(frozen=True)
class TaskResult:
    task_id: str
    base_profile: str
    generated_at: str | None
    profiles: tuple[Profile, ...]
    calculations: tuple[CalculationResult, ...]
    comparisons: tuple[ComparisonResult, ...]
    attributions: tuple[Attribution, ...]
    suggestions: tuple[str, ...]

    def summary(self) -> dict[str, Any]:
        summary: dict[str, Any] = {
            "profile_count": len(self.profiles),
            "comparison_count": len(self.comparisons),
            "matched_groups": sum(len(item.matched) for item in self.comparisons),
            "within_tolerance_groups": sum(item.within_count for item in self.comparisons),
            "over_tolerance_groups": sum(item.over_count for item in self.comparisons),
            "only_base_groups": sum(len(item.only_base) for item in self.comparisons),
            "only_compare_groups": sum(len(item.only_compare) for item in self.comparisons),
            "attribution_clues": sum(len(item.clues) for item in self.attributions),
            "status": "completed",
        }
        alignments = [
            item.alignment for item in self.comparisons if item.alignment is not None
        ]
        if alignments:
            summary["joint_comparisons"] = len(alignments)
            summary["collapsed_groups"] = sum(
                item.base_collapsed_groups + item.compare_collapsed_groups
                for item in alignments
            )
            summary["recovered_groups"] = sum(
                item.base_recovered_groups + item.compare_recovered_groups
                for item in alignments
            )
            summary["unmapped_groups"] = sum(
                item.base_unmapped_groups + item.compare_unmapped_groups
                for item in alignments
            )
        return summary

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "generated_at": self.generated_at,
            "base_profile": self.base_profile,
            "summary": self.summary(),
            "profiles": [profile.to_dict() for profile in self.profiles],
            "calculations": [item.to_dict() for item in self.calculations],
            "comparisons": [item.to_dict() for item in self.comparisons],
            "attributions": [item.to_dict() for item in self.attributions],
            "suggestions": list(self.suggestions),
        }


def payload_summary(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Read the summary from an already-serialized task payload."""
    summary = payload.get("summary", {})
    return dict(summary) if isinstance(summary, Mapping) else {}
