from __future__ import annotations

import json
from pathlib import Path

from koujing.reporter import render_json, render_markdown, write_reports


def test_json_output_is_stable_and_ordered(sales_task) -> None:
    first = render_json(sales_task)
    second = render_json(sales_task)
    assert first == second
    payload = json.loads(first)
    assert list(payload) == [
        "task_id",
        "generated_at",
        "base_profile",
        "summary",
        "profiles",
        "calculations",
        "comparisons",
        "attributions",
        "suggestions",
    ]
    assert payload["generated_at"] is None
    assert payload["summary"]["status"] == "completed"


def test_markdown_contains_required_sections(sales_task) -> None:
    markdown = render_markdown(sales_task)
    assert markdown.startswith("# 口径校准报告")
    for heading in (
        "## 1. 任务元信息",
        "## 2. 参与口径清单",
        "## 3. 各口径计算统计",
        "## 4. 比对结果",
        "## 5. 超差与缺失分组明细",
        "## 6. 差异归因线索",
        "## 7. 校准建议",
    ):
        assert heading in markdown
    assert "profile_sales_a" in markdown
    assert "profile_sales_b" in markdown
    assert "region=华中" in markdown


def test_markdown_is_stable(sales_task) -> None:
    assert render_markdown(sales_task) == render_markdown(sales_task)
    assert render_markdown(sales_task).endswith("\n")


def test_write_reports_creates_both_files(sales_task, tmp_path: Path) -> None:
    paths = write_reports(sales_task, tmp_path / "out")
    assert [path.suffix for path in paths] == [".json", ".md"]
    assert all(path.is_file() for path in paths)
    assert paths[0].read_text(encoding="utf-8") == render_json(sales_task)
    assert paths[1].read_text(encoding="utf-8") == render_markdown(sales_task)
    assert sales_task.task_id in paths[0].name


def test_reports_accept_stored_payload(sales_task, tmp_path: Path) -> None:
    payload = sales_task.to_dict()
    assert render_json(payload) == render_json(sales_task)
    assert render_markdown(payload) == render_markdown(sales_task)
    paths = write_reports(payload, tmp_path)
    assert len(paths) == 2


def test_report_rejects_unsafe_task_id(sales_task) -> None:
    payload = sales_task.to_dict()
    payload["task_id"] = "../escape"
    try:
        write_reports(payload, "unused")
    except ValueError as exc:
        assert "非法字符" in str(exc)
    else:
        raise AssertionError("unsafe task id should be rejected")
