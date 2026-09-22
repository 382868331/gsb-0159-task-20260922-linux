"""Independent parser: source text -> AST.

Plain recursive-descent recognizer::

    assign := cond ('=' assign)?
    cond   := add ('?' assign ':' cond)?
    add    := mul (('+'|'-') mul)*
    mul    := unary (('*'|'/') unary)*
    unary  := '-' unary | postfix
    postfix:= primary ('(' (assign (',' assign)*)? ')')*
    primary:= NUMBER | IDENT | '(' assign ')'

Comment attribution is structural. While parsing, every grammar boundary
*claims* the token gap on one side of a node::

    a + b        gap(a, '+') -> left.trail,  gap('+', b) -> right.lead
    a ? b : c    four gaps around '?' and ':' -> test/then/else sides
    -(x)         gap('-', x) -> operand.lead
    f(x, y)      callee trail, per-argument lead/trail
    (x)          the two gaps inside the parens -> inner node lead/trail

After the parse, each ``/*...*/`` token is looked up in the claim table. A
comment in an unclaimed gap (e.g. ``f( /*c*/ )`` with no argument) is
rejected, and two comments landing on the same node side are rejected: the
AST binds at most one lead and one trail per node. Since gaps are disjoint
and attribution never looks at neighboring siblings, comments cannot migrate
across sibling nodes.
"""

from __future__ import annotations

from .errors import ParseError
from .validation import MAX_NODES, validate_tree

T_IDENT = "ident"
T_NUMBER = "number"
T_PUNCT = "punct"
T_COMMENT = "comment"

_PUNCT = set("+-*/=?:(),")


class _Tok:
    __slots__ = ("kind", "value", "start")

    def __init__(self, kind: str, value: str, start: int):
        self.kind = kind
        self.value = value
        self.start = start


def _line_col(src: str, offset: int) -> tuple[int, int]:
    line = src.count("\n", 0, offset) + 1
    last_nl = src.rfind("\n", 0, offset)
    return line, offset - last_nl


def _err(src: str, offset: int, message: str) -> ParseError:
    line, col = _line_col(src, offset)
    return ParseError(message, offset, line, col)


def _tokenize(src: str) -> list[_Tok]:
    tokens: list[_Tok] = []
    i, n = 0, len(src)
    while i < n:
        ch = src[i]
        if ch in " \t\n\r":
            i += 1
            continue
        if ch == "/" and i + 1 < n and src[i + 1] == "*":
            j = i + 2
            while j < n and not (src[j] == "*" and j + 1 < n
                                 and src[j + 1] == "/"):
                if src[j] in "\n\r":
                    raise _err(src, j,
                               "comments must not contain newline characters")
                if src[j] == "\x00":
                    raise _err(src, j,
                               "comments must not contain NUL bytes")
                j += 1
            if j >= n:
                raise _err(src, i, "unterminated comment")
            tokens.append(_Tok(T_COMMENT, src[i + 2:j], i))
            i = j + 2
            continue
        if ch in _PUNCT:
            tokens.append(_Tok(T_PUNCT, ch, i))
            i += 1
            continue
        if ("a" <= ch <= "z") or ("A" <= ch <= "Z") or ch == "_":
            j = i + 1
            while j < n and (("a" <= src[j] <= "z")
                             or ("A" <= src[j] <= "Z")
                             or ("0" <= src[j] <= "9")
                             or src[j] == "_"):
                j += 1
            tokens.append(_Tok(T_IDENT, src[i:j], i))
            i = j
            continue
        if "0" <= ch <= "9":
            j = i + 1
            while j < n and "0" <= src[j] <= "9":
                j += 1
            tokens.append(_Tok(T_NUMBER, src[i:j], i))
            i = j
            continue
        raise _err(src, i, f"unexpected character {ch!r}")
    return tokens


class _Parser:
    def __init__(self, src: str, max_nodes: int):
        self.src = src
        self.tokens = _tokenize(src)
        self.max_nodes = max_nodes
        self.node_count = 0
        self.pos = 0  # global index into self.tokens
        # (prev non-comment index, next non-comment index) -> (node, side)
        self.gaps: dict[tuple[int, int], tuple[dict, str]] = {}

    # -- token stream ------------------------------------------------------

    def _skip(self) -> None:
        while (self.pos < len(self.tokens)
               and self.tokens[self.pos].kind == T_COMMENT):
            self.pos += 1

    def _peek(self) -> _Tok | None:
        self._skip()
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def _take(self, expected: str | None = None) -> _Tok:
        tok = self._peek()
        if tok is None:
            raise _err(self.src, len(self.src),
                       f"unexpected end of input"
                       + (f", expected {expected!r}" if expected else ""))
        if expected is not None and (tok.kind != T_PUNCT
                                     or tok.value != expected):
            raise _err(self.src, tok.start,
                       f"expected {expected!r}, got {tok.value!r}")
        self.pos += 1
        return tok

    def _count(self, tok: _Tok) -> None:
        self.node_count += 1
        if self.node_count > self.max_nodes:
            raise _err(self.src, tok.start,
                       f"expression exceeds {self.max_nodes} nodes "
                       "(interface limit)")

    def _claim(self, prev: int, nxt: int, node: dict, side: str) -> None:
        self.gaps[(prev, nxt)] = (node, side)

    # -- grammar: each method returns (node, first_idx, last_idx) ----------

    def parse(self) -> dict:
        root, _, _ = self._assign()
        leftover = self._peek()
        if leftover is not None:
            raise _err(self.src, leftover.start,
                       f"unexpected trailing token {leftover.value!r}")

        noncomment = [i for i, t in enumerate(self.tokens)
                      if t.kind != T_COMMENT]
        if not noncomment:
            raise _err(self.src, 0, "empty expression")
        first, last = noncomment[0], noncomment[-1]
        # Document-edge gaps always belong to the root node.
        self._claim(-1, first, root, "lead")
        self._claim(last, len(self.tokens), root, "trail")

        self._attach_comments()
        # Strict structural validation of the finished tree (raises ASTError
        # only if the parser itself built something off-contract).
        return validate_tree(root, max_nodes=self.max_nodes)

    def _assign(self) -> tuple[dict, int, int]:
        left, lf, ll = self._cond()
        tok = self._peek()
        if tok is not None and tok.kind == T_PUNCT and tok.value == "=":
            oi = self.pos
            self._take("=")
            right, rf, rl = self._assign()
            self._claim(ll, oi, left, "trail")
            self._claim(oi, rf, right, "lead")
            node = {"kind": "bin", "op": "=", "left": left, "right": right}
            self._count(tok)
            return node, lf, rl
        return left, lf, ll

    def _cond(self) -> tuple[dict, int, int]:
        test, tf, tl = self._add()
        tok = self._peek()
        if tok is None or tok.kind != T_PUNCT or tok.value != "?":
            return test, tf, tl
        qi = self.pos
        self._take("?")
        then, hf, hl = self._assign()
        colon = self._peek()
        if colon is None or colon.kind != T_PUNCT or colon.value != ":":
            at = self.tokens[self.pos].start if self.pos < len(self.tokens) \
                else len(self.src)
            raise _err(self.src, at, "ternary is missing its ':' branch")
        ci = self.pos
        self._take(":")
        else_node, ef, el = self._cond()
        self._claim(tl, qi, test, "trail")
        self._claim(qi, hf, then, "lead")
        self._claim(hl, ci, then, "trail")
        self._claim(ci, ef, else_node, "lead")
        node = {"kind": "cond", "cond": test, "then": then,
                "else": else_node}
        self._count(tok)
        return node, tf, el

    def _add(self) -> tuple[dict, int, int]:
        return self._binary_loop(self._mul, "+-")

    def _mul(self) -> tuple[dict, int, int]:
        return self._binary_loop(self._unary, "*/")

    def _binary_loop(self, sub, ops: str) -> tuple[dict, int, int]:
        left, lf, ll = sub()
        while True:
            tok = self._peek()
            if tok is None or tok.kind != T_PUNCT or tok.value not in ops:
                return left, lf, ll
            oi = self.pos
            self._take()
            right, _, rl = sub()
            rf = self._first_noncomment_after(oi)
            self._claim(ll, oi, left, "trail")
            self._claim(oi, rf, right, "lead")
            left = {"kind": "bin", "op": tok.value, "left": left,
                    "right": right}
            self._count(tok)
            ll = rl

    def _unary(self) -> tuple[dict, int, int]:
        tok = self._peek()
        if tok is not None and tok.kind == T_PUNCT and tok.value == "-":
            mi = self.pos
            self._take("-")
            operand, of, ol = self._unary()
            self._claim(mi, of, operand, "lead")
            node = {"kind": "neg", "operand": operand}
            self._count(tok)
            return node, mi, ol
        return self._postfix()

    def _postfix(self) -> tuple[dict, int, int]:
        node, nf, nl = self._primary()
        while True:
            tok = self._peek()
            if tok is None or tok.kind != T_PUNCT or tok.value != "(":
                return node, nf, nl
            oi = self.pos
            self._take("(")
            args: list[dict] = []
            if self._peek() is not None and self._peek().value != ")":
                arg, af, al = self._assign()
                self._claim(oi, af, arg, "lead")
                args.append(arg)
                while True:
                    comma = self._peek()
                    if comma is None or comma.value != ",":
                        break
                    ki = self.pos
                    self._take(",")
                    self._claim(al, ki, arg, "trail")
                    arg, af, al = self._assign()
                    self._claim(ki, af, arg, "lead")
                    args.append(arg)
                self._claim(al, self._next_index(), args[-1], "trail")
            close_tok = self._peek()
            if close_tok is None or close_tok.value != ")":
                at = self.tokens[self.pos].start if self.pos < len(self.tokens) \
                    else len(self.src)
                raise _err(self.src, at, "function call is missing ')'")
            ci = self.pos
            self._take(")")
            self._claim(nl, oi, node, "trail")
            node = {"kind": "call", "callee": node, "args": args}
            self._count(tok if tok is not None else close_tok)
            nl = ci

    def _primary(self) -> tuple[dict, int, int]:
        tok = self._peek()
        if tok is None:
            raise _err(self.src, len(self.src),
                       "expected an expression, got end of input")
        if tok.kind == T_NUMBER:
            idx = self.pos
            self._take()
            node = {"kind": "num", "value": int(tok.value)}
            self._count(tok)
            return node, idx, idx
        if tok.kind == T_IDENT:
            idx = self.pos
            self._take()
            node = {"kind": "var", "name": tok.value}
            self._count(tok)
            return node, idx, idx
        if tok.kind == T_PUNCT and tok.value == "(":
            oi = self.pos
            self._take("(")
            node, inner_f, inner_l = self._assign()
            close = self._peek()
            if close is None or close.value != ")":
                at = self.tokens[self.pos].start if self.pos < len(self.tokens) \
                    else len(self.src)
                raise _err(self.src, at,
                           "parenthesized expression is missing ')'")
            ci = self.pos
            self._take(")")
            self._claim(oi, inner_f, node, "lead")
            self._claim(inner_l, ci, node, "trail")
            # The parens become the node's outer span for claims above.
            return node, oi, ci
        raise _err(self.src, tok.start, f"unexpected token {tok.value!r}")

    # -- comment attachment ------------------------------------------------

    def _next_index(self) -> int:
        """Index of the current lookahead ')'/',' token (comments skipped)."""
        self._skip()
        return self.pos

    def _first_noncomment_after(self, idx: int) -> int:
        j = idx + 1
        while (j < len(self.tokens)
               and self.tokens[j].kind == T_COMMENT):
            j += 1
        return j

    def _attach_comments(self) -> None:
        for ci, tok in enumerate(self.tokens):
            if tok.kind != T_COMMENT:
                continue
            prev = ci - 1
            while prev >= 0 and self.tokens[prev].kind == T_COMMENT:
                prev -= 1
            nxt = ci + 1
            while (nxt < len(self.tokens)
                   and self.tokens[nxt].kind == T_COMMENT):
                nxt += 1
            owner = self.gaps.get((prev, nxt))
            if owner is None:
                raise _err(self.src, tok.start,
                           "comment sits in a gap that cannot be bound to "
                           "an AST node (empty argument list or stray "
                           "position)")
            node, side = owner
            if side in node:
                raise _err(self.src, tok.start,
                           f"two comments compete for one node {side}; "
                           "at most one lead and one trail are supported")
            node[side] = tok.value


def parse(source: str, *, max_nodes: int = MAX_NODES) -> dict:
    """Parse ``source`` into an AST dict.

    Raises :class:`~minparen.errors.ParseError` with 0-based offset and
    1-based line/column on invalid syntax, unterminated or newline-bearing
    comments, comments in a gap no node can bind, or when the expression
    contains more than ``max_nodes`` nodes.
    """
    if not isinstance(source, str):
        raise TypeError("source must be a str")
    return _Parser(source, max_nodes).parse()


__all__ = ["parse", "ParseError"]
