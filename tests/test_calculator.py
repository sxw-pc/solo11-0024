from __future__ import annotations

from dataclasses import replace

import pytest

from koujing.calculator import CalculationError, calculate_profile
from koujing.loader import load_profiles


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
