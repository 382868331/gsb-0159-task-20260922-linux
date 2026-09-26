"""Independent enumeration reference (NOT part of the tested core).

This module re-implements, from scratch and without importing
:mod:`minparen`, everything needed to *certify* minimal output:

* a second AST representation (immutable tuples);
* a second hand-written recursive-descent parser;
* a formatter that emits an explicit parenthesis pair for every node
  selected by a bit mask;
* an exhaustive search over all selectable pairs on small trees.

The search checks exact-tree recovery by parsing each candidate with its own
parser, ranks candidates by ``(pair count, length, string)`` and returns the
winner. A bounded ``budget`` separates "stopped early"
(:class:`~minparen.errors.BudgetExhaustedError`) from "examined everything,
nothing satisfies the predicate"
(:class:`~minparen.errors.NoSolutionError`).

Only structural trees are enumerated (no comments); comment expectations in
the test-suite are fixed strings hand-derived from the documented grammar.
"""

from __future__ import annotations

from minparen.errors import BudgetExhaustedError, NoSolutionError

# Tuple AST:
#   ('n', int) ('v', str) ('!', x) ('b', op, l, r)
#   ('?', test, then, else_) ('f', callee, [args...])

_PUNCT = set("+-*/=?:(),")


# --------------------------------------------------------------------------
# Independent parser
# --------------------------------------------------------------------------

def _lex(src: str) -> list[str]:
    out: list[str] = []
    i, n = 0, len(src)
    while i < n:
        ch = src[i]
        if ch in " \t\n\r":
            i += 1
        elif ch in _PUNCT:
            out.append(ch)
            i += 1
        elif ch.isalpha() or ch == "_":
            j = i + 1
            while j < n and (src[j].isalnum() or src[j] == "_"):
                j += 1
            out.append(src[i:j])
            i = j
        elif ch.isdigit():
            j = i + 1
            while j < n and src[j].isdigit():
                j += 1
            out.append(src[i:j])
            i = j
        else:
            raise ValueError(f"bad character {ch!r}")
    return out


class _RP:
    def __init__(self, toks: list[str]):
        self.t = toks
        self.i = 0

    def peek(self) -> str | None:
        return self.t[self.i] if self.i < len(self.t) else None

    def take(self, v: str | None = None) -> str:
        tok = self.peek()
        assert tok is not None and (v is None or tok == v), (v, tok)
        self.i += 1
        return tok

    def assign(self):
        left = self.cond()
        if self.peek() == "=":
            self.take("=")
            return ("b", "=", left, self.assign())
        return left

    def cond(self):
        test = self.add()
        if self.peek() == "?":
            self.take("?")
            then = self.assign()
            self.take(":")
            return ("?", test, then, self.cond())
        return test

    def add(self):
        node = self.mul()
        while self.peek() in ("+", "-"):
            op = self.take()
            node = ("b", op, node, self.mul())
        return node

    def mul(self):
        node = self.unary()
        while self.peek() in ("*", "/"):
            op = self.take()
            node = ("b", op, node, self.unary())
        return node

    def unary(self):
        if self.peek() == "-":
            self.take("-")
            return ("!", self.unary())
        return self.postfix()

    def postfix(self):
        node = self.primary()
        while self.peek() == "(":
            self.take("(")
            args = []
            if self.peek() != ")":
                args.append(self.assign())
                while self.peek() == ",":
                    self.take(",")
                    args.append(self.assign())
            self.take(")")
            node = ("f", node, tuple(args))
        return node

    def primary(self):
        tok = self.peek()
        assert tok is not None
        if tok[0].isdigit():
            self.take()
            return ("n", int(tok))
        if tok[0].isalpha() or tok[0] == "_":
            self.take()
            return ("v", tok)
        self.take("(")
        node = self.assign()
        self.take(")")
        return node


def ref_parse(src: str):
    p = _RP(_lex(src))
    tree = p.assign()
    if p.peek() is not None:
        raise ValueError("trailing tokens")
    return tree


# --------------------------------------------------------------------------
# Explicit-paren formatter and enumeration
# --------------------------------------------------------------------------

def _children(node):
    tag = node[0]
    if tag == "!":
        return [node[1]]
    if tag == "b":
        return [node[2], node[3]]
    if tag == "?":
        return [node[1], node[2], node[3]]
    if tag == "f":
        return [node[1], *node[2]]
    return []


def _core(node, wrap: set[int], ids: dict[int, int]) -> str:
    tag = node[0]
    if tag == "n":
        s = str(node[1])
    elif tag == "v":
        s = node[1]
    elif tag == "!":
        s = "-" + _emit(node[1], wrap, ids)
    elif tag == "b":
        s = _emit(node[2], wrap, ids) + node[1] + _emit(node[3], wrap, ids)
    elif tag == "?":
        s = (_emit(node[1], wrap, ids) + "?" + _emit(node[2], wrap, ids)
             + ":" + _emit(node[3], wrap, ids))
    else:
        s = (_emit(node[1], wrap, ids) + "("
             + ",".join(_emit(a, wrap, ids) for a in node[2]) + ")")
    return s


def _emit(node, wrap: set[int], ids: dict[int, int], is_root: bool = False) -> str:
    s = _core(node, wrap, ids)
    if not is_root and ids[id(node)] in wrap:
        return f"({s})"
    return s


def _collect(node, ids: dict[int, int], order: list, depth: int = 0) -> None:
    ids[id(node)] = len(order)
    order.append((node, depth))
    for ch in _children(node):
        _collect(ch, ids, order, depth + 1)


def best_wrapping(tree, *, budget: int | None = None, accept=None):
    """Return ``(string, pair_count)`` for the provably minimal rendering.

    ``budget`` caps the number of enumerated candidates; exceeding it raises
    :class:`BudgetExhaustedError`. If enumeration finishes but no candidate
    parses back to the same tree *and* satisfies ``accept``, raises
    :class:`NoSolutionError`.
    """
    ids: dict[int, int] = {}
    order: list = []
    _collect(tree, ids, order)
    # The root is never wrapped (a root pair can never be optimal); every
    # other node is an optional site, leaves included for completeness.
    sites = [n for n, _ in order[1:]]
    k = len(sites)

    best = None  # (pairs, length, string)
    tested = 0
    for mask in range(1 << k):
        if budget is not None and tested >= budget:
            raise BudgetExhaustedError(
                f"enumeration budget of {budget} candidates exhausted")
        wrap = {ids[id(sites[j])] for j in range(k) if mask & (1 << j)}
        s = _emit(tree, wrap, ids, is_root=True)
        tested += 1
        recovered = ref_parse(s)
        if recovered != tree:
            continue
        if accept is not None and not accept(s):
            continue
        cand = (bin(mask).count("1"), len(s), s)
        if best is None or cand < best:
            best = cand

    if best is None:
        raise NoSolutionError(
            "enumerated every parenthesization; none satisfies constraints")
    return best[2], best[0]


def all_valid(tree) -> list[str]:
    """Every distinct string that round-tps to ``tree`` (small trees only)."""
    ids: dict[int, int] = {}
    order: list = []
    _collect(tree, ids, order)
    sites = [n for n, _ in order[1:]]
    out = set()
    for mask in range(1 << len(sites)):
        wrap = {ids[id(sites[j])] for j in range(len(sites))
                if mask & (1 << j)}
        s = _emit(tree, wrap, ids, is_root=True)
        if ref_parse(s) == tree:
            out.add(s)
    return sorted(out)
