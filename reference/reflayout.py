"""Independent exhaustive layout reference (test-only, NOT core).

Re-implements the width-aware layout of :mod:`minparen.layout` from
scratch over the tuple AST of :mod:`reference.refenum`:

* its own precedence/slot table decides parentheses (generation only);
* every breakable group (``bin`` / ``cond`` / ``call`` with args) gets
  one bit in an enumeration mask: flat or broken;
* every candidate is certified by parsing it back with the independent
  parser :func:`reference.refenum.ref_parse` and requiring the
  identical tuple tree;
* candidates rank by ``(overflow, newlines, text)``.

Only structural trees are covered (no comments), mirroring
:mod:`reference.refenum`.  Suitable for small trees: the candidate
count is 2^k in the number of breakable groups.
"""

from __future__ import annotations

from minparen.errors import BudgetExhaustedError, NoSolutionError

from .refenum import ref_parse

INDENT = "  "

L_ASSIGN, L_COND, L_ADD, L_MUL, L_UNARY, L_CALL, L_LEAF = range(1, 8)

_SLOTS = {
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


def _level(node) -> int:
    tag = node[0]
    if tag in ("n", "v"):
        return L_LEAF
    if tag == "f":
        return L_CALL
    if tag == "!":
        return L_UNARY
    if tag == "b":
        if node[1] == "=":
            return L_ASSIGN
        return L_ADD if node[1] in "+-" else L_MUL
    return L_COND


def _slot_key(parent, side: str) -> tuple[str, str]:
    if parent[0] == "b":
        group = "bin=" if parent[1] == "=" else (
            "bin+" if parent[1] in "+-" else "bin*")
        return (group, side)
    kind = {"!": "neg", "?": "cond", "f": "call"}[parent[0]]
    return (kind, "arg" if side.startswith("arg") else side)


def _needs_parens(node, parent, side: str) -> bool:
    min_level, strict = _SLOTS[_slot_key(parent, side)]
    level = _level(node)
    return level < min_level or (strict and level == min_level)


def _flat(node, parent, side: str) -> str:
    tag = node[0]
    if tag == "n":
        core = str(node[1])
    elif tag == "v":
        core = node[1]
    elif tag == "!":
        core = "-" + _flat(node[1], node, "operand")
    elif tag == "b":
        core = (_flat(node[2], node, "left") + node[1]
                + _flat(node[3], node, "right"))
    elif tag == "?":
        core = (_flat(node[1], node, "cond") + "?"
                + _flat(node[2], node, "then") + ":"
                + _flat(node[3], node, "else"))
    else:
        core = (_flat(node[1], node, "callee") + "("
                + ",".join(_flat(a, node, f"arg{i}")
                           for i, a in enumerate(node[2])) + ")")
    if parent is not None and _needs_parens(node, parent, side):
        return f"({core})"
    return core


def _breakable(node) -> bool:
    tag = node[0]
    return tag in ("b", "?") or (tag == "f" and len(node[2]) > 0)


def _sites(node, path: tuple, out: list) -> None:
    if _breakable(node):
        out.append(path)
    tag = node[0]
    if tag == "!":
        _sites(node[1], path + (0,), out)
    elif tag == "b":
        _sites(node[2], path + (0,), out)
        _sites(node[3], path + (1,), out)
    elif tag == "?":
        for i in (1, 2, 3):
            _sites(node[i], path + (i - 1,), out)
    elif tag == "f":
        _sites(node[1], path + (0,), out)
        for i, arg in enumerate(node[2]):
            _sites(arg, path + (1 + i,), out)


def _render(node, parent, side: str, col: int, level: int, tail: str,
            broken: frozenset, path: tuple, width: int):
    """Mirror of the core shapes; returns (text, overflow, newlines)."""
    tag = node[0]
    flat_text = _flat(node, parent, side)
    flat_ovf = max(0, col + len(flat_text) + len(tail) - width)

    if tag == "!":
        parens = parent is not None and _needs_parens(node, parent, side)
        pre = "(" if parens else ""
        post = ")" if parens else ""
        op_text, op_ovf, op_nls = _render(
            node[1], node, "operand", col + len(pre) + 1, level,
            post + tail, broken, path + (0,), width)
        return pre + "-" + op_text + post, op_ovf, op_nls

    if not _breakable(node) or path not in broken:
        return flat_text, flat_ovf, 0

    parens = parent is not None and _needs_parens(node, parent, side)
    pre = "(" if parens else ""
    post = ")" if parens else ""
    col_core = col + len(pre)
    core_tail = post + tail
    child_col = (level + 1) * len(INDENT)
    child_ind = INDENT * (level + 1)

    if tag == "b":
        op = node[1]
        left = _render(node[2], node, "left", col_core, level,
                       " " + op, broken, path + (0,), width)
        right = _render(node[3], node, "right", child_col, level + 1,
                        core_tail, broken, path + (1,), width)
        text = pre + left[0] + " " + op + "\n" + child_ind + right[0] + post
        return text, left[1] + right[1], left[2] + 1 + right[2]

    if tag == "?":
        test = _render(node[1], node, "cond", col_core, level,
                       "?", broken, path + (0,), width)
        then = _render(node[2], node, "then", child_col, level + 1,
                       ":", broken, path + (1,), width)
        els = _render(node[3], node, "else", child_col, level + 1,
                      core_tail, broken, path + (2,), width)
        text = (pre + test[0] + "?\n" + child_ind + then[0] + ":\n"
                + child_ind + els[0] + post)
        return (text, test[1] + then[1] + els[1],
                test[2] + then[2] + els[2] + 2)

    callee = _render(node[1], node, "callee", col_core, level,
                     "(", broken, path + (0,), width)
    parts = [pre + callee[0] + "("]
    ovf = callee[1]
    nls = callee[2]
    args = node[2]
    for i, arg in enumerate(args):
        arg_tail = "," if i < len(args) - 1 else ""
        blk = _render(arg, node, f"arg{i}", child_col, level + 1,
                      arg_tail, broken, path + (1 + i,), width)
        parts.append("\n" + child_ind + blk[0] + arg_tail)
        ovf += blk[1]
        nls += blk[2] + 1
    close = INDENT * level + ")"
    ovf += max(0, len(close) + len(core_tail) - width)
    nls += 1
    return "".join(parts) + "\n" + close + post, ovf, nls


def best_layout(tree, width: int, *, budget: int | None = None):
    """Provably best layout: ``(text, (overflow, newlines, text))``.

    Enumerates every flat/broken mask, keeps only candidates that
    re-parse to the identical tree, and ranks by the cost tuple.
    ``budget`` caps enumerated candidates (:class:`BudgetExhaustedError`);
    finishing with no valid candidate raises :class:`NoSolutionError`.
    """
    paths: list = []
    _sites(tree, (), paths)
    best = None
    tested = 0
    for mask in range(1 << len(paths)):
        if budget is not None and tested >= budget:
            raise BudgetExhaustedError(
                f"layout enumeration budget of {budget} candidates exhausted")
        tested += 1
        broken = frozenset(p for i, p in enumerate(paths) if mask >> i & 1)
        text, ovf, nls = _render(tree, None, "root", 0, 0, "",
                                 broken, (), width)
        if ref_parse(text) != tree:
            continue
        cand = (ovf, nls, text)
        if best is None or cand < best:
            best = cand
    if best is None:
        raise NoSolutionError(
            "enumerated every layout; none parses back to the same tree")
    return best[2], best


__all__ = ["best_layout"]
