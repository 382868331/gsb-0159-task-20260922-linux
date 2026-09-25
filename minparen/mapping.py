"""Position-mapping print API: printed text -> AST node locations.

:func:`print_ast_mapped` performs the *same* traversal as
:func:`minparen.printer.print_ast` (both are driven by
:func:`minparen.printer._print_with_map`, so the emitted text is identical
by construction) and additionally records where every node, operator,
auto-added parenthesis and comment landed in the output string.

Coordinate contract
-------------------

* All coordinates are 0-based **Python string character offsets** into the
  printed text (``str`` indexing, not UTF-8 bytes -- a non-ASCII comment
  counts one offset per code point).
* Every interval is **half-open** ``[start, end)``: ``start`` is included,
  ``end`` is the first offset *after* the feature. Adjacent features share
  the boundary offset, and a query exactly on a boundary resolves to the
  feature that *starts* there.
* ``NodeSpan.span`` covers the whole node extent: the auto-added
  parenthesis pair (when present), its lead/trail comments and its syntax
  core. A parent's ``span`` always contains its children's spans, and the
  auto-added pair belongs to the node it groups (never to parent or
  children).
* ``NodeSpan.core`` is the **syntax core**: the node's own operators,
  punctuation and children -- *without* its lead/trail comments and
  *without* the auto-added pair. Slicing ``text[core[0]:core[1]]`` yields
  a string that :func:`minparen.parse` parses back to the same subtree
  (minus the subtree root's own comments).
* Comments are mapped separately (``NodeSpan.lead`` / ``NodeSpan.trail``
  and the flat :attr:`PrintMap.comments` list) and never counted in the
  core interval.

Positions are recorded while the text is emitted -- never by searching
the output for matching substrings -- so identical subtrees printed at
different places are still mapped to their own distinct intervals.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .printer import _print_with_map

# Position categories returned by :meth:`PrintMap.query`.
CODE = "code"            # leaf text (identifier / integer literal)
OPERATOR = "operator"    # an operator or call punctuation token
PAREN = "paren"          # an auto-added grouping parenthesis
COMMENT = "comment"      # inside a /* ... */ comment
WHITESPACE = "whitespace"  # a whitespace character (see module notes)

CATEGORIES = (CODE, OPERATOR, PAREN, COMMENT, WHITESPACE)


@dataclass(frozen=True, slots=True)
class OpSpan:
    """One operator/punctuation token owned by a node.

    ``symbol`` is one of ``+ - * / = ? : ( ) ,``; the ``(`` ``,`` ``)``
    symbols belong to ``call`` nodes (call parentheses are syntax, not
    auto-added grouping -- those live in :attr:`NodeSpan.parens`).
    """

    symbol: str
    span: tuple[int, int]
    path: str


@dataclass(frozen=True, slots=True)
class CommentSpan:
    """One ``/* ... */`` comment attached to a node (``side`` lead/trail)."""

    side: str
    path: str
    span: tuple[int, int]
    text: str  # comment body without the /* */ markers


@dataclass(frozen=True, slots=True)
class NodeSpan:
    """Where one AST node landed in the printed text.

    ``path`` is the deterministic accessor path from the root (the same
    scheme :class:`minparen.errors.ASTError` uses), e.g.
    ``root.right.args[1]``. ``parent`` is the parent's path (``None`` for
    the root).
    """

    path: str
    kind: str
    depth: int
    parent: str | None
    span: tuple[int, int]
    core: tuple[int, int]
    parens: tuple[tuple[int, int], tuple[int, int]] | None
    lead: tuple[int, int] | None
    trail: tuple[int, int] | None
    operators: tuple[OpSpan, ...]


@dataclass(frozen=True, slots=True)
class PositionInfo:
    """What sits at one offset of the printed text.

    ``category`` is one of :data:`CATEGORIES`. ``path`` is the owning
    node's path (``None`` for whitespace). ``span`` is the matched
    feature's own interval. ``detail`` is the operator symbol for
    ``"operator"``, the side (``"lead"``/``"trail"``) for ``"comment"``,
    else ``None``.
    """

    offset: int
    category: str
    path: str | None
    span: tuple[int, int] | None
    detail: str | None


class PrintMap:
    """Printed text plus the map back to AST nodes. Immutable snapshot.

    Instances are normally produced by :func:`print_ast_mapped`. The
    constructor is public so tools can build maps for text from other
    sources; hand-built maps may omit ``comments`` (queries then cannot
    classify comment interiors). Note the printer itself never emits
    whitespace outside comments, so the ``"whitespace"`` category can only
    fire for text supplied this way.
    """

    __slots__ = ("text", "nodes", "operators", "comments", "_by_path")

    def __init__(self, text: str, nodes: Iterable[NodeSpan] = (),
                 comments: Iterable[CommentSpan] = ()):
        if not isinstance(text, str):
            raise TypeError("text must be a str")
        self.text = text
        self.nodes = tuple(nodes)
        self.comments = tuple(sorted(comments, key=lambda c: c.span))
        self._by_path: dict[str, NodeSpan] = {}
        ops: list[OpSpan] = []
        for node in self.nodes:
            if node.path in self._by_path:
                raise ValueError(f"duplicate node path {node.path!r}")
            self._by_path[node.path] = node
            ops.extend(node.operators)
        self.operators = tuple(sorted(ops, key=lambda o: o.span))

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"PrintMap(text={self.text!r}, nodes={len(self.nodes)})"

    def node(self, path: str) -> NodeSpan:
        """Look up a node by its path; raises :class:`KeyError` if unknown."""
        return self._by_path[path]

    def query(self, offset: int) -> PositionInfo:
        """Classify one offset of :attr:`text` (half-open boundary rules).

        Raises :class:`TypeError` for non-int offsets (``bool`` rejected)
        and :class:`ValueError` when ``offset`` is outside
        ``[0, len(text))``.
        """
        if isinstance(offset, bool) or not isinstance(offset, int):
            raise TypeError("offset must be an int (bool rejected)")
        if not 0 <= offset < len(self.text):
            raise ValueError(
                f"offset {offset} out of range [0, {len(self.text)})")

        for comment in self.comments:
            if comment.span[0] <= offset < comment.span[1]:
                return PositionInfo(offset, COMMENT, comment.path,
                                    comment.span, comment.side)
        if self.text[offset] in " \t\n\r":
            return PositionInfo(offset, WHITESPACE, None,
                                (offset, offset + 1), None)
        for node in self.nodes:
            if node.parens is not None:
                for paren in node.parens:
                    if paren[0] <= offset < paren[1]:
                        return PositionInfo(offset, PAREN, node.path,
                                            paren, None)
            for op in node.operators:
                if op.span[0] <= offset < op.span[1]:
                    return PositionInfo(offset, OPERATOR, node.path,
                                        op.span, op.symbol)
        best: NodeSpan | None = None
        for node in self.nodes:
            if node.core[0] <= offset < node.core[1]:
                if best is None or node.depth > best.depth:
                    best = node
        if best is not None:
            return PositionInfo(offset, CODE, best.path, best.core, None)
        # Unreachable for maps produced by print_ast_mapped: every emitted
        # character is a comment, an auto paren, an operator or leaf text.
        raise ValueError(f"offset {offset} is not covered by the node map")


def print_ast_mapped(root: dict) -> PrintMap:
    """Serialize ``root`` like :func:`minparen.print_ast` and map positions.

    Returns a :class:`PrintMap` whose ``text`` is byte-identical to
    ``print_ast(root)``. Raises :class:`~minparen.errors.ASTError` on any
    contract violation; the input tree is not mutated.
    """
    text, records = _print_with_map(root)
    stack: list[str] = []
    nodes: list[NodeSpan] = []
    comments: list[CommentSpan] = []
    for record in records:
        depth = record["depth"]
        parent = stack[depth - 1] if depth else None
        stack[depth:] = [record["path"]]
        operators = tuple(
            OpSpan(symbol, span, record["path"])
            for symbol, span in record["operators"])
        nodes.append(NodeSpan(
            path=record["path"], kind=record["kind"], depth=depth,
            parent=parent, span=record["span"], core=record["core"],
            parens=record["parens"], lead=record["lead"],
            trail=record["trail"], operators=operators))
        if record["lead"] is not None:
            comments.append(CommentSpan("lead", record["path"],
                                        record["lead"], record["lead_text"]))
        if record["trail"] is not None:
            comments.append(CommentSpan("trail", record["path"],
                                        record["trail"],
                                        record["trail_text"]))
    return PrintMap(text, nodes, comments)


__all__ = [
    "print_ast_mapped",
    "PrintMap",
    "NodeSpan",
    "OpSpan",
    "CommentSpan",
    "PositionInfo",
    "CATEGORIES",
    "CODE",
    "OPERATOR",
    "PAREN",
    "COMMENT",
    "WHITESPACE",
]
