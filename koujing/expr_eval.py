"""Evaluation half of the safe expression engine.

The parser lives in :mod:`koujing.expression`; keeping evaluation separate
ensures every source file stays reviewable and under the project line limit.
"""

from __future__ import annotations

import math
from decimal import Decimal, InvalidOperation, localcontext
from typing import Any, Mapping, Sequence

from .expression import Node, _error, parse_expression
from .models import Number, normalize_number

# Decimal arithmetic is context-rounded, so give it enough headroom to keep the
# six reported decimal places significant even for long CSV values.
_DECIMAL_PRECISION = 200


def _number(value: Any) -> Number:
    """Historical float-mode number parsing (also the shared syntax gate)."""
    if isinstance(value, bool):
        raise _error("type_mismatch", "布尔值不能参与数值运算")
    if isinstance(value, (int, float)):
        if isinstance(value, float) and not math.isfinite(value):
            raise _error("invalid_number", "NaN 和无穷大不受支持")
        return value
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            raise _error("type_mismatch", "空值不能参与数值运算")
        try:
            return int(stripped)
        except ValueError:
            try:
                number = float(stripped)
            except ValueError as exc:
                raise _error("type_mismatch", f"无法将 {value!r} 当作数字") from exc
            if not math.isfinite(number):
                raise _error("invalid_number", "NaN 和无穷大不受支持")
            return number
    raise _error("type_mismatch", f"不支持的值类型 {type(value).__name__}")


def _decimal(value: Any) -> Decimal:
    """Parse a scalar into Decimal without ever going through binary float."""
    if isinstance(value, bool):
        raise _error("type_mismatch", "布尔值不能参与数值运算")
    if isinstance(value, Decimal):
        parsed = value
    elif isinstance(value, int):
        parsed = Decimal(value)
    elif isinstance(value, float):
        # Validate through the float path so both modes reject the same
        # non-finite inputs; str() keeps the shortest exact decimal form.
        _number(value)
        parsed = Decimal(str(value))
    elif isinstance(value, str):
        # Validate syntax with the float path so both modes agree on what is a
        # CSV number ("1_000" is accepted, "1__0" is not); Decimal then keeps
        # the exact value instead of the rounded double.
        _number(value)
        try:
            parsed = Decimal(value.strip())
        except InvalidOperation as exc:
            # float accepted the text, so this is unexpectedly exotic syntax.
            raise _error("type_mismatch", f"无法将 {value!r} 当作数字") from exc
    else:
        raise _error("type_mismatch", f"不支持的值类型 {type(value).__name__}")
    if not parsed.is_finite():
        raise _error("invalid_number", "NaN 和无穷大不受支持")
    return parsed


def _numeric(value: Any, decimal_mode: bool) -> Number | Decimal:
    """Return the working number type for the selected evaluation mode."""
    return _decimal(value) if decimal_mode else _number(value)


def _result(value: Number | Decimal, decimal_mode: bool) -> Number | Decimal:
    """Keep Decimal internally in decimal mode; normalize float mode eagerly.

    Decimal values are converted back to the public ``int``/``float`` contract
    only at the outer :func:`evaluate` boundary, so a chained metric such as
    ``sum(amount) - sum(refund)`` never loses precision between steps.
    """
    return value if decimal_mode else normalize_number(value)


def _boolean(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    raise _error("type_mismatch", f"需要布尔值，实际为 {type(value).__name__}")


def _equal(left: Any, right: Any, decimal_mode: bool = False) -> bool:
    if isinstance(left, str) and isinstance(right, str):
        return left == right
    if isinstance(left, bool) or isinstance(right, bool):
        if isinstance(left, bool) and isinstance(right, bool):
            return left is right
        raise _error("type_mismatch", "布尔值只能与布尔值比较")
    return _numeric(left, decimal_mode) == _numeric(right, decimal_mode)


def _ordered(left: Any, right: Any, decimal_mode: bool = False) -> tuple[Any, Any]:
    if isinstance(left, str) and isinstance(right, str):
        return left, right
    return _numeric(left, decimal_mode), _numeric(right, decimal_mode)


def _evaluate_call(
    node: Node,
    rows: Sequence[Mapping[str, Any]] | None,
    decimal_mode: bool,
) -> Any:
    if rows is None:
        raise _error("aggregate_context", "聚合函数只能在指标计算阶段使用", node.position)
    name = node.value
    if name == "count":
        if not node.children:
            return len(rows)
        return sum(
            1
            for row in rows
            if _evaluate_node(node.children[0], row, None, decimal_mode) is not None
        )
    values = [
        _numeric(_evaluate_node(node.children[0], row, None, decimal_mode), decimal_mode)
        for row in rows
    ]
    if name == "sum":
        # Decimal accumulation is order-stable and never inherits binary-float
        # drift, so shuffled CSV rows still normalize to the same value.
        return _result(sum(values, Decimal(0) if decimal_mode else 0), decimal_mode)
    if not values:
        raise _error("empty_aggregate", f"{name} 无法聚合空分组", node.position)
    if name == "avg":
        total = sum(values, Decimal(0) if decimal_mode else 0)
        return _result(total / len(values), decimal_mode)
    if name == "min":
        return _result(min(values), decimal_mode)
    return _result(max(values), decimal_mode)


def _evaluate_node(
    node: Node,
    row: Mapping[str, Any] | None,
    rows: Sequence[Mapping[str, Any]] | None,
    decimal_mode: bool,
) -> Any:
    if node.kind == "number":
        if decimal_mode and node.raw is not None:
            # Use the source text so a long literal keeps every digit instead
            # of inheriting the float already rounded by the tokenizer.
            return _decimal(node.raw)
        return node.value
    if node.kind in {"string", "bool"}:
        return node.value
    if node.kind == "list":
        return [_evaluate_node(child, row, rows, decimal_mode) for child in node.children]
    if node.kind == "column":
        if row is None:
            raise _error("column_outside_aggregate", f"列 {node.value!r} 必须位于聚合函数内", node.position)
        if node.value not in row:
            raise _error("unknown_column", f"行数据中不存在列 {node.value!r}", node.position)
        return row[node.value]
    if node.kind == "call":
        return _evaluate_call(node, rows, decimal_mode)
    if node.kind == "unary":
        value = _evaluate_node(node.children[0], row, rows, decimal_mode)
        if node.value == "not":
            return not _boolean(value)
        number = _numeric(value, decimal_mode)
        return _result(-number if node.value == "-" else number, decimal_mode)
    left = _evaluate_node(node.children[0], row, rows, decimal_mode)
    operator = node.value
    if operator == "and":
        return _boolean(left) and _boolean(
            _evaluate_node(node.children[1], row, rows, decimal_mode)
        )
    if operator == "or":
        return _boolean(left) or _boolean(
            _evaluate_node(node.children[1], row, rows, decimal_mode)
        )
    right = _evaluate_node(node.children[1], row, rows, decimal_mode)
    if operator == "in":
        if not isinstance(right, list):
            raise _error("type_mismatch", "in 的右侧必须是列表", node.position)
        return any(_equal(left, item, decimal_mode) for item in right)
    if operator == "==":
        return _equal(left, right, decimal_mode)
    if operator == "!=":
        return not _equal(left, right, decimal_mode)
    if operator in {"<", "<=", ">", ">="}:
        left, right = _ordered(left, right, decimal_mode)
        return {"<": left < right, "<=": left <= right, ">": left > right, ">=": left >= right}[operator]
    left_number = _numeric(left, decimal_mode)
    right_number = _numeric(right, decimal_mode)
    zero = Decimal(0) if decimal_mode else 0
    if operator == "/" and right_number == zero:
        raise _error("division_by_zero", "除数不能为 0", node.position)
    result = {
        "+": left_number + right_number,
        "-": left_number - right_number,
        "*": left_number * right_number,
        "/": left_number / right_number if right_number != zero else zero,
    }[operator]
    return _result(result, decimal_mode)


def _evaluate(
    node: Node,
    row: Mapping[str, Any] | None,
    rows: Sequence[Mapping[str, Any]] | None,
    decimal_mode: bool = False,
) -> Any:
    if not decimal_mode:
        return _evaluate_node(node, row, rows, False)
    with localcontext() as context:
        context.prec = _DECIMAL_PRECISION
        value = _evaluate_node(node, row, rows, True)
    # The public contract stays int/float; Decimal never leaves the engine.
    return normalize_number(value) if isinstance(value, Decimal) else value


def evaluate(
    node: Node,
    row: Mapping[str, Any] | None = None,
    rows: Sequence[Mapping[str, Any]] | None = None,
    decimal_mode: bool = False,
) -> Any:
    return _evaluate(node, row, rows, decimal_mode)


def evaluate_expression(
    text: str,
    row: Mapping[str, Any] | None = None,
    rows: Sequence[Mapping[str, Any]] | None = None,
    decimal_mode: bool = False,
) -> Any:
    return _evaluate(parse_expression(text), row, rows, decimal_mode)
