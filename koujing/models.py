"""Typed records shared by the calibration pipeline and its reporters."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Iterable, Mapping, Union

Number = Union[int, float]

# ASCII-only so that the numeric interpretation is identical on every
# platform and does not silently accept values such as ``nan`` or ``inf``.
_NUMERIC_TEXT = re.compile(r"^[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)$")


def group_sort_key(key: Iterable[Any]) -> tuple[tuple[int, Any, str], ...]:
    """Return the canonical sort key for a dimension-key tuple.

    Each part becomes ``(is_numeric, value_or_text, original_text)``:

    * non-numeric parts sort before numeric parts and compare as text;
    * numeric parts compare by their exact decimal value, so ``"2"`` sorts
      before ``"10"``;
    * the original text breaks ties, so ``"01"`` and ``"1"`` keep a stable
      order instead of collapsing into one another.

    Every pipeline stage (calculation, comparison and attribution) sorts with
    this key so the same group always appears at the same position.
    """
    parts: list[tuple[int, Any, str]] = []
    for part in key:
        text = str(part)
        if _NUMERIC_TEXT.fullmatch(text):
            parts.append((1, Decimal(text), text))
        else:
            parts.append((0, text, text))
    return tuple(parts)


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
class ComparisonResult:
    base_profile: str
    compare_profile: str
    dimensions: tuple[str, ...]
    tolerance_abs: float
    tolerance_rel: float
    matched: tuple[MatchedGroup, ...]
    only_base: tuple[SideGroup, ...]
    only_compare: tuple[SideGroup, ...]

    @property
    def within_count(self) -> int:
        return sum(1 for item in self.matched if item.within_tolerance)

    @property
    def over_count(self) -> int:
        return sum(1 for item in self.matched if not item.within_tolerance)

    def summary(self) -> dict[str, int]:
        return {
            "matched_groups": len(self.matched),
            "within_tolerance_groups": self.within_count,
            "over_tolerance_groups": self.over_count,
            "only_base_groups": len(self.only_base),
            "only_compare_groups": len(self.only_compare),
        }

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
        return {
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
        return {
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
