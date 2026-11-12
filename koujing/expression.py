"""A deliberately small, safe expression parser and evaluator.

Supported: numbers, strings, booleans, column references, + - * /, comparisons,
and / or / not, in, lists, parentheses, and the aggregate functions
sum / count / avg / min / max.  Python's eval and exec are never used.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Sequence

AGGREGATES = frozenset({"sum", "count", "avg", "min", "max"})
_COMPARISONS = frozenset({"==", "!=", "<", "<=", ">", ">=", "in"})


class ExpressionError(Exception):
    """Structured expression failure that callers can serialize safely."""

    def __init__(self, code: str, message: str, position: int | None = None):
        self.code = code
        self.message = message
        self.position = position
        super().__init__(message)

    def __str__(self) -> str:
        suffix = f" (位置 {self.position})" if self.position is not None else ""
        return f"{self.code}: {self.message}{suffix}"

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "position": self.position}


@dataclass(frozen=True)
class Token:
    kind: str
    value: Any
    position: int


@dataclass(frozen=True)
class Node:
    kind: str
    value: Any = None
    children: tuple["Node", ...] = ()
    position: int | None = None


def _error(code: str, message: str, position: int | None = None) -> ExpressionError:
    return ExpressionError(code, message, position)


def tokenize(text: str) -> list[Token]:
    if not isinstance(text, str):
        raise _error("invalid_expression", "表达式必须是字符串")
    tokens: list[Token] = []
    index, size = 0, len(text)
    while index < size:
        char = text[index]
        if char.isspace():
            index += 1
            continue
        position = index
        if char.isdigit() or (char == "." and index + 1 < size and text[index + 1].isdigit()):
            has_dot = False
            while index < size and (text[index].isdigit() or text[index] == "."):
                if text[index] == ".":
                    if has_dot:
                        raise _error("invalid_number", "数字格式错误", position)
                    has_dot = True
                index += 1
            raw = text[position:index]
            try:
                value = float(raw) if has_dot else int(raw)
            except ValueError as exc:
                raise _error("invalid_number", f"无法解析数字 {raw}", position) from exc
            tokens.append(Token("NUMBER", value, position))
        elif char.isalpha() or char == "_":
            while index < size and (text[index].isalnum() or text[index] == "_"):
                index += 1
            tokens.append(Token("IDENT", text[position:index], position))
        elif char in {"'", '"'}:
            quote, index = char, index + 1
            chars: list[str] = []
            while index < size and text[index] != quote:
                if text[index] == "\\":
                    index += 1
                    if index >= size:
                        raise _error("invalid_string", "字符串转义不完整", position)
                    chars.append({"n": "\n", "t": "\t"}.get(text[index], text[index]))
                else:
                    chars.append(text[index])
                index += 1
            if index >= size:
                raise _error("invalid_string", "字符串缺少结束引号", position)
            index += 1
            tokens.append(Token("STRING", "".join(chars), position))
        elif text[index:index + 2] in {"==", "!=", "<=", ">="}:
            tokens.append(Token("OP", text[index:index + 2], position))
            index += 2
        elif char in "+-*/(),[]<>":
            tokens.append(Token("OP", char, position))
            index += 1
        else:
            raise _error("unexpected_character", f"不支持的字符 {char!r}", position)
    tokens.append(Token("EOF", "<结束>", size))
    return tokens


class _Parser:
    def __init__(self, tokens: Sequence[Token]):
        self.tokens = list(tokens)
        self.index = 0

    @property
    def current(self) -> Token:
        return self.tokens[self.index]

    def _match(self, value: str) -> bool:
        if self.current.value == value:
            self.index += 1
            return True
        return False

    def _expect(self, value: str) -> Token:
        token = self.current
        if token.value != value:
            raise _error("unexpected_token", f"期望 {value!r}，实际为 {token.value!r}", token.position)
        self.index += 1
        return token

    def parse(self) -> Node:
        node = self._parse_or()
        if self.current.kind != "EOF":
            raise _error("unexpected_token", f"多余的内容 {self.current.value!r}", self.current.position)
        return node

    def _parse_or(self) -> Node:
        node = self._parse_and()
        while self._match("or"):
            node = Node("binary", "or", (node, self._parse_and()), node.position)
        return node

    def _parse_and(self) -> Node:
        node = self._parse_not()
        while self._match("and"):
            node = Node("binary", "and", (node, self._parse_not()), node.position)
        return node

    def _parse_not(self) -> Node:
        if self._match("not"):
            return Node("unary", "not", (self._parse_not(),))
        return self._parse_comparison()

    def _parse_comparison(self) -> Node:
        node = self._parse_sum()
        while self.current.value in _COMPARISONS:
            operator, position = self.current.value, self.current.position
            self.index += 1
            node = Node("binary", operator, (node, self._parse_sum()), position)
        return node

    def _parse_sum(self) -> Node:
        node = self._parse_term()
        while self.current.value in {"+", "-"}:
            operator, position = self.current.value, self.current.position
            self.index += 1
            node = Node("binary", operator, (node, self._parse_term()), position)
        return node

    def _parse_term(self) -> Node:
        node = self._parse_unary()
        while self.current.value in {"*", "/"}:
            operator, position = self.current.value, self.current.position
            self.index += 1
            node = Node("binary", operator, (node, self._parse_unary()), position)
        return node

    def _parse_unary(self) -> Node:
        if self.current.value in {"+", "-"}:
            operator, position = self.current.value, self.current.position
            self.index += 1
            return Node("unary", operator, (self._parse_unary(),), position)
        return self._parse_primary()

    def _parse_primary(self) -> Node:
        token = self.current
        if token.kind == "NUMBER":
            self.index += 1
            return Node("number", token.value, (), token.position)
        if token.kind == "STRING":
            self.index += 1
            return Node("string", token.value, (), token.position)
        if token.value == "(":
            self.index += 1
            node = self._parse_or()
            self._expect(")")
            return node
        if token.value == "[":
            return self._parse_list()
        if token.kind == "IDENT":
            self.index += 1
            name = token.value
            if name in {"true", "false"}:
                return Node("bool", name == "true", (), token.position)
            if name in {"and", "or", "not", "in"}:
                raise _error("unexpected_token", f"运算符 {name!r} 出现在错误位置", token.position)
            if self._match("("):
                return self._parse_call(name, token.position)
            return Node("column", name, (), token.position)
        raise _error("unexpected_token", f"无法解析的内容 {token.value!r}", token.position)

    def _parse_list(self) -> Node:
        position = self._expect("[").position
        items: list[Node] = []
        if not self._match("]"):
            items.append(self._parse_or())
            while self._match(","):
                items.append(self._parse_or())
            self._expect("]")
        return Node("list", None, tuple(items), position)

    def _parse_call(self, name: str, position: int) -> Node:
        if name not in AGGREGATES:
            raise _error("unknown_function", f"不支持的函数 {name!r}", position)
        args: list[Node] = []
        if not self._match(")"):
            args.append(self._parse_or())
            while self._match(","):
                args.append(self._parse_or())
            self._expect(")")
        if name == "count" and len(args) not in {0, 1}:
            raise _error("invalid_arguments", "count 接受 0 或 1 个参数", position)
        if name != "count" and len(args) != 1:
            raise _error("invalid_arguments", f"{name} 接受 1 个参数", position)
        return Node("call", name, tuple(args), position)


def parse_expression(text: str) -> Node:
    return _Parser(tokenize(text)).parse()


def _walk(node: Node) -> Iterable[Node]:
    yield node
    for child in node.children:
        yield from _walk(child)


def referenced_columns(node: Node) -> tuple[str, ...]:
    return tuple(sorted({item.value for item in _walk(node) if item.kind == "column"}))


def has_aggregate(node: Node) -> bool:
    return any(item.kind == "call" for item in _walk(node))


def _check_nested_aggregates(node: Node, inside: bool = False) -> None:
    if node.kind == "call":
        if inside:
            raise _error("nested_aggregate", "聚合函数不能相互嵌套", node.position)
        for child in node.children:
            _check_nested_aggregates(child, True)
    else:
        for child in node.children:
            _check_nested_aggregates(child, inside)


def validate_expression(
    text: str,
    columns: Iterable[str],
    *,
    allow_aggregates: bool = True,
    require_aggregate: bool = False,
) -> Node:
    node = parse_expression(text)
    available = set(columns)
    missing = sorted(set(referenced_columns(node)) - available)
    if missing:
        raise _error("unknown_column", f"引用了不存在的列: {', '.join(missing)}")
    _check_nested_aggregates(node)
    aggregates = has_aggregate(node)
    if aggregates and not allow_aggregates:
        raise _error("aggregate_not_allowed", "该表达式不允许使用聚合函数")
    if require_aggregate and not aggregates:
        raise _error("aggregate_required", "指标表达式必须包含聚合函数")
    return node


def evaluate(node: Node, row: Any = None, rows: Any = None) -> Any:
    from .expr_eval import evaluate as implementation

    return implementation(node, row, rows)


def evaluate_expression(text: str, row: Any = None, rows: Any = None) -> Any:
    from .expr_eval import evaluate_expression as implementation

    return implementation(text, row, rows)
