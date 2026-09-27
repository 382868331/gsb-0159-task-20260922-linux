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
    parse, print_ast, print_ast_mapped, validate_tree,
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


# --------------------------------------------------------------------------
# Source-map tests: print_ast_mapped
# --------------------------------------------------------------------------

def _walk_paths(node, path="root"):
    """Yield the accessor path of every node in a dict AST."""
    yield path
    kind = node["kind"]
    if kind == "neg":
        yield from _walk_paths(node["operand"], path + ".operand")
    elif kind == "bin":
        yield from _walk_paths(node["left"], path + ".left")
        yield from _walk_paths(node["right"], path + ".right")
    elif kind == "cond":
        yield from _walk_paths(node["cond"], path + ".cond")
        yield from _walk_paths(node["then"], path + ".then")
        yield from _walk_paths(node["else"], path + ".else")
    elif kind == "call":
        yield from _walk_paths(node["callee"], path + ".callee")
        for i, arg in enumerate(node["args"]):
            yield from _walk_paths(arg, path + ".args[" + str(i) + "]")


def _node_at_path(node, path):
    for part in path.split(".")[1:]:
        if part.startswith("args["):
            node = node["args"][int(part[5:-1])]
        else:
            node = node[part]
    return node


class SourceMapTests(unittest.TestCase):
    def _check_tree(self, tree):
        result = print_ast_mapped(tree)
        # Same text as the plain printer.
        self.assertEqual(result.text, print_ast(tree))
        # One span per node occurrence, keyed by its unique path.
        paths = list(_walk_paths(tree))
        self.assertEqual(sorted(s.path for s in result.spans),
                         sorted(paths))
        self.assertEqual(len({s.path for s in result.spans}),
                         len(result.spans))
        root = result.span_for("root")
        self.assertEqual((root.start, root.end), (0, len(result.text)))
        for span in result.spans:
            node = _node_at_path(tree, span.path)
            self.assertEqual(span.kind, node["kind"])
            # Offsets are sane and the core sits inside the full span.
            self.assertLessEqual(span.start, span.core_start)
            self.assertLessEqual(span.core_start, span.core_end)
            self.assertLessEqual(span.core_end, span.end)
            # Added parens literally surround the core.
            if span.parens.startswith("added"):
                self.assertEqual(result.text[span.start], "(")
                self.assertEqual(result.text[span.end - 1], ")")
                self.assertEqual(span.core_start, span.start + 1)
                self.assertEqual(span.core_end, span.end - 1)
            else:
                self.assertEqual(span.parens, "omitted-tight")
                self.assertEqual((span.start, span.end),
                                 (span.core_start, span.core_end))
            # The node's own comments are inside its core span.
            core = result.text[span.core_start:span.core_end]
            if node.get("lead") is not None:
                self.assertTrue(core.startswith("/*" + node["lead"] + "*/"))
            if node.get("trail") is not None:
                self.assertTrue(core.endswith("/*" + node["trail"] + "*/"))
        # Nesting is consistent with the AST hierarchy: every child's
        # span is strictly inside its parent's span.
        by_path = {s.path: s for s in result.spans}
        for span in result.spans:
            if span.path == "root":
                continue
            parent_path = span.path.rsplit(".", 1)[0]
            parent = by_path[parent_path]
            self.assertLessEqual(parent.start, span.start)
            self.assertGreaterEqual(parent.end, span.end)
            self.assertLess(span.end - span.start,
                            parent.end - parent.start)
        # Every offset maps back to some node; the innermost mapping of
        # a leaf's text is that leaf.
        for offset in range(len(result.text)):
            self.assertIsNotNone(result.node_at(offset))
        return result

    def test_mapped_matches_print_ast_on_enumeration_trees(self):
        for tree in gen_trees(max_nodes=8, max_depth=4)[::53]:
            self._check_tree(from_ref(tree))

    def test_identical_subexpressions_get_distinct_spans(self):
        tree = binop("+", var("x"), var("x"))
        result = self._check_tree(tree)
        self.assertEqual(result.text, "x+x")
        left = result.span_for("root.left")
        right = result.span_for("root.right")
        self.assertEqual((left.start, left.end), (0, 1))
        self.assertEqual((right.start, right.end), (2, 3))
        self.assertEqual(result.node_at(0).path, "root.left")
        self.assertEqual(result.node_at(2).path, "root.right")
        self.assertEqual(result.node_at(1).path, "root")

    def test_structural_parens_attribution(self):
        tree = binop("-", var("a"), binop("-", var("b"), var("c")))
        result = self._check_tree(tree)
        self.assertEqual(result.text, "a-(b-c)")
        span = result.span_for("root.right")
        self.assertEqual(span.parens, "added-structural")
        self.assertEqual(span.slot, "bin+.right")
        self.assertEqual(result.text[span.start:span.end], "(b-c)")
        self.assertEqual(result.text[span.core_start:span.core_end], "b-c")
        self.assertEqual(result.span_for("root.left").parens,
                         "omitted-tight")

    def test_comment_forced_parens_attribution(self):
        tree = binop("+", var("a", lead="L"), var("b"))
        result = self._check_tree(tree)
        self.assertEqual(result.text, "(/*L*/a)+b")
        span = result.span_for("root.left")
        self.assertEqual(span.parens, "added-comment")
        self.assertEqual(result.text[span.start:span.end], "(/*L*/a)")

    def test_nested_cond_and_neg_spans(self):
        tree = cond(var("a"), neg(var("b")),
                    cond(var("c"), var("d"), var("e")))
        result = self._check_tree(tree)
        self.assertEqual(result.text, "a?-b:c?d:e")
        inner = result.span_for("root.else")
        self.assertEqual(result.text[inner.start:inner.end], "c?d:e")
        self.assertEqual(result.node_at(result.text.index("-")).path,
                         "root.then")

    def test_call_spans(self):
        tree = call(var("f"), [binop("+", var("x"), var("y")), num(7)])
        result = self._check_tree(tree)
        self.assertEqual(result.text, "f(x+y,7)")
        arg0 = result.span_for("root.args[0]")
        self.assertEqual(result.text[arg0.start:arg0.end], "x+y")
        self.assertEqual(result.span_for("root.callee").slot,
                         "call.callee")

    def test_span_for_unknown_path_raises(self):
        result = print_ast_mapped(var("a"))
        with self.assertRaises(KeyError):
            result.span_for("root.left")

    def test_mapped_validation_still_strict(self):
        with self.assertRaises(ASTError):
            print_ast_mapped({"kind": "num", "value": -1})


if __name__ == "__main__":
    unittest.main(verbosity=2)
