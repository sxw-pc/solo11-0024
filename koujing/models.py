"""Typed records shared by the calibration pipeline and its reporters."""

from __future__ import annotations

import copy
import math
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Union

Number = Union[int, float]

# Explicit schema version of the serialized task payload archived in the
# ledger. v1 is the original shape (no optional signal keys); v2 declares the
# optional signals below. The writer stamps PAYLOAD_SCHEMA_VERSION on every
# save; the read path migrates older payloads in memory only.
PAYLOAD_SCHEMA_VERSION = 2
MIN_READABLE_PAYLOAD_VERSION = 1

# Optional signals introduced after schema v1, with the declared defaults used
# to backfill archived payloads at read time. Absence means "no signals were
# recorded", so every default is the empty container. Values must be
# JSON-native and are deep-copied before being inserted anywhere.
TASK_SUMMARY_DEFAULTS: dict[str, Any] = {"refined_signals": {}}
COMPARISON_SUMMARY_DEFAULTS: dict[str, Any] = {"refined_signals": {}}
MATCHED_GROUP_DEFAULTS: dict[str, Any] = {"signals": []}

# Required structure the read path (reporter / ledger queries) dereferences.
# Missing or mistyped required keys make a payload unmigratable: refusing is
# safer than guessing values for archived history.
_REQUIRED_PAYLOAD_KEYS: dict[str, tuple[type, ...]] = {
    "task_id": (str,),
    "generated_at": (str, type(None)),
    "base_profile": (str,),
    "summary": (dict,),
    "profiles": (list,),
    "calculations": (list,),
    "comparisons": (list,),
    "attributions": (list,),
    "suggestions": (list,),
}
_TASK_SUMMARY_COUNT_KEYS = (
    "profile_count",
    "comparison_count",
    "matched_groups",
    "within_tolerance_groups",
    "over_tolerance_groups",
    "only_base_groups",
    "only_compare_groups",
)
_COMPARISON_REQUIRED_KEYS: dict[str, tuple[type, ...]] = {
    "base_profile": (str,),
    "compare_profile": (str,),
    "summary": (dict,),
    "matched": (list,),
    "only_base": (list,),
    "only_compare": (list,),
}
_COMPARISON_SUMMARY_COUNT_KEYS = (
    "matched_groups",
    "within_tolerance_groups",
    "over_tolerance_groups",
    "only_base_groups",
    "only_compare_groups",
)
_MATCHED_REQUIRED_KEYS = (
    "label",
    "base_value",
    "compare_value",
    "diff",
    "diff_rate",
    "within_tolerance",
    "base_zero",
)
_SIDE_REQUIRED_KEYS = ("label", "value")


class PayloadSchemaError(ValueError):
    """Structured rejection for an archived payload that cannot be migrated."""

    def __init__(self, message: str, code: str = "payload_migration_error"):
        self.message = message
        self.code = code
        super().__init__(message)


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


def _require_shape(
    mapping: Mapping[str, Any], spec: Mapping[str, tuple[type, ...]], context: str
) -> None:
    for key, types in spec.items():
        if key not in mapping:
            raise PayloadSchemaError(f"{context} 缺少必需字段: {key}")
        value = mapping[key]
        if isinstance(value, bool) or not isinstance(value, types):
            raise PayloadSchemaError(f"{context} 字段 {key} 类型无效，无法安全迁移")


def _require_counts(summary: Mapping[str, Any], keys: Iterable[str], context: str) -> None:
    for key in keys:
        if key not in summary:
            raise PayloadSchemaError(f"{context} 缺少必需字段: {key}")
        value = summary[key]
        if isinstance(value, bool) or not isinstance(value, int):
            raise PayloadSchemaError(f"{context} 字段 {key} 必须是整数，无法安全迁移")


def _validate_payload_structure(payload: Mapping[str, Any]) -> None:
    _require_shape(payload, _REQUIRED_PAYLOAD_KEYS, "payload")
    _require_counts(payload["summary"], _TASK_SUMMARY_COUNT_KEYS, "payload.summary")
    for index, comparison in enumerate(payload["comparisons"]):
        context = f"payload.comparisons[{index}]"
        if not isinstance(comparison, Mapping):
            raise PayloadSchemaError(f"{context} 必须是对象，无法安全迁移")
        _require_shape(comparison, _COMPARISON_REQUIRED_KEYS, context)
        _require_counts(
            comparison["summary"], _COMPARISON_SUMMARY_COUNT_KEYS, f"{context}.summary"
        )
        for item in comparison["matched"]:
            if not isinstance(item, Mapping):
                raise PayloadSchemaError(f"{context}.matched 条目必须是对象，无法安全迁移")
            for key in _MATCHED_REQUIRED_KEYS:
                if key not in item:
                    raise PayloadSchemaError(f"{context}.matched 条目缺少必需字段: {key}")
        for side in ("only_base", "only_compare"):
            for item in comparison[side]:
                if not isinstance(item, Mapping):
                    raise PayloadSchemaError(
                        f"{context}.{side} 条目必须是对象，无法安全迁移"
                    )
                for key in _SIDE_REQUIRED_KEYS:
                    if key not in item:
                        raise PayloadSchemaError(
                            f"{context}.{side} 条目缺少必需字段: {key}"
                        )


def _backfill(mapping: dict[str, Any], defaults: Mapping[str, Any], context: str) -> None:
    for key, default in defaults.items():
        if key not in mapping:
            mapping[key] = copy.deepcopy(default)
            continue
        value = mapping[key]
        if isinstance(value, bool) or not isinstance(value, type(default)):
            raise PayloadSchemaError(
                f"{context} 字段 {key} 与声明的信号类型冲突，无法安全迁移"
            )


def migrate_payload(payload: Any, version: Any) -> dict[str, Any]:
    """Return a current-schema copy of an archived task payload.

    Older payloads are lifted to the current schema by backfilling the
    declared optional-signal defaults; the input is never mutated and nothing
    is written back to the ledger. Payloads whose structure cannot be
    migrated safely are rejected with PayloadSchemaError instead of being
    guessed at.
    """
    if isinstance(version, bool) or not isinstance(version, int):
        raise PayloadSchemaError(
            f"payload 版本无效: {version!r}", "unsupported_payload_version"
        )
    if version > PAYLOAD_SCHEMA_VERSION:
        raise PayloadSchemaError(
            f"payload 版本 {version} 高于当前支持的 {PAYLOAD_SCHEMA_VERSION}，请升级后再读取",
            "unsupported_payload_version",
        )
    if version < MIN_READABLE_PAYLOAD_VERSION:
        raise PayloadSchemaError(
            f"payload 版本 {version} 过旧，无法安全迁移",
            "unsupported_payload_version",
        )
    if not isinstance(payload, Mapping):
        raise PayloadSchemaError("payload 必须是 JSON 对象，无法安全迁移")
    _validate_payload_structure(payload)
    migrated: dict[str, Any] = copy.deepcopy(dict(payload))
    if version == PAYLOAD_SCHEMA_VERSION:
        return migrated
    _backfill(migrated["summary"], TASK_SUMMARY_DEFAULTS, "payload.summary")
    for index, comparison in enumerate(migrated["comparisons"]):
        context = f"payload.comparisons[{index}]"
        _backfill(comparison["summary"], COMPARISON_SUMMARY_DEFAULTS, f"{context}.summary")
        for item in comparison["matched"]:
            _backfill(item, MATCHED_GROUP_DEFAULTS, f"{context}.matched 条目")
    return migrated
