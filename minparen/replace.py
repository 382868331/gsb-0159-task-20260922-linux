"""Replace one AST subtree inside valid source text, yielding a single edit.

:func:`replace_subtree` parses ``source`` (a valid expression), locates one
node by an accessor ``path`` (the same shape :class:`~minparen.errors.ASTError`
uses, e.g. ``"root.right.args[1]"``), validates the ``replacement`` AST, and
returns one :class:`Edit` describing the minimal necessary change:

* the edit span is exactly the target subtree's own source span — its tokens,
  any grouping parentheses the parser attributed to it, and its own lead/trail
  comments (those comments belong to the replaced subtree and leave with it);
* every byte outside the span — whitespace, comments owned by other nodes,
  untouched operands — is preserved verbatim;
* the replacement text is the minimal-parenthesis rendering of the new
  subtree *in the vacated slot*: parentheses are added exactly when the slot
  (parent kind + side + associativity) or the replacement's own comments
  require them, per the same slot table as :func:`minparen.print_ast`.

Span offsets are **UTF-8 byte offsets** into the original source, so they are
stable for tools that operate on encoded text (Unicode may appear in
comments). The function is pure: it parses and validates up front and either
returns a complete ``Edit`` or raises; there is no partial state to roll back.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping

from .errors import ASTError, PathError
from .parser import parse_with_spans
from .printer import _emit
from .validation import MAX_NODES, validate_tree

# Path := "root" ( "." name | "[" index "]" )*   — same shape as ASTError.path.
_PATH_RE = re.compile(r"root(?:\.[A-Za-z_][A-Za-z0-9_]*|\[(0|[1-9][0-9]*)\])*\Z")
_SEGMENT_RE = re.compile(r"\.([A-Za-z_][A-Za-z0-9_]*)|\[([0-9]+)\]")

# Named children each node kind exposes to a path segment.
_NAMED_CHILDREN = {
    "neg": ("operand",),
    "bin": ("left", "right"),
    "cond": ("cond", "then", "else"),
    "call": ("callee",),
}


@dataclass(frozen=True)
class Edit:
    """One replacement span over the original source text.

    ``start``/``end`` are UTF-8 byte offsets (``end`` exclusive) into the
    source passed to :func:`replace_subtree`; ``replacement`` is the text
    that belongs in their place.
    """

    start: int
    end: int
    replacement: str

    def apply(self, source: str) -> str:
        """Splice this edit into ``source`` and return the new text."""
        if not isinstance(source, str):
            raise TypeError("source must be a str")
        data = source.encode("utf-8")
        if not (0 <= self.start <= self.end <= len(data)):
            raise ValueError(
                f"edit span [{self.start}, {self.end}) does not fit a "
                f"source of {len(data)} UTF-8 bytes")
        return (data[:self.start]
                + self.replacement.encode("utf-8")
                + data[self.end:]).decode("utf-8")


def _parse_path(path: str) -> list[str | int]:
    if not _PATH_RE.fullmatch(path):
        raise PathError(
            "path must be 'root' followed by '.field' or '[index]' segments",
            path)
    segments: list[str | int] = []
    for match in _SEGMENT_RE.finditer(path):
        name, index = match.groups()
        segments.append(name if name is not None else int(index))
    return segments


def _navigate(tree: dict, path: str) -> tuple[dict | None, str, dict]:
    """Resolve ``path`` to ``(parent, side, node)``; root has no parent."""
    parent: dict | None = None
    side = "root"
    node = tree
    in_args = False  # just consumed '.args' on a call node
    for segment in _parse_path(path):
        kind = node["kind"]
        if isinstance(segment, int):
            if not in_args:
                raise PathError(
                    "an '[index]' segment must directly follow '.args'",
                    path)
            if segment >= len(node["args"]):
                raise PathError(
                    f"argument index [{segment}] is out of range for a call "
                    f"with {len(node['args'])} argument(s)", path)
            parent, side, node = node, f"arg{segment}", node["args"][segment]
            in_args = False
        elif segment == "args":
            if kind != "call":
                raise PathError(
                    f"only 'call' nodes have an 'args' list, "
                    f"not kind {kind!r}", path)
            if in_args:
                raise PathError("expected '[index]' after '.args'", path)
            in_args = True
        else:
            if in_args:
                raise PathError("expected '[index]' after '.args'", path)
            allowed = _NAMED_CHILDREN.get(kind, ())
            if segment not in allowed:
                raise PathError(
                    f"node of kind {kind!r} has no child {segment!r}; "
                    f"expected one of {allowed or '(none — it is a leaf)'}",
                    path)
            parent, side, node = node, segment, node[segment]
    if in_args:
        raise PathError("path ends at '.args'; add an '[index]' segment",
                        path)
    return parent, side, node


def _count_nodes(node: dict) -> int:
    kind = node["kind"]
    if kind in ("num", "var"):
        return 1
    if kind == "neg":
        return 1 + _count_nodes(node["operand"])
    if kind == "bin":
        return 1 + _count_nodes(node["left"]) + _count_nodes(node["right"])
    if kind == "cond":
        return (1 + _count_nodes(node["cond"]) + _count_nodes(node["then"])
                + _count_nodes(node["else"]))
    return (1 + _count_nodes(node["callee"])
            + sum(_count_nodes(a) for a in node["args"]))


def replace_subtree(source: str, path: str, replacement: Mapping[str, Any],
                    *, max_nodes: int = MAX_NODES) -> Edit:
    """Return the single :class:`Edit` that swaps one subtree for another.

    ``source`` must be a valid expression (parsed with the public grammar);
    ``path`` locates the subtree to replace (``"root"`` replaces the whole
    expression); ``replacement`` is an AST dict validated with the same
    strictness as :func:`minparen.validate_tree`.

    Raises:

    * :class:`TypeError` — ``source`` or ``path`` is not a ``str``;
    * :class:`~minparen.errors.ParseError` — ``source`` is not a valid
      expression (no edit is produced);
    * :class:`~minparen.errors.ASTError` — ``replacement`` violates the AST
      contract, or the combined tree would exceed ``max_nodes`` nodes;
    * :class:`~minparen.errors.PathError` — ``path`` is malformed or does
      not resolve against the parsed tree.

    All checks run before the edit is computed and neither input is mutated,
    so a raised error never leaves a half-applied change behind.
    """
    if not isinstance(source, str):
        raise TypeError("source must be a str")
    if not isinstance(path, str):
        raise TypeError("path must be a str")
    tree, spans = parse_with_spans(source, max_nodes=max_nodes)
    safe_replacement = validate_tree(replacement, max_nodes=max_nodes)
    parent, side, target = _navigate(tree, path)

    combined = (_count_nodes(tree) - _count_nodes(target)
                + _count_nodes(safe_replacement))
    if combined > max_nodes:
        raise ASTError(
            f"replacement would grow the tree to {combined} nodes, past the "
            f"{max_nodes}-node interface limit", path)

    new_text = _emit(safe_replacement, parent, side)
    char_start, char_end = spans[id(target)]
    # Spans are reported as UTF-8 byte offsets, not character offsets.
    byte_start = len(source[:char_start].encode("utf-8"))
    byte_end = byte_start + len(source[char_start:char_end].encode("utf-8"))
    return Edit(byte_start, byte_end, new_text)


__all__ = ["Edit", "replace_subtree"]
