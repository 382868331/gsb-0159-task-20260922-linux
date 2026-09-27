"""Position mapping between the minimal-parenthesis output and AST nodes.

:func:`print_ast_mapped` renders exactly the same text as
:func:`minparen.printer.print_ast` (same slot table, same comment rules,
therefore the same provably minimal parenthesization) and additionally
records, for every AST node, the half-open character range it occupies in
the output.

Node identity is by *position in the tree*, expressed as the same accessor
path used by :class:`~minparen.errors.ASTError` (``root.left.args[1]``).
Two textually identical subexpressions at different positions are different
nodes with different paths, different ``node_id`` values and different
spans; they are never merged.

Span invariant: a node's ``(start, end)`` covers its own parentheses (when
added) and its bound comments; every child's span lies strictly inside its
parent's span, so ranges nest exactly like the expression hierarchy.
Characters between child spans (operators, ``? : , ( )`` call punctuation)
belong to the parent node.

Parenthesis attribution:

* every *added* pair is owned by exactly one node and explained by
  ``paren_reasons`` -- ``"structural"`` (the parent slot cannot carry the
  child bare), ``"comment-lead"`` / ``"comment-trail"`` (the edge gap is
  claimed by the enclosing node, so the comment owner must wrap itself);
* an *omitted* pair is reported as ``paren_status="omitted"`` with reason
  ``"slot-tight"`` -- the slot provably carries the child without parens;
* call ``( )`` after a callee is fixed syntax, not an added pair, and is
  therefore not in :attr:`FormatResult.parens`.
"""

from __future__ import annotations

from dataclasses import dataclass

from .printer import (
    _END_ENCLOSED,
    _FRONT_ENCLOSED,
    _SLOTS,
    _level,
    _normalize_side,
    _slot_key,
)
from .validation import validate_tree

REASON_STRUCTURAL = "structural"
REASON_COMMENT_LEAD = "comment-lead"
REASON_COMMENT_TRAIL = "comment-trail"
REASON_SLOT_TIGHT = "slot-tight"

STATUS_ADDED = "added"
STATUS_OMITTED = "omitted"
STATUS_ROOT = "root"


@dataclass(frozen=True)
class ParenPair:
    """One parenthesis pair added around a node, with its justification."""

    node_path: str
    open: int
    close: int
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class NodeSpan:
    """Output range of one AST node.

    ``start``/``end`` is the half-open range of the whole node including
    added parentheses and bound comments; ``core_start``/``core_end`` is
    the range inside any added parentheses (equal to ``start``/``end``
    when no pair was added). ``slot`` names the parent slot the node sits
    in (``"root"`` for the root).
    """

    node_id: int
    path: str
    kind: str
    start: int
    end: int
    core_start: int
    core_end: int
    slot: str
    paren_status: str
    paren_reasons: tuple[str, ...]

    def contains(self, offset: int) -> bool:
        return self.start <= offset < self.end


@dataclass(frozen=True)
class FormatResult:
    """Formatted text plus the node/position correspondence."""

    text: str
    nodes: tuple[NodeSpan, ...]
    parens: tuple[ParenPair, ...]

    def span_for(self, path: str) -> NodeSpan:
        """Return the span of the node at ``path`` (e.g. ``root.right``)."""
        for span in self.nodes:
            if span.path == path:
                return span
        raise KeyError(f"no node at path {path!r}")

    def node_at(self, offset: int) -> NodeSpan:
        """Return the innermost node owning output position ``offset``.

        Operator and call punctuation belongs to the enclosing (parent)
        node; an added parenthesis character belongs to the node whose
        pair it is. Raises :class:`IndexError` outside the text.
        """
        if isinstance(offset, bool) or not isinstance(offset, int):
            raise TypeError("offset must be an int")
        if not 0 <= offset < len(self.text):
            raise IndexError(
                f"offset {offset} outside output of length {len(self.text)}")
        best: NodeSpan | None = None
        for span in self.nodes:
            if span.contains(offset) and (
                    best is None
                    or span.end - span.start < best.end - best.start):
                best = span
        assert best is not None  # the root always contains every offset
        return best


def _paren_reasons(node: dict, parent: dict, side: str) -> tuple[str, ...]:
    side = _normalize_side(side)
    min_level, strict = _SLOTS[_slot_key(parent, side)]
    reasons: list[str] = []
    level = _level(node)
    if level < min_level or (strict and level == min_level):
        reasons.append(REASON_STRUCTURAL)
    if node.get("lead") is not None and side in _FRONT_ENCLOSED:
        reasons.append(REASON_COMMENT_LEAD)
    if node.get("trail") is not None and side in _END_ENCLOSED:
        reasons.append(REASON_COMMENT_TRAIL)
    return tuple(reasons)


def _slot_name(parent: dict | None, side: str) -> str:
    if parent is None:
        return "root"
    group, norm = _slot_key(parent, _normalize_side(side))
    return f"{group}.{norm}"


class _Mapper:
    def __init__(self) -> None:
        self.parts: list[str] = []
        self.nodes: list[NodeSpan] = []
        self.parens: list[ParenPair] = []
        self._next_id = 0

    def pos(self) -> int:
        return sum(len(p) for p in self.parts)

    def emit(self, node: dict, parent: dict | None, side: str,
             path: str) -> None:
        node_id = self._next_id
        self._next_id += 1

        reasons: tuple[str, ...] = ()
        if parent is None:
            status = STATUS_ROOT
        else:
            reasons = _paren_reasons(node, parent, side)
            status = STATUS_ADDED if reasons else STATUS_OMITTED
            if not reasons:
                reasons = (REASON_SLOT_TIGHT,)

        start = self.pos()
        if status == STATUS_ADDED:
            self.parts.append("(")
        core_start = self.pos()
        lead = node.get("lead")
        if lead is not None:
            self.parts.append(f"/*{lead}*/")

        kind = node["kind"]
        if kind == "num":
            self.parts.append(str(node["value"]))
        elif kind == "var":
            self.parts.append(node["name"])
        elif kind == "neg":
            self.parts.append("-")
            self.emit(node["operand"], node, "operand", f"{path}.operand")
        elif kind == "bin":
            self.emit(node["left"], node, "left", f"{path}.left")
            self.parts.append(node["op"])
            self.emit(node["right"], node, "right", f"{path}.right")
        elif kind == "cond":
            self.emit(node["cond"], node, "cond", f"{path}.cond")
            self.parts.append("?")
            self.emit(node["then"], node, "then", f"{path}.then")
            self.parts.append(":")
            self.emit(node["else"], node, "else", f"{path}.else")
        else:  # call
            self.emit(node["callee"], node, "callee", f"{path}.callee")
            self.parts.append("(")
            for i, arg in enumerate(node["args"]):
                if i:
                    self.parts.append(",")
                self.emit(arg, node, f"arg{i}", f"{path}.args[{i}]")
            self.parts.append(")")

        trail = node.get("trail")
        if trail is not None:
            self.parts.append(f"/*{trail}*/")
        core_end = self.pos()
        if status == STATUS_ADDED:
            self.parts.append(")")
        end = self.pos()

        if status == STATUS_ADDED:
            self.parens.append(ParenPair(
                node_path=path, open=start, close=end - 1, reasons=reasons))
        self.nodes.append(NodeSpan(
            node_id=node_id, path=path, kind=kind,
            start=start, end=end, core_start=core_start, core_end=core_end,
            slot=_slot_name(parent, side),
            paren_status=status, paren_reasons=reasons))


def print_ast_mapped(root: dict) -> FormatResult:
    """Render ``root`` like :func:`print_ast` and return the position map.

    The rendered text is byte-identical to ``print_ast(root)``; the same
    :class:`~minparen.errors.ASTError` is raised for invalid input, and the
    input tree is not mutated.
    """
    safe = validate_tree(root)
    mapper = _Mapper()
    mapper.emit(safe, None, "root", "root")
    nodes = tuple(sorted(mapper.nodes, key=lambda s: s.node_id))
    return FormatResult(
        text="".join(mapper.parts),
        nodes=nodes,
        parens=tuple(mapper.parens),
    )


__all__ = [
    "print_ast_mapped",
    "FormatResult",
    "NodeSpan",
    "ParenPair",
    "REASON_STRUCTURAL",
    "REASON_COMMENT_LEAD",
    "REASON_COMMENT_TRAIL",
    "REASON_SLOT_TIGHT",
    "STATUS_ADDED",
    "STATUS_OMITTED",
    "STATUS_ROOT",
]
