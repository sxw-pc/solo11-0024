"""Single-profile metric calculation.

The execution order is fixed: filter -> exclusions -> dimensions grouping ->
metric aggregation.  Every recoverable row or group error is counted instead
of being raised as a bare exception.

When a profile declares a coarser grain than its source data (``source_grain``
plus ``rollup_key``), a controlled roll-up runs after the fine-grain grouping:
``sum`` / ``min`` / ``max`` are re-evaluated over the merged fine rows, while
``count`` is re-evaluated with distinct-entity semantics so the same entity's
fine rows are not counted twice.  Fine rows lacking the entity key are counted
as unrollable rather than silently dropped.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from .expression import ExpressionError, Node, parse_expression
from .expr_eval import evaluate
from .models import (
    CalcStats,
    CalculationResult,
    GRAIN_LEVELS,
    GroupValue,
    Profile,
    RollupTrace,
    normalize_number,
)


class CalculationError(Exception):
    def __init__(self, message: str, code: str = "calculation_error"):
        self.message = message
        self.code = code
        super().__init__(message)

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}


def _source_path(dataset_dir: Path, source: str) -> Path:
    if not source or Path(source).name != source or "/" in source or "\\" in source:
        raise CalculationError(f"非法数据集文件名: {source}", "invalid_dataset")
    path = dataset_dir / source
    if not path.is_file():
        raise CalculationError(f"数据集不存在: {source}", "dataset_not_found")
    return path


def _entity_key(row: dict[str, Any], rollup_key: str) -> str | None:
    """Return the roll-up entity key for a fine row, or ``None`` when unusable."""
    if rollup_key not in row:
        return None
    value = row[rollup_key]
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _rewrite_counts(node: Node, rows: list[dict[str, Any]], key: str) -> Node:
    """Rewrite ``count`` calls to distinct-entity counts for the target grain.

    Every other node is kept unchanged so the standard evaluator still owns
    arithmetic, comparisons and the distributive aggregates.  ``count`` must be
    recomputed at the declared grain: summing per-entity fine-grain counts would
    count one entity with several fine rows multiple times.
    """
    if node.kind == "call" and node.value == "count":
        if not node.children:
            keys = {_entity_key(row, key) for row in rows}
            distinct = len({item for item in keys if item is not None})
        else:
            inner = node.children[0]
            distinct = len(
                {
                    entity
                    for row in rows
                    if (entity := _entity_key(row, key)) is not None
                    and evaluate(inner, row=row) is not None
                }
            )
        return Node("number", distinct, (), node.position)
    if not node.children:
        return node
    return Node(
        node.kind,
        node.value,
        tuple(_rewrite_counts(child, rows, key) for child in node.children),
        node.position,
    )


def _evaluate_rollup_metric(
    metric_node: Node, rows: list[dict[str, Any]], rollup_key: str
) -> Any:
    rewritten = _rewrite_counts(metric_node, rows, rollup_key)
    return evaluate(rewritten, row=None, rows=rows)


def _roll_up_groups(
    grouped: dict[tuple[str, ...], list[dict[str, Any]]],
    metric_node: Node,
    profile: Profile,
) -> tuple[list[GroupValue], int, int, int]:
    """Merge fine-grain groups into the coarser declared-grain groups.

    Returns the rolled-up groups plus ``(unparsable_rows, unrollable_rows,
    rows_aggregated)`` counters.  Groups whose fine rows all lack an entity key
    produce no output group and are never silently dropped.
    """
    merged: dict[tuple[str, ...], list[dict[str, Any]]] = {}
    unrollable_rows = 0
    for key, group_rows in grouped.items():
        usable = [row for row in group_rows if _entity_key(row, profile.rollup_key) is not None]
        unrollable_rows += len(group_rows) - len(usable)
        if usable:
            merged.setdefault(key, []).extend(usable)

    groups: list[GroupValue] = []
    unparsable = 0
    rows_aggregated = 0
    for key in sorted(merged, key=lambda item: tuple(str(part) for part in item)):
        group_rows = merged[key]
        try:
            value = _evaluate_rollup_metric(metric_node, group_rows, profile.rollup_key)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ExpressionError("type_mismatch", "指标结果必须是数值")
            value = normalize_number(value)
        except (ExpressionError, ValueError):
            unparsable += len(group_rows)
            continue
        groups.append(GroupValue(key=key, value=value))
        rows_aggregated += len(group_rows)
    return groups, unparsable, unrollable_rows, rows_aggregated


def calculate_profile(profile: Profile, dataset_dir: Path | str) -> CalculationResult:
    dataset_dir = Path(dataset_dir)
    path = _source_path(dataset_dir, profile.source)
    try:
        filter_node = parse_expression(profile.filter)
        exclusion_nodes = [parse_expression(text) for text in profile.exclusions]
        metric_node = parse_expression(profile.metric)
    except ExpressionError as exc:
        raise CalculationError(f"口径 {profile.id} 表达式无效: {exc}", "invalid_expression") from exc

    rows_read = rows_filtered = rows_excluded = 0
    unparsable = 0
    accepted: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if not reader.fieldnames:
                raise CalculationError(f"数据集缺少表头: {profile.source}", "invalid_dataset")
            for row in reader:
                rows_read += 1
                try:
                    decision = evaluate(filter_node, row=row)
                except ExpressionError:
                    unparsable += 1
                    continue
                if not isinstance(decision, bool):
                    unparsable += 1
                    continue
                if not decision:
                    continue
                rows_filtered += 1
                excluded = False
                try:
                    for exclusion in exclusion_nodes:
                        flag = evaluate(exclusion, row=row)
                        if not isinstance(flag, bool):
                            raise ExpressionError("type_mismatch", "排除条件必须返回布尔值")
                        if flag:
                            excluded = True
                            break
                except ExpressionError:
                    unparsable += 1
                    continue
                if excluded:
                    rows_excluded += 1
                    continue
                accepted.append(row)
    except OSError as exc:
        raise CalculationError(f"无法读取数据集 {profile.source}: {exc}", "dataset_read_error") from exc

    grouped: dict[tuple[str, ...], list[dict[str, Any]]] = {}
    for row in accepted:
        try:
            values = [row[dimension] for dimension in profile.dimensions]
            if any(value is None for value in values):
                raise KeyError("missing dimension value")
            key = tuple(str(value) for value in values)
        except KeyError:
            unparsable += 1
            continue
        grouped.setdefault(key, []).append(row)

    rollup_enabled = (
        profile.source_grain is not None
        and GRAIN_LEVELS[profile.source_grain] < GRAIN_LEVELS[profile.grain]
    )
    groups: list[GroupValue] = []
    rows_aggregated = 0
    rollup_trace: RollupTrace | None = None
    if rollup_enabled:
        groups, rollup_failures, unrollable_rows, rows_aggregated = _roll_up_groups(
            grouped, metric_node, profile
        )
        unparsable += rollup_failures
        rollup_trace = RollupTrace(
            source_grain=profile.source_grain,
            grain=profile.grain,
            rollup_key=profile.rollup_key,
            unrollable_rows=unrollable_rows,
            rolled_up_rows=rows_aggregated,
        )
    else:
        for key in sorted(grouped, key=lambda item: tuple(str(part) for part in item)):
            group_rows = grouped[key]
            try:
                value = evaluate(metric_node, row=None, rows=group_rows)
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    raise ExpressionError("type_mismatch", "指标结果必须是数值")
                value = normalize_number(value)
            except (ExpressionError, ValueError):
                unparsable += len(group_rows)
                continue
            groups.append(GroupValue(key=key, value=value))
            rows_aggregated += len(group_rows)

    stats = CalcStats(
        rows_read=rows_read,
        rows_filtered=rows_filtered,
        rows_excluded=rows_excluded,
        rows_aggregated=rows_aggregated,
        groups=len(groups),
        unparsable_rows=unparsable,
    )
    return CalculationResult(
        profile_id=profile.id,
        profile_name=profile.name,
        grain=profile.grain,
        dimensions=profile.dimensions,
        stats=stats,
        groups=tuple(groups),
        rollup=rollup_trace,
    )
