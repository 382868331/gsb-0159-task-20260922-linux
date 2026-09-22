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


def _needs_parens(node: dict, parent: dict, side: str) -> bool:
    side = _normalize_side(side)
    min_level, strict = _SLOTS[_slot_key(parent, side)]
    level = _level(node)
    if level < min_level or (strict and level == min_level):
        return True
    if node.get("lead") is not None and side in _FRONT_ENCLOSED:
        return True
    if node.get("trail") is not None and side in _END_ENCLOSED:
        return True
    return False


def _emit(node: dict, parent: dict | None, side: str) -> str:
    kind = node["kind"]
    if kind == "num":
        core = str(node["value"])
    elif kind == "var":
        core = node["name"]
    elif kind == "neg":
        core = "-" + _emit(node["operand"], node, "operand")
    elif kind == "bin":
        core = (_emit(node["left"], node, "left") + node["op"]
                + _emit(node["right"], node, "right"))
    elif kind == "cond":
        core = (_emit(node["cond"], node, "cond") + "?"
                + _emit(node["then"], node, "then") + ":"
                + _emit(node["else"], node, "else"))
    else:  # call
        callee = _emit(node["callee"], node, "callee")
        args = ",".join(_emit(arg, node, f"arg{i}")
                        for i, arg in enumerate(node["args"]))
        core = f"{callee}({args})"

    lead = "" if node.get("lead") is None else f"/*{node['lead']}*/"
    trail = "" if node.get("trail") is None else f"/*{node['trail']}*/"
    if parent is not None and _needs_parens(node, parent, side):
        return f"({lead}{core}{trail})"
    return f"{lead}{core}{trail}"


def print_ast(root: dict) -> str:
    """Serialize a validated AST with the minimum number of parentheses.

    Raises :class:`~minparen.errors.ASTError` (with a node path) on any
    contract violation. The input is not mutated; it is copied during
    validation.
    """
    from .validation import validate_tree

    safe = validate_tree(root)
    return _emit(safe, None, "root")


__all__ = ["print_ast"]
