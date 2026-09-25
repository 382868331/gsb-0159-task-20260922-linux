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

# The single emission traversal at the bottom of this module records, for
# every node, exactly where its span / syntax core / auto-added parens /
# comments / operators land in the output. print_ast discards that record;
# minparen.mapping.print_ast_mapped exposes it. Both share _print_with_map,
# so the mapped text is identical to the plain text by construction.

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
    """Accumulates the output text and one mutable record per node.

    Records are appended in pre-order (a node before its children) and are
    converted into immutable public objects by :mod:`minparen.mapping`.
    All offsets are 0-based Python string offsets, half-open ``[start, end)``.
    """

    def __init__(self) -> None:
        self.parts: list[str] = []
        self.pos: int = 0
        self.nodes: list[dict] = []

    def write(self, s: str) -> tuple[int, int]:
        start = self.pos
        self.parts.append(s)
        self.pos += len(s)
        return (start, self.pos)


def _emit(node: dict, parent: dict | None, side: str, path: str,
          depth: int, rec: _Recorder) -> None:
    kind = node["kind"]
    record = {"path": path, "kind": kind, "depth": depth,
              "parens": None, "lead": None, "trail": None,
              "lead_text": None, "trail_text": None, "operators": []}
    rec.nodes.append(record)
    start = rec.pos

    need = parent is not None and _needs_parens(node, parent, side)
    paren_open = None
    if need:
        paren_open = rec.write("(")[0]

    lead = node.get("lead")
    if lead is not None:
        record["lead"] = rec.write(f"/*{lead}*/")
        record["lead_text"] = lead

    core_start = rec.pos
    if kind == "num":
        rec.write(str(node["value"]))
    elif kind == "var":
        rec.write(node["name"])
    elif kind == "neg":
        record["operators"].append(("-", rec.write("-")))
        _emit(node["operand"], node, "operand", f"{path}.operand",
              depth + 1, rec)
    elif kind == "bin":
        _emit(node["left"], node, "left", f"{path}.left", depth + 1, rec)
        record["operators"].append((node["op"], rec.write(node["op"])))
        _emit(node["right"], node, "right", f"{path}.right", depth + 1, rec)
    elif kind == "cond":
        _emit(node["cond"], node, "cond", f"{path}.cond", depth + 1, rec)
        record["operators"].append(("?", rec.write("?")))
        _emit(node["then"], node, "then", f"{path}.then", depth + 1, rec)
        record["operators"].append((":", rec.write(":")))
        _emit(node["else"], node, "else", f"{path}.else", depth + 1, rec)
    else:  # call
        _emit(node["callee"], node, "callee", f"{path}.callee", depth + 1, rec)
        record["operators"].append(("(", rec.write("(")))
        for i, arg in enumerate(node["args"]):
            if i:
                record["operators"].append((",", rec.write(",")))
            _emit(arg, node, f"arg{i}", f"{path}.args[{i}]", depth + 1, rec)
        record["operators"].append((")", rec.write(")")))
    core_end = rec.pos

    trail = node.get("trail")
    if trail is not None:
        record["trail"] = rec.write(f"/*{trail}*/")
        record["trail_text"] = trail

    if need:
        record["parens"] = ((paren_open, paren_open + 1), rec.write(")"))

    record["core"] = (core_start, core_end)
    record["span"] = (start, rec.pos)


def _print_with_map(root: dict) -> tuple[str, list[dict]]:
    """Validate ``root`` and return ``(text, raw node records)``.

    This is the single traversal behind both :func:`print_ast` and
    :func:`minparen.mapping.print_ast_mapped`, so the plain text and the
    mapped text are identical by construction.
    """
    from .validation import validate_tree

    safe = validate_tree(root)
    rec = _Recorder()
    _emit(safe, None, "root", "root", 0, rec)
    return "".join(rec.parts), rec.nodes


def print_ast(root: dict) -> str:
    """Serialize a validated AST with the minimum number of parentheses.

    Raises :class:`~minparen.errors.ASTError` (with a node path) on any
    contract violation. The input is not mutated; it is copied during
    validation.
    """
    text, _ = _print_with_map(root)
    return text


__all__ = ["print_ast"]
