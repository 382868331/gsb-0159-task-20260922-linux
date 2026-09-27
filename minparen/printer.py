"""Minimal-parenthesis printer.

Optimality argument (structure)
-------------------------------

A child in a fixed parent *slot* either parses back unchanged without
parentheses or does not. That depends only on the child's binding level and
the slot (parent kind + side); it is independent of other nodes and of
ancestors, because the parent's own parentheses (when present) already
isolate it. The table :data:`_SLOTS` encodes exactly the tight slots,
including the ternary quirk that an assignment is tight in the ``then``
branch (``a?b=c:d``) but not in ``else``, and that a unary minus accepts
calls/leaves/additive operands per the grammar. Choosing "no parens" in
every tight slot minimizes the paren count; no spaces or other optional
characters are emitted, minimizing length, and rendering is deterministic,
settling the lexicographic tie-break.

Binding levels (loose -> tight):
    assign 1, cond 2, add 3, mul 4, unary 5, call 6, leaf 7.

    parent bin '=' : left  >= cond(2)       ; right >= assign(1)
    parent bin +/- : left  >= add(3)        ; right >  add(3) (left assoc.)
    parent bin */ /: left  >= mul(4)        ; right >  mul(4)
    parent cond    : test  >= add(3)        ; then >= assign(1)
                     else  >= cond(2)
    parent neg     : operand >= unary(5)
    parent call    : callee >= call(6)      ; args >= assign(1)

Optimality argument (comments)
------------------------------

The parser attributes each comment gap to exactly one node side:

* a gap claimed for the *child*: right of a binary operator, both sides of
  ``?``/``:``, after the unary ``-``, both sides of call argument
  separators, and inside a grouping pair;
* a gap claimed for the *enclosing* node: before a left operand, before a
  ternary test, before a call callee, after a right operand, after a
  ternary else, after a unary operand, and the two document edges.

A node's own lead/trail binds correctly with no parentheses when its edge
claims that side for the child itself; when the edge hands the gap to the
enclosing node, the node must carry its own pair -- the comment then sits
just inside ``( )`` and the grouping gaps are claimed for the node. This
decision is local to the comment owner, so the minimum number of pairs is
added, and placing the pair around the owner (rather than a higher
ancestor) puts the closing ``)`` as early as possible; since ``)`` sorts
before every operator punctuation, that is the lexicographically smallest
minimum-length form.
"""

from __future__ import annotations

from dataclasses import dataclass

# Binding levels.
L_ASSIGN, L_COND, L_ADD, L_MUL, L_UNARY, L_CALL, L_LEAF = range(1, 8)

# Slot -> (minimum tight level, strict?). A child is tight without parens iff
# level >= min_level and (not strict or level > min_level).
_SLOTS: dict[tuple[str, str], tuple[int, bool]] = {
    ("bin=", "left"): (L_COND, False),
    ("bin=", "right"): (L_ASSIGN, False),
    ("bin+", "left"): (L_ADD, False),
    ("bin+", "right"): (L_ADD, True),
    ("bin*", "left"): (L_MUL, False),
    ("bin*", "right"): (L_MUL, True),
    ("cond", "cond"): (L_ADD, False),
    ("cond", "then"): (L_ASSIGN, False),
    ("cond", "else"): (L_COND, False),
    ("neg", "operand"): (L_UNARY, False),
    ("call", "callee"): (L_CALL, False),
    ("call", "arg"): (L_ASSIGN, False),
}

# Edges whose outer-front gap is claimed for the enclosing node rather than
# for the child: a lead comment on the child needs its own parentheses.
_FRONT_ENCLOSED = frozenset({"left", "cond", "callee"})
# Symmetric set for a trail comment.
_END_ENCLOSED = frozenset({"right", "else", "operand"})


def _normalize_side(side: str) -> str:
    return "arg" if side.startswith("arg") else side


def _level(node: dict) -> int:
    kind = node["kind"]
    if kind in ("num", "var"):
        return L_LEAF
    if kind == "call":
        return L_CALL
    if kind == "neg":
        return L_UNARY
    if kind == "bin":
        if node["op"] == "=":
            return L_ASSIGN
        return L_ADD if node["op"] in "+-" else L_MUL
    return L_COND


def _slot_key(parent: dict, side: str) -> tuple[str, str]:
    if parent["kind"] == "bin":
        group = "bin=" if parent["op"] == "=" else (
            "bin+" if parent["op"] in "+-" else "bin*")
        return (group, side)
    return (parent["kind"], side)


# Paren disposition recorded on every span:
#   "added-structural" -- the parent slot cannot parse the child back
#                         without a grouping pair (see _SLOTS);
#   "added-comment"    -- the slot is structurally tight, but the node's
#                         own lead/trail comment sits in a gap claimed by
#                         the enclosing node, so the node carries its own
#                         pair to keep the comment bound to itself;
#   "omitted-tight"    -- the slot accepts the child as-is; no pair emitted.
P_ADDED_STRUCTURAL = "added-structural"
P_ADDED_COMMENT = "added-comment"
P_OMITTED_TIGHT = "omitted-tight"


def _paren_disposition(node: dict, parent: dict | None, side: str) -> str:
    if parent is None:
        return P_OMITTED_TIGHT
    side = _normalize_side(side)
    min_level, strict = _SLOTS[_slot_key(parent, side)]
    level = _level(node)
    if level < min_level or (strict and level == min_level):
        return P_ADDED_STRUCTURAL
    if node.get("lead") is not None and side in _FRONT_ENCLOSED:
        return P_ADDED_COMMENT
    if node.get("trail") is not None and side in _END_ENCLOSED:
        return P_ADDED_COMMENT
    return P_OMITTED_TIGHT


def _needs_parens(node: dict, parent: dict, side: str) -> bool:
    return _paren_disposition(node, parent, side) != P_OMITTED_TIGHT


@dataclass(frozen=True)
class NodeSpan:
    """Output range of one AST node occurrence in the printed text.

    ``start``/``end`` are 0-based half-open offsets covering everything the
    node owns: its own parentheses (when added) and its lead/trail comments.
    ``core_start``/``core_end`` exclude the added parentheses but still
    include the comments. Spans nest exactly like the AST: a parent's span
    strictly contains every child's span, and sibling spans are disjoint and
    ordered. Two textually identical subexpressions at different positions
    yield two distinct spans with different ``path`` values.
    """

    path: str
    kind: str
    start: int
    end: int
    core_start: int
    core_end: int
    parens: str
    slot: str

    def __contains__(self, offset: int) -> bool:
        return self.start <= offset < self.end


@dataclass(frozen=True)
class PrintResult:
    """Printed text plus the per-node source map."""

    text: str
    spans: tuple[NodeSpan, ...]

    def span_for(self, path: str) -> NodeSpan:
        """Return the span of the node at ``path`` (e.g. ``root.right``)."""
        for span in self.spans:
            if span.path == path:
                return span
        raise KeyError(path)

    def node_at(self, offset: int) -> NodeSpan | None:
        """Return the innermost node whose span covers ``offset``."""
        best: NodeSpan | None = None
        for span in self.spans:
            if offset in span and (
                    best is None
                    or span.end - span.start < best.end - best.start):
                best = span
        return best


class _Emitter:
    """Single-pass emitter that records a span for every node."""

    def __init__(self) -> None:
        self._buf: list[str] = []
        self._len = 0
        self.spans: list[NodeSpan] = []

    def _write(self, text: str) -> None:
        self._buf.append(text)
        self._len += len(text)

    def emit(self, node: dict, parent: dict | None, side: str,
             path: str) -> None:
        start = self._len
        disposition = _paren_disposition(node, parent, side)
        if disposition != P_OMITTED_TIGHT:
            self._write("(")
        core_start = self._len
        if node.get("lead") is not None:
            self._write("/*" + node["lead"] + "*/")
        self._emit_core(node, path)
        if node.get("trail") is not None:
            self._write("/*" + node["trail"] + "*/")
        core_end = self._len
        if disposition != P_OMITTED_TIGHT:
            self._write(")")
        slot = "root" if parent is None else (
            _slot_key(parent, _normalize_side(side))[0]
            + "." + _normalize_side(side))
        self.spans.append(NodeSpan(
            path=path, kind=node["kind"], start=start, end=self._len,
            core_start=core_start, core_end=core_end,
            parens=disposition, slot=slot))

    def _emit_core(self, node: dict, path: str) -> None:
        kind = node["kind"]
        if kind == "num":
            self._write(str(node["value"]))
        elif kind == "var":
            self._write(node["name"])
        elif kind == "neg":
            self._write("-")
            self.emit(node["operand"], node, "operand", path + ".operand")
        elif kind == "bin":
            self.emit(node["left"], node, "left", path + ".left")
            self._write(node["op"])
            self.emit(node["right"], node, "right", path + ".right")
        elif kind == "cond":
            self.emit(node["cond"], node, "cond", path + ".cond")
            self._write("?")
            self.emit(node["then"], node, "then", path + ".then")
            self._write(":")
            self.emit(node["else"], node, "else", path + ".else")
        else:  # call
            self.emit(node["callee"], node, "callee", path + ".callee")
            self._write("(")
            for i, arg in enumerate(node["args"]):
                if i:
                    self._write(",")
                self.emit(arg, node, "arg" + str(i),
                          path + ".args[" + str(i) + "]")
            self._write(")")


def print_ast_mapped(root: dict) -> PrintResult:
    """Like ``print_ast``, but also maps every node to its output range.

    The returned ``PrintResult`` carries the same minimal-parenthesis
    text as ``print_ast`` and one ``NodeSpan`` per AST node
    occurrence, including the paren disposition explaining why each pair
    was added (structural slot or comment ownership) or omitted (tight
    slot). Raises ``ASTError`` on contract violations; the input is
    not mutated.
    """
    from .validation import validate_tree

    safe = validate_tree(root)
    emitter = _Emitter()
    emitter.emit(safe, None, "root", "root")
    return PrintResult("".join(emitter._buf), tuple(emitter.spans))


def print_ast(root: dict) -> str:
    """Serialize a validated AST with the minimum number of parentheses.

    Raises :class:`~minparen.errors.ASTError` (with a node path) on any
    contract violation. The input is not mutated; it is copied during
    validation.
    """
    return print_ast_mapped(root).text


__all__ = ["print_ast", "print_ast_mapped", "PrintResult", "NodeSpan"]
