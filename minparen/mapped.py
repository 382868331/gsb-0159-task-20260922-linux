"""Position-mapped printing: the minimal text plus exact source spans.

:func:`print_ast_mapped` emits the very same string as
:func:`minparen.print_ast` (both share the single emission path in
:mod:`minparen.printer`) and additionally reports, for a validated AST:

* ``text``      -- the printed string;
* ``nodes``     -- one :class:`NodeSpan` per AST node (pre-order), keyed by
  a deterministic ``path`` (``root``, ``root.right``, ``root.args[0]`` ...)
  with:
    - ``core``   half-open ``[start, end)`` of the node's *syntax core*:
      its own operators and operands, **excluding** its lead/trail comments
      and its auto-added parentheses. ``parse(text[core])`` reproduces the
      node's comment-free subtree;
    - ``span``   the node's full coverage: auto-added parens and comments
      included. A parent's ``span`` always contains its children's spans;
    - ``parens`` the auto-added parenthesis pair (owned by this node), or
      ``None`` when the slot needed none;
    - ``lead`` / ``trail``  the node's own comment token spans, or ``None``;
* ``operators`` -- one :class:`OperatorSpan` per operator token (binary
  ``+ - * / =``, unary ``-``, ternary ``?`` and ``:``), each linked to the
  owning node path;
* ``comments``  -- one :class:`CommentSpan`` per ``/*...*/`` token, linked
  to the owning node path and side (``lead``/``trail``).

All coordinates are **Python string character offsets** (not UTF-8 bytes),
half-open ``[start, end)``. Non-ASCII comment text counts one offset per
character, exactly as ``len()`` and slicing see it.

Positions are recorded while emitting (cursor-based); nothing is derived by
searching the output for equal substrings, so two identical subtrees still
map to their own distinct spans.

:meth:`MappedPrint.query` classifies a single offset. Boundary rule: spans
are half-open, so an offset that ends one span and starts the next belongs
to the span that *starts* there. Offsets landing on a comment, an operator,
an auto-added paren, or whitespace are reported as exactly that category --
never silently attributed to a neighboring node.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from .printer import _render

# Query categories.
CORE = "core"
OPERATOR = "operator"
COMMENT = "comment"
PAREN = "paren"
WHITESPACE = "whitespace"


@dataclass(frozen=True)
class NodeSpan:
    """Span record for one AST node.

    ``node`` is the validated (canonicalized) node dict owned by
    ``MappedPrint.tree``; ``path`` locates the same position in the
    caller's own tree (same scheme as
    :class:`minparen.errors.ASTError` paths).
    """

    path: str
    kind: str
    node: dict
    core: tuple[int, int]
    span: tuple[int, int]
    lead: Optional[tuple[int, int]]
    trail: Optional[tuple[int, int]]
    parens: Optional[tuple[tuple[int, int], tuple[int, int]]]


@dataclass(frozen=True)
class OperatorSpan:
    """One operator token. ``unary`` is True for the prefix ``-``."""

    op: str
    span: tuple[int, int]
    path: str
    unary: bool = False


@dataclass(frozen=True)
class CommentSpan:
    """One ``/*...*/`` token. ``span`` covers the delimiters; ``text`` is
    the inner comment text. ``side`` is ``"lead"`` or ``"trail"``."""

    text: str
    span: tuple[int, int]
    path: str
    side: str


@dataclass(frozen=True)
class QueryResult:
    """Outcome of :meth:`MappedPrint.query` for one offset.

    Exactly one of ``node``/``operator``/``comment`` carries the hit
    (``node`` is also set as the *owner* for operator/comment/paren hits).
    """

    offset: int
    category: str
    node: Optional[NodeSpan] = None
    operator: Optional[OperatorSpan] = None
    comment: Optional[CommentSpan] = None


_PATH_STEP = re.compile(r"\.([A-Za-z]+)(?:\[(\d+)\])?")


@dataclass(frozen=True)
class MappedPrint:
    """Printed text plus the full position mapping."""

    text: str
    tree: dict
    nodes: tuple[NodeSpan, ...]
    operators: tuple[OperatorSpan, ...]
    comments: tuple[CommentSpan, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "_by_path", {n.path: n for n in self.nodes})

    # -- lookups -----------------------------------------------------------

    def node_at(self, path: str) -> NodeSpan:
        """Return the :class:`NodeSpan` for ``path`` (KeyError if absent)."""
        return self._by_path[path]

    def resolve(self, path: str) -> dict:
        """Walk ``path`` (e.g. ``root.right.args[0]``) into ``self.tree``."""
        if path == "root":
            return self.tree
        if not path.startswith("root."):
            raise ValueError(f"invalid node path {path!r}")
        node = self.tree
        rest = path[4:]
        while rest:
            m = _PATH_STEP.match(rest)
            if m is None:
                raise ValueError(f"invalid node path {path!r}")
            node = node[m.group(1)]
            if m.group(2) is not None:
                node = node[int(m.group(2))]
            rest = rest[m.end():]
        return node

    # -- position query ----------------------------------------------------

    def query(self, offset: int) -> QueryResult:
        """Classify the character at ``offset`` (0-based, half-open rules).

        Categories: ``"comment"``, ``"operator"``, ``"paren"`` (auto-added
        pair owned by the reported node), ``"whitespace"``, or ``"core"``
        (with the innermost node whose core covers the offset). Raises
        ``TypeError`` for non-int offsets and ``ValueError`` when ``offset``
        is outside ``[0, len(text))``.
        """
        if isinstance(offset, bool) or not isinstance(offset, int):
            raise TypeError("offset must be an int (bool rejected)")
        if offset < 0 or offset >= len(self.text):
            raise ValueError(
                f"offset {offset} outside [0, {len(self.text)})")
        for c in self.comments:
            if c.span[0] <= offset < c.span[1]:
                return QueryResult(offset, COMMENT,
                                   node=self._by_path[c.path], comment=c)
        for o in self.operators:
            if o.span[0] <= offset < o.span[1]:
                return QueryResult(offset, OPERATOR,
                                   node=self._by_path[o.path], operator=o)
        for n in self.nodes:
            p = n.parens
            if p is not None and (p[0][0] <= offset < p[0][1]
                                  or p[1][0] <= offset < p[1][1]):
                return QueryResult(offset, PAREN, node=n)
        if self.text[offset].isspace():
            # The printer never emits whitespace outside comments; this
            # branch keeps the query contract total for hand-built maps.
            return QueryResult(offset, WHITESPACE)
        best: Optional[NodeSpan] = None
        for n in self.nodes:
            s, e = n.core
            if s <= offset < e and (
                    best is None or (e - s) < (best.core[1] - best.core[0])):
                best = n
        return QueryResult(offset, CORE, node=best)


def print_ast_mapped(root: dict) -> MappedPrint:
    """Print ``root`` minimally and return the text plus position mapping.

    Same validation and error semantics as :func:`minparen.print_ast`
    (:class:`~minparen.errors.ASTError` on contract violations; the input
    is not mutated). ``MappedPrint.text`` is byte-identical to
    ``print_ast(root)``.
    """
    safe, rec, text = _render(root)
    nodes = tuple(
        NodeSpan(path=e["path"], kind=e["kind"], node=e["node"],
                 core=e["core"], span=e["span"], lead=e["lead"],
                 trail=e["trail"], parens=e["parens"])
        for e in rec.nodes
    )
    operators = tuple(
        OperatorSpan(op=o["op"], span=o["span"], path=o["path"],
                     unary=o["unary"])
        for o in rec.operators
    )
    comments = tuple(
        CommentSpan(text=c["text"], span=c["span"], path=c["path"],
                    side=c["side"])
        for c in rec.comments
    )
    return MappedPrint(text=text, tree=safe, nodes=nodes,
                       operators=operators, comments=comments)


__all__ = [
    "print_ast_mapped",
    "MappedPrint",
    "NodeSpan",
    "OperatorSpan",
    "CommentSpan",
    "QueryResult",
    "CORE",
    "OPERATOR",
    "COMMENT",
    "PAREN",
    "WHITESPACE",
]
