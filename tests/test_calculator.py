from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from koujing.calculator import CalculationError, calculate_profile
from koujing.comparator import compare_calculations
from koujing.config import DEFAULT_DATASET_DIR, DEFAULT_PROFILE_DIR
from koujing.loader import load_profiles
from koujing.reporter import render_json
from koujing.runner import run_calibration


ROLLUP_CSV = (
    "order_id,region,user_id,amount,status,channel\n"
    "O1,华东,U1,100,paid,web\n"
    "O2,华东,U1,200,paid,web\n"
    "O3,华东,U2,300,paid,web\n"
    "O4,华北,U3,50,paid,web\n"
    "O5,华北,,70,paid,web\n"
    "O6,华南,U4,90,cancelled,web\n"
    "O7,华中,,80,paid,web\n"
    "O8,华中,,60,paid,web\n"
    "O9,华东,U5,999,paid,test\n"
)


@pytest.fixture
def rollup_env(tmp_path: Path, profile_factory):
    dataset_dir = tmp_path / "datasets"
    dataset_dir.mkdir()
    (dataset_dir / "rollup_orders.csv").write_text(ROLLUP_CSV, encoding="utf-8")

    def make(metric: str, **changes):
        profile = profile_factory(
            "profile_rollup",
            source="rollup_orders.csv",
            grain="user",
            filter="status == 'paid'",
            metric=metric,
            dimensions=("region",),
            exclusions=("channel == 'test'",),
            source_grain="order",
            rollup_key="user_id",
            **changes,
        )
        return profile, calculate_profile(profile, dataset_dir)

    return dataset_dir, make


def test_order_to_user_sum_rollup_matches_row_recompute(rollup_env) -> None:
    dataset_dir, calculate = rollup_env
    profile, result = calculate("sum(amount)")
    # 按行重算口径：仅对带用户键的细粒度行按区域求和
    # 华东 = 100 + 200 + 300（O9 被 exclusion 剔除），华北 = O4 的 50（O5 空键不可上卷）
    assert result.group_map() == {("华东",): 600, ("华北",): 50}

    # 用 filter+exclusion 把不可上卷行排除后，普通订单粒度路径与上卷路径结果一致，
    # 证明 sum 上卷等价于同一行集合上的按行重算（sum 可安全合并）
    order_profile = replace(
        profile,
        grain="order",
        filter="status == 'paid' and user_id != ''",
        source_grain=None,
        rollup_key=None,
    )
    order_result = calculate_profile(order_profile, dataset_dir)
    assert order_result.rollup is None
    assert order_result.group_map() == result.group_map()


def test_order_to_user_count_dedup_does_not_double_count(rollup_env) -> None:
    _, calculate = rollup_env
    _, counted = calculate("count(order_id)")
    # U1 在华东有两张订单，用户级去重后只计一次：华东 = {U1, U2} = 2，华北 = {U3} = 1
    assert counted.group_map() == {("华东",): 2, ("华北",): 1}
    # 若错误地把每用户订单数相加，华东会得到 3；这里必须稳定为去重值 2
    assert counted.group_map()[("华东",)] != 3

    _, star_count = calculate("count()")
    assert star_count.group_map() == counted.group_map()

    # count 谓词同样按用户键去重
    _, predicate_count = calculate("count(amount)")
    assert predicate_count.group_map() == {("华东",): 2, ("华北",): 1}


def test_groups_missing_user_key_are_unrollable(rollup_env) -> None:
    _, calculate = rollup_env
    _, result = calculate("sum(amount)")
    trace = result.rollup
    assert trace is not None
    assert trace.source_grain == "order"
    assert trace.grain == "user"
    assert trace.rollup_key == "user_id"
    # O5（华北空键）、O7/O8（华中整组缺键）共 3 行无法上卷，不能静默丢弃
    assert trace.unrollable_rows == 3
    assert trace.rolled_up_rows == result.stats.rows_aggregated == 4
    # 华中整组没有可用用户键，不产生输出分组，但行数已计入不可上卷
    assert ("华中",) not in result.group_map()
    # O6 被 filter 剔除，从未进入分组
    assert ("华南",) not in result.group_map()

    payload = result.to_dict()
    assert list(payload)[-1] == "rollup"
    assert payload["rollup"] == {
        "source_grain": "order",
        "grain": "user",
        "rollup_key": "user_id",
        "unrollable_rows": 3,
        "rolled_up_rows": 4,
    }


def test_distributive_min_max_merge_over_fine_rows(rollup_env) -> None:
    _, calculate = rollup_env
    _, minimum = calculate("min(amount)")
    _, maximum = calculate("max(amount)")
    assert minimum.group_map() == {("华东",): 100, ("华北",): 50}
    assert maximum.group_map() == {("华东",): 300, ("华北",): 50}
    # 合并 min/max 与 count 混合表达式时，count 仍按用户去重：600 / 2 = 300
    _, combined = calculate("sum(amount) / count()")
    assert combined.group_map() == {("华东",): 300, ("华北",): 50}


def test_rollup_preserves_filter_exclude_group_order(rollup_env) -> None:
    _, calculate = rollup_env
    _, result = calculate("count(order_id)")
    stats = result.stats
    assert stats.rows_read == 9
    assert stats.rows_filtered == 8       # O6 被 filter 剔除
    assert stats.rows_excluded == 1       # O9 被 exclusion 剔除
    # 仅有用户键的 O1-O4 进入上卷聚合；O5/O7/O8 计入不可上卷而非聚合行
    assert stats.rows_aggregated == 4
    assert stats.groups == 2
    assert stats.unparsable_rows == 0
    assert result.rollup.unrollable_rows == 3


def test_order_grain_results_remain_byte_identical(tmp_path: Path) -> None:
    # 未声明 source_grain 的既有口径：结果字典不带 rollup 键，端到端报告字节不变
    outcome = run_calibration(
        ["profile_sales_a", "profile_sales_b"],
        DEFAULT_PROFILE_DIR,
        DEFAULT_DATASET_DIR,
        tmp_path / "ledger.db",
        report_dir=tmp_path / "reports",
        write_reports_enabled=True,
    )
    for calculation in outcome.task.calculations:
        payload = calculation.to_dict()
        assert "rollup" not in payload
        assert list(payload) == [
            "profile_id",
            "profile_name",
            "grain",
            "dimensions",
            "stats",
            "groups",
        ]
    expected = (
        Path(__file__).resolve().parents[1] / "fixtures" / "expected" / "sales_gap.json"
    )
    assert render_json(outcome.task) == expected.read_text(encoding="utf-8")


def test_rolled_up_profile_is_comparable_with_native_user_grain(rollup_env) -> None:
    # 上卷后的 user 粒度结果与一份原生 user 粒度数据按同一维度对齐比对
    dataset_dir, calculate = rollup_env
    (dataset_dir / "native_users.csv").write_text(
        "user_id,region,orders,status\n"
        "U1,华东,2,paid\n"
        "U2,华东,1,paid\n"
        "U3,华北,1,paid\n",
        encoding="utf-8",
    )
    _, rolled_up = calculate("count(order_id)")

    profile = replace(
        calculate("count(order_id)")[0],
        id="profile_native",
        source="native_users.csv",
        grain="user",
        metric="count(user_id)",
        exclusions=(),
        source_grain=None,
        rollup_key=None,
    )
    native_result = calculate_profile(profile, dataset_dir)
    assert native_result.group_map() == {("华东",): 2, ("华北",): 1}
    comparison = compare_calculations(native_result, rolled_up)
    assert {item.key: item.within_tolerance for item in comparison.matched} == {
        ("华东",): True,
        ("华北",): True,
    }
    assert not comparison.only_base and not comparison.only_compare


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
