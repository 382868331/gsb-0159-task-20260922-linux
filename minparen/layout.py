"""Width-aware line-break layout, kept separate from syntax printing.

Parentheses are still decided purely by grammar precedence
(:mod:`minparen.printer`); this module only decides, for every
``bin`` / ``cond`` / ``call`` group, whether the group is printed *flat*
(one line) or *broken* (children on their own indented lines).  Layout
never adds or removes parentheses to escape a width limit.

Broken shapes (indent unit: two spaces per level)::

    LEFT +            TEST?             callee(
      RIGHT             THEN:             arg1,
                        ELSE              arg2
                                      )

A ``neg`` node adds no break of its own; its operand is laid out
recursively after the ``-`` (this transparent form always exists and
never loses to the all-flat form, so no choice is needed).

Cost model (lexicographic order, lower is better):

1. ``overflow`` -- total characters beyond ``width`` summed over all
   output lines.  A single unsplittable token longer than ``width``
   overflows; the excess is fully counted here.
2. ``newlines`` -- number of line breaks.
3. the output text itself, lexicographically.

Optimality / shared subproblems: a child's column depends only on the
parent state (never on sibling choices), overflow and newline count are
additive over lines, and the tie-break string is concatenative, so
per-state dynamic programming over
``(node, slot, column, level, tail)`` is exact.  The memo table is the
shared-subproblem structure that avoids exponential re-computation on
large trees; small trees are cross-checked against the exhaustive
enumerator in :mod:`reference.reflayout`.

Budget: ``budget`` caps the number of expanded states.  Exceeding it
raises :class:`~minparen.errors.BudgetExhaustedError` whose ``best``
attribute carries the best legal layout found so far
(``proven_optimal=False``) -- distinct from a finished, provably
optimal search.
"""

from __future__ import annotations

from dataclasses import dataclass

from .errors import BudgetExhaustedError
from .printer import _emit, _needs_parens
from .validation import validate_tree

MIN_WIDTH = 1
MAX_WIDTH = 80
INDENT = "  "


@dataclass(frozen=True)
class FormatResult:
    """Outcome of :func:`format_ast`.

    ``cost`` orders candidates: ``(overflow, newlines, text)``.
    ``proven_optimal`` is True only when the search ran to completion.
    ``expanded`` counts distinct layout states explored.
    """

    text: str
    overflow: int
    newlines: int
    proven_optimal: bool
    expanded: int
    width: int

    @property
    def cost(self) -> tuple[int, int, str]:
        return (self.overflow, self.newlines, self.text)


class _Abort(Exception):
    """Internal: budget ran out mid-search."""


def _ctx_key(parent: dict | None, side: str) -> tuple:
    if parent is None:
        return ("root", None, side)
    return (parent["kind"], parent.get("op"),
            "arg" if side.startswith("arg") else side)


class _Engine:
    def __init__(self, width: int, budget: int | None):
        self.width = width
        self.budget = budget
        self.memo: dict = {}
        self.expanded = 0

    def render(self, node: dict, parent: dict | None, side: str,
               col: int, level: int, tail: str) -> tuple[str, int, int]:
        """Best block for ``node`` starting at ``col``/``level``.

        ``tail`` is the text the parent appends to the node's last line
        (counted for overflow, not included in the returned text).
        Returns ``(text, overflow, newlines)``; the text holds no
        leading indent on its first line and absolute indents after.
        """
        # For col >= width every option's first-line overflow shifts by
        # the same constant and all choices are col-invariant, so the
        # state can be canonicalized to col == width exactly.
        col_key = col if col < self.width else self.width
        key = (id(node), _ctx_key(parent, side), col_key, level, tail)
        hit = self.memo.get(key)
        if hit is None:
            self.expanded += 1
            if self.budget is not None and self.expanded > self.budget:
                raise _Abort()
            hit = self._compute(node, parent, side, col_key, level, tail)
            self.memo[key] = hit
        text, ovf, nls = hit
        return text, ovf + (col - col_key), nls

    def _wrap(self, node: dict, parent: dict | None, side: str,
              col: int, tail: str) -> tuple[str, str, int, str]:
        parens = parent is not None and _needs_parens(node, parent, side)
        lead = "" if node.get("lead") is None else f"/*{node['lead']}*/"
        trail = "" if node.get("trail") is None else f"/*{node['trail']}*/"
        pre = ("(" if parens else "") + lead
        post = trail + (")" if parens else "")
        return pre, post, col + len(pre), post + tail

    def _compute(self, node: dict, parent: dict | None, side: str,
                 col: int, level: int, tail: str) -> tuple[str, int, int]:
        width = self.width
        kind = node["kind"]
        flat_text = _emit(node, parent, side)
        flat_ovf = max(0, col + len(flat_text) + len(tail) - width)

        if kind == "neg":
            # Transparent: "-" + best operand block.  Dominates the
            # all-flat form (which is the operand's own flat option).
            pre, post, col_core, core_tail = self._wrap(
                node, parent, side, col, tail)
            op_text, op_ovf, op_nls = self.render(
                node["operand"], node, "operand",
                col_core + 1, level, core_tail)
            return pre + "-" + op_text + post, op_ovf, op_nls

        breakable = kind in ("bin", "cond") or (
            kind == "call" and len(node["args"]) > 0)
        if not breakable or flat_ovf == 0:
            # A fitting flat group always wins: any broken form keeps
            # overflow >= 0 and adds at least one newline.
            return flat_text, flat_ovf, 0

        pre, post, col_core, core_tail = self._wrap(
            node, parent, side, col, tail)
        child_col = (level + 1) * len(INDENT)
        child_ind = INDENT * (level + 1)

        if kind == "bin":
            op = node["op"]
            left = self.render(node["left"], node, "left",
                               col_core, level, " " + op)
            right = self.render(node["right"], node, "right",
                                child_col, level + 1, core_tail)
            b_text = (pre + left[0] + " " + op + "\n"
                      + child_ind + right[0] + post)
            b_ovf = left[1] + right[1]
            b_nls = left[2] + 1 + right[2]
        elif kind == "cond":
            test = self.render(node["cond"], node, "cond",
                               col_core, level, "?")
            then = self.render(node["then"], node, "then",
                               child_col, level + 1, ":")
            els = self.render(node["else"], node, "else",
                              child_col, level + 1, core_tail)
            b_text = (pre + test[0] + "?\n"
                      + child_ind + then[0] + ":\n"
                      + child_ind + els[0] + post)
            b_ovf = test[1] + then[1] + els[1]
            b_nls = test[2] + then[2] + els[2] + 2
        else:  # call with at least one argument
            callee = self.render(node["callee"], node, "callee",
                                 col_core, level, "(")
            parts = [pre + callee[0] + "("]
            b_ovf = callee[1]
            b_nls = callee[2]
            args = node["args"]
            for i, arg in enumerate(args):
                arg_tail = "," if i < len(args) - 1 else ""
                blk = self.render(arg, node, f"arg{i}",
                                  child_col, level + 1, arg_tail)
                parts.append("\n" + child_ind + blk[0] + arg_tail)
                b_ovf += blk[1]
                b_nls += blk[2] + 1
            close = INDENT * level + ")"
            b_ovf += max(0, len(close) + len(core_tail) - width)
            b_nls += 1
            b_text = "".join(parts) + "\n" + close + post

        best = min((flat_ovf, 0, flat_text), (b_ovf, b_nls, b_text))
        return best[2], best[0], best[1]


def _check_width(width: int) -> int:
    if isinstance(width, bool) or not isinstance(width, int):
        raise TypeError(f"width must be an int, got {type(width).__name__}")
    if not MIN_WIDTH <= width <= MAX_WIDTH:
        raise ValueError(
            f"width must be between {MIN_WIDTH} and {MAX_WIDTH}, got {width}")
    return width


def format_ast(root: dict, width: int, *, budget: int | None = None) -> FormatResult:
    """Lay out a validated AST within ``width`` columns (1..80).

    Returns a :class:`FormatResult`; ``result.text`` re-parses to the
    identical tree.  The input AST is not modified (validation copies
    it).  With ``budget`` set, exceeding the state budget raises
    :class:`~minparen.errors.BudgetExhaustedError` whose ``best``
    attribute holds a legal but not-proven-optimal layout.
    """
    width = _check_width(width)
    if budget is not None:
        if isinstance(budget, bool) or not isinstance(budget, int):
            raise TypeError("budget must be an int or None")
        if budget < 0:
            raise ValueError("budget must be non-negative")
    safe = validate_tree(root)
    engine = _Engine(width, budget)
    try:
        text, ovf, nls = engine.render(safe, None, "root", 0, 0, "")
    except _Abort:
        flat = _emit(safe, None, "root")
        best = FormatResult(flat, max(0, len(flat) - width), 0, False,
                            engine.expanded, width)
        raise BudgetExhaustedError(
            f"layout budget of {budget} states exhausted; "
            "best legal layout so far is available as 'best'",
            best=best) from None
    return FormatResult(text, ovf, nls, True, engine.expanded, width)


__all__ = ["format_ast", "FormatResult", "MIN_WIDTH", "MAX_WIDTH", "INDENT"]
