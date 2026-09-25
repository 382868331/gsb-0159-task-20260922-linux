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

Position mapping
----------------

:func:`_render` is the single emission path behind both :func:`print_ast`
and :func:`minparen.mapped.print_ast_mapped`. It walks the validated tree
once, writing into a cursor-based recorder, so every span it reports is the
exact half-open ``[start, end)`` character range of the emitted text --
never derived by searching the output for equal substrings (two identical
subtrees still get two distinct, correct spans).
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


class _Recorder:
    """Emission buffer that remembers where every piece landed.

    ``pos`` is the cursor: the next character written lands at this offset.
    Spans are half-open ``(start, end)`` pairs of Python string offsets.
    """

    __slots__ = ("parts", "pos", "nodes", "operators", "comments")

    def __init__(self) -> None:
        self.parts: list[str] = []
        self.pos = 0
        self.nodes: list[dict] = []       # pre-order node span records
        self.operators: list[dict] = []   # operator token records
        self.comments: list[dict] = []    # comment token records

    def write(self, s: str) -> tuple[int, int]:
        start = self.pos
        self.parts.append(s)
        self.pos += len(s)
        return (start, self.pos)


def _emit(node: dict, parent: dict | None, side: str, path: str,
          rec: _Recorder) -> None:
    """Emit ``node`` and record its spans.

    Layout per node (parens only when the slot requires them)::

        (  /*lead*/  core  /*trail*/  )      <- with auto-added parens
        /*lead*/  core  /*trail*/            <- without

    The auto-added parens belong to this node (recorded in its entry and
    covered by its ``span``); the lead/trail comments are mapped separately
    and stay outside the node's ``core`` span; a parent's ``span`` always
    covers the spans of its children.
    """
    kind = node["kind"]
    entry: dict = {"path": path, "kind": kind, "node": node,
                   "lead": None, "trail": None, "parens": None}
    rec.nodes.append(entry)

    lead = node.get("lead")
    trail = node.get("trail")
    parens = parent is not None and _needs_parens(node, parent, side)

    open_span = close_span = None
    if parens:
        open_span = rec.write("(")
    if lead is not None:
        entry["lead"] = rec.write(f"/*{lead}*/")
        rec.comments.append({"span": entry["lead"], "text": lead,
                             "path": path, "side": "lead"})

    core_start = rec.pos
    if kind == "num":
        rec.write(str(node["value"]))
    elif kind == "var":
        rec.write(node["name"])
    elif kind == "neg":
        rec.operators.append({"op": "-", "unary": True,
                              "span": rec.write("-"), "path": path})
        _emit(node["operand"], node, "operand", f"{path}.operand", rec)
    elif kind == "bin":
        _emit(node["left"], node, "left", f"{path}.left", rec)
        rec.operators.append({"op": node["op"], "unary": False,
                              "span": rec.write(node["op"]), "path": path})
        _emit(node["right"], node, "right", f"{path}.right", rec)
    elif kind == "cond":
        _emit(node["cond"], node, "cond", f"{path}.cond", rec)
        rec.operators.append({"op": "?", "unary": False,
                              "span": rec.write("?"), "path": path})
        _emit(node["then"], node, "then", f"{path}.then", rec)
        rec.operators.append({"op": ":", "unary": False,
                              "span": rec.write(":"), "path": path})
        _emit(node["else"], node, "else", f"{path}.else", rec)
    else:  # call
        _emit(node["callee"], node, "callee", f"{path}.callee", rec)
        rec.write("(")
        for i, arg in enumerate(node["args"]):
            if i:
                rec.write(",")
            _emit(arg, node, f"arg{i}", f"{path}.args[{i}]", rec)
        rec.write(")")
    core_end = rec.pos
    entry["core"] = (core_start, core_end)

    if trail is not None:
        entry["trail"] = rec.write(f"/*{trail}*/")
        rec.comments.append({"span": entry["trail"], "text": trail,
                             "path": path, "side": "trail"})
    if parens:
        close_span = rec.write(")")
        entry["parens"] = (open_span, close_span)

    if parens:
        entry["span"] = (open_span[0], close_span[1])
    else:
        start = entry["lead"][0] if lead is not None else core_start
        end = entry["trail"][1] if trail is not None else core_end
        entry["span"] = (start, end)


def _render(root: dict):
    """Validate ``root`` and emit ``(safe_tree, recorder, text)``.

    Internal shared backend of :func:`print_ast` and
    :func:`minparen.mapped.print_ast_mapped`; the input is not mutated.
    """
    from .validation import validate_tree

    safe = validate_tree(root)
    rec = _Recorder()
    _emit(safe, None, "root", "root", rec)
    return safe, rec, "".join(rec.parts)


def print_ast(root: dict) -> str:
    """Serialize a validated AST with the minimum number of parentheses.

    Raises :class:`~minparen.errors.ASTError` (with a node path) on any
    contract violation. The input is not mutated; it is copied during
    validation.
    """
    return _render(root)[2]


__all__ = ["print_ast"]
