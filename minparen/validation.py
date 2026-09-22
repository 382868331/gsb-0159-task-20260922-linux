"""Strict validation and canonicalization of AST nodes."""

from __future__ import annotations

import re
from typing import Any, Mapping

from .ast_nodes import BIN_OPS, Kinds
from .errors import ASTError

MAX_NODES = 300

_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")

_EXPECTED_FIELDS = {
    Kinds.NUM.value: ("value",),
    Kinds.VAR.value: ("name",),
    Kinds.NEG.value: ("operand",),
    Kinds.BIN.value: ("op", "left", "right"),
    Kinds.COND.value: ("cond", "then", "else"),
    Kinds.CALL.value: ("callee", "args"),
}
_COMMENT_KEYS = ("lead", "trail")
_ALLOWED_KEYS = {
    k: {"kind"} | set(fields) | set(_COMMENT_KEYS)
    for k, fields in _EXPECTED_FIELDS.items()
}


def validate_tree(root: Any, *, max_nodes: int = MAX_NODES) -> dict:
    """Validate an AST and return an independent canonical deep copy.

    Raises :class:`~minparen.errors.ASTError` with a locator path for any
    malformed node, invalid field, illegal comment text, cycle, or when the
    tree contains more than ``max_nodes`` nodes.
    """
    seen: set[int] = set()
    counter = {"n": 0}
    return _validate(root, "root", seen, counter, max_nodes)


def _check_comment(text: Any, path: str) -> str | None:
    if text is None:
        return None
    if not isinstance(text, str):
        raise ASTError("comment text must be a string", path)
    if "\n" in text or "\r" in text:
        raise ASTError("comment text must not contain newline characters", path)
    if "*/" in text:
        raise ASTError("comment text must not contain the '*/' terminator", path)
    if "\x00" in text:
        raise ASTError("comment text must not contain NUL bytes", path)
    return text


def _validate(node: Any, path: str, seen: set[int],
              counter: dict, max_nodes: int) -> dict:
    if not isinstance(node, Mapping):
        raise ASTError("node must be a mapping with a 'kind' field", path)
    if id(node) in seen:
        raise ASTError("AST must be a tree; shared/cyclic node reference", path)
    seen.add(id(node))
    counter["n"] += 1
    if counter["n"] > max_nodes:
        raise ASTError(
            f"AST has more than {max_nodes} nodes (interface limit)", path)

    kind = node.get("kind")
    if not isinstance(kind, str) or kind not in _ALLOWED_KEYS:
        raise ASTError(
            f"unknown kind {kind!r}; expected one of "
            f"{sorted(k.value for k in Kinds)}", path)

    extra = set(node) - _ALLOWED_KEYS[kind]
    missing = set(_EXPECTED_FIELDS[kind]) - set(node)
    if extra:
        raise ASTError(f"unexpected field(s) {sorted(extra)} for kind {kind!r}",
                       path)
    if missing:
        raise ASTError(f"missing field(s) {sorted(missing)} for kind {kind!r}",
                       path)

    out: dict = {"kind": kind}
    for key in _COMMENT_KEYS:
        comment = _check_comment(node.get(key), f"{path}.{key}")
        if comment is not None:
            out[key] = comment

    if kind == Kinds.NUM.value:
        value = node["value"]
        # bool is a subclass of int: reject it explicitly per contract.
        if isinstance(value, bool) or not isinstance(value, int):
            raise ASTError("num 'value' must be an int (bool rejected)", path)
        if value < 0:
            raise ASTError("num 'value' must be non-negative", path)
        out["value"] = value

    elif kind == Kinds.VAR.value:
        name = node["name"]
        if not isinstance(name, str) or _IDENT_RE.fullmatch(name) is None:
            raise ASTError(
                "var 'name' must match [A-Za-z_][A-Za-z0-9_]*", path)
        out["name"] = name

    elif kind == Kinds.NEG.value:
        out["operand"] = _validate(
            node["operand"], f"{path}.operand", seen, counter, max_nodes)

    elif kind == Kinds.BIN.value:
        op = node["op"]
        if not isinstance(op, str) or op not in BIN_OPS:
            raise ASTError(f"bin 'op' must be one of {BIN_OPS}", path)
        out["op"] = op
        out["left"] = _validate(
            node["left"], f"{path}.left", seen, counter, max_nodes)
        out["right"] = _validate(
            node["right"], f"{path}.right", seen, counter, max_nodes)

    elif kind == Kinds.COND.value:
        out["cond"] = _validate(
            node["cond"], f"{path}.cond", seen, counter, max_nodes)
        out["then"] = _validate(
            node["then"], f"{path}.then", seen, counter, max_nodes)
        out["else"] = _validate(
            node["else"], f"{path}.else", seen, counter, max_nodes)

    elif kind == Kinds.CALL.value:
        out["callee"] = _validate(
            node["callee"], f"{path}.callee", seen, counter, max_nodes)
        raw_args = node["args"]
        if not isinstance(raw_args, list):
            raise ASTError("call 'args' must be a list", path)
        args = []
        for i, arg in enumerate(raw_args):
            args.append(_validate(
                arg, f"{path}.args[{i}]", seen, counter, max_nodes))
        out["args"] = args

    return out


__all__ = ["validate_tree", "MAX_NODES"]
