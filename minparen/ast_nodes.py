"""Expression AST: the public data structure.

Node kinds (see :class:`NodeKind`):

``num``   non-negative integer literal.      field ``value``: int (not bool)
``var``   ASCII identifier ``[A-Za-z_][A-Za-z0-9_]*``.  field ``name``
``neg``   unary minus prefix.                field ``operand``
``bin``   binary operator.                   fields ``op``, ``left``, ``right``
          op in ``+ - * / =``; ``=`` is right associative, the rest are left.
``cond``  ternary ``cond ? then : else`` (right associative in both branches).
          fields ``cond``, ``then``, ``else``
``call``  function application ``callee(args...)``.
          fields ``callee``, ``args`` (list of Nodes, possibly empty)

Each node may carry comments, attached to the node itself and never migrating
to a sibling:

* ``lead``  printed immediately *before* the node's own opening parenthesis
            (or before the leaf), inside the node span.
* ``trail`` printed immediately *after* the node's closing parenthesis (or
            after the leaf), inside the node span.

Comment text must be one line (no ``\\n``/``\\r``) and must not contain the
``*/`` terminator or a NUL byte; it may be empty (``/**/``).

All fields are validated by :mod:`minparen.validation` before printing or
parsing returns. Nodes are mutable dicts; callers that keep trees across
mutations should construct fresh ones.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Mapping, Sequence

from .errors import ASTError

NodeKind = str  # documented literal values below; see NodeKind enum helpers.


class Kinds(str, Enum):
    NUM = "num"
    VAR = "var"
    NEG = "neg"
    BIN = "bin"
    COND = "cond"
    CALL = "call"


BIN_OPS = ("+", "-", "*", "/", "=")
LEFT_BIN_OPS = ("+", "-", "*", "/")
_RIGHT_BIN_OPS = ("=",)

# Precedence levels, low number = looser binding (printed outer).
P_ASSIGN = 1
P_COND = 2
P_ADD = 3
P_MUL = 4
P_UNARY = 5
P_CALL = 6
P_LEAF = 7


def num(value: int, lead: str | None = None, trail: str | None = None) -> dict:
    """Convenience constructor for a ``num`` node."""
    return _node("num", value=value, lead=lead, trail=trail)


def var(name: str, lead: str | None = None, trail: str | None = None) -> dict:
    """Convenience constructor for a ``var`` node."""
    return _node("var", name=name, lead=lead, trail=trail)


def neg(operand: Mapping, lead: str | None = None, trail: str | None = None) -> dict:
    """Convenience constructor for a unary ``-`` node."""
    return _node("neg", operand=dict(operand), lead=lead, trail=trail)


def binop(op: str, left: Mapping, right: Mapping,
          lead: str | None = None, trail: str | None = None) -> dict:
    """Convenience constructor for a binary node."""
    return _node("bin", op=op, left=dict(left), right=dict(right),
                 lead=lead, trail=trail)


def cond(test: Mapping, then: Mapping, else_: Mapping,
         lead: str | None = None, trail: str | None = None) -> dict:
    """Convenience constructor for a ``?:`` node (field name ``else``)."""
    node = {"kind": "cond", "cond": dict(test), "then": dict(then),
            "else": dict(else_)}
    if lead is not None:
        node["lead"] = lead
    if trail is not None:
        node["trail"] = trail
    return node


def call(callee: Mapping, args: Sequence[Mapping],
         lead: str | None = None, trail: str | None = None) -> dict:
    """Convenience constructor for a function-call node."""
    return _node("call", callee=dict(callee), args=[dict(a) for a in args],
                 lead=lead, trail=trail)


def _node(kind: str, **fields: Any) -> dict:
    node: dict = {"kind": kind}
    for key, value in fields.items():
        if key in ("lead", "trail"):
            if value is not None:
                node[key] = value
        else:
            node[key] = value
    return node


__all__ = [
    "Kinds", "NodeKind", "BIN_OPS", "LEFT_BIN_OPS",
    "P_ASSIGN", "P_COND", "P_ADD", "P_MUL", "P_UNARY", "P_CALL", "P_LEAF",
    "num", "var", "neg", "binop", "cond", "call",
]
