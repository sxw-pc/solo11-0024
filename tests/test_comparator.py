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
