"""Safe arithmetic evaluator for catalog formulas (CLAUDE.md rule 8: never eval/exec).

The formula text is parsed with :func:`ast.parse` (parsing only, nothing is executed) and
the tree is walked against a whitelist: numbers, names, + - * / // % **, unary +/-,
comparisons, ``a if cond else b``, ``and``/``or``/``not`` and a fixed set of math functions.
Attribute access, subscripts, calls to anything else, lambdas, comprehensions and strings
are rejected. Sizes are bounded so a formula cannot burn CPU or memory.
"""

from __future__ import annotations

import ast
import math
from collections.abc import Callable, Mapping
from functools import lru_cache

__all__ = ["ExprError", "evaluate", "evaluate_with_variables", "names_in", "parse"]

MAX_LEN = 400
MAX_NODES = 200
MAX_EXPONENT = 64
MAX_ABS = 1e15


class ExprError(ValueError):
    """Formula rejected or failed to evaluate."""


_FUNCS: dict[str, Callable[..., float]] = {
    "min": min,
    "max": max,
    "abs": abs,
    "ceil": math.ceil,
    "floor": math.floor,
    "round": round,
    "sqrt": math.sqrt,
    "log": math.log,
    "exp": math.exp,
}

_BIN: dict[type[ast.operator], Callable[[float, float], float]] = {
    ast.Add: lambda a, b: a + b,
    ast.Sub: lambda a, b: a - b,
    ast.Mult: lambda a, b: a * b,
    ast.Div: lambda a, b: a / b,
    ast.FloorDiv: lambda a, b: a // b,
    ast.Mod: lambda a, b: a % b,
    ast.Pow: lambda a, b: a**b,
}

_CMP: dict[type[ast.cmpop], Callable[[float, float], bool]] = {
    ast.Lt: lambda a, b: a < b,
    ast.LtE: lambda a, b: a <= b,
    ast.Gt: lambda a, b: a > b,
    ast.GtE: lambda a, b: a >= b,
    ast.Eq: lambda a, b: a == b,
    ast.NotEq: lambda a, b: a != b,
}

_ALLOWED = (
    ast.Expression,
    ast.BinOp,
    ast.UnaryOp,
    ast.BoolOp,
    ast.Compare,
    ast.IfExp,
    ast.Call,
    ast.Name,
    ast.Load,
    ast.Constant,
    ast.UAdd,
    ast.USub,
    ast.Not,
    ast.And,
    ast.Or,
    *_BIN,
    *_CMP,
)


@lru_cache(maxsize=1024)
def parse(text: str) -> ast.Expression:
    """Parse and whitelist-check a formula. Raises :class:`ExprError`."""
    if len(text) > MAX_LEN:
        raise ExprError(f"formula longer than {MAX_LEN} characters")
    try:
        tree = ast.parse(text.strip(), mode="eval")
    except SyntaxError as exc:
        raise ExprError(f"syntax error: {exc.msg}") from None
    nodes = list(ast.walk(tree))
    if len(nodes) > MAX_NODES:
        raise ExprError(f"formula has more than {MAX_NODES} nodes")
    for node in nodes:
        if not isinstance(node, _ALLOWED):
            raise ExprError(f"'{type(node).__name__}' is not allowed in formulas")
        if isinstance(node, ast.Constant) and (
            isinstance(node.value, bool) or not isinstance(node.value, int | float)
        ):
            raise ExprError("only numeric constants are allowed")
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in _FUNCS:
                raise ExprError("only min, max, abs, ceil, floor, round, sqrt, log, exp")
            if node.keywords:
                raise ExprError("keyword arguments are not allowed")
    return tree


def names_in(text: str) -> set[str]:
    """Variable names a formula reads (excluding function names)."""
    tree = parse(text)
    funcs = {id(n.func) for n in ast.walk(tree) if isinstance(n, ast.Call)}
    return {n.id for n in ast.walk(tree) if isinstance(n, ast.Name) and id(n) not in funcs}


def _num(x: float) -> float:
    if isinstance(x, bool):
        x = float(x)
    if not math.isfinite(x) or abs(x) > MAX_ABS:
        raise ExprError(f"result out of range: {x}")
    return float(x)


def _eval(node: ast.AST, env: Mapping[str, float]) -> float:
    match node:
        case ast.Expression(body=body):
            return _eval(body, env)
        case ast.Constant(value=v):
            return _num(v)
        case ast.Name(id=name):
            if name not in env:
                raise ExprError(f"unknown variable '{name}'")
            return _num(env[name])
        case ast.UnaryOp(op=ast.USub(), operand=x):
            return -_eval(x, env)
        case ast.UnaryOp(op=ast.UAdd(), operand=x):
            return _eval(x, env)
        case ast.UnaryOp(op=ast.Not(), operand=x):
            return float(not _eval(x, env))
        case ast.BinOp(left=left, op=op, right=right):
            a, b = _eval(left, env), _eval(right, env)
            if isinstance(op, ast.Pow) and abs(b) > MAX_EXPONENT:
                raise ExprError(f"exponent above {MAX_EXPONENT}")
            try:
                return _num(_BIN[type(op)](a, b))
            except ZeroDivisionError:
                raise ExprError("division by zero") from None
        case ast.BoolOp(op=op, values=values):
            vals = [_eval(v, env) for v in values]
            return float(all(vals) if isinstance(op, ast.And) else any(vals))
        case ast.Compare(left=left, ops=ops, comparators=comps):
            cur = _eval(left, env)
            for op, comp in zip(ops, comps, strict=True):
                nxt = _eval(comp, env)
                if not _CMP[type(op)](cur, nxt):
                    return 0.0
                cur = nxt
            return 1.0
        case ast.IfExp(test=test, body=body, orelse=orelse):
            return _eval(body, env) if _eval(test, env) else _eval(orelse, env)
        case ast.Call(func=ast.Name(id=fname), args=args):
            try:
                return _num(_FUNCS[fname](*(_eval(a, env) for a in args)))
            except (TypeError, ValueError) as exc:
                raise ExprError(f"{fname}(): {exc}") from None
    raise ExprError(f"unsupported expression '{type(node).__name__}'")


def evaluate(text: str, env: Mapping[str, float]) -> float:
    """Evaluate a whitelisted formula with numeric variables."""
    return _eval(parse(text), env)


def evaluate_with_variables(
    formula: str, variables: Mapping[str, str], env: Mapping[str, float]
) -> tuple[float, dict[str, float]]:
    """Evaluate derived ``variables`` in order, then ``formula``.

    Returns the result and the full environment used (for display and provenance).
    """
    scope = dict(env)
    for name, expr in variables.items():
        scope[name] = evaluate(expr, scope)
    return evaluate(formula, scope), scope
