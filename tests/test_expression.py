from __future__ import annotations

import math
from decimal import Decimal

import pytest

from koujing.expr_eval import evaluate, evaluate_expression
from koujing.expression import (
    ExpressionError,
    has_aggregate,
    parse_expression,
    referenced_columns,
    validate_expression,
)


def test_arithmetic_precedence() -> None:
    assert evaluate_expression("2 + 3 * 4") == 14
    assert evaluate_expression("(2 + 3) * 4") == 20
    assert evaluate_expression("-(2 + 3) * 2") == -10


def test_numeric_strings_from_csv() -> None:
    row = {"amount": "1500", "quantity": "2"}
    assert evaluate_expression("amount * quantity + 100", row=row) == 3100


def test_comparisons_and_boolean_operators() -> None:
    row = {"status": "paid", "amount": "1200"}
    assert evaluate_expression("status == 'paid' and amount >= 1000", row=row) is True
    assert evaluate_expression("status != 'paid' or amount < 1000", row=row) is False


def test_in_list_and_not() -> None:
    row = {"region": "华东", "enabled": "1"}
    assert evaluate_expression("region in ['华东', '华南']", row=row) is True
    assert evaluate_expression("not (region in ['华北'])", row=row) is True
    assert evaluate_expression("region in []", row=row) is False


def test_boolean_literals_and_nested_logic() -> None:
    assert evaluate_expression("true and not false or false") is True
    assert evaluate_expression("(true or false) and false") is False


def test_short_circuit_and_skips_failure() -> None:
    assert evaluate_expression("false and (1 / 0 > 1)") is False


def test_short_circuit_or_skips_unknown_column() -> None:
    assert evaluate_expression("true or missing_column > 1") is True


def test_division_by_zero_is_structured() -> None:
    with pytest.raises(ExpressionError) as caught:
        evaluate_expression("10 / (2 - 2)")
    assert caught.value.code == "division_by_zero"
    assert caught.value.to_dict()["code"] == "division_by_zero"


def test_unknown_column_at_runtime() -> None:
    with pytest.raises(ExpressionError) as caught:
        evaluate_expression("missing_column + 1", row={"amount": "1"})
    assert caught.value.code == "unknown_column"


def test_validation_unknown_column() -> None:
    with pytest.raises(ExpressionError) as caught:
        validate_expression("sum(missing)", ["amount"])
    assert caught.value.code == "unknown_column"


def test_type_mismatch() -> None:
    with pytest.raises(ExpressionError) as caught:
        evaluate_expression("region + 1", row={"region": "华东"})
    assert caught.value.code == "type_mismatch"


def test_syntax_error_and_position() -> None:
    with pytest.raises(ExpressionError) as caught:
        parse_expression("1 +")
    assert caught.value.code == "unexpected_token"
    assert caught.value.position is not None


def test_unknown_function_rejected() -> None:
    with pytest.raises(ExpressionError) as caught:
        parse_expression("median(amount)")
    assert caught.value.code == "unknown_function"


def test_aggregate_functions() -> None:
    rows = [{"amount": "100"}, {"amount": "300"}, {"amount": "200"}]
    assert evaluate_expression("sum(amount)", rows=rows) == 600
    assert evaluate_expression("count()", rows=rows) == 3
    assert evaluate_expression("count(amount)", rows=rows) == 3
    assert evaluate_expression("avg(amount)", rows=rows) == 200
    assert evaluate_expression("min(amount)", rows=rows) == 100
    assert evaluate_expression("max(amount)", rows=rows) == 300


def test_aggregate_arithmetic_and_empty_sum() -> None:
    rows = [{"amount": "100"}, {"amount": "200"}]
    assert evaluate_expression("sum(amount) / count()", rows=rows) == 150
    assert evaluate_expression("sum(amount)", rows=[]) == 0


def test_aggregate_requires_group_context() -> None:
    node = parse_expression("sum(amount)")
    with pytest.raises(ExpressionError) as caught:
        evaluate(node, row={"amount": "1"})
    assert caught.value.code == "aggregate_context"


def test_nested_aggregate_rejected() -> None:
    with pytest.raises(ExpressionError) as caught:
        validate_expression("sum(avg(amount))", ["amount"])
    assert caught.value.code == "nested_aggregate"


def test_aggregate_rejected_in_row_expression() -> None:
    with pytest.raises(ExpressionError) as caught:
        validate_expression("sum(amount) > 1", ["amount"], allow_aggregates=False)
    assert caught.value.code == "aggregate_not_allowed"


def test_metric_requires_aggregate() -> None:
    with pytest.raises(ExpressionError) as caught:
        validate_expression("amount * 2", ["amount"], require_aggregate=True)
    assert caught.value.code == "aggregate_required"


def test_expression_metadata_helpers() -> None:
    node = parse_expression("sum(amount) + tax")
    assert has_aggregate(node) is True
    assert referenced_columns(node) == ("amount", "tax")
    assert has_aggregate(parse_expression("amount + tax")) is False


def test_decimal_high_precision_literal_keeps_all_digits() -> None:
    rows = [{"amount": "0"}]
    assert (
        evaluate_expression(
            "sum(amount) + 12345678901234567890.125",
            rows=rows,
            decimal_mode=True,
        )
        == float(Decimal("12345678901234567890.125"))
    )
    # The same literal without decimal mode retains the historical float parse.
    assert evaluate_expression("12345678901234567890.125") == float(
        "12345678901234567890.125"
    )


def test_decimal_sum_removes_binary_float_drift() -> None:
    rows = [{"amount": "0.1"}, {"amount": "0.2"}]
    assert evaluate_expression("sum(amount)", rows=rows) == 0.3
    decimal_value = evaluate_expression("sum(amount)", rows=rows, decimal_mode=True)
    assert decimal_value == 0.3
    assert isinstance(decimal_value, float)
    assert evaluate_expression("sum(amount) + 0.1 + 0.2", rows=rows, decimal_mode=True) == 0.6


def test_decimal_accumulation_does_not_overflow_float_precision() -> None:
    rows = [
        {"amount": "12345678901234567890.12"},
        {"amount": "12345678901234567890.13"},
    ]
    # Higher digits than a double can carry are lost on the float path, while
    # the decimal path keeps the exact subtraction result.
    assert evaluate_expression("sum(amount) / 2", rows=rows) == evaluate_expression(
        "sum(amount) / 2", rows=rows
    )
    exact = evaluate_expression(
        "sum(amount) - 24691357802469135780", rows=rows, decimal_mode=True
    )
    drifted = evaluate_expression("sum(amount) - 24691357802469135780", rows=rows)
    assert exact == 0.25
    assert drifted != exact


def test_decimal_accumulation_is_order_independent() -> None:
    rows = [
        {"amount": "10000000.1"},
        {"amount": "0.2"},
        {"amount": "0.05"},
    ]
    expected = evaluate_expression("sum(amount)", rows=rows, decimal_mode=True)
    for permutation in (
        [rows[1], rows[2], rows[0]],
        [rows[2], rows[0], rows[1]],
        list(reversed(rows)),
    ):
        value = evaluate_expression("sum(amount)", rows=permutation, decimal_mode=True)
        assert value == expected == 10000000.35
        assert not math.isclose(value, 10000000.350000001, rel_tol=0, abs_tol=1e-9)
    float_total = evaluate_expression(
        "sum(amount)", rows=[{"amount": "10000000.1"}] * 100
    )
    decimal_total = evaluate_expression(
        "sum(amount)", rows=[{"amount": "10000000.1"}] * 100, decimal_mode=True
    )
    assert float_total != decimal_total
    assert decimal_total == 1000000010


def test_decimal_average_and_division_use_decimal_semantics() -> None:
    rows = [{"amount": "0.1"}, {"amount": "0.2"}, {"amount": "0.3"}]
    assert evaluate_expression("avg(amount)", rows=rows, decimal_mode=True) == 0.2
    assert (
        evaluate_expression("sum(amount) / count()", rows=rows, decimal_mode=True) == 0.2
    )
    assert (
        evaluate_expression("sum(amount) * 0.1", rows=rows, decimal_mode=True) == 0.06
    )


def test_decimal_comparisons_stay_numeric() -> None:
    rows = [
        {"amount": "12345678901234567890.12"},
        {"amount": "12345678901234567890.13"},
    ]
    assert (
        evaluate_expression(
            "avg(amount) == 12345678901234567890.125", rows=rows, decimal_mode=True
        )
        is True
    )
    assert (
        evaluate_expression(
            "avg(amount) != 12345678901234567890.125", rows=rows, decimal_mode=True
        )
        is False
    )
    assert (
        evaluate_expression(
            "sum(amount) >= 24691357802469135780.25", rows=rows, decimal_mode=True
        )
        is True
    )


def test_decimal_mode_returns_only_int_or_float() -> None:
    cases = [
        ("sum(amount)", [{"amount": "0.1"}, {"amount": "0.2"}]),
        ("avg(amount)", [{"amount": "0.1"}, {"amount": "0.2"}]),
        ("min(amount)", [{"amount": "12345678901234567890.12"}]),
        ("max(amount)", [{"amount": "1"}, {"amount": "2"}]),
        ("sum(amount) - 0.1", [{"amount": "0.2"}]),
        ("sum(amount)", []),
        ("count()", [{"amount": "1"}]),
    ]
    for text, rows in cases:
        value = evaluate_expression(text, rows=rows, decimal_mode=True)
        assert isinstance(value, (int, float)) and not isinstance(value, bool), text
        assert not isinstance(value, Decimal)


def test_decimal_empty_aggregate_still_structured() -> None:
    with pytest.raises(ExpressionError) as caught:
        evaluate_expression("avg(amount)", rows=[], decimal_mode=True)
    assert caught.value.code == "empty_aggregate"
    with pytest.raises(ExpressionError) as caught:
        evaluate_expression("min(amount)", rows=[], decimal_mode=True)
    assert caught.value.code == "empty_aggregate"
    assert evaluate_expression("sum(amount)", rows=[], decimal_mode=True) == 0


def test_decimal_invalid_value_is_structured() -> None:
    rows = [{"amount": "1.5"}, {"amount": "not-a-number"}]
    with pytest.raises(ExpressionError) as caught:
        evaluate_expression("sum(amount)", rows=rows, decimal_mode=True)
    assert caught.value.code == "type_mismatch"
    for invalid in ("NaN", "Infinity", "1__0", "1e400", ""):
        with pytest.raises(ExpressionError):
            evaluate_expression(
                "sum(amount)", rows=[{"amount": invalid}], decimal_mode=True
            )
    # Both modes accept the same numeric string syntax, including underscores.
    assert (
        evaluate_expression("sum(amount)", rows=[{"amount": "1_000.5"}], decimal_mode=True)
        == 1000.5
    )


def test_decimal_mode_keeps_boolean_semantics() -> None:
    row = {"status": "paid", "amount": "1200"}
    assert (
        evaluate_expression(
            "status == 'paid' and amount >= 1000", row=row, decimal_mode=True
        )
        is True
    )
    with pytest.raises(ExpressionError) as caught:
        evaluate_expression("10 / (2 - 2)", decimal_mode=True)
    assert caught.value.code == "division_by_zero"


def test_evaluate_node_accepts_decimal_mode() -> None:
    node = parse_expression("sum(amount)")
    rows = [{"amount": "0.1"}, {"amount": "0.2"}]
    assert evaluate(node, rows=rows) == 0.3
    assert evaluate(node, rows=rows, decimal_mode=True) == 0.3
    assert isinstance(evaluate(node, rows=rows, decimal_mode=True), float)
