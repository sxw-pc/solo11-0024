from __future__ import annotations

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
