"""Tests for the position-mapping print API (minparen.mapping).

Independence notes:

* Hand-written span/operator/comment expectations are derived from the
  documented grammar, not from the implementation.
* Every node's *core* slice is re-parsed with the independent
  :func:`minparen.parse` and must equal the original subtree (comments
  stripped) -- the expected value never comes from the mapping code.
* Span tiling and per-character query sweeps check the map against the
  text itself, character by character, including sibling boundaries.
"""

from __future__ import annotations

import unittest

from minparen import (
    ASTError, parse, print_ast, print_ast_mapped,
)
from minparen.ast_nodes import binop, call, cond, neg, num, var
from minparen.mapping import PrintMap, NodeSpan

from test_minparen import (
    COMMENT_PRINT_CASES, STRUCTURAL_CASES, from_ref, gen_trees,
)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def subtree_at(root: dict, path: str) -> dict:
    """Resolve a mapping path like ``root.right.args[1]`` against a tree."""
    node = root
    for part in path.split(".")[1:]:
        if part.startswith("args["):
            node = node["args"][int(part[5:-1])]
        else:
            node = node[part]
    return node


def strip_root_comments(node: dict) -> dict:
    """Copy ``node`` without its own lead/trail (descendants keep theirs).

    A node's core slice excludes only the node's *own* comments; comments
    of descendants sit inside the core and re-bind to the same nodes when
    the slice is parsed standalone.
    """
    return {k: v for k, v in node.items() if k not in ("lead", "trail")}


def span_features(node: NodeSpan) -> list[tuple[str, tuple[int, int]]]:
    """The ordered features that must tile ``node.span`` exactly."""
    parts = []
    if node.parens is not None:
        parts.append(("paren", node.parens[0]))
    if node.lead is not None:
        parts.append(("lead", node.lead))
    parts.append(("core", node.core))
    if node.trail is not None:
        parts.append(("trail", node.trail))
    if node.parens is not None:
        parts.append(("paren", node.parens[1]))
    return parts


def classify_manually(mapped, offset: int):
    """Independent (test-side) classification of one offset."""
    for comment in mapped.comments:
        if comment.span[0] <= offset < comment.span[1]:
            return "comment", comment.path
    if mapped.text[offset] in " \t\n\r":
        return "whitespace", None
    for node in mapped.nodes:
        if node.parens is not None:
            for paren in node.parens:
                if paren[0] <= offset < paren[1]:
                    return "paren", node.path
        for op in node.operators:
            if op.span[0] <= offset < op.span[1]:
                return "operator", node.path
    best = None
    for node in mapped.nodes:
        if node.core[0] <= offset < node.core[1]:
            if best is None or node.depth > best.depth:
                best = node
    assert best is not None, f"offset {offset} uncovered"
    return "code", best.path


def check_map_invariants(testcase, tree):
    """All structural invariants of the map for one tree."""
    mapped = print_ast_mapped(tree)
    text = mapped.text
    testcase.assertEqual(text, print_ast(tree))
    by_path = {n.path: n for n in mapped.nodes}

    # Root covers the whole text; paths are unique and resolvable.
    testcase.assertEqual(mapped.nodes[0].path, "root")
    testcase.assertEqual(mapped.nodes[0].span, (0, len(text)))
    testcase.assertIsNone(mapped.nodes[0].parent)
    testcase.assertEqual(len(by_path), len(mapped.nodes))

    for node in mapped.nodes:
        # Every feature interval lies inside the text and is non-empty.
        for label, (s, e) in span_features(node):
            testcase.assertLess(s, e, (node.path, label))
            testcase.assertGreaterEqual(s, 0)
            testcase.assertLessEqual(e, len(text))

        # span = parens? + lead? + core + trail? + parens? tiled exactly.
        parts = span_features(node)
        testcase.assertEqual(parts[0][1][0], node.span[0], node.path)
        testcase.assertEqual(parts[-1][1][1], node.span[1], node.path)
        for (_, (_, prev_end)), (_, (next_start, _)) in zip(parts,
                                                            parts[1:]):
            testcase.assertEqual(prev_end, next_start, node.path)

        # core = children spans + own operator spans tiled exactly.
        children = [n for n in mapped.nodes if n.parent == node.path]
        pieces = [c.span for c in children]
        pieces += [op.span for op in node.operators]
        pieces.sort()
        if pieces:
            testcase.assertEqual(pieces[0][0], node.core[0], node.path)
            testcase.assertEqual(pieces[-1][1], node.core[1], node.path)
            for (_, prev_end), (next_start, _) in zip(pieces, pieces[1:]):
                testcase.assertEqual(prev_end, next_start, node.path)
        else:  # a leaf: the core is the leaf text itself
            testcase.assertIn(node.kind, ("num", "var"))
            testcase.assertLess(node.core[0], node.core[1])

        # Parent containment: span inside parent span and parent core.
        if node.parent is not None:
            parent = by_path[node.parent]
            testcase.assertLessEqual(parent.span[0], node.span[0])
            testcase.assertLessEqual(node.span[1], parent.span[1])
            testcase.assertLessEqual(parent.core[0], node.span[0])
            testcase.assertLessEqual(node.span[1], parent.core[1])

        # Auto-added parens hug the span and belong to this node only.
        if node.parens is not None:
            (os_, oe), (cs, ce) = node.parens
            testcase.assertEqual((os_, oe), (node.span[0], node.span[0] + 1))
            testcase.assertEqual((cs, ce), (node.span[1] - 1, node.span[1]))
            testcase.assertEqual(text[os_], "(")
            testcase.assertEqual(text[cs], ")")

        # Comment slices really are /* ... */ at the recorded positions.
        for side, span in (("lead", node.lead), ("trail", node.trail)):
            if span is not None:
                slice_ = text[span[0]:span[1]]
                testcase.assertTrue(slice_.startswith("/*"))
                testcase.assertTrue(slice_.endswith("*/"))

        # Operator slices match their recorded symbols.
        for op in node.operators:
            testcase.assertEqual(text[op.span[0]:op.span[1]], op.symbol)

        # Independent certificate: the core slice re-parses to the same
        # subtree (only the subtree root's own comments are outside the
        # core), checked with the standalone parser.
        core_text = text[node.core[0]:node.core[1]]
        expected = strip_root_comments(subtree_at(tree, node.path))
        testcase.assertEqual(parse(core_text), expected,
                             (node.path, core_text))

    # Flat aggregates are sorted and consistent with the per-node data.
    testcase.assertEqual([c.span for c in mapped.comments],
                         sorted(c.span for c in mapped.comments))
    testcase.assertEqual([o.span for o in mapped.operators],
                         sorted(o.span for o in mapped.operators))
    per_node_ops = sorted(op.span for n in mapped.nodes
                          for op in n.operators)
    testcase.assertEqual([o.span for o in mapped.operators], per_node_ops)
    per_node_comments = sorted(
        span for n in mapped.nodes for span in (n.lead, n.trail)
        if span is not None)
    testcase.assertEqual([c.span for c in mapped.comments],
                         per_node_comments)

    # Per-character sweep: query() agrees with manual classification at
    # every offset, hence also on every sibling/feature boundary.
    for offset in range(len(text)):
        info = mapped.query(offset)
        expected_category, expected_path = classify_manually(mapped,
                                                             offset)
        testcase.assertEqual(info.category, expected_category,
                             (text, offset))
        testcase.assertEqual(info.path, expected_path, (text, offset))
        testcase.assertEqual(info.offset, offset)
        testcase.assertLessEqual(info.span[0], offset)
        testcase.assertLess(offset, info.span[1])
        if info.category == "operator":
            testcase.assertEqual(text[info.span[0]:info.span[1]],
                                 info.detail)
        if info.category == "comment":
            testcase.assertIn(info.detail, ("lead", "trail"))
    return mapped


# --------------------------------------------------------------------------
# Hand-written expectations
# --------------------------------------------------------------------------

class HandwrittenSpanTests(unittest.TestCase):
    def test_right_associative_assignment(self):
        mapped = print_ast_mapped(parse("a=b=c"))
        self.assertEqual(mapped.text, "a=b=c")
        root = mapped.node("root")
        self.assertEqual(root.span, (0, 5))
        self.assertEqual(root.core, (0, 5))
        self.assertIsNone(root.parens)
        self.assertEqual([(o.symbol, o.span) for o in root.operators],
                         [("=", (1, 2))])
        right = mapped.node("root.right")
        self.assertEqual(right.span, (2, 5))
        self.assertEqual([(o.symbol, o.span) for o in right.operators],
                         [("=", (3, 4))])
        self.assertEqual(mapped.node("root.left").span, (0, 1))
        self.assertEqual(mapped.node("root.right.left").span, (2, 3))
        self.assertEqual(mapped.node("root.right.right").span, (4, 5))
        # Half-open boundary: offset 1 is the operator, not the left leaf.
        self.assertEqual(mapped.query(1).category, "operator")
        self.assertEqual(mapped.query(1).detail, "=")
        self.assertEqual(mapped.query(1).path, "root")
        self.assertEqual(mapped.query(2).path, "root.right.left")
        self.assertEqual(mapped.query(2).category, "code")

    def test_nested_ternary(self):
        mapped = print_ast_mapped(parse("a?b:c?d:e"))
        self.assertEqual(mapped.text, "a?b:c?d:e")
        root = mapped.node("root")
        self.assertEqual(root.span, (0, 9))
        self.assertEqual([(o.symbol, o.span) for o in root.operators],
                         [("?", (1, 2)), (":", (3, 4))])
        inner = mapped.node("root.else")
        self.assertEqual(inner.span, (4, 9))
        self.assertEqual([(o.symbol, o.span) for o in inner.operators],
                         [("?", (5, 6)), (":", (7, 8))])
        self.assertEqual(mapped.query(5).category, "operator")
        self.assertEqual(mapped.query(5).path, "root.else")
        self.assertEqual(mapped.query(4).category, "code")
        self.assertEqual(mapped.query(4).path, "root.else.cond")

    def test_consecutive_negations(self):
        mapped = print_ast_mapped(parse("---x"))
        self.assertEqual(mapped.text, "---x")
        for depth, path in enumerate(
                ("root", "root.operand", "root.operand.operand")):
            node = mapped.node(path)
            self.assertEqual(node.kind, "neg")
            self.assertEqual(node.span, (depth, 4))
            self.assertEqual(node.core, (depth, 4))
            self.assertEqual([(o.symbol, o.span) for o in node.operators],
                             [("-", (depth, depth + 1))])
        leaf = mapped.node("root.operand.operand.operand")
        self.assertEqual(leaf.span, (3, 4))
        # Offset 1 belongs to the *inner* neg's operator, not the outer.
        info = mapped.query(1)
        self.assertEqual(info.category, "operator")
        self.assertEqual(info.path, "root.operand")

    def test_auto_parens_belong_to_the_grouped_node(self):
        mapped = print_ast_mapped(parse("a-(b-c)"))
        self.assertEqual(mapped.text, "a-(b-c)")
        right = mapped.node("root.right")
        self.assertEqual(right.span, (2, 7))
        self.assertEqual(right.core, (3, 6))
        self.assertEqual(right.parens, ((2, 3), (6, 7)))
        self.assertIsNone(mapped.node("root").parens)
        # The parens classify as "paren" owned by the right child.
        for offset in (2, 6):
            info = mapped.query(offset)
            self.assertEqual(info.category, "paren")
            self.assertEqual(info.path, "root.right")
        # ...and the core inside them is unaffected.
        self.assertEqual(mapped.query(4).category, "operator")
        self.assertEqual(mapped.query(4).path, "root.right")

    def test_comments_mapped_outside_core(self):
        tree = binop("-", var("a"),
                     binop("-", var("b"), var("c"), lead="L", trail="T"))
        mapped = print_ast_mapped(tree)
        self.assertEqual(mapped.text, "a-(/*L*/b-c/*T*/)")
        right = mapped.node("root.right")
        self.assertEqual(right.span, (2, 17))
        self.assertEqual(right.parens, ((2, 3), (16, 17)))
        self.assertEqual(right.lead, (3, 8))
        self.assertEqual(right.core, (8, 11))
        self.assertEqual(right.trail, (11, 16))
        self.assertEqual(mapped.text[8:11], "b-c")
        # Comments are separate intervals, not part of the syntax core.
        self.assertEqual(len(mapped.comments), 2)
        lead, trail = mapped.comments
        self.assertEqual((lead.side, lead.text, lead.span), ("lead", "L",
                                                             (3, 8)))
        self.assertEqual((trail.side, trail.text, trail.span),
                         ("trail", "T", (11, 16)))
        info = mapped.query(5)
        self.assertEqual(info.category, "comment")
        self.assertEqual(info.path, "root.right")
        self.assertEqual(info.detail, "lead")
        # Boundary: the offset right after the comment is the child's core.
        self.assertEqual(mapped.query(8).category, "code")
        self.assertEqual(mapped.query(8).path, "root.right.left")

    def test_non_ascii_comment_offsets_are_character_offsets(self):
        tree = binop("+", var("x", trail="héllo✓"), num(1))
        mapped = print_ast_mapped(tree)
        self.assertEqual(mapped.text, "x/*héllo✓*/+1")
        left = mapped.node("root.left")
        # "/*" + 6 code points + "*/" = 10 characters, not UTF-8 bytes.
        self.assertEqual(left.trail, (1, 11))
        self.assertEqual(mapped.text[1:11], "/*héllo✓*/")
        self.assertEqual(left.core, (0, 1))
        comment = mapped.comments[0]
        self.assertEqual(comment.text, "héllo✓")
        self.assertEqual(mapped.query(4).category, "comment")
        self.assertEqual(mapped.query(11).category, "operator")
        self.assertEqual(mapped.query(11).detail, "+")

    def test_non_ascii_lead_comment_on_root(self):
        mapped = print_ast_mapped(var("a", lead="注释"))
        self.assertEqual(mapped.text, "/*注释*/a")
        root = mapped.node("root")
        self.assertEqual(root.span, (0, 7))
        self.assertEqual(root.lead, (0, 6))
        self.assertEqual(root.core, (6, 7))
        self.assertEqual(mapped.query(3).category, "comment")
        self.assertEqual(mapped.query(6).category, "code")

    def test_call_operators_and_args(self):
        mapped = print_ast_mapped(parse("f(x,-y)"))
        self.assertEqual(mapped.text, "f(x,-y)")
        root = mapped.node("root")
        self.assertEqual([(o.symbol, o.span) for o in root.operators],
                         [("(", (1, 2)), (",", (3, 4)), (")", (6, 7))])
        self.assertEqual(mapped.node("root.callee").span, (0, 1))
        self.assertEqual(mapped.node("root.args[0]").span, (2, 3))
        self.assertEqual(mapped.node("root.args[1]").span, (4, 6))
        self.assertEqual(mapped.node("root.args[1].operand").span, (5, 6))
        info = mapped.query(3)
        self.assertEqual((info.category, info.detail, info.path),
                         ("operator", ",", "root"))

    def test_identical_subtrees_get_distinct_spans(self):
        # Two subtrees print byte-identically; positions must come from the
        # emission, never from substring search.
        tree = binop("*", binop("+", var("a"), var("b")),
                     binop("+", var("a"), var("b")))
        mapped = print_ast_mapped(tree)
        self.assertEqual(mapped.text, "(a+b)*(a+b)")
        left = mapped.node("root.left")
        right = mapped.node("root.right")
        self.assertEqual(left.span, (0, 5))
        self.assertEqual(right.span, (6, 11))
        self.assertEqual(mapped.text[left.core[0]:left.core[1]], "a+b")
        self.assertEqual(mapped.text[right.core[0]:right.core[1]], "a+b")
        self.assertNotEqual(left.core, right.core)
        self.assertEqual(mapped.query(1).path, "root.left.left")
        self.assertEqual(mapped.query(7).path, "root.right.left")
        self.assertEqual(mapped.query(2).path, "root.left")
        self.assertEqual(mapped.query(8).path, "root.right")


# --------------------------------------------------------------------------
# Query contract
# --------------------------------------------------------------------------

class QueryContractTests(unittest.TestCase):
    def test_out_of_range_and_type_errors(self):
        mapped = print_ast_mapped(parse("a+b"))
        with self.assertRaises(ValueError):
            mapped.query(-1)
        with self.assertRaises(ValueError):
            mapped.query(3)
        with self.assertRaises(ValueError):
            mapped.query(100)
        with self.assertRaises(TypeError):
            mapped.query(1.0)
        with self.assertRaises(TypeError):
            mapped.query(True)
        with self.assertRaises(TypeError):
            mapped.query("1")

    def test_whitespace_category_for_foreign_text(self):
        # The printer never emits whitespace outside comments; a hand-built
        # map over foreign text still classifies it explicitly instead of
        # forcing it onto a neighbouring node.
        leaf = NodeSpan(path="root", kind="var", depth=0, parent=None,
                        span=(0, 1), core=(0, 1), parens=None, lead=None,
                        trail=None, operators=())
        mapped = PrintMap("a + b", [leaf])
        info = mapped.query(1)
        self.assertEqual(info.category, "whitespace")
        self.assertIsNone(info.path)
        self.assertEqual(info.span, (1, 2))
        self.assertEqual(mapped.query(0).category, "code")

    def test_unknown_path_raises_key_error(self):
        mapped = print_ast_mapped(parse("a"))
        with self.assertRaises(KeyError):
            mapped.node("root.nope")

    def test_mapped_print_rejects_invalid_ast_like_print_ast(self):
        with self.assertRaises(ASTError):
            print_ast_mapped({"kind": "num", "value": True})
        with self.assertRaises(ASTError):
            print_ast_mapped(var("a", lead="bad */ terminator"))

    def test_input_tree_is_not_mutated(self):
        tree = binop("-", var("a"),
                     binop("-", var("b"), var("c"), lead="L"))
        snapshot = repr(tree)
        print_ast_mapped(tree)
        self.assertEqual(repr(tree), snapshot)


# --------------------------------------------------------------------------
# Bulk invariant checks over many trees
# --------------------------------------------------------------------------

class BulkInvariantTests(unittest.TestCase):
    def test_structural_cases(self):
        for source, _expected in STRUCTURAL_CASES:
            with self.subTest(source=source):
                check_map_invariants(self, parse(source))

    def test_comment_cases(self):
        for tree, _expected in COMMENT_PRINT_CASES:
            with self.subTest(tree=repr(tree)):
                check_map_invariants(self, tree)

    def test_enumeration_sample(self):
        trees = gen_trees(max_nodes=8, max_depth=4)
        for tree in trees[::37]:
            with self.subTest(tree=repr(tree)):
                check_map_invariants(self, from_ref(tree))

    def test_mixed_feature_tree(self):
        # Ternary + right-associative assignment + negation chain +
        # non-ASCII comment in one tree.
        tree = cond(
            var("flag"),
            binop("=", var("x"),
                  binop("=", var("y"), neg(neg(var("z"))))),
            call(var("f"), [var("v", lead="注释✓")]),
        )
        mapped = check_map_invariants(self, tree)
        self.assertEqual(mapped.text, "flag?x=y=--z:f(/*注释✓*/v)")
        self.assertEqual(mapped.query(4).category, "operator")   # '?'
        self.assertEqual(mapped.query(4).detail, "?")
        assign = mapped.node("root.then.right")
        self.assertEqual(assign.kind, "bin")
        self.assertEqual(mapped.text[assign.core[0]:assign.core[1]],
                         "y=--z")
        info = mapped.query(mapped.node("root.else.args[0]").lead[0] + 3)
        self.assertEqual(info.category, "comment")


if __name__ == "__main__":
    unittest.main(verbosity=2)
