from __future__ import annotations

import json
from pathlib import Path

import pytest

from koujing.config import DEFAULT_DATASET_DIR, DEFAULT_PROFILE_DIR
from koujing.loader import ProfileError
from koujing.reporter import render_json
from koujing.runner import RunnerError, regenerate_reports, run_calibration
from koujing.store import Store


def _run(tmp_path: Path, **changes):
    values = {
        "profile_ids": ["profile_sales_a", "profile_sales_b"],
        "profile_dir": DEFAULT_PROFILE_DIR,
        "dataset_dir": DEFAULT_DATASET_DIR,
        "db_path": tmp_path / "runner.db",
        "report_dir": tmp_path / "reports",
    }
    values.update(changes)
    return run_calibration(**values)


def test_end_to_end_summary_and_reports(tmp_path: Path) -> None:
    outcome = _run(tmp_path)
    assert outcome.task.task_id.startswith("kj-")
    assert outcome.task.summary()["matched_groups"] == 4
    assert outcome.task.summary()["over_tolerance_groups"] == 2
    assert outcome.task.summary()["only_base_groups"] == 1
    assert outcome.task.summary()["only_compare_groups"] == 1
    assert len(outcome.report_paths) == 2
    assert all(path.is_file() for path in outcome.report_paths)
    stored = Store(tmp_path / "runner.db").get_task(outcome.task.task_id)
    assert stored is not None
    assert stored["task_id"] == outcome.task.task_id


def test_repeated_run_is_byte_identical(tmp_path: Path) -> None:
    first = _run(tmp_path)
    first_bytes = [path.read_bytes() for path in first.report_paths]
    second = _run(tmp_path)
    second_bytes = [path.read_bytes() for path in second.report_paths]
    assert first.task.task_id == second.task.task_id
    assert first_bytes == second_bytes


def test_timestamp_does_not_change_task_id(tmp_path: Path) -> None:
    plain = _run(tmp_path, db_path=tmp_path / "a.db", report_dir=tmp_path / "a")
    stamped = _run(
        tmp_path,
        db_path=tmp_path / "b.db",
        report_dir=tmp_path / "b",
        generated_at="2026-01-01T00:00:00+00:00",
    )
    assert plain.task.task_id == stamped.task.task_id
    assert stamped.task.generated_at == "2026-01-01T00:00:00+00:00"
    assert json.loads(stamped.report_paths[0].read_text(encoding="utf-8"))["generated_at"]


def test_tolerance_changes_task_id(tmp_path: Path) -> None:
    first = _run(tmp_path, report_dir=None, write_reports_enabled=False)
    second = _run(
        tmp_path,
        db_path=tmp_path / "other.db",
        report_dir=None,
        write_reports_enabled=False,
        tolerance_rel=0.2,
    )
    assert first.task.task_id != second.task.task_id


def test_expected_sales_gap_fixture(tmp_path: Path, project_root: Path) -> None:
    outcome = _run(tmp_path)
    actual = json.loads(render_json(outcome.task))
    expected_path = project_root / "fixtures" / "expected" / "sales_gap.json"
    expected = json.loads(expected_path.read_text(encoding="utf-8"))
    assert actual == expected


def test_regenerate_reports_from_task_id(tmp_path: Path) -> None:
    outcome = _run(tmp_path)
    out = tmp_path / "regenerated"
    paths = regenerate_reports(outcome.task.task_id, tmp_path / "runner.db", out)
    assert len(paths) == 2
    assert paths[0].read_text(encoding="utf-8") == render_json(outcome.task)


def test_unknown_profile_and_too_few(tmp_path: Path) -> None:
    with pytest.raises(ProfileError):
        _run(tmp_path, profile_ids=["profile_sales_a", "unknown"])
    with pytest.raises(RunnerError) as caught:
        _run(tmp_path, profile_ids=["profile_sales_a"])
    assert caught.value.code == "too_few_profiles"


def test_missing_report_directory_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(RunnerError) as caught:
        _run(tmp_path, report_dir=None)
    assert caught.value.code == "missing_report_dir"
