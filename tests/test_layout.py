"""Tests for width-aware layout (minparen.format_ast).

The strongest checks do NOT derive expected values from the core:
reference.layout_enum is an independently written enumerator of every
legal layout; the core DP must match its optimum byte for byte.
Hand-written expected strings cover the broken shapes directly. Round
trips through parse are only a supplementary consistency check.
"""

from __future__ import annotations

import unittest

from minparen import (
    BudgetExhaustedError, NoSolutionError, format_ast, parse, print_ast,
)
from minparen.ast_nodes import binop, call, cond, neg, num, var
from reference import layout_enum


def penalty(text: str, width: int) -> tuple[int, int, str]:
    lines = text.split("\n")
    overflow = sum(max(0, len(line) - width) for line in lines)
    return (overflow, len(lines) - 1, text)


# --------------------------------------------------------------------------
# Hand-derived expectations
# --------------------------------------------------------------------------

class HandWrittenLayouts(unittest.TestCase):
    def test_flat_when_it_fits(self):
        tree = parse("f(a+b, c*d)")
        self.assertEqual(format_ast(tree, 80), "f(a+b,c*d)")
        self.assertEqual(format_ast(tree, 10), "f(a+b,c*d)")

    def test_call_breaks_one_arg_per_line(self):
        tree = parse("f(a+b, c*d)")
        self.assertEqual(format_ast(tree, 9), "f(\n  a+b,\n  c*d\n)")

    def test_bin_breaks_after_operator(self):
        tree = parse("alpha+beta+gamma")
        self.assertEqual(format_ast(tree, 10),
                         "alpha +\n  beta +\n  gamma")

    def test_cond_breaks_all_three_parts(self):
        tree = parse("alpha?beta:gamma")
        self.assertEqual(format_ast(tree, 8),
                         "alpha ?\n  beta :\n  gamma")

    def test_short_cond_prefers_single_overflow_char(self):
        # Penalty order: 1 overflow char beats adding 2 newlines.
        tree = parse("a?b:c")
        self.assertEqual(format_ast(tree, 4), "a?b:c")

    def test_nested_groups_break_independently(self):
        tree = parse("a-(b-c)*d+e")
        self.assertEqual(format_ast(tree, 8),
                         "a -\n  (b -\n    c) *\n    d +\n  e")

    def test_unbreakable_word_overflows_but_is_preserved(self):
        tree = var("a_very_long_identifier")
        # Only one legal layout; the overflow is unavoidable and counted.
        self.assertEqual(format_ast(tree, 4), "a_very_long_identifier")
        self.assertEqual(penalty(format_ast(tree, 4), 4)[0], 18)

    def test_overflow_beats_newlines(self):
        # Overflow is compared before newline count: breaking to zero
        # overflow wins even though it adds a newline...
        tree = parse("abc+defgh")
        self.assertEqual(format_ast(tree, 8), "abc +\n  defgh")
        # ...but when breaking cannot reduce the overflow, the flat form
        # wins on the newline count.
        tree2 = parse("abcdefg+h")
        self.assertEqual(format_ast(tree2, 8), "abcdefg+h")

    def test_width_one_is_deterministic(self):
        tree = parse("f(a, b?c:d)")
        first = format_ast(tree, 1)
        self.assertEqual(first, format_ast(tree, 1))
        self.assertEqual(parse(first), tree)

    def test_parens_come_from_syntax_not_layout(self):
        # Breaking must never add or remove parentheses.
        tree = parse("a*(b+c)")
        for width in (1, 2, 5, 80):
            out = format_ast(tree, width)
            self.assertEqual(out.replace("\n", "").replace(" ", ""),
                             "a*(b+c)")

    def test_comments_stay_on_their_node_lines(self):
        tree = parse("a-(/*inner*/b-c/*end*/)")
        out = format_ast(tree, 6)
        self.assertEqual(parse(out), tree)
        self.assertIn("/*inner*/", out)
        self.assertIn("/*end*/", out)


# --------------------------------------------------------------------------
# Small-tree generator (structural trees only)
# --------------------------------------------------------------------------

LEAVES = [var("a"), var("bb"), num(7)]


def gen_trees(max_depth: int):
    # Every node is deep-copied: the contract requires a proper tree
    # (shared substructure is rejected by validate_tree).
    import copy

    def fresh(node):
        return copy.deepcopy(node)

    trees = list(LEAVES)
    if max_depth == 0:
        return trees
    subs = gen_trees(max_depth - 1)
    for left in subs:
        for right in subs:
            trees.append(binop("+", fresh(left), fresh(right)))
            trees.append(binop("=", fresh(left), fresh(right)))
    for test in subs:
        trees.append(cond(fresh(test), fresh(test), fresh(test)))
    trees.append(neg(fresh(subs[0])))
    trees.append(call(var("f"), [fresh(s) for s in subs[:2]]))
    trees.append(call(var("gg"), [fresh(subs[0])]))
    return trees


class EnumerationCrossCheck(unittest.TestCase):
    def test_matches_independent_enumerator(self):
        # All depth-1 trees plus a deterministic stride sample of the
        # larger set: the enumerator is exponential by design.
        widths = (1, 2, 3, 4, 5, 8, 13, 21, 40, 80)
        for tree in gen_trees(1) + gen_trees(2)[::13]:
            for width in widths:
                with self.subTest(tree=print_ast(tree), width=width):
                    self.assertEqual(format_ast(tree, width),
                                     layout_enum.best_layout(tree, width))

    def test_round_trip_all_widths(self):
        # Every width 1..80 on small trees; a deterministic stride sample
        # of the larger generated set keeps the runtime bounded.
        trees = gen_trees(1) + gen_trees(2)[::37]
        for tree in trees:
            for width in range(1, 81):
                with self.subTest(tree=print_ast(tree), width=width):
                    self.assertEqual(parse(format_ast(tree, width)), tree)

    def test_ast_is_not_mutated(self):
        import copy
        tree = parse("f(a+b, c?d:e, g(1, 22))")
        snapshot = copy.deepcopy(tree)
        for width in (1, 7, 33, 80):
            format_ast(tree, width)
        self.assertEqual(tree, snapshot)


# --------------------------------------------------------------------------
# Errors, budget semantics, determinism
# --------------------------------------------------------------------------

class ContractAndBudget(unittest.TestCase):
    def test_width_domain(self):
        tree = parse("a+b")
        for bad in (0, -1, 81, 1000):
            with self.subTest(bad=bad):
                self.assertRaises(ValueError, format_ast, tree, bad)
        for bad_type in (True, 1.5, "10", None):
            with self.subTest(bad_type=bad_type):
                self.assertRaises(TypeError, format_ast, tree, bad_type)

    def test_budget_exhaustion_carries_legal_fallback(self):
        tree = parse("f(a+b, c*d, g(x, y?z:w))")
        with self.assertRaises(BudgetExhaustedError) as caught:
            format_ast(tree, 10, budget=1)
        fallback = caught.exception.best_effort
        # The fallback is a legal layout (re-parses to the same tree) but
        # its optimality is unproven -- distinct from a proven optimum.
        self.assertEqual(parse(fallback), tree)
        self.assertEqual(fallback, print_ast(tree))

    def test_sufficient_budget_matches_unbounded(self):
        tree = parse("f(a+b, c*d, g(x, y?z:w))")
        for width in (1, 5, 12, 80):
            self.assertEqual(format_ast(tree, width, budget=10_000),
                             format_ast(tree, width))

    def test_enumerator_budget_vs_no_solution(self):
        tree = parse("a-(b-c)")
        with self.assertRaises(BudgetExhaustedError):
            layout_enum.best_layout(tree, 5, budget=1)
        with self.assertRaises(NoSolutionError):
            layout_enum.best_layout(tree, 5, accept=lambda s: "ZZZ" in s)
        self.assertEqual(layout_enum.best_layout(tree, 80), "a-(b-c)")

    def test_determinism(self):
        tree = parse("x=a?b:c-(d+e)*f")
        for width in (1, 3, 17, 80):
            self.assertEqual(format_ast(tree, width),
                             format_ast(tree, width))


if __name__ == "__main__":
    unittest.main()
