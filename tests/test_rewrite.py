"""Tests for subtree replacement (``replace_subtree``).

Expected output strings are hand-derived from the documented grammar (never
produced by the implementation under test). In addition, every splice is
checked *relationally*: the result must parse to exactly the intended
grafted tree, and the reported UTF-8 byte interval must reproduce the result
when applied to the UTF-8 encoding of the source. A small deterministic
enumeration (all depth-2 replacement shapes across every kind of parent
slot) strengthens the hand cases.
"""

from __future__ import annotations

import copy
import unittest

from minparen import (
    ASTError, ParseError, RewriteError, parse, print_ast,
    replace_subtree,
)
from minparen.ast_nodes import binop, call, cond, neg, num, var


# Hand-derived (source, path, replacement) -> (result text, replacement text)
HAND_CASES = [
    # Old pair belonged to the replaced subtree: it vanishes; a new pair is
    # added only when the replacement's level needs one in that slot.
    ("a+(b+c)", ("right",), binop("*", var("x"), var("y")),
     "a+x*y", "x*y"),
    ("a+(b+c)", ("right",), binop("-", var("x"), var("y")),
     "a+(x-y)", "(x-y)"),
    ("a-(b-c)", ("right", "left"),
     binop("+", var("x"), var("y")), "a-(x+y-c)", "x+y"),
    ("a-(b-c)", ("right", "right"),
     binop("+", var("x"), var("y")), "a-(b-(x+y))", "(x+y)"),
    # Nested parentheses: outer pairs survive (bytes outside the interval),
    # a boundary pair is added when needed.
    ("((a+b))*c", ("left", "left"),
     cond(var("p"), var("q"), var("r")), "(((p?q:r)+b))*c", "(p?q:r)"),
    ("((a+b))*c", ("left",),
     cond(var("p"), var("q"), var("r")), "(p?q:r)*c", "(p?q:r)"),
    # Ternary branch asymmetry: assign is tight in `then`, not in `else`.
    ("a?b:c", ("then",), binop("=", var("x"), var("y")),
     "a?x=y:c", "x=y"),
    ("a?b:c", ("else",), binop("=", var("x"), var("y")),
     "a?b:(x=y)", "(x=y)"),
    # Unary-minus operand slot and call slots.
    ("-(a)", ("operand",), binop("+", var("x"), var("y")),
     "-(x+y)", "(x+y)"),
    ("-a", ("operand",), call(var("f"), []), "-f()", "f()"),
    ("f((a))", ("args", 0), binop("+", var("b"), var("c")),
     "f(b+c)", "b+c"),
    ("f(x)(y)", ("callee",), var("g"), "g(y)", "g"),
    # Whole-root replacement; no boundary pair is ever added.
    ("/*lead*/a", (), var("b"), "b", "b"),
    # Trailing newline / end-of-expression comment bound to an ancestor is
    # outside the interval and stays byte-for-byte identical.
    ("a+(b+c)\n", ("right",), var("x"), "a+x\n", "x"),
]


class HandRewriteTests(unittest.TestCase):
    def test_hand_derived_results(self):
        for src, path, repl, expected, repl_text in HAND_CASES:
            with self.subTest(src=src, path=path):
                edit = replace_subtree(src, path, repl)
                self.assertEqual(edit.result, expected)
                self.assertEqual(edit.replacement, repl_text)

    def test_callable_selector(self):
        edit = replace_subtree(
            "a+b*c", lambda t: t["right"]["left"], var("z"))
        self.assertEqual(edit.result, "a+z*c")

    def test_path_through_call_args(self):
        edit = replace_subtree(
            "f(g(a),h(b))", ("args", 1, "args", 0), var("z"))
        self.assertEqual(edit.result, "f(g(a),h(z))")

    def test_grouping_pair_belongs_to_its_node_span(self):
        # The parser consumes grouping parentheses into the inner node's
        # span; selecting that leaf replaces the pair too. Left additive
        # slot needs no new pair for a same-level replacement.
        edit = replace_subtree(
            "(a)+b", ("left",), binop("-", var("x"), var("y")))
        self.assertEqual(edit.result, "x-y+b")
        self.assertEqual(parse(edit.result),
                         binop("+", binop("-", var("x"), var("y")),
                               var("b")))


class CommentAndSpanTests(unittest.TestCase):
    def test_untouched_comments_are_verbatim(self):
        # End-of-expression trail comment owned by the root survives.
        edit = replace_subtree(
            "a+b/*tail*/", ("right",), binop("*", var("x"), var("y")))
        self.assertEqual(edit.result, "a+x*y/*tail*/")
        # A sibling's lead comment survives.
        edit = replace_subtree("f(/*a*/a,b)", ("args", 1), var("z"))
        self.assertEqual(edit.result, "f(/*a*/a,z)")

    def test_comments_owned_by_target_leave_with_it(self):
        # The lead comment sits in the gap claimed for the right child, so
        # it belongs to the replaced subtree and is removed with the span.
        edit = replace_subtree("a+/*b-lead*/b", ("right",), var("x"))
        self.assertEqual(edit.result, "a+x")
        self.assertEqual(edit.start_char, 2)
        self.assertEqual(edit.end_char, 13)

    def test_replacement_carrying_comment_forces_boundary_parens(self):
        edit = replace_subtree("a+b", ("left",), var("x", lead="L"))
        self.assertEqual(edit.result, "(/*L*/x)+b")
        edit = replace_subtree("a+b", ("right",), var("x", trail="T"))
        self.assertEqual(edit.result, "a+(x/*T*/)")
        # Right-side gaps are claimed for the child itself: no parens.
        edit = replace_subtree("a+b", ("right",), var("x", lead="L"))
        self.assertEqual(edit.result, "a+/*L*/x")

    def test_unicode_byte_offsets_in_comments(self):
        # Multi-byte text only ever legally appears in comments.
        src = "a+b/* αβ 😀 */"
        edit = replace_subtree(
            src, ("right",), binop("*", var("x"), var("y")))
        self.assertEqual(edit.result, "a+x*y/* αβ 😀 */")
        self.assertEqual((edit.start_byte, edit.end_byte), (2, 3))
        self.assertEqual((edit.start_char, edit.end_char), (2, 3))
        encoded = src.encode("utf-8")
        self.assertEqual(
            encoded[:edit.start_byte] + edit.replacement.encode("utf-8")
            + encoded[edit.end_byte:],
            edit.result.encode("utf-8"))

        # A multi-byte comment *before* the interval shifts byte vs char
        # offsets but never splits a character.
        src2 = "/*α*/a+b"
        edit = replace_subtree(src2, ("left",), var("z"))
        self.assertEqual(edit.result, "/*α*/z+b")
        self.assertEqual((edit.start_char, edit.end_char), (5, 6))
        self.assertEqual((edit.start_byte, edit.end_byte), (6, 7))

        # A multi-byte comment owned by the target widens the byte interval.
        src3 = "a+/*中文*/b"
        edit = replace_subtree(src3, ("right",), var("z"))
        self.assertEqual(edit.result, "a+z")
        self.assertEqual((edit.start_char, edit.end_char), (2, 9))
        self.assertEqual((edit.start_byte, edit.end_byte), (2, 13))


class EditConsistencyTests(unittest.TestCase):
    def test_byte_splice_reconstructs_result(self):
        cases = [
            ("a-(b-(c-d))", ("right", "right", "right"),
             binop("+", var("x"), var("y"))),
            ("((a+b))*c", ("left", "left"),
             cond(var("p"), var("q"), var("r"))),
            ("f(g(a),h(b))", ("args", 0), cond(var("p"), num(1), num(2))),
        ]
        for src, path, repl in cases:
            with self.subTest(src=src):
                edit = replace_subtree(src, path, repl)
                encoded = src.encode("utf-8")
                rebuilt = (encoded[:edit.start_byte]
                           + edit.replacement.encode("utf-8")
                           + encoded[edit.end_byte:])
                self.assertEqual(rebuilt.decode("utf-8"), edit.result)
                # The interval must be minimal in the sense that its edges
                # touch the replaced span: bytes just outside are unchanged.
                self.assertEqual(edit.result[:edit.start_byte],
                                 src[:edit.start_char])

    def test_result_parses_to_intended_tree(self):
        """Relational oracle: parse(result) == graft(parse(src), replacement)."""
        def graft(node, path, replacement):
            if path == ():
                return replacement
            node = dict(node)
            key = path[0]
            if key == "args":
                i = path[1]
                node["args"] = [
                    graft(a, path[2:], replacement) if i == j else a
                    for j, a in enumerate(node["args"])]
            else:
                node[key] = graft(node[key], path[1:], replacement)
            return node

        cases = [
            ("a*b+c", ("left",), binop("+", var("x"), var("y"))),
            ("f(g(1))", ("args", 0, "callee"), var("h")),
            ("a?b:c", ("cond",), binop("=", var("x"), var("y"))),
            ("a=b", ("right",), cond(var("p"), var("q"), var("r"))),
            ("f(x,y)", ("args", 0), neg(var("z"))),
        ]
        for src, path, repl in cases:
            with self.subTest(src=src, path=path):
                edit = replace_subtree(src, path, repl)
                intended = graft(parse(src), path, repl)
                self.assertEqual(parse(edit.result), intended)

    def test_single_contiguous_interval(self):
        edit = replace_subtree(
            "a+(b+c)", ("right",), binop("-", var("x"), var("y")))
        self.assertLessEqual(edit.start_byte, edit.end_byte)
        self.assertEqual(edit.result.count("("), edit.result.count(")"))


class EnumeratedRewriteTests(unittest.TestCase):
    @staticmethod
    def _shapes(depth: int):
        if depth == 0:
            return [var("a"), num(1)]
        prev = EnumeratedRewriteTests._shapes(depth - 1)
        out = list(prev) + [neg(copy.deepcopy(x)) for x in prev]
        for op in ("+", "*", "="):
            for left in prev:
                for right in prev:
                    out.append(binop(op, copy.deepcopy(left),
                                     copy.deepcopy(right)))
        return out

    def test_all_depth2_shapes_in_every_slot(self):
        shapes = self._shapes(2)
        self.assertEqual(len(shapes), 800)
        slots = [
            ("a+b", ("left",)), ("a+b", ("right",)),
            ("a*b", ("left",)), ("a*b", ("right",)),
            ("a=b", ("left",)), ("a=b", ("right",)),
            ("a?b:c", ("cond",)), ("a?b:c", ("then",)),
            ("a?b:c", ("else",)),
            ("-a", ("operand",)),
            ("f(x)", ("args", 0)), ("f(x)", ("callee",)),
        ]

        def graft(node, path, replacement):
            if path == ():
                return replacement
            node = dict(node)
            key = path[0]
            if key == "args":
                i = path[1]
                node["args"] = [
                    graft(a, path[2:], replacement) if i == j else a
                    for j, a in enumerate(node["args"])]
            else:
                node[key] = graft(node[key], path[1:], replacement)
            return node

        checked = 0
        for src, path in slots:
            base = parse(src)
            for shape in shapes:
                repl = copy.deepcopy(shape)
                edit = replace_subtree(src, path, repl)
                # Tree identity, not numeric equivalence.
                self.assertEqual(parse(edit.result),
                                 graft(base, path, repl))
                encoded = src.encode("utf-8")
                rebuilt = (encoded[:edit.start_byte]
                           + edit.replacement.encode("utf-8")
                           + encoded[edit.end_byte:]).decode("utf-8")
                self.assertEqual(rebuilt, edit.result)
                checked += 1
        self.assertEqual(checked, 12 * len(shapes))

    def test_boundary_parens_match_standalone_minimal_printing(self):
        # The replacement rendered in its slot must equal the substring the
        # standalone minimal printer gives that same node-in-context: print
        # the whole intended grafted tree and locate the boundary text.
        repl = binop("-", var("x"), var("y"))
        edit = replace_subtree("a+(b+c)", ("right",), repl)
        whole = print_ast(binop("+", var("a"),
                                binop("-", var("x"), var("y"))))
        self.assertEqual(whole, "a+(x-y)")
        self.assertEqual(edit.result, whole)
        self.assertEqual(edit.replacement, "(x-y)")


class FailureTests(unittest.TestCase):
    def test_invalid_source_raises_parse_error(self):
        for bad in ("a+", "((a)", "f(,)", "@", ""):
            with self.subTest(bad=bad):
                with self.assertRaises(ParseError):
                    replace_subtree(bad, ("right",), var("z"))

    def test_bad_target_path(self):
        for path in (("nope",), ("args",), ("left", "x"), (1,),
                     ("args", 9)):
            with self.subTest(path=path):
                with self.assertRaises(RewriteError):
                    replace_subtree("a+b", path, var("z"))

    def test_selector_result_must_belong_to_parsed_tree(self):
        foreign = parse("z")
        with self.assertRaises(RewriteError):
            replace_subtree("a+b", lambda tree: foreign, var("z"))
        with self.assertRaises(RewriteError):
            replace_subtree("a+b", lambda tree: "not-a-node", var("z"))

    def test_invalid_replacement_raises_ast_error(self):
        with self.assertRaises(ASTError):
            replace_subtree("a+b", ("right",), {"kind": "num", "value": -1})
        with self.assertRaises(ASTError):
            replace_subtree("a+b", ("right",), {"kind": "wat"})
        with self.assertRaises(ASTError):
            # Illegal comment text in the replacement.
            replace_subtree("a+b", ("right",), var("x", lead="a */ b"))

    def test_combined_node_limit_enforced(self):
        big = var("a")
        for _ in range(300):  # 301 nodes
            big = neg(big)
        with self.assertRaises(ASTError):
            replace_subtree("a", (), big)
        # Exactly 300 after replacement is accepted.
        ok_tree = var("a")
        for _ in range(299):  # 300 nodes
            ok_tree = neg(ok_tree)
        edit = replace_subtree("a", (), ok_tree)
        parse(edit.result)

    def test_inputs_are_not_mutated(self):
        source = "a+(b+c)"
        replacement = binop("-", var("x"), var("y"))
        replace_subtree(source, ("right",), replacement)
        self.assertEqual(source, "a+(b+c)")
        self.assertEqual(replacement, binop("-", var("x"), var("y")))

    def test_non_str_source_type(self):
        with self.assertRaises(TypeError):
            replace_subtree(b"a+b", ("right",), var("z"))

    def test_existing_entry_points_still_work(self):
        # Backward-compatibility smoke check after parser span bookkeeping.
        tree = parse("a-(b-c)")
        self.assertEqual(print_ast(tree), "a-(b-c)")
        self.assertEqual(print_ast(parse("a?b?c:d:e?f:g")),
                         "a?b?c:d:e?f:g")


if __name__ == "__main__":
    unittest.main(verbosity=2)
