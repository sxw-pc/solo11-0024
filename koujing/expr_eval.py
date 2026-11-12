"""Evaluation half of the safe expression engine.

The parser lives in :mod:`koujing.expression`; keeping evaluation separate
ensures every source file stays reviewable and under the project line limit.
"""

from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

from .expression import Node, _error, parse_expression
from .models import Number, normalize_number


def _number(value: Any) -> Number:
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


def _boolean(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    raise _error("type_mismatch", f"需要布尔值，实际为 {type(value).__name__}")


def _equal(left: Any, right: Any) -> bool:
    if isinstance(left, str) and isinstance(right, str):
        return left == right
    if isinstance(left, bool) or isinstance(right, bool):
        if isinstance(left, bool) and isinstance(right, bool):
            return left is right
        raise _error("type_mismatch", "布尔值只能与布尔值比较")
    return _number(left) == _number(right)


def _ordered(left: Any, right: Any) -> tuple[Any, Any]:
    if isinstance(left, str) and isinstance(right, str):
        return left, right
    return _number(left), _number(right)


def _evaluate_call(node: Node, rows: Sequence[Mapping[str, Any]] | None) -> Any:
    if rows is None:
        raise _error("aggregate_context", "聚合函数只能在指标计算阶段使用", node.position)
    name = node.value
    if name == "count":
        if not node.children:
            return len(rows)
        return sum(1 for row in rows if _evaluate(node.children[0], row, None) is not None)
    values = [_number(_evaluate(node.children[0], row, None)) for row in rows]
    if name == "sum":
        return normalize_number(sum(values))
    if not values:
        raise _error("empty_aggregate", f"{name} 无法聚合空分组", node.position)
    if name == "avg":
        return normalize_number(sum(values) / len(values))
    if name == "min":
        return normalize_number(min(values))
    return normalize_number(max(values))


def _evaluate(
    node: Node,
    row: Mapping[str, Any] | None,
    rows: Sequence[Mapping[str, Any]] | None,
) -> Any:
    if node.kind in {"number", "string", "bool"}:
        return node.value
    if node.kind == "list":
        return [_evaluate(child, row, rows) for child in node.children]
    if node.kind == "column":
        if row is None:
            raise _error("column_outside_aggregate", f"列 {node.value!r} 必须位于聚合函数内", node.position)
        if node.value not in row:
            raise _error("unknown_column", f"行数据中不存在列 {node.value!r}", node.position)
        return row[node.value]
    if node.kind == "call":
        return _evaluate_call(node, rows)
    if node.kind == "unary":
        value = _evaluate(node.children[0], row, rows)
        if node.value == "not":
            return not _boolean(value)
        number = _number(value)
        return normalize_number(-number if node.value == "-" else number)
    left = _evaluate(node.children[0], row, rows)
    operator = node.value
    if operator == "and":
        return _boolean(left) and _boolean(_evaluate(node.children[1], row, rows))
    if operator == "or":
        return _boolean(left) or _boolean(_evaluate(node.children[1], row, rows))
    right = _evaluate(node.children[1], row, rows)
    if operator == "in":
        if not isinstance(right, list):
            raise _error("type_mismatch", "in 的右侧必须是列表", node.position)
        return any(_equal(left, item) for item in right)
    if operator == "==":
        return _equal(left, right)
    if operator == "!=":
        return not _equal(left, right)
    if operator in {"<", "<=", ">", ">="}:
        left, right = _ordered(left, right)
        return {"<": left < right, "<=": left <= right, ">": left > right, ">=": left >= right}[operator]
    left_number, right_number = _number(left), _number(right)
    if operator == "/" and right_number == 0:
        raise _error("division_by_zero", "除数不能为 0", node.position)
    result = {
        "+": left_number + right_number,
        "-": left_number - right_number,
        "*": left_number * right_number,
        "/": left_number / right_number if right_number != 0 else 0,
    }[operator]
    return normalize_number(result)


def evaluate(
    node: Node,
    row: Mapping[str, Any] | None = None,
    rows: Sequence[Mapping[str, Any]] | None = None,
) -> Any:
    return _evaluate(node, row, rows)


def evaluate_expression(
    text: str,
    row: Mapping[str, Any] | None = None,
    rows: Sequence[Mapping[str, Any]] | None = None,
) -> Any:
    return _evaluate(parse_expression(text), row, rows)
