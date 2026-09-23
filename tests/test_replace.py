"""Tests for minparen.replace_subtree (single-edit subtree replacement).

Oracles are independent of the implementation under test:

* hand-computed edit spans and replacement strings (byte offsets derived by
  counting UTF-8 bytes in the test, shown next to each case);
* structural checks that re-parse the edited source and compare against a
  tree rebuilt by test-local path surgery (never via replace_subtree);
* a small-scale enumeration over all tiny trees and all node paths, checking
  the untouched-span preservation and parenthesis-necessity properties.
"""

from __future__ import annotations

import copy
import unittest

from minparen import (
    ASTError, Edit, ParseError, PathError, parse, replace_subtree,
    validate_tree,
)
from minparen.ast_nodes import binop, call, cond, neg, num, var


def utf8(s: str) -> bytes:
    return s.encode("utf-8")


# --------------------------------------------------------------------------
# Hand-computed cases: (source, path, replacement, (start, end, text), applied)
# All-ASCII cases have byte offsets equal to character offsets; the Unicode
# cases further down carry the byte derivation in comments.
# --------------------------------------------------------------------------

HAND_CASES = [
    # Boundary parens required: right of '-' is a strict additive slot.
    ("a-(b-c)", "root.right", binop("+", var("x"), var("y")),
     (2, 7, "(x+y)"), "a-(x+y)"),
    # Same shape, multiplicative replacement: tight, no parens; the old
    # grouping parens leave with the replaced subtree.
    ("a+(b*c)", "root.right", binop("*", var("x"), var("y")),
     (2, 7, "x*y"), "a+x*y"),
    # Root replacement never takes parens.
    ("a+b", "root", cond(var("x"), var("y"), var("z")),
     (0, 3, "x?y:z"), "x?y:z"),
    # Ternary else slot: an assignment needs parens there...
    ("a?b:(c=d)", "root.else", binop("=", var("x"), var("y")),
     (4, 9, "(x=y)"), "a?b:(x=y)"),
    # ...but the then slot accepts it bare (C-style quirk of the grammar).
    ("a?b=c:d", "root.then", binop("=", var("p"), var("q")),
     (2, 5, "p=q"), "a?p=q:d"),
    # Nested redundant parens belong to the replaced node's span.
    ("a*((b+c))", "root.right", binop("-", var("x"), var("y")),
     (2, 9, "(x-y)"), "a*(x-y)"),
    # Call argument slot accepts a bare assignment.
    ("f(a,b)", "root.args[1]", binop("=", var("x"), var("y")),
     (4, 5, "x=y"), "f(a,x=y)"),
    # Unary-minus operand: grouping parens are part of the operand span.
    ("-(a+b)", "root.operand", var("z"), (1, 6, "z"), "-z"),
    # Right operand of '=' is the whole right-associative chain.
    ("a=b=c", "root.right", num(7), (2, 5, "7"), "a=7"),
    # Callee of a chained call.
    ("f()()", "root.callee", var("g"), (0, 3, "g"), "g()"),
    # Deep path through two argument lists.
    ("f(g(x),2)", "root.args[0].args[0]", num(9), (4, 5, "9"), "f(g(9),2)"),
    # Replacement trail comment in an end-enclosed slot forces its own pair.
    ("a*b", "root.right", var("y", trail="c"),
     (2, 3, "(y/*c*/)"), "a*(y/*c*/)"),
    # Replacement lead comment in a front-enclosed slot (callee).
    ("f(x)", "root.callee", var("h", lead="L"),
     (0, 1, "(/*L*/h)"), "(/*L*/h)(x)"),
    # Replacement lead comment in a child-claimed slot needs no parens.
    ("a*b", "root.right", var("y", lead="L"), (2, 3, "/*L*/y"), "a*/*L*/y"),
]


class HandComputedTests(unittest.TestCase):
    def test_spans_text_and_applied_source(self):
        for source, path, repl, (start, end, text), applied in HAND_CASES:
            with self.subTest(source=source, path=path):
                edit = replace_subtree(source, path, repl)
                self.assertEqual((edit.start, edit.end, edit.replacement),
                                 (start, end, text))
                self.assertEqual(edit.apply(source), applied)

    def test_edited_source_parses_to_the_expected_tree(self):
        for source, path, repl, _, applied in HAND_CASES:
            with self.subTest(source=source, path=path):
                expected = _swap(parse(source), _segments(path),
                                 validate_tree(repl))
                self.assertEqual(parse(applied), expected)


# --------------------------------------------------------------------------
# Untouched text and comments
# --------------------------------------------------------------------------

class PreservationTests(unittest.TestCase):
    def test_whitespace_outside_span_is_verbatim(self):
        source = "a  +  /*keep*/ b"
        edit = replace_subtree(source, "root.left", var("x"))
        self.assertEqual((edit.start, edit.end), (0, 1))
        self.assertEqual(edit.apply(source), "x  +  /*keep*/ b")

    def test_trailing_comment_of_enclosing_node_stays(self):
        # Line-end comment binds to the root's trail; replacing the right
        # operand must not touch it.
        source = "a+b /*tail*/"
        edit = replace_subtree(source, "root.right", var("c"))
        self.assertEqual(edit.apply(source), "a+c /*tail*/")
        self.assertEqual(parse(edit.apply(source))["trail"], "tail")

    def test_comment_owned_by_replaced_node_leaves_with_it(self):
        source = "a+/*gone*/b"
        edit = replace_subtree(source, "root.right", var("x"))
        self.assertEqual((edit.start, edit.end), (2, 11))
        self.assertEqual(edit.apply(source), "a+x")

    def test_root_span_includes_its_own_edge_comments(self):
        source = "/*L*/a+b/*T*/"
        edit = replace_subtree(source, "root", var("z"))
        self.assertEqual((edit.start, edit.end), (0, len(source)))
        self.assertEqual(edit.apply(source), "z")

    def test_sibling_comments_on_both_sides_survive(self):
        # /*L*/ binds to root.lead (document edge), /*T*/ to a.trail,
        # /*P*/ to b.lead, /*Q*/ to root.trail (document edge). Replacing
        # the right operand removes only b and its own /*P*/.
        source = "/*L*/a/*T*/+/*P*/b/*Q*/"
        edit = replace_subtree(source, "root.right", num(3))
        self.assertEqual(edit.apply(source), "/*L*/a/*T*/+3/*Q*/")


# --------------------------------------------------------------------------
# Unicode and UTF-8 byte spans
# --------------------------------------------------------------------------

class UnicodeTests(unittest.TestCase):
    def test_byte_offsets_after_multibyte_comment(self):
        # "/*é注释*/" is 7 chars but 1+1+2+3+3+1+1 = 12 bytes; then
        # "x" (1) and "+" (1) put "y" at byte 14.
        source = "/*é注释*/x+y"
        edit = replace_subtree(source, "root.right", var("z"))
        self.assertEqual((edit.start, edit.end), (14, 15))
        self.assertEqual(edit.replacement, "z")
        self.assertEqual(edit.apply(source), "/*é注释*/x+z")
        # Cross-check the span against an independent byte count.
        self.assertEqual(utf8(source)[:edit.start], utf8("/*é注释*/x+"))
        self.assertEqual(utf8(source)[edit.end:], b"")

    def test_byte_offsets_with_multibyte_trailing_comment(self):
        # "a" (1 byte) + "/*尾*/" (7 bytes) + "+" (1) -> "b" at byte 9..10.
        source = "a/*尾*/+b"
        edit = replace_subtree(source, "root.right", var("c"))
        self.assertEqual((edit.start, edit.end), (9, 10))
        self.assertEqual(edit.apply(source), "a/*尾*/+c")
        self.assertEqual(utf8(source)[:9], utf8("a/*尾*/+"))

    def test_multibyte_text_inside_replacement_comment(self):
        source = "a*b"
        edit = replace_subtree(source, "root.right", var("y", lead="新"))
        self.assertEqual(edit.replacement, "/*新*/y")
        applied = edit.apply(source)
        self.assertEqual(applied, "a*/*新*/y")
        self.assertEqual(parse(applied)["right"]["lead"], "新")


# --------------------------------------------------------------------------
# Small-scale enumeration: every tiny tree, every node path
# --------------------------------------------------------------------------

def _size(node: dict) -> int:
    kind = node["kind"]
    if kind in ("num", "var"):
        return 1
    if kind == "neg":
        return 1 + _size(node["operand"])
    if kind == "bin":
        return 1 + _size(node["left"]) + _size(node["right"])
    if kind == "cond":
        return 1 + sum(_size(node[k]) for k in ("cond", "then", "else"))
    return 1 + _size(node["callee"]) + sum(_size(a) for a in node["args"])


def _gen_trees(max_nodes: int, max_depth: int) -> list[dict]:
    out = [num(1), var("a")]
    if max_depth <= 1 or max_nodes < 2:
        return out
    subs = _gen_trees(max_nodes - 1, max_depth - 1)
    # Deep-copy on composition: generated trees must be real trees, not DAGs.
    for sub in subs:
        out.append(neg(copy.deepcopy(sub)))
    for op in ("+", "-", "*", "/", "="):
        for left in subs:
            for right in subs:
                if _size(left) + _size(right) + 1 <= max_nodes:
                    out.append(binop(op, copy.deepcopy(left),
                                     copy.deepcopy(right)))
    for test in subs:
        for then in subs:
            for els in subs:
                if _size(test) + _size(then) + _size(els) + 1 <= max_nodes:
                    out.append(cond(copy.deepcopy(test), copy.deepcopy(then),
                                    copy.deepcopy(els)))
    for callee in subs:
        if _size(callee) + 1 <= max_nodes:
            out.append(call(copy.deepcopy(callee), []))
        for arg in subs:
            if _size(callee) + _size(arg) + 1 <= max_nodes:
                out.append(call(copy.deepcopy(callee),
                                [copy.deepcopy(arg)]))
    return out


def _paths(node: dict, prefix: tuple = ()) -> list[tuple]:
    """All node paths as segment tuples, root first."""
    out = [prefix]
    kind = node["kind"]
    if kind == "neg":
        out += _paths(node["operand"], prefix + ("operand",))
    elif kind == "bin":
        out += _paths(node["left"], prefix + ("left",))
        out += _paths(node["right"], prefix + ("right",))
    elif kind == "cond":
        for key in ("cond", "then", "else"):
            out += _paths(node[key], prefix + (key,))
    elif kind == "call":
        out += _paths(node["callee"], prefix + ("callee",))
        for i, arg in enumerate(node["args"]):
            out += _paths(arg, prefix + ("args", i))
    return out


def _path_str(segments: tuple) -> str:
    out = "root"
    for seg in segments:
        out += f"[{seg}]" if isinstance(seg, int) else f".{seg}"
    return out


def _segments(path: str) -> tuple:
    """Test-local inverse of _path_str (no shared code with the core)."""
    segs: list = []
    i = 4  # len("root")
    while i < len(path):
        if path[i] == ".":
            j = i + 1
            while j < len(path) and path[j] not in ".[":
                j += 1
            segs.append(path[i + 1:j])
            i = j
        else:
            j = path.index("]", i)
            segs.append(int(path[i + 1:j]))
            i = j + 1
    return tuple(segs)


def _swap(node: dict, segments: tuple, repl: dict) -> dict:
    """Rebuild ``node`` with the subtree at ``segments`` replaced."""
    if not segments:
        return repl
    node = dict(node)
    head, rest = segments[0], segments[1:]
    if head == "args":
        index, rest = rest[0], rest[1:]
        node["args"] = [_swap(a, rest, repl) if i == index else a
                        for i, a in enumerate(node["args"])]
    else:
        node[head] = _swap(node[head], rest, repl)
    return node


class EnumerationTests(unittest.TestCase):
    def test_every_path_of_every_tiny_tree(self):
        from minparen import print_ast

        replacement = binop("+", var("r"), num(1))  # renders "r+1" or "(r+1)"
        safe_replacement = validate_tree(replacement)
        trees = [t for t in
                 {repr(t): t for t in _gen_trees(5, 3)}.values()]
        self.assertGreater(len(trees), 100)
        checked = 0
        for tree in trees:
            source = print_ast(tree)
            source_bytes = utf8(source)
            for segments in _paths(tree):
                path = _path_str(segments)
                edit = replace_subtree(source, path, replacement)
                applied_bytes = utf8(edit.apply(source))
                # 1. Untouched bytes outside the span are preserved verbatim.
                self.assertEqual(applied_bytes[:edit.start],
                                 source_bytes[:edit.start])
                self.assertEqual(applied_bytes[len(applied_bytes)
                                               - (len(source_bytes)
                                                  - edit.end):],
                                 source_bytes[edit.end:])
                # 2. The edited source parses to exactly the swapped tree.
                expected = _swap(tree, segments, safe_replacement)
                self.assertEqual(parse(edit.apply(source)), expected)
                # 3. Boundary parens, when present, are necessary: splicing
                # the unwrapped text must fail to parse or change the tree.
                if edit.replacement.startswith("("):
                    inner = edit.replacement[1:-1]
                    alt = (source_bytes[:edit.start] + utf8(inner)
                           + source_bytes[edit.end:]).decode("utf-8")
                    try:
                        alt_tree = parse(alt)
                    except ParseError:
                        pass  # no valid parse without the parens at all
                    else:
                        self.assertNotEqual(alt_tree, expected)
                checked += 1
        self.assertGreater(checked, 500)


# --------------------------------------------------------------------------
# Failure cases: explicit errors, no partial state
# --------------------------------------------------------------------------

class FailureTests(unittest.TestCase):
    def test_invalid_source_raises_parse_error(self):
        with self.assertRaises(ParseError):
            replace_subtree("a+", "root", var("x"))
        with self.assertRaises(ParseError):
            replace_subtree("f(/*orphan*/)", "root", var("x"))

    def test_bad_paths_raise_path_error(self):
        bad = [
            ("a+b", "root.left.x"),      # var has no children
            ("a+b", "root.args[0]"),     # not a call
            ("f(a)", "root.args[3]"),    # index out of range
            ("f(a)", "root.args"),       # missing [index]
            ("f(a)", "root.args.x"),     # junk after .args
            ("a+b", "root[0]"),          # index without .args
            ("a+b", "foo"),              # must start with root
            ("a+b", ""),                 # empty
            ("a+b", "root..left"),       # malformed
            ("a+b", "root.left."),       # trailing dot
            ("a+b", " root"),            # whitespace
        ]
        for source, path in bad:
            with self.subTest(path=path):
                with self.assertRaises(PathError):
                    replace_subtree(source, path, var("x"))

    def test_invalid_replacement_raises_ast_error(self):
        for repl in ({"kind": "num", "value": -1},
                     {"kind": "num", "value": True},
                     {"kind": "wat"},
                     {"kind": "var"},
                     42,
                     "x+y"):
            with self.subTest(repl=repl):
                with self.assertRaises(ASTError):
                    replace_subtree("a+b", "root", repl)

    def test_type_errors(self):
        with self.assertRaises(TypeError):
            replace_subtree(b"a+b", "root", var("x"))
        with self.assertRaises(TypeError):
            replace_subtree("a+b", ["root"], var("x"))
        with self.assertRaises(TypeError):
            Edit(0, 1, "x").apply(b"ab")

    def test_combined_node_limit(self):
        big = var("x")
        for _ in range(299):
            big = neg(big)  # 300 nodes
        # 3 - 1 + 300 = 302 > 300 default limit.
        with self.assertRaises(ASTError):
            replace_subtree("a+b", "root.left", big)
        # The same edit is legal with a raised limit.
        edit = replace_subtree("a+b", "root.left", big, max_nodes=400)
        self.assertTrue(edit.apply("a+b").startswith("-" * 299))
        # Replacing the whole tree with a 300-node tree fits exactly.
        edit = replace_subtree("a+b", "root", big)
        self.assertEqual(edit.apply("a+b"), "-" * 299 + "x")

    def test_inputs_are_not_mutated(self):
        source = "a+/*c*/b"
        replacement = binop("+", var("x"), var("y"), lead="L")
        snapshot = copy.deepcopy(replacement)
        replace_subtree(source, "root.right", replacement)
        self.assertEqual(replacement, snapshot)
        self.assertEqual(source, "a+/*c*/b")

    def test_edit_apply_checks_span(self):
        with self.assertRaises(ValueError):
            Edit(0, 5, "x").apply("ab")
        self.assertEqual(Edit(1, 2, "z").apply("abc"), "azc")


if __name__ == "__main__":
    unittest.main(verbosity=2)
