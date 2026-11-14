from __future__ import annotations

import json
from pathlib import Path

import pytest

from koujing import cli
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


MIXED_BUCKETS_A = [
    ("Alpha", 100),
    ("alpha", 100),
    ("BETA", 100),
    ("beta", 100),
    ("0", 100),
    ("01", 10),
    ("1", 30),
    ("2", 20),
    ("10", 100),
    ("OnlyBaseA", 5),
    ("OnlyBaseB", 5),
]
MIXED_BUCKETS_B = [
    ("Alpha", 200),
    ("alpha", 100),
    ("BETA", 100),
    ("beta", 150),
    ("0", 0),
    ("01", 10),
    ("1", 60),
    ("10", 100),
    ("OnlyCompareA", 5),
    ("OnlyCompareB", 5),
    ("100", 7),
]


def _write_mixed_fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Build a profile/dataset pair whose keys mix numbers, digits and case."""
    profile_dir = tmp_path / "mixed-profiles"
    dataset_dir = tmp_path / "mixed-datasets"
    profile_dir.mkdir()
    dataset_dir.mkdir()
    for name, rows in (("a", MIXED_BUCKETS_A), ("b", MIXED_BUCKETS_B)):
        lines = ["bucket,status,amount"]
        lines.extend(f"{bucket},paid,{amount}" for bucket, amount in rows)
        (dataset_dir / f"mixed_{name}.csv").write_text(
            "\n".join(lines) + "\n", encoding="utf-8"
        )
    templates = {
        "profile_mixed_a": (
            "mixed_a.csv",
            "status == 'paid'",
        ),
        "profile_mixed_b": (
            "mixed_b.csv",
            "status in ['paid', 'settled']",
        ),
    }
    for profile_id, (source, filter_expression) in templates.items():
        text = (
            f"id: {profile_id}\n"
            f"name: {profile_id}\n"
            f"source: {source}\n"
            "grain: order\n"
            f'filter: "{filter_expression}"\n'
            'metric: "sum(amount)"\n'
            "dimensions:\n"
            "  - bucket\n"
            "exclusions: []\n"
            'notes: "mixed-type key fixture"\n'
        )
        (profile_dir / f"{profile_id}.yaml").write_text(text, encoding="utf-8")
    db_path = tmp_path / "mixed.db"
    return profile_dir, dataset_dir, db_path


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


def test_dataset_line_endings_do_not_change_task_id(tmp_path: Path) -> None:
    plain = _run(tmp_path, report_dir=None, write_reports_enabled=False)
    dataset_dir = tmp_path / "crlf-datasets"
    dataset_dir.mkdir()
    for name in ("orders_a.csv", "orders_b.csv", "events.csv"):
        text = (DEFAULT_DATASET_DIR / name).read_text(encoding="utf-8")
        (dataset_dir / name).write_text(
            text.replace("\n", "\r\n"), encoding="utf-8", newline=""
        )
    normalized = _run(
        tmp_path,
        dataset_dir=dataset_dir,
        db_path=tmp_path / "crlf.db",
        report_dir=None,
        write_reports_enabled=False,
    )
    assert normalized.task.task_id == plain.task.task_id


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


def _run_mixed(tmp_path: Path):
    profile_dir, dataset_dir, db_path = _write_mixed_fixture(tmp_path)
    return run_calibration(
        ["profile_mixed_a", "profile_mixed_b"],
        profile_dir,
        dataset_dir,
        db_path,
        report_dir=tmp_path / "mixed-reports",
    )


def test_run_show_replay_keeps_mixed_key_markdown_bytes(
    tmp_path: Path, capsys
) -> None:
    outcome = _run_mixed(tmp_path)
    json_path, markdown_path = outcome.report_paths
    first_markdown = markdown_path.read_bytes()

    # The detail table concatenates matched, only_base and only_compare, so a
    # drifted single-side ordering would change these rows.
    text = first_markdown.decode("utf-8")
    only_base_rows = [line for line in text.splitlines() if line.startswith("| 仅基准有 ")]
    only_compare_rows = [line for line in text.splitlines() if line.startswith("| 仅比对有 ")]
    assert [line.split(" | ")[3] for line in only_base_rows] == [
        "bucket=OnlyBaseA",
        "bucket=OnlyBaseB",
        "bucket=2",
    ]
    assert [line.split(" | ")[3] for line in only_compare_rows] == [
        "bucket=OnlyCompareA",
        "bucket=OnlyCompareB",
        "bucket=100",
    ]

    exit_code = cli.main(
        ["show", outcome.task.task_id, "--db", str(tmp_path / "mixed.db"), "--format", "markdown"]
    )
    assert exit_code == 0
    assert capsys.readouterr().out.encode("utf-8") == first_markdown

    regenerated = regenerate_reports(
        outcome.task.task_id, tmp_path / "mixed.db", tmp_path / "mixed-replay"
    )
    assert regenerated[1].read_bytes() == first_markdown
    assert regenerated[0].read_bytes() == json_path.read_bytes()


def test_mixed_key_calculation_order_is_stable(tmp_path: Path) -> None:
    outcome = _run_mixed(tmp_path)
    assert list(outcome.task.calculations[0].group_map()) == [
        ("Alpha",),
        ("BETA",),
        ("OnlyBaseA",),
        ("OnlyBaseB",),
        ("alpha",),
        ("beta",),
        ("0",),
        ("01",),
        ("1",),
        ("2",),
        ("10",),
    ]
    comparison = outcome.task.comparisons[0]
    assert [item.key for item in comparison.only_base] == [
        ("OnlyBaseA",),
        ("OnlyBaseB",),
        ("2",),
    ]
    assert [item.key for item in comparison.only_compare] == [
        ("OnlyCompareA",),
        ("OnlyCompareB",),
        ("100",),
    ]
    assert [item.group_key for item in outcome.task.attributions] == [
        ("Alpha",),
        ("OnlyBaseA",),
        ("OnlyBaseB",),
        ("OnlyCompareA",),
        ("OnlyCompareB",),
        ("beta",),
        ("0",),
        ("1",),
        ("2",),
        ("100",),
    ]


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
