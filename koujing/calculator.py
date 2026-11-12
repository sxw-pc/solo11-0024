"""Single-profile metric calculation.

The execution order is fixed: filter -> exclusions -> dimensions grouping ->
metric aggregation.  Every recoverable row or group error is counted instead
of being raised as a bare exception.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from .expression import ExpressionError, parse_expression
from .expr_eval import evaluate
from .models import CalcStats, CalculationResult, GroupValue, Profile, normalize_number


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

    groups: list[GroupValue] = []
    rows_aggregated = 0
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
    )
