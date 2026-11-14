"""Stable Markdown and JSON report rendering."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from .models import TaskResult


def _payload(task: TaskResult | Mapping[str, Any]) -> dict[str, Any]:
    return task.to_dict() if isinstance(task, TaskResult) else dict(task)


def render_json(task: TaskResult | Mapping[str, Any]) -> str:
    return json.dumps(_payload(task), ensure_ascii=False, indent=2) + "\n"


def _cell(value: Any) -> str:
    if value is None:
        return "—"
    return str(value).replace("|", "\\|").replace("\n", " ")


def _rate(value: Any) -> str:
    if value is None:
        return "分母为0，不计算"
    return f"{float(value):.4%}"


def _profile_table(profiles: list[dict[str, Any]]) -> list[str]:
    lines = [
        "| 口径 ID | 名称 | 数据源 | 粒度 | 过滤 | 指标 | 维度 |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for profile in profiles:
        values = (
            profile["id"],
            profile["name"],
            profile["source"],
            profile["grain"],
            profile["filter"],
            profile["metric"],
            ", ".join(profile["dimensions"]),
        )
        lines.append("| " + " | ".join(_cell(item) for item in values) + " |")
    return lines


def _stats_table(payload: dict[str, Any]) -> list[str]:
    names = {item["id"]: item["name"] for item in payload["profiles"]}
    lines = [
        "| 口径 | 读取行数 | 过滤后行数 | 排除行数 | 聚合行数 | 分组数 | 无法解析行数 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for calculation in payload["calculations"]:
        stats = calculation["stats"]
        values = (
            names.get(calculation["profile_id"], calculation["profile_id"]),
            stats["rows_read"],
            stats["rows_filtered"],
            stats["rows_excluded"],
            stats["rows_aggregated"],
            stats["groups"],
            stats["unparsable_rows"],
        )
        lines.append("| " + " | ".join(_cell(item) for item in values) + " |")
    return lines


def _comparison_table(payload: dict[str, Any]) -> list[str]:
    lines = [
        "| 基准 | 比对 | 匹配组 | 容差内 | 超差 | 仅基准 | 仅比对 |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for item in payload["comparisons"]:
        summary = item["summary"]
        values = (
            item["base_profile"],
            item["compare_profile"],
            summary["matched_groups"],
            summary["within_tolerance_groups"],
            summary["over_tolerance_groups"],
            summary["only_base_groups"],
            summary["only_compare_groups"],
        )
        lines.append("| " + " | ".join(_cell(value) for value in values) + " |")
    return lines


def _alignment_lines(payload: dict[str, Any]) -> list[str]:
    rows: list[tuple[str, str, dict[str, Any]]] = []
    for comparison in payload["comparisons"]:
        alignment = comparison.get("alignment")
        if isinstance(alignment, Mapping):
            rows.append(
                (comparison["base_profile"], comparison["compare_profile"], dict(alignment))
            )
    if not rows:
        return []
    mode_names = {"exact": "维度一致", "common": "公共维度归并"}
    lines = [
        "",
        "**维度归并信号（联合比对）**",
        "",
        "| 基准 | 比对 | 模式 | 公共维度 | 侧 | 独有维度 | 原组数 | 归并塌陷组 | 恢复行 | 未映射组 |",
        "| --- | --- | --- | --- | --- | --- | ---: | ---: | ---: | ---: |",
    ]
    for base_id, compare_id, alignment in rows:
        mode = mode_names.get(str(alignment.get("mode")), str(alignment.get("mode")))
        common = ", ".join(str(item) for item in alignment["common_dimensions"]) or "—"
        for side, prefix in (("基准", "base"), ("比对", "compare")):
            extra = ", ".join(str(item) for item in alignment[f"{prefix}_extra_dimensions"]) or "无"
            values = (
                base_id,
                compare_id,
                mode,
                common,
                side,
                extra,
                alignment[f"{prefix}_source_groups"],
                alignment[f"{prefix}_collapsed_groups"],
                alignment[f"{prefix}_recovered_groups"],
                alignment[f"{prefix}_unmapped_groups"],
            )
            lines.append("| " + " | ".join(_cell(value) for value in values) + " |")
    return lines


def _detail_table(payload: dict[str, Any]) -> list[str]:
    lines = [
        "| 类型 | 基准口径 | 比对口径 | 分组键 | 基准值 | 比对值 | 差异 | 差异率 | 标记 |",
        "| --- | --- | --- | --- | ---: | ---: | ---: | ---: | --- |",
    ]
    found = False
    for comparison in payload["comparisons"]:
        base_id, other_id = comparison["base_profile"], comparison["compare_profile"]
        for item in comparison["matched"]:
            if item["within_tolerance"]:
                continue
            found = True
            marker = "分母为0" if item["base_zero"] else "超差"
            values = (
                "超差",
                base_id,
                other_id,
                item["label"],
                item["base_value"],
                item["compare_value"],
                item["diff"],
                _rate(item["diff_rate"]),
                marker,
            )
            lines.append("| " + " | ".join(_cell(value) for value in values) + " |")
        for item in comparison["only_base"]:
            found = True
            values = ("仅基准有", base_id, other_id, item["label"], item["value"], "—", "—", "—", "缺失")
            lines.append("| " + " | ".join(_cell(value) for value in values) + " |")
        for item in comparison["only_compare"]:
            found = True
            values = ("仅比对有", base_id, other_id, item["label"], "—", item["value"], "—", "—", "缺失")
            lines.append("| " + " | ".join(_cell(value) for value in values) + " |")
    if not found:
        lines.append("| 无 | — | — | — | — | — | — | — | — |")
    return lines


def _attribution_lines(payload: dict[str, Any]) -> list[str]:
    if not payload["attributions"]:
        return ["无条款级归因线索。"]
    lines: list[str] = []
    for item in payload["attributions"]:
        if lines:
            lines.append("")
        lines.append(
            f"### {item['group_label']}（{item['base_profile']} → {item['compare_profile']}）"
        )
        for clue in item["clues"]:
            lines.append(f"- 条款 `{clue['clause']}`")
            lines.append(f"  - 基准：`{_cell(clue['base'])}`")
            lines.append(f"  - 比对：`{_cell(clue['compare'])}`")
            lines.append(f"  - 说明：{clue['explanation']}")
            lines.append(f"  - 候选影响分组：{', '.join(clue['affected_groups'])}")
    return lines


def render_markdown(task: TaskResult | Mapping[str, Any]) -> str:
    payload = _payload(task)
    summary = payload["summary"]
    generated = payload["generated_at"] or "未启用（确定性模式）"
    lines = [
        "# 口径校准报告",
        "",
        "## 1. 任务元信息",
        "",
        f"- 任务 ID：`{payload['task_id']}`",
        f"- 基准口径：`{payload['base_profile']}`",
        f"- 生成时间：{generated}",
        f"- 口径数：{summary['profile_count']}",
        f"- 比对次数：{summary['comparison_count']}",
        f"- 匹配组：{summary['matched_groups']}；超差组：{summary['over_tolerance_groups']}；"
        f"仅基准组：{summary['only_base_groups']}；仅比对组：{summary['only_compare_groups']}",
        *(
            [
                f"- 联合比对：{summary['joint_comparisons']} 次；归并塌陷组：{summary['collapsed_groups']}；"
                f"恢复行：{summary['recovered_groups']}；未映射组：{summary['unmapped_groups']}"
            ]
            if summary.get("joint_comparisons")
            else []
        ),
        "",
        "## 2. 参与口径清单",
        "",
        *_profile_table(payload["profiles"]),
        "",
        "## 3. 各口径计算统计",
        "",
        *_stats_table(payload),
        "",
        "## 4. 比对结果",
        "",
        *_comparison_table(payload),
        *_alignment_lines(payload),
        "",
        "## 5. 超差与缺失分组明细",
        "",
        *_detail_table(payload),
        "",
        "## 6. 差异归因线索",
        "",
        *_attribution_lines(payload),
        "",
        "## 7. 校准建议",
        "",
    ]
    lines.extend(f"- {item}" for item in payload["suggestions"])
    lines.append("")
    return "\n".join(lines)


def write_reports(
    task: TaskResult | Mapping[str, Any],
    out_dir: Path | str,
    formats: Iterable[str] = ("json", "md"),
) -> tuple[Path, ...]:
    payload = _payload(task)
    task_id = str(payload["task_id"])
    if not task_id or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for char in task_id):
        raise ValueError("task_id 含非法字符，拒绝写入报告")
    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    renderers = {"json": render_json, "md": render_markdown}
    requested = list(formats)
    unknown = [item for item in requested if item not in renderers]
    if unknown:
        raise ValueError(f"不支持的报告格式: {', '.join(unknown)}")
    written: list[Path] = []
    for fmt in requested:
        path = out_path / f"calibration-{task_id}.{fmt}"
        with path.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(renderers[fmt](payload))
        written.append(path)
    return tuple(written)
