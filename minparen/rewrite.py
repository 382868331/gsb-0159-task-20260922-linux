"""Replace one AST subtree inside a valid expression's source text.

The operation is a single, *textual* splice driven by the parsed tree:

1. :func:`parse` the source (so an invalid source fails before anything is
   built) and record every node's source span, including the grouping
   parentheses the parser consumed for it and the comments bound to it;
2. select exactly one node (a ``root -> child`` path, or a callable that
   returns a node of the parsed tree);
3. render the replacement subtree in the selected node's *slot* of its
   parent, reusing the printer's precedence/associativity/comment rules, so
   the pair count added at the boundary is the minimum that preserves the
   tree;
4. splice the rendered text over the selected span.

Because the span covers the node's old grouping pair(s), redundant parens
that belonged to the replaced subtree disappear with it; parens owned by
untouched ancestors are bytes outside the interval and survive verbatim.
Comments bound to an untouched node (an ancestor or a sibling) lie outside
the interval and are never modified; comments bound to the selected node
travel inside its span and are replaced together with it.

Everything is a pure function over fresh validated copies: inputs are never
mutated, and a failure (bad source, bad selector, invalid replacement or a
result over the node limit) raises before any result is produced, so no
half-updated state can be observed.
"""

from __future__ import annotations

from typing import Any, Callable, Mapping, Sequence

from .errors import RewriteError
from .parser import _Parser
from .printer import _emit
from .validation import MAX_NODES, validate_tree

__all__ = ["SubtreeEdit", "replace_subtree"]


class SubtreeEdit:
    """The one contiguous edit a subtree replacement needs.

    All offsets are half-open. ``start_byte``/``end_byte`` index the
    **UTF-8 encoding** of the original source and never split a multi-byte
    character; ``start_char``/``end_char`` are the corresponding Python
    ``str`` indices. ``replacement`` is the rendered text written into the
    interval; ``result`` is the full source after the splice.
    """

    __slots__ = ("start_byte", "end_byte", "start_char", "end_char",
                 "replacement", "result")

    def __init__(self, start_char: int, end_char: int,
                 replacement: str, source: str):
        self.start_char = start_char
        self.end_char = end_char
        self.replacement = replacement
        prefix = source[:start_char].encode("utf-8")
        self.start_byte = len(prefix)
        self.end_byte = len(source[:end_char].encode("utf-8"))
        self.result = source[:start_char] + replacement + source[end_char:]

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (f"SubtreeEdit(byte {self.start_byte}:{self.end_byte}, "
                f"{self.replacement!r})")

    def as_tuple(self) -> tuple[int, int, str]:
        """``(start_byte, end_byte, replacement)``."""
        return (self.start_byte, self.end_byte, self.replacement)


def _walk(node: Mapping):
    """Yield ``(node, parent, side)`` for every node (root has parent None)."""
    stack: list[tuple[Mapping, Mapping | None, str | None]] = [(node, None, None)]
    while stack:
        cur, parent, side = stack.pop()
        yield cur, parent, side
        kind = cur["kind"]
        if kind == "neg":
            stack.append((cur["operand"], cur, "operand"))
        elif kind == "bin":
            stack.append((cur["right"], cur, "right"))
            stack.append((cur["left"], cur, "left"))
        elif kind == "cond":
            stack.append((cur["else"], cur, "else"))
            stack.append((cur["then"], cur, "then"))
            stack.append((cur["cond"], cur, "cond"))
        elif kind == "call":
            for i, arg in enumerate(cur["args"]):
                stack.append((arg, cur, f"arg{i}"))
            stack.append((cur["callee"], cur, "callee"))


def _select(root: Mapping,
            target: Callable[[Mapping], Mapping] | Sequence[str | int]
            ) -> tuple[Mapping, Mapping | None, str | None]:
    if callable(target):
        try:
            wanted = target(root)
        except Exception as exc:  # a broken selector is a usage error
            raise RewriteError(
                "target selector raised while choosing a subtree") from exc
    else:
        wanted = root
        for key in target:
            try:
                wanted = wanted[key]
            except (KeyError, IndexError, TypeError) as exc:
                raise RewriteError(
                    f"target path does not exist in the parsed tree: {target!r}"
                ) from exc
    if not isinstance(wanted, Mapping) or "kind" not in wanted:
        raise RewriteError("the selected target is not an AST node")
    for node, parent, side in _walk(root):
        if node is wanted:
            return node, parent, side
    raise RewriteError(
        "the selected target node does not belong to the parsed tree "
        "(build selectors from the tree handed to the callable, or use a "
        "key path rooted at the parsed tree)")


def replace_subtree(
    source: str,
    target: Callable[[Mapping], Mapping] | Sequence[str | int],
    replacement: Mapping,
    *,
    max_nodes: int = MAX_NODES,
) -> SubtreeEdit:
    """Replace one selected subtree and return its UTF-8 edit interval.

    ``target`` selects the node to replace inside the parsed tree. It is
    either a sequence of child keys from the root (e.g.
    ``("right", "right")`` or ``("args", 0)``) or a callable
    ``root -> node``.

    ``replacement`` is a new AST dict (the documented node shape); it is
    validated independently. The replacement is rendered in the selected
    node's parent slot with the minimum parentheses that keep the resulting
    text's parse identical to the intended tree.

    Raises:

    * :class:`~minparen.errors.ParseError` for an invalid ``source``;
    * :class:`~minparen.errors.ASTError` for an invalid ``replacement`` or
      a resulting tree over ``max_nodes``;
    * :class:`~minparen.errors.RewriteError` for a target that is not a node
      of the parsed tree.

    Neither ``source`` nor ``replacement`` is mutated.
    """
    if not isinstance(source, str):
        raise TypeError("source must be a str")
    parser = _Parser(source, max_nodes)
    tree, spans = parser.parse_with_spans()
    old_node, parent, side = _select(tree, target)

    # Independent validation of the replacement (fresh deep copy); an
    # invalid replacement fails here, before anything is assembled.
    new_node = validate_tree(replacement, max_nodes=max_nodes)

    # Assemble the intended tree on fresh copies and validate the whole
    # thing, so the combined node-count limit is enforced before rendering.
    candidate_tree = new_node if parent is None \
        else _graft(tree, old_node, new_node)
    validate_tree(candidate_tree, max_nodes=max_nodes)

    # The printer's slot table decides the boundary parens; parent/side come
    # straight from the parsed tree. parent=None means the whole expression
    # is being replaced and no pair is ever added around a root.
    slot_side = side if side is not None else "root"
    rendered = _emit(new_node, parent, slot_side)
    start_char, end_char = spans[id(old_node)]
    if not (0 <= start_char <= end_char <= len(source)):  # defensive
        raise RewriteError("internal span outside the source")
    return SubtreeEdit(start_char, end_char, rendered, source)


def _graft(root: Mapping, old_node: Mapping, new_node: Mapping) -> dict:
    """Return a structural copy of ``root`` with ``old_node`` (identity)
    replaced by ``new_node``; every other node is a fresh dict."""
    def go(node: Mapping) -> Any:
        if node is old_node:
            return new_node
        out = dict(node)
        kind = node["kind"]
        if kind == "neg":
            out["operand"] = go(node["operand"])
        elif kind == "bin":
            out["left"] = go(node["left"])
            out["right"] = go(node["right"])
        elif kind == "cond":
            out["cond"] = go(node["cond"])
            out["then"] = go(node["then"])
            out["else"] = go(node["else"])
        elif kind == "call":
            out["callee"] = go(node["callee"])
            out["args"] = [go(a) for a in node["args"]]
        return out

    return go(root)
