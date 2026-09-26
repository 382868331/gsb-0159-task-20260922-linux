"""Independent layout-enumeration reference (NOT part of the tested core).

Like :mod:`reference.refenum` for parentheses, this module re-implements
the width-aware layout rules from scratch -- its own precedence/slot
table, its own flat printer, its own broken-shape builders -- and then
enumerates *every* legal layout of a small tree (each group independently
flat or broken, flat groups flat all the way down) to certify that
:func:`minparen.format_ast` returns the optimum under the documented
penalty order ``(overflow, newlines, text)``.

It imports nothing from :mod:`minparen` except the public error types.
Only structural trees are enumerated (no comments); comment expectations
in the test-suite are fixed strings.

Bounded search separates "stopped early"
(:class:`~minparen.errors.BudgetExhaustedError`) from "enumerated
everything, no layout satisfies the predicate"
(:class:`~minparen.errors.NoSolutionError`).
"""

from __future__ import annotations

from minparen.errors import BudgetExhaustedError, NoSolutionError

INDENT = 2

# --- independent precedence / slot table (levels: loose -> tight) ----------
_A, _C, _P, _M, _U, _F, _L = range(1, 8)

_TIGHT = {
    ("=", "l"): (_C, 0), ("=", "r"): (_A, 0),
    ("+", "l"): (_P, 0), ("+", "r"): (_P, 1),
    ("*", "l"): (_M, 0), ("*", "r"): (_M, 1),
    ("?", "c"): (_P, 0), ("?", "t"): (_A, 0), ("?", "e"): (_C, 0),
    ("-", "o"): (_U, 0),
    ("(", "f"): (_F, 0), ("(", "a"): (_A, 0),
}


def _lvl(n: dict) -> int:
    k = n["kind"]
    if k in ("num", "var"):
        return _L
    if k == "call":
        return _F
    if k == "neg":
        return _U
    if k == "bin":
        return _A if n["op"] == "=" else (_P if n["op"] in "+-" else _M)
    return _C


def _group(op_kind: str) -> str:
    if op_kind == "bin=":
        return "="
    if op_kind == "bin+-":
        return "+"
    if op_kind == "bin*/":
        return "*"
    return op_kind


def _paren_free(child: dict, parent: dict | None, slot: str) -> bool:
    if parent is None:
        return True
    pk = parent["kind"]
    if pk == "bin":
        group = "bin=" if parent["op"] == "=" else (
            "bin+-" if parent["op"] in "+-" else "bin*/")
        key = (_group(group), {"left": "l", "right": "r"}[slot])
    elif pk == "cond":
        key = ("?", {"cond": "c", "then": "t", "else": "e"}[slot])
    elif pk == "neg":
        key = ("-", "o")
    else:
        key = ("(", "f" if slot == "callee" else "a")
    level, strict = _TIGHT[key]
    cl = _lvl(child)
    return cl > level or (cl == level and not strict)


# --- independent flat printer ----------------------------------------------

def _flat(node: dict, parent: dict | None = None, slot: str = "") -> str:
    k = node["kind"]
    if k == "num":
        s = str(node["value"])
    elif k == "var":
        s = node["name"]
    elif k == "neg":
        s = "-" + _flat(node["operand"], node, "operand")
    elif k == "bin":
        s = (_flat(node["left"], node, "left") + node["op"]
             + _flat(node["right"], node, "right"))
    elif k == "cond":
        s = (_flat(node["cond"], node, "cond") + "?"
             + _flat(node["then"], node, "then") + ":"
             + _flat(node["else"], node, "else"))
    else:
        s = (_flat(node["callee"], node, "callee") + "("
             + ",".join(_flat(a, node, "arg") for a in node["args"]) + ")")
    if _paren_free(node, parent, slot):
        return s
    return f"({s})"


# --- independent layout enumeration -----------------------------------------

def _layouts(node: dict, parent: dict | None, slot: str, col: int,
             counter: list[int], budget: int | None):
    """Yield every legal layout of ``node`` at absolute column ``col``.

    A layout is a list of absolute lines (first line included, indented).
    """
    counter[0] += 1
    if budget is not None and counter[0] > budget:
        raise BudgetExhaustedError(
            f"layout enumeration budget of {budget} candidate(s) exhausted; "
            "the optimum is unknown, not proven absent")

    def wrap(core_lines):
        if _paren_free(node, parent, slot):
            return core_lines
        if len(core_lines) == 1:
            return [" " * col + "(" + core_lines[0][col:] + ")"]
        return [" " * col + "(" + core_lines[0][col:],
                *core_lines[1:-1], core_lines[-1] + ")"]

    yield wrap([" " * col + _flat(node)]).copy() if False else \
        wrap([" " * col + _flat(node)])

    k = node["kind"]
    if k in ("num", "var"):
        return
    if k == "neg":
        for sub in _layouts(node["operand"], node, "operand", col + 1,
                            counter, budget):
            yield wrap([sub[0][:col] + "-" + sub[0][col + 1:], *sub[1:]])
    elif k == "bin":
        for left in _layouts(node["left"], node, "left", col,
                             counter, budget):
            for right in _layouts(node["right"], node, "right",
                                  col + INDENT, counter, budget):
                yield wrap([*left[:-1], left[-1] + " " + node["op"],
                            *right])
    elif k == "cond":
        for test in _layouts(node["cond"], node, "cond", col,
                             counter, budget):
            for then in _layouts(node["then"], node, "then",
                                 col + INDENT, counter, budget):
                for otherwise in _layouts(node["else"], node, "else",
                                          col + INDENT, counter, budget):
                    yield wrap([*test[:-1], test[-1] + " ?",
                                *then[:-1], then[-1] + " :",
                                *otherwise])
    elif k == "call" and node["args"]:
        for callee in _layouts(node["callee"], node, "callee", col,
                               counter, budget):
            heads = [[*callee[:-1], callee[-1] + "("]]
            for i, arg in enumerate(node["args"]):
                last = i == len(node["args"]) - 1
                heads = [head + block[:-1]
                         + [block[-1] + ("" if last else ",")]
                         for head in heads
                         for block in _layouts(arg, node, "arg",
                                               col + INDENT, counter,
                                               budget)]
            for head in heads:
                yield wrap([*head, " " * col + ")"])


def _penalty(lines: list[str], width: int) -> tuple[int, int, str]:
    overflow = sum(max(0, len(line) - width) for line in lines)
    return (overflow, len(lines) - 1, "\n".join(lines))


def best_layout(tree: dict, width: int, *, budget: int | None = None,
                accept=None) -> str:
    """Enumerate every legal layout of ``tree`` and return the best text.

    Raises :class:`BudgetExhaustedError` when more than ``budget``
    candidate layouts would have to be examined (answer unknown), and
    :class:`NoSolutionError` when the enumeration completed but no layout
    satisfies ``accept`` (proven absent).
    """
    counter = [0]
    best: tuple[int, int, str] | None = None
    for lines in _layouts(tree, None, "", 0, counter, budget):
        text = "\n".join(lines)
        if accept is not None and not accept(text):
            continue
        key = _penalty(lines, width)
        if best is None or key < best:
            best = key
    if best is None:
        raise NoSolutionError(
            "enumerated every legal layout; none satisfies the predicate")
    return best[2]


__all__ = ["best_layout", "INDENT"]
