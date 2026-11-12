"""Clause-level evidence and calibration advice for divergent groups."""

from __future__ import annotations

import json
from typing import Iterable

from .models import (
    Attribution,
    AttributionClue,
    ComparisonResult,
    Profile,
    group_label,
)

CLAUSE_ORDER = ("filter", "metric", "exclusions", "grain")
_EXPLANATIONS = {
    "filter": "过滤条件不同，进入聚合的订单/事件集合可能不同。",
    "metric": "聚合表达式不同，同一行集合上的指标计算方式不同。",
    "exclusions": "排除规则不同，被剔除的行可能不同。",
    "grain": "声明粒度不同，指标的最小统计单元不同。",
}


def _exclusions_text(values: Iterable[str]) -> str:
    return json.dumps(list(values), ensure_ascii=False, separators=(",", ":"))


def clause_differences(base: Profile, other: Profile) -> list[tuple[str, str, str]]:
    """Return only the declared clauses that actually differ."""
    pairs: list[tuple[str, str, str]] = []
    if base.filter != other.filter:
        pairs.append(("filter", base.filter, other.filter))
    if base.metric != other.metric:
        pairs.append(("metric", base.metric, other.metric))
    if base.exclusions != other.exclusions:
        pairs.append(
            (
                "exclusions",
                _exclusions_text(base.exclusions),
                _exclusions_text(other.exclusions),
            )
        )
    if base.grain != other.grain:
        pairs.append(("grain", base.grain, other.grain))
    return pairs


def _divergent_groups(comparison: ComparisonResult) -> list[tuple[str, ...]]:
    keys = [item.key for item in comparison.matched if not item.within_tolerance]
    keys.extend(item.key for item in comparison.only_base)
    keys.extend(item.key for item in comparison.only_compare)
    return sorted(set(keys), key=lambda key: tuple(str(part) for part in key))


def attribute_comparison(
    base: Profile,
    other: Profile,
    comparison: ComparisonResult,
) -> list[Attribution]:
    """Build conservative evidence; it explains candidates, not causality."""
    differences = clause_differences(base, other)
    divergent = _divergent_groups(comparison)
    if not differences or not divergent:
        return []
    labels = tuple(group_label(comparison.dimensions, key) for key in divergent)
    attributions: list[Attribution] = []
    for key in divergent:
        clues = tuple(
            AttributionClue(
                clause=clause,
                base=base_value,
                compare=compare_value,
                explanation=_EXPLANATIONS[clause],
                affected_groups=labels,
            )
            for clause, base_value, compare_value in differences
        )
        attributions.append(
            Attribution(
                base_profile=base.id,
                compare_profile=other.id,
                group_key=key,
                group_label=group_label(comparison.dimensions, key),
                clues=clues,
            )
        )
    return attributions


def _has_divergence(comparison: ComparisonResult) -> bool:
    return bool(comparison.over_count or comparison.only_base or comparison.only_compare)


def build_suggestions(
    comparisons: Iterable[ComparisonResult],
    attributions: Iterable[Attribution],
) -> list[str]:
    comparisons = list(comparisons)
    attributions = list(attributions)
    if not comparisons:
        return ["本次未执行跨口径比对，请至少选择两个口径。"]
    if not any(_has_divergence(item) for item in comparisons):
        return ["所有参与比对的分组均在容差内，当前口径无需调整。"]

    used = {clue.clause for item in attributions for clue in item.clues}
    suggestions: list[str] = []
    advice = {
        "filter": "统一过滤条件的业务边界，并在口径文档中写明生效字段与取值。",
        "metric": "确认指标应采用的聚合函数、分母与单位，必要时补充分子分母声明。",
        "exclusions": "对齐排除规则，逐条确认测试、内部、退款等特殊行的处理方式。",
        "grain": "明确统计粒度，避免订单级与用户级数据在同一结论中混用。",
    }
    for clause in CLAUSE_ORDER:
        if clause in used:
            suggestions.append(f"统一 {clause} 条款：{advice[clause]}")
    if any(item.only_base or item.only_compare for item in comparisons):
        suggestions.append("核对维度取值覆盖范围，补齐仅在单侧出现的分组，或说明其缺失原因。")
    if not attributions:
        suggestions.append("未发现声明条款差异，优先核对两侧数据源的采集、清洗与更新时间。")
    return suggestions
