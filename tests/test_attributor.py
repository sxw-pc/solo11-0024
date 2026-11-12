from __future__ import annotations

from dataclasses import replace

from koujing.attributor import (
    attribute_comparison,
    build_suggestions,
    clause_differences,
)
from koujing.comparator import compare_calculations


def test_single_filter_difference(calc_factory, sales_profiles) -> None:
    base = sales_profiles[0]
    other = replace(base, id="profile_other", filter="status == 'settled'")
    comparison = compare_calculations(
        calc_factory(base.id, {("华东",): 100}),
        calc_factory(other.id, {("华东",): 120}),
    )
    attributions = attribute_comparison(base, other, comparison)
    assert len(attributions) == 1
    assert [clue.clause for clue in attributions[0].clues] == ["filter"]
    assert attributions[0].clues[0].affected_groups == ("region=华东",)


def test_multiple_clause_differences(calc_factory, sales_profiles) -> None:
    base = sales_profiles[0]
    other = replace(
        base,
        id="profile_other",
        filter="status == 'settled'",
        metric="avg(amount)",
    )
    comparison = compare_calculations(
        calc_factory(base.id, {("华东",): 100}),
        calc_factory(other.id, {("华东",): 120}),
    )
    attributions = attribute_comparison(base, other, comparison)
    assert [clue.clause for clue in attributions[0].clues] == ["filter", "metric"]
    assert len(attributions[0].clues[0].affected_groups) == 1


def test_no_clause_difference_returns_empty(calc_factory, profile_factory) -> None:
    base = profile_factory("profile_base")
    other = profile_factory("profile_other")
    comparison = compare_calculations(
        calc_factory(base.id, {("华东",): 100}),
        calc_factory(other.id, {("华东",): 120}),
    )
    assert clause_differences(base, other) == []
    assert attribute_comparison(base, other, comparison) == []


def test_all_within_tolerance_returns_empty(calc_factory, sales_profiles) -> None:
    base = sales_profiles[0]
    other = replace(base, id="profile_other", metric="avg(amount)")
    comparison = compare_calculations(
        calc_factory(base.id, {("华东",): 100}),
        calc_factory(other.id, {("华东",): 100}),
    )
    assert comparison.over_count == 0
    assert attribute_comparison(base, other, comparison) == []


def test_only_groups_are_attributed(calc_factory, sales_profiles) -> None:
    base = sales_profiles[0]
    other = replace(base, id="profile_other", exclusions=("channel == 'other'",))
    comparison = compare_calculations(
        calc_factory(base.id, {("华东",): 100, ("西北",): 20}),
        calc_factory(other.id, {("华东",): 100}),
    )
    attributions = attribute_comparison(base, other, comparison)
    assert len(attributions) == 1
    assert attributions[0].group_key == ("西北",)
    assert attributions[0].clues[0].clause == "exclusions"


def test_suggestions_cover_multiple_clauses(calc_factory, sales_profiles) -> None:
    base = sales_profiles[0]
    other = replace(
        base,
        id="profile_other",
        grain="user",
        exclusions=("channel == 'other'",),
    )
    comparison = compare_calculations(
        calc_factory(base.id, {("华东",): 100}),
        calc_factory(other.id, {("华东",): 120}),
    )
    attributions = attribute_comparison(base, other, comparison)
    suggestions = build_suggestions([comparison], attributions)
    assert len(suggestions) == 2
    assert any("exclusions" in item for item in suggestions)
    assert any("grain" in item for item in suggestions)


def test_no_divergence_suggestion(calc_factory, profile_factory) -> None:
    base = profile_factory("profile_base")
    other = profile_factory("profile_other")
    comparison = compare_calculations(
        calc_factory(base.id, {("华东",): 100}),
        calc_factory(other.id, {("华东",): 100}),
    )
    suggestions = build_suggestions([comparison], [])
    assert suggestions == ["所有参与比对的分组均在容差内，当前口径无需调整。"]
