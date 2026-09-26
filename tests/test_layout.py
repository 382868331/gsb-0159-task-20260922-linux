"""Tests for the width-aware layout engine (minparen.layout).

The strongest check compares the DP engine against
:mod:`reference.reflayout`, an independent exhaustive enumerator over
all flat/broken masks with its own parser as the round-trip oracle.
"""

from __future__ import annotations

import copy
import unittest

from minparen import (
    BudgetExhaustedError, ParseError, format_ast, parse, print_ast,
)
from minparen.ast_nodes import binop, call, cond, neg, num, var
from reference import reflayout, refenum


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


LEAVES = [("v", "a"), ("v", "b"), ("n", 1)]


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


def gen_trees(max_nodes: int, max_depth: int):
    cache: dict = {}

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
            for left in go(budget - 1, depth - 1):
                used = _size(left)
                if used + 2 > budget:
                    continue
                for right in go(budget - 1 - used, depth - 1):
                    out.append(("b", op, left, right))
        for tnode in go(budget - 1, depth - 1):
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


class EnumerationLayoutTests(unittest.TestCase):
    def test_dp_matches_exhaustive_enumeration(self):
        trees = gen_trees(max_nodes=8, max_depth=4)
        uniq = list(dict.fromkeys(trees))
        stride = max(1, len(uniq) // 60)
        sample = uniq[::stride]
        self.assertGreater(len(sample), 40)
        for tree in sample:
            core_tree = from_ref(tree)
            for width in (1, 2, 5, 17, 80):
                with self.subTest(tree=tree, width=width):
                    res = format_ast(core_tree, width)
                    _, expected = reflayout.best_layout(tree, width)
                    self.assertEqual(res.cost, expected)
                    self.assertTrue(res.proven_optimal)

    def test_round_trip_and_determinism_all_widths(self):
        tree = parse("result=compute(alpha+beta*gamma,"
                     "flag?adjust(x,y-z):fallback,1000)")
        for width in range(1, 81):
            with self.subTest(width=width):
                res = format_ast(tree, width)
                self.assertEqual(parse(res.text), tree)
                again = format_ast(tree, width)
                self.assertEqual(res.text, again.text)
                for line in res.text.split("\n"):
                    overflow = max(0, len(line) - width)
                    self.assertGreaterEqual(res.overflow, overflow)

    def test_output_parens_come_from_syntax_only(self):
        # Layout must never add parentheses to escape the width limit.
        trees = gen_trees(max_nodes=8, max_depth=4)
        for tree in trees[::53]:
            core_tree = from_ref(tree)
            flat = print_ast(core_tree)
            for width in (1, 7):
                laid = format_ast(core_tree, width).text
                self.assertEqual(laid.count("("), flat.count("("))
                self.assertEqual(laid.count(")"), flat.count(")"))


class CostModelTests(unittest.TestCase):
    def test_unbreakable_token_may_overflow_but_counts(self):
        name = "a_very_long_identifier_that_cannot_split"
        res = format_ast(var(name), 5)
        self.assertEqual(res.text, name)
        self.assertEqual(res.newlines, 0)
        self.assertEqual(res.overflow, len(name) - 5)

    def test_overflow_is_total_over_lines(self):
        tree = parse("alpha+beta+gamma+delta")
        res = format_ast(tree, 10)
        expected = sum(max(0, len(line) - 10)
                       for line in res.text.split("\n"))
        self.assertEqual(res.overflow, expected)
        self.assertEqual(res.newlines, res.text.count("\n"))

    def test_fitting_flat_always_wins(self):
        tree = parse("f(a+b,c?d:e)")
        res = format_ast(tree, 80)
        self.assertEqual(res.text, "f(a+b,c?d:e)")
        self.assertEqual(res.cost, (0, 0, "f(a+b,c?d:e)"))

    def test_width_one_is_deterministic(self):
        tree = parse("x?y:z")
        res = format_ast(tree, 1)
        self.assertEqual(res.text, format_ast(tree, 1).text)
        self.assertEqual(parse(res.text), tree)


class BudgetAndWidthTests(unittest.TestCase):
    def test_budget_exhausted_carries_legal_best(self):
        tree = parse("result=compute(alpha+beta*gamma,"
                     "flag?adjust(x,y-z):fallback,1000)")
        with self.assertRaises(BudgetExhaustedError) as cm:
            format_ast(tree, 18, budget=3)
        best = cm.exception.best
        self.assertIsNotNone(best)
        self.assertFalse(best.proven_optimal)
        # The fallback is a real legal layout of the same tree.
        self.assertEqual(parse(best.text), tree)
        # A generous budget finishes and proves optimality.
        full = format_ast(tree, 18, budget=1_000_000)
        self.assertTrue(full.proven_optimal)
        self.assertEqual(full.text, format_ast(tree, 18).text)

    def test_width_validation(self):
        tree = var("a")
        for bad in (0, -1, 81, 1000):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    format_ast(tree, bad)
        for bad in (True, "8", 8.0, None):
            with self.subTest(bad=bad):
                with self.assertRaises(TypeError):
                    format_ast(tree, bad)

    def test_budget_validation(self):
        with self.assertRaises(TypeError):
            format_ast(var("a"), 10, budget="x")
        with self.assertRaises(ValueError):
            format_ast(var("a"), 10, budget=-1)

    def test_input_tree_not_modified(self):
        tree = parse("f(a+b*c,d?e:g-h)")
        snapshot = copy.deepcopy(tree)
        format_ast(tree, 4)
        self.assertEqual(tree, snapshot)

    def test_old_interface_untouched(self):
        self.assertEqual(print_ast(parse("a-(b-c)")), "a-(b-c)")
        with self.assertRaises(ParseError):
            parse("a+")


if __name__ == "__main__":
    unittest.main(verbosity=2)
