from __future__ import annotations

import json
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from koujing.calculator import CalculationError, calculate_profile
from koujing.loader import load_profiles
from koujing.reporter import render_json


def test_sales_a_group_values(fixture_dirs, sales_profiles) -> None:
    _, dataset_dir = fixture_dirs
    result = calculate_profile(sales_profiles[0], dataset_dir)
    values = {key: value for key, value in result.group_map().items()}
    assert values[("华东",)] == 2500
    assert values[("华北",)] == 800
    assert values[("华南",)] == 600
    assert values[("华中",)] == 400
    assert values[("西北",)] == 450
    assert ("东北",) not in values


def test_sales_a_statistics(fixture_dirs, sales_profiles) -> None:
    _, dataset_dir = fixture_dirs
    stats = calculate_profile(sales_profiles[0], dataset_dir).stats
    assert stats.rows_read == 10
    assert stats.rows_filtered == 8
    assert stats.rows_excluded == 1
    assert stats.rows_aggregated == 6
    assert stats.groups == 5
    assert stats.unparsable_rows == 1


def test_exclusion_removes_test_channel(fixture_dirs, sales_profiles) -> None:
    _, dataset_dir = fixture_dirs
    result = calculate_profile(sales_profiles[0], dataset_dir)
    assert ("西南",) not in result.group_map()
    assert result.stats.rows_excluded == 1


def test_filter_and_exclusion_statistics_for_b(fixture_dirs, sales_profiles) -> None:
    _, dataset_dir = fixture_dirs
    result = calculate_profile(sales_profiles[1], dataset_dir)
    assert result.stats.rows_read == 8
    assert result.stats.rows_filtered == 7
    assert result.stats.rows_excluded == 1
    assert result.stats.rows_aggregated == 6
    assert result.group_map()[("华东",)] == 2700


def test_active_user_count(fixture_dirs) -> None:
    profile_dir, dataset_dir = fixture_dirs
    profile = load_profiles(profile_dir, dataset_dir, ["profile_active_user"])[0]
    result = calculate_profile(profile, dataset_dir)
    assert result.group_map() == {("华东",): 2, ("华北",): 1, ("华南",): 1}
    assert result.stats.rows_filtered == 4
    assert result.stats.groups == 3


def test_groups_are_sorted_and_serialized(fixture_dirs, sales_profiles) -> None:
    _, dataset_dir = fixture_dirs
    result = calculate_profile(sales_profiles[0], dataset_dir)
    labels = [item["label"] for item in result.to_dict()["groups"]]
    assert labels == sorted(labels)
    assert list(result.to_dict()) == [
        "profile_id",
        "profile_name",
        "grain",
        "dimensions",
        "stats",
        "groups",
    ]
    assert list(result.to_dict()["stats"]) == [
        "rows_read",
        "rows_filtered",
        "rows_excluded",
        "rows_aggregated",
        "groups",
        "unparsable_rows",
    ]


def test_missing_dataset_is_structured(fixture_dirs, sales_profiles) -> None:
    _, dataset_dir = fixture_dirs
    broken = replace(sales_profiles[0], source="missing.csv")
    with pytest.raises(CalculationError) as caught:
        calculate_profile(broken, dataset_dir)
    assert caught.value.code == "dataset_not_found"


def test_metric_row_error_counts_group(fixture_dirs, sales_profiles) -> None:
    _, dataset_dir = fixture_dirs
    result = calculate_profile(sales_profiles[0], dataset_dir)
    kept = result.stats.rows_filtered - result.stats.rows_excluded
    assert result.stats.rows_aggregated + result.stats.unparsable_rows == kept
    assert result.stats.unparsable_rows == 1


DECIMAL_CSV = (
    "order_id,region,status,amount\n"
    "D-001,华东,paid,0.1\n"
    "D-002,华东,paid,0.2\n"
    "D-003,华北,paid,12345678901234567890.12\n"
    "D-004,华北,paid,12345678901234567890.13\n"
    "D-005,华南,paid,not-a-number\n"
    "D-006,华南,paid,0.3\n"
)


@pytest.fixture
def decimal_env(tmp_path: Path):
    dataset_dir = tmp_path / "datasets"
    dataset_dir.mkdir()
    (dataset_dir / "decimal.csv").write_text(DECIMAL_CSV, encoding="utf-8")
    profile_dir = tmp_path / "profiles"
    profile_dir.mkdir()
    (profile_dir / "profile_decimal.yaml").write_text(
        "id: profile_decimal\n"
        "name: 小数聚合口径\n"
        "source: decimal.csv\n"
        "grain: order\n"
        "filter: \"status == 'paid'\"\n"
        "metric: \"sum(amount)\"\n"
        "dimensions:\n"
        "  - region\n"
        "exclusions: []\n"
        "notes: \"高精度小数聚合样例。\"\n"
        "decimal_mode: true\n",
        encoding="utf-8",
    )
    (profile_dir / "profile_float.yaml").write_text(
        "id: profile_float\n"
        "name: 浮点聚合口径\n"
        "source: decimal.csv\n"
        "grain: order\n"
        "filter: \"status == 'paid'\"\n"
        "metric: \"sum(amount)\"\n"
        "dimensions:\n"
        "  - region\n"
        "exclusions: []\n"
        "notes: \"同一数据的浮点对照。\"\n",
        encoding="utf-8",
    )
    loaded = {
        profile.id: profile
        for profile in load_profiles(
            profile_dir, dataset_dir, ["profile_decimal", "profile_float"]
        )
    }
    return dataset_dir, loaded


def test_decimal_mode_groups_are_reproducible(decimal_env) -> None:
    dataset_dir, profiles = decimal_env
    result = calculate_profile(profiles["profile_decimal"], dataset_dir)
    values = result.group_map()
    assert values[("华东",)] == 0.3
    # The exact decimal total is 24691357802469135780.25; the public contract
    # collapses it to a stable float-representable value, never a Decimal.
    assert values[("华北",)] == 24691357802469134336
    # Invalid row is isolated to its group instead of failing the whole table.
    assert ("华南",) not in values
    assert result.stats.rows_read == 6
    assert result.stats.rows_filtered == 6
    assert result.stats.rows_aggregated == 4
    assert result.stats.groups == 2
    assert result.stats.unparsable_rows == 2
    for value in values.values():
        assert isinstance(value, (int, float)) and not isinstance(value, Decimal)


def test_call_level_decimal_mode_overrides_profile(decimal_env) -> None:
    dataset_dir, profiles = decimal_env
    profile = replace(
        profiles["profile_float"], metric="sum(amount) - 24691357802469135780"
    )
    assert profile.decimal_mode is False
    floated = calculate_profile(profile, dataset_dir)
    decimalized = calculate_profile(profile, dataset_dir, decimal_mode=True)
    assert floated.group_map()[("华北",)] != decimalized.group_map()[("华北",)]
    assert decimalized.group_map()[("华北",)] == 0.25
    # Explicit False wins over a profile that declares decimal aggregation.
    disabled = calculate_profile(
        replace(profiles["profile_decimal"], metric=profile.metric),
        dataset_dir,
        decimal_mode=False,
    )
    assert disabled.group_map()[("华北",)] == floated.group_map()[("华北",)]


def test_decimal_mode_high_precision_group_matches_difference(decimal_env) -> None:
    dataset_dir, profiles = decimal_env
    profile = replace(profiles["profile_decimal"], metric="sum(amount) - 24691357802469135780")
    result = calculate_profile(profile, dataset_dir, decimal_mode=True)
    assert result.group_map()[("华北",)] == 0.25
    # Fewer significant digits survive the float path for the same rows.
    floated = calculate_profile(
        replace(profile, decimal_mode=False), dataset_dir
    )
    assert floated.group_map()[("华北",)] != 0.25


def test_decimal_report_is_json_serializable(decimal_env, tmp_path: Path) -> None:
    dataset_dir, profiles = decimal_env
    result = calculate_profile(profiles["profile_decimal"], dataset_dir)
    payload = result.to_dict()
    encoded = render_json(
        {
            "task_id": "kj-decimal",
            "generated_at": None,
            "base_profile": result.profile_id,
            "summary": {"status": "completed"},
            "profiles": [profiles["profile_decimal"].to_dict()],
            "calculations": [payload],
            "comparisons": [],
            "attributions": [],
            "suggestions": [],
        }
    )
    decoded = json.loads(encoded)
    groups = decoded["calculations"][0]["groups"]
    assert [group["key"] for group in groups] == [["华东"], ["华北"]]
    assert [group["value"] for group in groups] == [0.3, 24691357802469134336]
    assert not any(isinstance(value, Decimal) for value in groups)


def test_decimal_mode_aggregation_order_does_not_change_value(decimal_env, tmp_path: Path) -> None:
    dataset_dir, profiles = decimal_env
    shuffled_dir = tmp_path / "shuffled"
    shuffled_dir.mkdir()
    lines = DECIMAL_CSV.strip().splitlines()
    header, body = lines[0], lines[1:]
    reordered = [header]
    for region in ("华北", "华南", "华东"):
        reordered.extend(line for line in body if region in line)
    (shuffled_dir / "decimal.csv").write_text(
        "\n".join(reordered) + "\n", encoding="utf-8"
    )
    original = calculate_profile(profiles["profile_decimal"], dataset_dir)
    reordered = calculate_profile(profiles["profile_decimal"], shuffled_dir)
    assert original.group_map() == reordered.group_map()


def test_decimal_empty_group_still_reports_empty_aggregate(decimal_env, tmp_path: Path) -> None:
    dataset_dir, profiles = decimal_env
    profile = replace(
        profiles["profile_decimal"],
        metric="avg(amount)",
        filter="region == '西南'",
    )
    result = calculate_profile(profile, dataset_dir)
    assert result.stats.groups == 0
    assert result.stats.rows_filtered == 0
    assert result.stats.unparsable_rows == 0
    assert result.group_map() == {}


def test_decimal_and_float_paths_agree_on_normalized_type(tmp_path: Path, profile_factory) -> None:
    dataset_dir = tmp_path / "datasets"
    dataset_dir.mkdir()
    (dataset_dir / "sample.csv").write_text(
        "id,region,status,amount\n"
        "1,华东,paid,0.0000025\n"
        "2,华北,paid,1.0000005\n",
        encoding="utf-8",
    )
    profile = profile_factory("profile_decimal", source="sample.csv")
    floated = calculate_profile(profile, dataset_dir)
    decimalized = calculate_profile(profile, dataset_dir, decimal_mode=True)
    assert floated.group_map() == decimalized.group_map()
    for value in decimalized.group_map().values():
        assert isinstance(value, (int, float)) and not isinstance(value, Decimal)


def test_decimal_partially_invalid_group_counts_only_bad_rows(tmp_path: Path, profile_factory) -> None:
    dataset_dir = tmp_path / "datasets"
    dataset_dir.mkdir()
    (dataset_dir / "sample.csv").write_text(
        "id,region,status,amount\n"
        "1,华东,paid,0.1\n"
        "2,华东,paid,broken\n"
        "3,华北,paid,0.2\n"
        "4,华北,cancelled,0.3\n",
        encoding="utf-8",
    )
    profile = profile_factory(
        "profile_decimal", source="sample.csv", decimal_mode=True
    )
    result = calculate_profile(profile, dataset_dir)
    assert result.group_map() == {("华北",): 0.2}
    assert result.stats.rows_read == 4
    assert result.stats.rows_filtered == 3
    assert result.stats.rows_aggregated == 1
    assert result.stats.unparsable_rows == 2
    assert result.stats.groups == 1
