from __future__ import annotations

import pytest

from koujing.comparator import ComparisonError, compare_calculations


def test_all_groups_match(calc_factory) -> None:
    base = calc_factory("a", {("华东",): 100, ("华北",): 200})
    other = calc_factory("b", {("华东",): 100, ("华北",): 200})
    result = compare_calculations(base, other)
    assert len(result.matched) == 2
    assert result.within_count == 2
    assert result.over_count == 0
    assert result.only_base == ()
    assert result.only_compare == ()


def test_only_base_and_only_compare(calc_factory) -> None:
    base = calc_factory("a", {("华东",): 100, ("西北",): 50})
    other = calc_factory("b", {("华东",): 100, ("西南",): 70})
    result = compare_calculations(base, other)
    assert [item.key for item in result.only_base] == [("西北",)]
    assert [item.key for item in result.only_compare] == [("西南",)]
    assert len(result.matched) == 1


def test_absolute_tolerance_over(calc_factory) -> None:
    base = calc_factory("a", {("华东",): 100})
    other = calc_factory("b", {("华东",): 120})
    result = compare_calculations(base, other, tolerance_abs=10, tolerance_rel=0)
    item = result.matched[0]
    assert item.diff == 20
    assert item.within_tolerance is False
    assert item.status == "over_tolerance"


def test_absolute_tolerance_within(calc_factory) -> None:
    base = calc_factory("a", {("华东",): 100})
    other = calc_factory("b", {("华东",): 108})
    result = compare_calculations(base, other, tolerance_abs=10, tolerance_rel=0)
    assert result.matched[0].within_tolerance is True


def test_relative_tolerance_boundary_is_inclusive(calc_factory) -> None:
    base = calc_factory("a", {("华东",): 100})
    other = calc_factory("b", {("华东",): 110})
    result = compare_calculations(base, other, tolerance_abs=0, tolerance_rel=0.1)
    item = result.matched[0]
    assert item.diff_rate == 0.1
    assert item.within_tolerance is True


def test_relative_tolerance_over(calc_factory) -> None:
    base = calc_factory("a", {("华东",): 100})
    other = calc_factory("b", {("华东",): 123})
    result = compare_calculations(base, other, tolerance_abs=0, tolerance_rel=0.2)
    assert result.matched[0].within_tolerance is False


def test_zero_denominator_is_marked(calc_factory) -> None:
    base = calc_factory("a", {("华东",): 0})
    other = calc_factory("b", {("华东",): 5})
    result = compare_calculations(base, other, tolerance_abs=0, tolerance_rel=0)
    item = result.matched[0]
    assert item.diff_rate is None
    assert item.base_zero is True
    assert item.within_tolerance is False
    assert result.to_dict()["matched"][0]["diff_rate"] is None


def test_zero_denominator_within_absolute_tolerance(calc_factory) -> None:
    base = calc_factory("a", {("华东",): 0})
    other = calc_factory("b", {("华东",): 5})
    result = compare_calculations(base, other, tolerance_abs=10, tolerance_rel=0)
    assert result.matched[0].diff_rate is None
    assert result.matched[0].within_tolerance is True


def test_zero_difference_on_zero_base(calc_factory) -> None:
    base = calc_factory("a", {("华东",): 0})
    other = calc_factory("b", {("华东",): 0})
    item = compare_calculations(base, other).matched[0]
    assert item.diff_rate == 0
    assert item.base_zero is True
    assert item.within_tolerance is True


def test_dimension_mismatch_raises(calc_factory) -> None:
    base = calc_factory("a", {("华东",): 1}, dimensions=("region",))
    other = calc_factory("b", {("web",): 1}, dimensions=("channel",))
    with pytest.raises(ComparisonError) as caught:
        compare_calculations(base, other)
    assert caught.value.code == "dimension_mismatch"


def test_joint_compares_on_common_dimensions(calc_factory) -> None:
    base = calc_factory(
        "a", {("华东",): 100, ("华北",): 200}, dimensions=("region",)
    )
    other = calc_factory(
        "b",
        {("华东", "web"): 60, ("华东", "app"): 40, ("华北", "web"): 200},
        dimensions=("region", "channel"),
    )
    result = compare_calculations(base, other, joint=True)
    assert result.dimensions == ("region",)
    assert [item.key for item in result.matched] == [("华东",), ("华北",)]
    east = result.matched[0]
    assert east.base_value == 100
    assert east.compare_value == 100
    assert result.within_count == 2
    assert result.only_base == ()
    assert result.only_compare == ()
    alignment = result.alignment
    assert alignment is not None
    assert alignment.mode == "common"
    assert alignment.common_dimensions == ("region",)
    assert alignment.base_extra_dimensions == ()
    assert alignment.compare_extra_dimensions == ("channel",)
    assert alignment.base_source_groups == 2
    assert alignment.compare_source_groups == 3
    assert alignment.base_collapsed_groups == 0
    assert alignment.compare_collapsed_groups == 1
    assert alignment.compare_recovered_groups == 0
    assert alignment.compare_unmapped_groups == 0


def test_joint_aligns_reordered_dimensions(calc_factory) -> None:
    base = calc_factory(
        "a", {("华东", "web"): 100}, dimensions=("region", "channel")
    )
    other = calc_factory(
        "b", {("web", "华东"): 100}, dimensions=("channel", "region")
    )
    result = compare_calculations(base, other, joint=True)
    assert result.dimensions == ("region", "channel")
    assert len(result.matched) == 1
    assert result.matched[0].within_tolerance is True
    alignment = result.alignment
    assert alignment is not None
    assert alignment.mode == "common"
    assert alignment.common_dimensions == ("region", "channel")
    assert alignment.base_collapsed_groups == 0
    assert alignment.compare_collapsed_groups == 0


def test_joint_without_common_dimensions_still_raises(calc_factory) -> None:
    base = calc_factory("a", {("华东",): 1}, dimensions=("region",))
    other = calc_factory("b", {("web",): 1}, dimensions=("channel",))
    with pytest.raises(ComparisonError) as caught:
        compare_calculations(base, other, joint=True)
    assert caught.value.code == "dimension_mismatch"


def test_joint_recovers_rows_missing_dimension_values(calc_factory) -> None:
    base = calc_factory(
        "a", {("华东",): 100, ("华南",): 30}, dimensions=("region",)
    )
    other = calc_factory(
        "b",
        {("华东", "web", "s1"): 60, ("华东", "app", "s2"): 40, ("华南",): 30},
        dimensions=("region", "channel", "sku"),
    )
    result = compare_calculations(base, other, joint=True)
    assert result.within_count == 2
    assert result.only_base == ()
    assert result.only_compare == ()
    alignment = result.alignment
    assert alignment is not None
    assert alignment.compare_source_groups == 3
    assert alignment.compare_recovered_groups == 1
    assert alignment.compare_unmapped_groups == 0
    assert alignment.compare_collapsed_groups == 1
    south = next(item for item in result.matched if item.key == ("华南",))
    assert south.compare_value == 30


def test_joint_counts_unmapped_groups(calc_factory) -> None:
    base = calc_factory("a", {("web",): 100}, dimensions=("channel",))
    other = calc_factory(
        "b",
        {("华东", "web"): 60, ("华南", "web"): 40, ("华北",): 10},
        dimensions=("region", "channel"),
    )
    result = compare_calculations(base, other, joint=True)
    assert len(result.matched) == 1
    assert result.matched[0].key == ("web",)
    assert result.matched[0].compare_value == 100
    alignment = result.alignment
    assert alignment is not None
    assert alignment.compare_recovered_groups == 0
    assert alignment.compare_unmapped_groups == 1
    assert alignment.compare_collapsed_groups == 1


def test_joint_with_identical_dimensions_marks_exact(calc_factory) -> None:
    base = calc_factory("a", {("华东",): 100})
    other = calc_factory("b", {("华东",): 100})
    result = compare_calculations(base, other, joint=True)
    alignment = result.alignment
    assert alignment is not None
    assert alignment.mode == "exact"
    assert alignment.common_dimensions == ("region",)
    assert alignment.base_source_groups == 1
    assert alignment.compare_collapsed_groups == 0


def test_joint_result_serializes_alignment(calc_factory) -> None:
    base = calc_factory("a", {("华东",): 100}, dimensions=("region",))
    other = calc_factory(
        "b", {("华东", "web"): 100}, dimensions=("region", "channel")
    )
    result = compare_calculations(base, other, joint=True)
    payload = result.to_dict()
    alignment = payload["alignment"]
    assert alignment["mode"] == "common"
    assert alignment["common_dimensions"] == ["region"]
    assert alignment["compare_extra_dimensions"] == ["channel"]
    assert alignment["compare_source_groups"] == 1
    assert alignment["compare_collapsed_groups"] == 0
    summary = result.summary()
    assert summary["matched_groups"] == 1
    assert summary["compare_source_groups"] == 1
    assert summary["compare_recovered_groups"] == 0
    assert summary["compare_unmapped_groups"] == 0
    assert payload["matched"][0]["label"] == "region=华东"


def test_alignment_absent_without_joint(calc_factory) -> None:
    base = calc_factory("a", {("华东",): 100})
    other = calc_factory("b", {("华东",): 100})
    result = compare_calculations(base, other)
    assert result.alignment is None
    assert "alignment" not in result.to_dict()
    assert result.summary() == {
        "matched_groups": 1,
        "within_tolerance_groups": 1,
        "over_tolerance_groups": 0,
        "only_base_groups": 0,
        "only_compare_groups": 0,
    }


def test_negative_tolerance_raises(calc_factory) -> None:
    base = calc_factory("a", {("华东",): 1})
    other = calc_factory("b", {("华东",): 1})
    with pytest.raises(ComparisonError) as caught:
        compare_calculations(base, other, tolerance_abs=-1)
    assert caught.value.code == "invalid_tolerance"


def test_summary_and_serialized_status(calc_factory) -> None:
    base = calc_factory("a", {("华东",): 100, ("西北",): 1})
    other = calc_factory("b", {("华东",): 200, ("西南",): 2})
    result = compare_calculations(base, other, tolerance_abs=0, tolerance_rel=0)
    assert result.summary() == {
        "matched_groups": 1,
        "within_tolerance_groups": 0,
        "over_tolerance_groups": 1,
        "only_base_groups": 1,
        "only_compare_groups": 1,
    }
    payload = result.to_dict()
    assert payload["matched"][0]["status"] == "over_tolerance"
    assert payload["only_base"][0]["label"] == "region=西北"
