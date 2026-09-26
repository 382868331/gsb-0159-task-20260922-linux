"""Width-aware layout: choose flat or broken forms per group.

This module is deliberately separate from minparen.printer. The printer
decides *where parentheses must go* -- that is pure syntax and never
changes. This module decides *where lines may break* -- that is pure
layout and never adds parentheses to escape a bad layout.

Layout model
------------

Every composite node (bin, cond, call, neg) is a *group* with exactly
two legal shapes:

* **flat** -- the whole subtree renders on one line, identical to the
  minimal-parenthesis printer output (no optional spaces);
* **broken** -- the group puts its children on continuation lines,
  indented by INDENT columns relative to the group start:

  - bin:   "left <op>" then "right" on the next line;
  - cond:  "test ?" / "then :" / "else" each on their own line;
  - call:  "callee(" then one argument per line, then ")" on its own;
  - neg:   no break of its own; the operand is optimized in place.

Children of a broken group are optimized recursively; a flat group is
flat all the way down. Leaves cannot break: a single token wider than
the column width simply overflows, and the overflow is counted.

Penalty (lexicographic order, lower wins)
-----------------------------------------

1. **overflow** -- total number of characters beyond "width",
   summed over all output lines;
2. **newlines** -- number of line breaks;
3. **text** -- the output string itself, compared lexicographically.

Optimality (shared subproblems)
-------------------------------

A parent may append a separator (" +", " ?", " :", "(", ",") or the
node's own trail comment and closing paren to a child's last line, which
can push that line over the width. The key observation is that the
length of that appended suffix is *statically known* for every slot, so
each subproblem is parameterized by (node, column, appended-length k)
and has a single best block: overflow and newline counts add over
disjoint lines, and because two distinct layouts of one node always
differ before either ends (a break inserts a newline *inside* the node
span, never after it), the lexicographic tie-break composes through
concatenation. Each subproblem is solved once and memoized, so the
search is linear in the number of (node, column, k) triples and never
enumerates the 2^k flat/broken combinations. "budget" bounds the number
of subproblems solved; exhausting it raises
minparen.errors.BudgetExhaustedError carrying a legal but unproven
fallback layout in its "best_effort" attribute, which is distinct from
a proven optimum.
"""

from __future__ import annotations

from .errors import BudgetExhaustedError
from .printer import _emit, _needs_parens
from .validation import validate_tree

MIN_WIDTH = 1
MAX_WIDTH = 80
INDENT = 2


def _check_width(width: int) -> int:
    if isinstance(width, bool) or not isinstance(width, int):
        raise TypeError("width must be an int")
    if not MIN_WIDTH <= width <= MAX_WIDTH:
        raise ValueError(
            f"width must be between {MIN_WIDTH} and {MAX_WIDTH}, "
            f"got {width}")
    return width


def _check_budget(budget: int | None) -> int | None:
    if budget is None:
        return None
    if isinstance(budget, bool) or not isinstance(budget, int):
        raise TypeError("budget must be an int or None")
    if budget < 0:
        raise ValueError("budget must be non-negative")
    return budget


class _Ctx:
    __slots__ = ("width", "budget", "states", "memo")

    def __init__(self, width: int, budget: int | None):
        self.width = width
        self.budget = budget
        self.states = 0
        self.memo: dict[tuple[int, bool, int, int], list[str]] = {}


def _cost(block: list[str], k: int, width: int) -> tuple[int, int, str]:
    """Penalty of a block whose last line will be extended by k chars."""
    overflow = sum(max(0, len(line) - width) for line in block[:-1])
    overflow += max(0, len(block[-1]) + k - width)
    return (overflow, len(block) - 1, "\n".join(block))


def _comment(node: dict, key: str) -> str:
    value = node.get(key)
    return "" if value is None else f"/*{value}*/"


def _compose(node: dict, col: int, first_col: int, k: int, ctx: _Ctx
             ) -> list[str] | None:
    """Broken shape of a group core (no own comments/parens), absolute
    lines. The group starts at "col"; its first-line content starts at
    "first_col" (after any paren/lead-comment prefix); continuation
    lines indent from "col". "k" is the suffix length the enclosing
    context will append to this group's last line."""
    kind = node["kind"]
    if kind in ("num", "var"):
        return None
    if kind == "neg":
        # "-" only extends the first line; k reaches the operand's end.
        operand = _best(node["operand"], _needs_parens(
            node["operand"], node, "operand"), first_col + 1, k, ctx)
        return [operand[0][:first_col] + "-" + operand[0][first_col + 1:],
                *operand[1:]]
    if kind == "bin":
        op_suffix = 1 + len(node["op"])
        left = _best(node["left"], _needs_parens(
            node["left"], node, "left"), first_col, op_suffix, ctx)
        right = _best(node["right"], _needs_parens(
            node["right"], node, "right"), col + INDENT, k, ctx)
        return [*left[:-1], left[-1] + " " + node["op"], *right]
    if kind == "cond":
        test = _best(node["cond"], _needs_parens(
            node["cond"], node, "cond"), first_col, 2, ctx)
        then = _best(node["then"], _needs_parens(
            node["then"], node, "then"), col + INDENT, 2, ctx)
        otherwise = _best(node["else"], _needs_parens(
            node["else"], node, "else"), col + INDENT, k, ctx)
        return [*test[:-1], test[-1] + " ?",
                *then[:-1], then[-1] + " :",
                *otherwise]
    # call
    if not node["args"]:
        return None  # "f()" has nothing to break
    callee = _best(node["callee"], _needs_parens(
        node["callee"], node, "callee"), first_col, 1, ctx)
    lines = [*callee[:-1], callee[-1] + "("]
    for i, arg in enumerate(node["args"]):
        last = i == len(node["args"]) - 1
        # "," follows a non-last argument; the closing paren is its own
        # line, so nothing is appended to the last argument.
        block = _best(arg, _needs_parens(arg, node, f"arg{i}"),
                      col + INDENT, 0 if last else 1, ctx)
        lines += block
        if not last:
            lines[-1] += ","
    lines.append(" " * col + ")")
    return lines


def _best(node: dict, parens: bool, col: int, k: int, ctx: _Ctx
          ) -> list[str]:
    key = (id(node), parens, col, k)
    hit = ctx.memo.get(key)
    if hit is not None:
        return hit
    ctx.states += 1
    if ctx.budget is not None and ctx.states > ctx.budget:
        raise BudgetExhaustedError(
            f"layout search budget of {ctx.budget} subproblem(s) exhausted "
            f"after {ctx.states - 1}; a legal layout exists (see "
            "best_effort) but optimality is unproven")

    prefix = ("(" if parens else "") + _comment(node, "lead")
    suffix = _comment(node, "trail") + (")" if parens else "")
    core_col = col + len(prefix)

    # Flat text of the bare core: strip this node's own comments (children
    # keep theirs); parens/comments wrap it below.
    bare = {k2: v for k2, v in node.items() if k2 not in ("lead", "trail")}
    candidates = [[" " * core_col + _emit(bare, None, "root")]]

    composed = _compose(node, col, core_col, len(suffix), ctx)
    if composed is not None:
        candidates.append(composed)

    pad = " " * col + prefix
    wrapped = [[pad + block[0][core_col:], *block[1:-1],
                block[-1] + suffix] if len(block) > 1 else
               [pad + block[0][core_col:] + suffix]
               for block in candidates]

    best = min(wrapped, key=lambda block: _cost(block, k, ctx.width))
    ctx.memo[key] = best
    return best


def format_ast(root: dict, width: int = MAX_WIDTH, *,
               budget: int | None = None) -> str:
    """Render a validated AST, choosing flat or broken groups for width.

    "width" is the target column width, an int in 1..80 (inclusive);
    every width in that range yields one deterministic, provably optimal
    result under the (overflow, newlines, text) penalty order. "budget"
    optionally bounds the number of layout subproblems solved; on
    exhaustion minparen.errors.BudgetExhaustedError is raised with a
    legal (fully flat) but unproven layout in its "best_effort"
    attribute. The input tree is validated and never mutated.
    """
    width = _check_width(width)
    budget = _check_budget(budget)
    safe = validate_tree(root)
    ctx = _Ctx(width, budget)
    try:
        block = _best(safe, False, 0, 0, ctx)
    except BudgetExhaustedError as exc:
        exc.best_effort = _emit(safe, None, "root")
        raise
    return "\n".join(block)


__all__ = ["format_ast", "MIN_WIDTH", "MAX_WIDTH", "INDENT"]
