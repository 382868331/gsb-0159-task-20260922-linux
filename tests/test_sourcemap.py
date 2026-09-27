"""Tests for output<->AST node position mapping (minparen.sourcemap).

These tests do not derive expected *text* from the core; minimality of the
text is certified by the independent enumerator via the structural test
suite.  Here we certify the mapping itself:

* mapped text is byte-identical to print_ast on every enumerated tree;
* every node gets a distinct id/path even for identical subexpressions;
* spans nest exactly like the tree hierarchy;
* node_at maps punctuation to the parent and parens to their owner;
* added/omitted parentheses carry the right attribution.
"""

from __future__ import annotations

import unittest

from minparen import (
    parse, print_ast, print_ast_mapped, validate_tree,
)
from minparen.ast_nodes import binop, call, cond, neg, num, var
from minparen.sourcemap import (
    REASON_COMMENT_LEAD,
    REASON_SLOT_TIGHT,
    REASON_STRUCTURAL,
    STATUS_ADDED,
    STATUS_OMITTED,
)

from tests.test_minparen import from_ref, gen_trees, _size


class MappedTextTests(unittest.TestCase):
    def test_text_equals_print_ast_on_enumeration(self):
        trees = gen_trees(max_nodes=9, max_depth=4)
        uniq = list(dict.fromkeys(trees))
        sample = [t for t in uniq if _size(t) <= 5]
        rest = [t for t in uniq if _size(t) > 5]
        stride = max(1, len(rest) // 80)
        sample.extend(rest[::stride])
        self.assertGreater(len(sample), 200)
        for tree in sample:
            ast = from_ref(tree)
            result = print_ast_mapped(ast)
            self.assertEqual(result.text, print_ast(ast))
            # The mapped text must itself parse back to the identical tree.
            self.assertEqual(parse(result.text), validate_tree(ast))

    def test_one_node_per_ast_node_distinct_identical_subtrees(self):
        # x and x appear twice; f(x,x) repeats identical args.
        tree = call(var("f"),
                    [binop("+", var("x"), var("x")), var("x"), var("x")])
        result = print_ast_mapped(tree)
        self.assertEqual(result.text, "f(x+x,x,x)")
        # 1 call + 1 bin + 5 vars = 7 distinct node occurrences.
        self.assertEqual(len(result.nodes), 7)
        self.assertEqual(len({s.node_id for s in result.nodes}), 7)
        paths = [s.path for s in result.nodes]
        self.assertEqual(len(paths), len(set(paths)))
        # The identical leaves sit at different ranges.
        a = result.span_for("root.args[0].left")
        b = result.span_for("root.args[0].right")
        c = result.span_for("root.args[1]")
        d = result.span_for("root.args[2]")
        self.assertEqual((a.start, a.end), (2, 3))
        self.assertEqual((b.start, b.end), (4, 5))
        self.assertEqual((c.start, c.end), (6, 7))
        self.assertEqual((d.start, d.end), (8, 9))
        # ... and each occurrence owns its own slice of identical text.
        for span in (a, b, c, d):
            self.assertEqual(result.text[span.start:span.end], "x")

    def test_spans_nest_like_hierarchy(self):
        trees = gen_trees(max_nodes=9, max_depth=4)
        uniq = list(dict.fromkeys(trees))
        sample = [t for t in uniq if _size(t) <= 5]
        rest = [t for t in uniq if _size(t) > 5]
        sample.extend(rest[::max(1, len(rest) // 80)])
        for tree in sample:
            ast = from_ref(tree)
            result = print_ast_mapped(ast)
            by_path = {s.path: s for s in result.nodes}

            def walk(node, path):
                span = by_path[path]
                if path != "root":
                    parent_path = _parent_path(path)
                    ps = by_path[parent_path]
                    self.assertGreaterEqual(span.start, ps.core_start)
                    self.assertLessEqual(span.end, ps.core_end)
                if node["kind"] == "neg":
                    walk(node["operand"], f"{path}.operand")
                elif node["kind"] == "bin":
                    walk(node["left"], f"{path}.left")
                    walk(node["right"], f"{path}.right")
                elif node["kind"] == "cond":
                    walk(node["cond"], f"{path}.cond")
                    walk(node["then"], f"{path}.then")
                    walk(node["else"], f"{path}.else")
                elif node["kind"] == "call":
                    walk(node["callee"], f"{path}.callee")
                    for i, arg in enumerate(node["args"]):
                        walk(arg, f"{path}.args[{i}]")
            walk(ast, "root")
            # Every pair of spans is disjoint or nested (tree ordering).
            for i, s1 in enumerate(result.nodes):
                for s2 in result.nodes[i + 1:]:
                    disjoint = s1.end <= s2.start or s2.end <= s1.start
                    nested = (s1.start <= s2.start and s2.end <= s1.end) or (
                        s2.start <= s1.start and s1.end <= s2.end)
                    self.assertTrue(disjoint or nested,
                                    (result.text, s1, s2))

    def test_root_span_is_whole_text(self):
        for src in ("a-(b-c)", "a?b=c:d?e:f", "f(g(),1)", "-(a*b)"):
            result = print_ast_mapped(parse(src))
            root = result.span_for("root")
            self.assertEqual((root.start, root.end), (0, len(result.text)))


class PositionLookupTests(unittest.TestCase):
    def test_node_at_punctuation_belongs_to_parent(self):
        result = print_ast_mapped(parse("a-(b-c)"))
        # '-' of the outer subtraction is owned by the root bin node.
        self.assertEqual(result.node_at(1).path, "root")
        # Added '(' and ')' belong to the node that justified them.
        self.assertEqual(result.node_at(2).path, "root.right")
        self.assertEqual(result.node_at(6).path, "root.right")
        # Inner '-' belongs to the inner bin node.
        self.assertEqual(result.node_at(4).path, "root.right")
        # Leaves resolve to themselves.
        self.assertEqual(result.node_at(0).path, "root.left")
        self.assertEqual(result.node_at(3).path, "root.right.left")
        self.assertEqual(result.node_at(5).path, "root.right.right")

    def test_node_at_every_offset_is_innermost(self):
        tree = parse("(a+b)*(c-d)?f(x):-g")
        result = print_ast_mapped(tree)
        for offset in range(len(result.text)):
            owner = result.node_at(offset)
            containing = [s for s in result.nodes
                          if s.start <= offset < s.end]
            self.assertIn(owner, containing)
            smallest = min(containing, key=lambda s: s.end - s.start)
            self.assertEqual(owner.path, smallest.path)
        with self.assertRaises(IndexError):
            result.node_at(len(result.text))

    def test_call_parens_are_syntax_not_added_pairs(self):
        result = print_ast_mapped(parse("f(x)"))
        # No added pairs: call parens belong to the call node's own span but
        # are not attribution entries.
        self.assertEqual(result.parens, ())
        call_span = result.span_for("root")
        self.assertEqual(call_span.paren_status, "root")
        self.assertEqual(result.node_at(1).path, "root")  # '('
        self.assertEqual(result.node_at(3).path, "root")  # ')'


class ParenAttributionTests(unittest.TestCase):
    def test_structural_pair_attribution(self):
        tree = parse("a-(b-c)")
        result = print_ast_mapped(tree)
        [pair] = result.parens
        self.assertEqual(pair.node_path, "root.right")
        self.assertEqual(result.text[pair.open], "(")
        self.assertEqual(result.text[pair.close], ")")
        self.assertEqual(pair.reasons, (REASON_STRUCTURAL,))
        self.assertEqual(
            result.span_for("root.right").paren_status, STATUS_ADDED)

    def test_omitted_pair_is_reported_and_explained(self):
        # a+b*c: the right child 'b*c' legally rides the '+' slot bare.
        result = print_ast_mapped(parse("a+b*c"))
        right = result.span_for("root.right")
        self.assertEqual(right.paren_status, STATUS_OMITTED)
        self.assertEqual(right.paren_reasons, (REASON_SLOT_TIGHT,))
        self.assertEqual(right.slot, "bin+.right")
        # ... whereas a*(b+c) requires a structural pair.
        result2 = print_ast_mapped(parse("a*(b+c)"))
        pair = result2.span_for("root.right")
        self.assertEqual(pair.paren_status, STATUS_ADDED)
        self.assertEqual(pair.paren_reasons, (REASON_STRUCTURAL,))

    def test_comment_only_pair_attribution(self):
        # (/*L*/a*b)+c: without the lead comment, a*b on the left of '+'
        # needs no parentheses; the pair exists solely because of the
        # comment edge being claimed by the enclosing '+' node.
        tree = binop("+", binop("*", var("a"), var("b"), lead="L"),
                     var("c"))
        result = print_ast_mapped(tree)
        self.assertEqual(result.text, "(/*L*/a*b)+c")
        [pair] = result.parens
        self.assertEqual(pair.node_path, "root.left")
        self.assertEqual(pair.reasons, (REASON_COMMENT_LEAD,))
        span = result.span_for("root.left")
        self.assertEqual(span.paren_status, STATUS_ADDED)
        self.assertEqual(result.text[span.core_start:span.core_end],
                         "/*L*/a*b")
        # The comment text itself still belongs to the comment owner.
        self.assertEqual(result.node_at(span.core_start + 1).path,
                         "root.left")

    def test_structural_and_comment_reasons_both_reported(self):
        # Lead comment on a left operand that is too loose for the slot:
        # the pair is needed both structurally and for the comment.
        tree = binop("*", binop("+", var("a"), var("b"), lead="L"),
                     var("c"))
        result = print_ast_mapped(tree)
        self.assertEqual(result.text, "(/*L*/a+b)*c")
        [pair] = result.parens
        self.assertIn(REASON_STRUCTURAL, pair.reasons)
        self.assertIn(REASON_COMMENT_LEAD, pair.reasons)

    def test_all_added_pairs_have_justification(self):
        trees = gen_trees(max_nodes=9, max_depth=4)
        uniq = list(dict.fromkeys(trees))
        for tree in uniq[::97]:
            result = print_ast_mapped(from_ref(tree))
            added = {s.path for s in result.nodes
                     if s.paren_status == STATUS_ADDED}
            self.assertEqual(added, {p.node_path for p in result.parens})
            for pair in result.parens:
                self.assertTrue(pair.reasons)
                self.assertNotIn(REASON_SLOT_TIGHT, pair.reasons)


def _parent_path(path: str) -> str:
    head, tail = path.rsplit(".", 1)
    return head


if __name__ == "__main__":
    unittest.main()
