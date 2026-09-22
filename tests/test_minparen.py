"""Tests for minparen.

The strongest checks here do NOT derive expected values from the core:

* ``test_enumeration_depth4`` certifies minimality against
  :mod:`reference.refenum`, an independent parser + exhaustive
  parenthesis enumerator (no import of the core's printer/parser).
* Hand-written expected strings cover associativity, ternary quirks,
  unary minus, calls and comment binding.
* Parse/print round trips are used only as a supplementary consistency
  check.
"""

from __future__ import annotations

import unittest

from minparen import (
    ASTError, BudgetExhaustedError, NoSolutionError, ParseError,
    parse, print_ast, validate_tree,
)
from minparen.ast_nodes import binop, call, cond, neg, num, var
from reference import refenum


# --------------------------------------------------------------------------
# Tuple-AST (reference shape) <-> core dict conversion
# --------------------------------------------------------------------------

def from_ref(node) -> dict:
    tag = node[0]
    if tag == "n":
        return num(node[1])
    if tag == "v":
        return var(node[1])
    if tag == "!":
        return neg(from_ref(node[1]))
    if tag == "b":
        return binop(node[1], from_ref(node[2]), from_ref(node[3]))
    if tag == "?":
        return cond(from_ref(node[1]), from_ref(node[2]),
                    from_ref(node[3]))
    return call(from_ref(node[1]), [from_ref(a) for a in node[2]])


# --------------------------------------------------------------------------
# Hand-derived expectations
# --------------------------------------------------------------------------

STRUCTURAL_CASES = [
    # (source, minimal rendering)
    ("a", "a"),
    ("0", "0"),
    ("a+b+c", "a+b+c"),
    ("(a+b)+c", "a+b+c"),
    ("a+(b+c)", "a+(b+c)"),
    ("a-(b-c)", "a-(b-c)"),
    ("(a-b)-c", "a-b-c"),
    ("a*b*c", "a*b*c"),
    ("a*(b*c)", "a*(b*c)"),
    ("a+b*c", "a+b*c"),
    ("(a+b)*c", "(a+b)*c"),
    ("a*(b+c)", "a*(b+c)"),
    ("a*b+c", "a*b+c"),
    ("a-b+c-d", "a-b+c-d"),
    ("a-(b-(c-d))", "a-(b-(c-d))"),
    ("a=b=c", "a=b=c"),
    ("a=(b=c)", "a=b=c"),
    ("(a=b)=c", "(a=b)=c"),
    ("a+b=c", "a+b=c"),
    ("a=(b+c)", "a=b+c"),
    ("(a+b)=c", "a+b=c"),
    ("-a", "-a"),
    ("--a", "--a"),
    ("-a+b", "-a+b"),
    ("a+-b", "a+-b"),
    ("a- -b".replace(" ", ""), "a--b"),
    ("-(a+b)", "-(a+b)"),
    ("-(a*b)", "-(a*b)"),
    ("-a*b", "-a*b"),
    ("(-a)*b", "-a*b"),
    ("-( -a)".replace(" ", ""), "--a"),
    ("-f()", "-f()"),
    ("(-f)()", "(-f)()"),
    ("f()", "f()"),
    ("f(a,b,c)", "f(a,b,c)"),
    ("f()()", "f()()"),
    ("(f())()", "f()()"),
    ("(f+g)()", "(f+g)()"),
    ("f(g(a))", "f(g(a))"),
    ("a?b:c", "a?b:c"),
    ("a?b:c?d:e", "a?b:c?d:e"),
    ("a?(b?c:d):e", "a?b?c:d:e"),
    ("(a?b:c)?d:e", "(a?b:c)?d:e"),
    ("a?b:(c?d:e)", "a?b:c?d:e"),
    ("a?b=c:d", "a?b=c:d"),
    ("a?(b=c):d", "a?b=c:d"),
    ("a?b:(c=d)", "a?b:(c=d)"),
    ("(a=b)?c:d", "(a=b)?c:d"),
    ("f(a?b:c)", "f(a?b:c)"),
    ("f(a=b)", "f(a=b)"),
    ("-(a?b:c)", "-(a?b:c)"),
    ("-(a=b)", "-(a=b)"),
    ("a?1:2?3:4", "a?1:2?3:4"),
]


class StructuralTests(unittest.TestCase):
    def test_handwritten_expectations(self):
        for source, expected in STRUCTURAL_CASES:
            with self.subTest(source=source):
                tree = parse(source)
                self.assertEqual(print_ast(tree), expected)
                # The minimal form must itself parse to the identical tree.
                self.assertEqual(parse(expected), tree)

    def test_exact_tree_not_numeric_equivalence(self):
        # a-(b-c) must not collapse to a-b+c.
        t = parse("a-(b-c)")
        self.assertEqual(print_ast(t), "a-(b-c)")
        self.assertNotEqual(print_ast(t), print_ast(parse("a-b+c")))


# --------------------------------------------------------------------------
# Exhaustive enumeration on small depth-4 trees
# --------------------------------------------------------------------------

LEAVES = [("v", "a"), ("v", "b"), ("n", 1)]


def gen_trees(max_nodes: int, max_depth: int):
    """All tuple trees up to the size/depth budget (bounded generator)."""
    cache: dict[tuple[int, int], list] = {}

    def go(budget: int, depth: int):
        if (budget, depth) in cache:
            return cache[(budget, depth)]
        out = list(LEAVES)
        if depth <= 1 or budget <= 2:
            cache[(budget, depth)] = out
            return out
        for sub in go(budget - 1, depth - 1):
            out.append(("!", sub))
        for op in ("+", "-", "*", "/", "="):
            lefts = go(budget - 1, depth - 1)
            for li, left in enumerate(lefts):
                used = _size(left)
                if used + 2 > budget:
                    continue
                for right in go(budget - 1 - used, depth - 1):
                    out.append(("b", op, left, right))
        tests = go(budget - 1, depth - 1)
        for tnode in tests:
            used = _size(tnode)
            for then in go(budget - 1 - used, depth - 1):
                u2 = used + _size(then)
                if u2 + 2 > budget:
                    continue
                for els in go(budget - 1 - u2, depth - 1):
                    out.append(("?", tnode, then, els))
        for callee in go(budget - 1, depth - 1):
            used = _size(callee)
            out.append(("f", callee, ()))
            if used + 2 <= budget:
                for arg in go(budget - 1 - used, depth - 1):
                    out.append(("f", callee, (arg,)))
        cache[(budget, depth)] = out
        return out

    return go(max_nodes, max_depth)


def _size(node) -> int:
    tag = node[0]
    if tag in ("n", "v"):
        return 1
    if tag == "!":
        return 1 + _size(node[1])
    if tag == "b":
        return 1 + _size(node[2]) + _size(node[3])
    if tag == "?":
        return 1 + sum(_size(x) for x in node[1:])
    return 1 + _size(node[1]) + sum(_size(a) for a in node[2])


def _walk_ref(node):
    yield node
    tag = node[0]
    if tag == "!":
        yield from _walk_ref(node[1])
    elif tag == "b":
        yield from _walk_ref(node[2])
        yield from _walk_ref(node[3])
    elif tag == "?":
        for ch in node[1:]:
            yield from _walk_ref(ch)
    elif tag == "f":
        yield from _walk_ref(node[1])
        for a in node[2]:
            yield from _walk_ref(a)


class EnumerationTests(unittest.TestCase):
    def test_enumeration_depth4(self):
        trees = gen_trees(max_nodes=9, max_depth=4)
        uniq = list(dict.fromkeys(trees))
        # Every small tree is checked exhaustively; larger trees (up to
        # depth 4 / 9 nodes) are sampled with a fixed stride, no randomness.
        sample = [t for t in uniq if _size(t) <= 5]
        rest = [t for t in uniq if _size(t) > 5]
        stride = max(1, len(rest) // 80)
        sample.extend(rest[::stride])
        self.assertGreater(len(sample), 200)
        for tree in sample:
            expected, pairs = refenum.best_wrapping(tree)
            got = print_ast(from_ref(tree))
            self.assertEqual(got, expected)
            # Added pairs must equal the enumerator's provable minimum
            # (call '(' tokens are syntax, not added pairs).
            call_parens = sum(1 for n in _walk_ref(tree) if n[0] == "f")
            self.assertEqual(got.count("("), pairs + call_parens)

    def test_core_output_is_provably_minimum_on_focus_set(self):
        # The exact acceptance examples from the task statement.
        for src in ("a-(b-c)", "a?b:c?d:e", "a?b?c:d:e", "-(a+b)",
                    "--a", "a*b+c", "(a=b)?c:d", "a?b:(c=d)"):
            tree = refenum.ref_parse(src)
            expected, _ = refenum.best_wrapping(tree)
            self.assertEqual(print_ast(parse(src)), expected)


class ReferenceSemanticsTests(unittest.TestCase):
    def test_budget_distinct_from_no_solution(self):
        tree = refenum.ref_parse("a-(b-c)-(x-y)")
        # Budget smaller than the candidate space -> cannot conclude.
        with self.assertRaises(BudgetExhaustedError):
            refenum.best_wrapping(tree, budget=3)
        # Impossible extra constraint -> full search proves no solution.
        with self.assertRaises(NoSolutionError):
            refenum.best_wrapping(tree, accept=lambda s: "Z" in s)
        # Without the constraint the same tree solves fine.
        s, _ = refenum.best_wrapping(tree, budget=1 << 20)
        self.assertEqual(refenum.ref_parse(s), tree)

    def test_independent_parsers_agree(self):
        for src in ("a+b*c", "a=b?c:d", "f(x,-y)", "a?b?c:d:e?f:g"):
            self.assertEqual(print_ast(parse(src)),
                             refenum.best_wrapping(refenum.ref_parse(src))[0])


# --------------------------------------------------------------------------
# Comments
# --------------------------------------------------------------------------

COMMENT_PRINT_CASES = [
    # AST built directly -> exact minimal text.
    (binop("-", var("a"),
           binop("-", var("b"), var("c"), lead="L", trail="T")),
     "a-(/*L*/b-c/*T*/)"),
    (binop("+", binop("-", var("a"), var("b"), trail="T"), var("c")),
     "a-b/*T*/+c"),
    (binop("+", var("a"), binop("-", var("b"), var("c"), lead="L")),
     "a+(/*L*/b-c)"),
    (cond(var("a"),
          binop("=", var("b"), var("c"), lead="L", trail="T"),
          var("d")),
     "a?/*L*/b=c/*T*/:d"),
    (cond(binop("=", var("a"), var("b"), lead="L"), var("c"), var("d")),
     "(/*L*/a=b)?c:d"),
    (cond(var("a"), var("b"),
          binop("=", var("c"), var("d"), trail="T")),
     "a?b:(c=d/*T*/)"),
    (neg(var("a"), trail="T"), "-a/*T*/"),
    (neg(neg(var("a"), lead="L")), "-/*L*/-a"),
    (call(var("f"), [var("x", lead="L", trail="T")]), "f(/*L*/x/*T*/)"),
    (call(var("f", trail="cf"), [var("x")]), "f/*cf*/(x)"),
    (call(var("f", lead="cf"), [var("x")]), "(/*cf*/f)(x)"),
    (binop("+", var("a", lead="p", trail="q"),
           neg(var("b"), lead="n")),
     "(/*p*/a/*q*/)+/*n*/-b"),
    (var("a", lead="hi", trail="bye"), "/*hi*/a/*bye*/"),
]


class CommentTests(unittest.TestCase):
    def test_comment_print_expectations(self):
        for tree, expected in COMMENT_PRINT_CASES:
            with self.subTest(expected=expected):
                self.assertEqual(print_ast(tree), expected)

    def test_comment_round_trip_preserves_binding(self):
        for _, text in COMMENT_PRINT_CASES:
            with self.subTest(text=text):
                tree = parse(text)
                self.assertEqual(print_ast(tree), text)

    def test_comments_do_not_migrate_to_siblings(self):
        # Trail on the callee must not appear on the call, etc.
        tree = call(var("f", trail="callee"), [var("x", lead="arg")])
        text = print_ast(tree)
        again = parse(text)
        self.assertEqual(again["callee"].get("trail"), "callee")
        self.assertNotIn("trail", again)
        self.assertEqual(again["args"][0].get("lead"), "arg")
        # Left child's lead must survive on the child, not the parent.
        t2 = parse("(/*L*/a-b)+c")
        self.assertEqual(t2["left"].get("lead"), "L")
        self.assertNotIn("lead", t2)

    def test_unrepresentable_comment_positions_rejected(self):
        # Comment in an empty argument list binds to no node.
        with self.assertRaises(ParseError) as cm:
            parse("f(/*orphan*/)")
        self.assertIsInstance(cm.exception.offset, int)
        # Two comments in one gap cannot both attach to one node side.
        with self.assertRaises(ParseError):
            parse("a+/*1*//*2*/b")

    def test_comment_text_restrictions(self):
        with self.assertRaises(ASTError):
            print_ast(var("a", lead="bad */ terminator"))
        with self.assertRaises(ASTError):
            print_ast(var("a", trail="line\nbreak"))
        with self.assertRaises(ParseError):
            parse("/*line\nbreak*/a")
        with self.assertRaises(ParseError):
            parse("/*unterminated")


# --------------------------------------------------------------------------
# Parser errors and validation
# --------------------------------------------------------------------------

class ErrorTests(unittest.TestCase):
    def test_parse_errors_are_located(self):
        bad = ["", "a+", "(a", "a?", "f(a,", "a b", "@", "a=", "?-1",
               "f(a,,b)", "a+)", "((a)", "*a"]
        for src in bad:
            with self.subTest(src=src):
                with self.assertRaises(ParseError) as cm:
                    parse(src)
                self.assertGreaterEqual(cm.exception.offset, 0)
                self.assertGreaterEqual(cm.exception.line, 1)
                self.assertGreaterEqual(cm.exception.column, 1)

    def test_multiline_error_position(self):
        try:
            parse("a\n +\n@")
        except ParseError as e:
            self.assertEqual(e.line, 3)
        else:  # pragma: no cover
            self.fail("expected ParseError")

    def test_validate_rejects_bool_and_float_numbers(self):
        with self.assertRaises(ASTError):
            validate_tree({"kind": "num", "value": True})
        with self.assertRaises(ASTError):
            validate_tree({"kind": "num", "value": 1.5})
        with self.assertRaises(ASTError):
            validate_tree({"kind": "num", "value": -1})

    def test_validate_rejects_bad_shapes(self):
        bad = [
            {"kind": "wat"},
            {"kind": "var"},
            {"kind": "var", "name": "9x"},
            {"kind": "var", "name": "a", "extra": 1},
            {"kind": "bin", "op": "%", "left": var("a"),
             "right": num(1)},
            {"kind": "neg", "operand": "x"},
            {"kind": "call", "callee": var("f"), "args": (var("x"),)},
        ]
        for tree in bad:
            with self.subTest(tree=tree):
                with self.assertRaises(ASTError):
                    validate_tree(tree)

    def test_node_limit_and_cycles(self):
        # 301 nodes.
        root = var("x")
        for _ in range(300):
            root = neg(root)
        with self.assertRaises(ASTError):
            print_ast(root)
        # Exactly 300 is accepted.
        root = var("x")
        for _ in range(299):
            root = neg(root)
        print_ast(root)
        # A cyclic "tree".
        node = {"kind": "neg"}
        node["operand"] = node
        with self.assertRaises(ASTError):
            validate_tree(node)

    def test_parse_node_limit(self):
        with self.assertRaises(ParseError):
            parse("-" * 300 + "a", max_nodes=10)

    def test_validation_returns_independent_copy(self):
        original = var("a")
        safe = validate_tree(original)
        safe["name"] = "b"
        self.assertEqual(original["name"], "a")

    def test_non_str_source_type(self):
        with self.assertRaises(TypeError):
            parse(b"a+b")


# --------------------------------------------------------------------------
# Supplementary round-trip fuzz (deterministic, no core-derived oracles)
# --------------------------------------------------------------------------

class RoundTripTests(unittest.TestCase):
    def test_print_parse_print_stable_on_enumeration_trees(self):
        trees = gen_trees(max_nodes=8, max_depth=4)
        for tree in trees[::37]:
            text = print_ast(from_ref(tree))
            again = parse(text)
            self.assertEqual(print_ast(again), text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
