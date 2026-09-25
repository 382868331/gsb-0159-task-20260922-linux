"""Tests for minparen.mapped: position-mapped printing and offset queries.

The core-span checks parse each reported core substring with the
*independent* parser and compare against the comment-free subtree -- the
expected value never comes from the mapping itself. Sibling boundaries are
verified character by character. Positions are never derived by searching
the output for equal substrings; identical subtrees are checked to map to
distinct, individually correct spans.
"""

from __future__ import annotations

import unittest

from minparen import ASTError, parse, print_ast, print_ast_mapped
from minparen.ast_nodes import binop, call, cond, neg, num, var
from minparen.mapped import MappedPrint, NodeSpan

from test_minparen import (
    COMMENT_PRINT_CASES, STRUCTURAL_CASES, from_ref, gen_trees,
)


def bare(node: dict) -> dict:
    """Comment-free deep copy of a node (the shape parse() returns)."""
    out = {k: v for k, v in node.items() if k not in ("lead", "trail")}
    for key in ("operand", "left", "right", "cond", "then", "else",
                "callee"):
        if key in out:
            out[key] = bare(out[key])
    if "args" in out:
        out["args"] = [bare(a) for a in out["args"]]
    return out


def children_of(node: dict) -> list[dict]:
    kind = node["kind"]
    if kind == "neg":
        return [node["operand"]]
    if kind == "bin":
        return [node["left"], node["right"]]
    if kind == "cond":
        return [node["cond"], node["then"], node["else"]]
    if kind == "call":
        return [node["callee"], *node["args"]]
    return []


# Required feature mix: conditional expressions, right-associative
# assignment, consecutive unary minus, and non-ASCII comments.
FEATURE_TREES = [
    cond(var("a"), binop("=", var("b"), var("c")),
         cond(var("d"), var("e"), var("f"))),          # nested ternary
    binop("=", var("x"), binop("=", var("y"),
          binop("=", var("z"), num(7)))),              # a=b=c chain
    neg(neg(neg(var("a")))),                           # ---a
    binop("-", neg(var("a")), neg(neg(var("b")))),     # -a---b
    cond(binop("=", var("a"), num(1)),
         neg(binop("+", var("b"), var("c"))),
         call(var("f"), [var("g", lead="前"),
                         num(2, trail="尾α")])),
    binop("+", var("n", lead="注释", trail="結語"),
          var("m", trail="β")),
    call(call(var("h"), []), [binop("=", var("a"), var("b")),
                              cond(var("c"), num(1), num(2))]),
    binop("*", binop("+", var("a"), var("b")),
          binop("+", var("a"), var("b"))),             # identical subtrees
]


class TextIdentityTests(unittest.TestCase):
    def test_mapped_text_equals_print_ast(self):
        for source, _ in STRUCTURAL_CASES:
            with self.subTest(source=source):
                tree = parse(source)
                self.assertEqual(print_ast_mapped(tree).text,
                                 print_ast(tree))
        for tree, _ in COMMENT_PRINT_CASES:
            with self.subTest(tree=tree):
                self.assertEqual(print_ast_mapped(tree).text,
                                 print_ast(tree))
        for tree in FEATURE_TREES:
            self.assertEqual(print_ast_mapped(tree).text, print_ast(tree))

    def test_mapped_text_equals_print_ast_on_enumeration_sample(self):
        for tree in gen_trees(max_nodes=8, max_depth=4)[::53]:
            core_tree = from_ref(tree)
            self.assertEqual(print_ast_mapped(core_tree).text,
                             print_ast(core_tree))

    def test_invalid_ast_still_rejected(self):
        with self.assertRaises(ASTError):
            print_ast_mapped({"kind": "num", "value": True})
        with self.assertRaises(ASTError):
            print_ast_mapped(var("a", lead="bad */ terminator"))


class CoreSpanTests(unittest.TestCase):
    def check_tree(self, tree):
        mp = print_ast_mapped(tree)
        self.assertGreater(len(mp.nodes), 0)
        for ns in mp.nodes:
            with self.subTest(path=ns.path, text=mp.text):
                core_text = mp.text[ns.core[0]:ns.core[1]]
                # Independent check: the core substring parses back to the
                # node's subtree. Descendants' comments may sit inside the
                # core range, so both sides are compared comment-free.
                self.assertEqual(bare(parse(core_text)), bare(ns.node))
                # The node's own comments stay outside its core span.
                for cspan in (ns.lead, ns.trail):
                    if cspan is not None:
                        self.assertTrue(
                            cspan[1] <= ns.core[0]
                            or cspan[0] >= ns.core[1])
        return mp

    def test_core_spans_parse_to_subtrees(self):
        for tree in FEATURE_TREES:
            self.check_tree(tree)
        for source, _ in STRUCTURAL_CASES:
            self.check_tree(parse(source))
        for tree, _ in COMMENT_PRINT_CASES:
            self.check_tree(tree)

    def test_parent_span_contains_child_spans(self):
        for tree in FEATURE_TREES:
            mp = print_ast_mapped(tree)
            for ns in mp.nodes:
                for child in children_of(ns.node):
                    # Locate the child's span via the mapping's own paths.
                    cns = self.child_span(mp, ns, child)
                    self.assertLessEqual(ns.span[0], cns.span[0])
                    self.assertGreaterEqual(ns.span[1], cns.span[1])

    def child_span(self, mp: MappedPrint, parent: NodeSpan,
                   child: dict) -> NodeSpan:
        for ns in mp.nodes:
            if ns.node is child:
                return ns
        self.fail(f"child of {parent.path} not found in mapping")

    def test_auto_parens_belong_to_the_node(self):
        mp = print_ast_mapped(parse("a-(b-c)"))
        right = mp.node_at("root.right")
        self.assertEqual(mp.text, "a-(b-c)")
        self.assertEqual(right.parens, ((2, 3), (6, 7)))
        self.assertEqual(right.span, (2, 7))
        self.assertEqual(right.core, (3, 6))
        self.assertEqual(mp.text[right.core[0]:right.core[1]], "b-c")
        # The root owns no parens and its span covers the child's.
        root = mp.node_at("root")
        self.assertIsNone(root.parens)
        self.assertEqual(root.span, (0, 7))

    def test_comment_spans_and_core_exclusion(self):
        tree = binop("-", var("a"),
                     binop("-", var("b"), var("c"),
                           lead="inner", trail="end"))
        mp = print_ast_mapped(tree)
        self.assertEqual(mp.text, "a-(/*inner*/b-c/*end*/)")
        right = mp.node_at("root.right")
        self.assertEqual(mp.text[right.lead[0]:right.lead[1]], "/*inner*/")
        self.assertEqual(mp.text[right.trail[0]:right.trail[1]], "/*end*/")
        self.assertEqual(mp.text[right.core[0]:right.core[1]], "b-c")
        # Comments are mapped in the comment list, on the right side.
        sides = {(c.path, c.side) for c in mp.comments}
        self.assertEqual(sides, {("root.right", "lead"),
                                 ("root.right", "trail")})
        for c in mp.comments:
            self.assertEqual(mp.text[c.span[0]:c.span[1]], f"/*{c.text}*/")

    def test_non_ascii_comment_offsets_are_character_offsets(self):
        tree = binop("+", var("n", lead="注释", trail="結語"),
                     var("m", trail="β"))
        mp = print_ast_mapped(tree)
        # Both children carry comments on edges the parent claims, so each
        # gets its own auto-added pair around the commented node.
        self.assertEqual(mp.text, "(/*注释*/n/*結語*/)+(m/*β*/)")
        left = mp.node_at("root.left")
        self.assertEqual(left.lead, (1, 7))     # "/*注释*/" is 6 chars
        self.assertEqual(left.core, (7, 8))
        self.assertEqual(left.trail, (8, 14))
        self.assertEqual(left.parens, ((0, 1), (14, 15)))
        right = mp.node_at("root.right")
        self.assertEqual(mp.text[right.trail[0]:right.trail[1]], "/*β*/")
        # Whole text still round-trips through the independent parser.
        self.assertEqual(parse(mp.text), mp.tree)

    def test_identical_subtrees_get_distinct_spans(self):
        tree = binop("*", binop("+", var("a"), var("b")),
                     binop("+", var("a"), var("b")))
        mp = print_ast_mapped(tree)
        self.assertEqual(mp.text, "(a+b)*(a+b)")
        left, right = mp.node_at("root.left"), mp.node_at("root.right")
        self.assertNotEqual(left.span, right.span)
        self.assertLessEqual(left.span[1], right.span[0])
        # Each core parses to the same subtree, but the spans are the
        # mapping's own -- not found by searching for "a+b".
        for ns in (left, right):
            self.assertEqual(parse(mp.text[ns.core[0]:ns.core[1]]),
                             bare(left.node))
        self.assertEqual(left.core, (1, 4))
        self.assertEqual(right.core, (7, 10))


class SiblingBoundaryTests(unittest.TestCase):
    """Char-by-char composition checks between adjacent siblings."""

    def check_tree(self, tree):
        mp = print_ast_mapped(tree)
        text = mp.text
        ops_by_node: dict[str, list] = {}
        for o in mp.operators:
            ops_by_node.setdefault(o.path, []).append(o)
        for ns in mp.nodes:
            node, core = ns.node, ns.core
            kind = ns.kind
            if kind == "bin":
                left = mp.node_at(ns.path + ".left")
                right = mp.node_at(ns.path + ".right")
                (op,) = ops_by_node[ns.path]
                self.assertEqual(core[0], left.span[0])
                self.assertEqual(core[1], right.span[1])
                self.assertEqual(op.span, (left.span[1], right.span[0]))
                for i in range(left.span[1], right.span[0]):
                    self.assertEqual(text[i], node["op"])
            elif kind == "cond":
                c = mp.node_at(ns.path + ".cond")
                t = mp.node_at(ns.path + ".then")
                e = mp.node_at(ns.path + ".else")
                q, colon = ops_by_node[ns.path]
                self.assertEqual((q.op, colon.op), ("?", ":"))
                self.assertEqual(core[0], c.span[0])
                self.assertEqual(core[1], e.span[1])
                self.assertEqual(q.span, (c.span[1], t.span[0]))
                self.assertEqual(colon.span, (t.span[1], e.span[0]))
                self.assertEqual(text[q.span[0]:q.span[1]], "?")
                self.assertEqual(text[colon.span[0]:colon.span[1]], ":")
            elif kind == "neg":
                operand = mp.node_at(ns.path + ".operand")
                (minus,) = ops_by_node[ns.path]
                self.assertTrue(minus.unary)
                self.assertEqual(minus.span, (core[0], core[0] + 1))
                self.assertEqual(operand.span, (core[0] + 1, core[1]))
            elif kind == "call":
                callee = mp.node_at(ns.path + ".callee")
                self.assertEqual(core[0], callee.span[0])
                self.assertEqual(text[callee.span[1]], "(")
                pos = callee.span[1] + 1
                for i in range(len(node["args"])):
                    arg = mp.node_at(f"{ns.path}.args[{i}]")
                    self.assertEqual(arg.span[0], pos)
                    pos = arg.span[1]
                    if i + 1 < len(node["args"]):
                        self.assertEqual(text[pos], ",")
                        pos += 1
                self.assertEqual(text[pos], ")")
                self.assertEqual(core[1], pos + 1)
        return mp

    def test_sibling_boundaries(self):
        for tree in FEATURE_TREES:
            self.check_tree(tree)
        for source, _ in STRUCTURAL_CASES:
            self.check_tree(parse(source))
        for tree, _ in COMMENT_PRINT_CASES:
            self.check_tree(tree)

    def test_every_offset_accounted_for(self):
        # Sweep every offset of every feature tree: the query category must
        # agree with raw span membership, and "core" hits must be innermost.
        for tree in FEATURE_TREES:
            mp = self.check_tree(tree)
            for i in range(len(mp.text)):
                r = mp.query(i)
                in_comment = any(c.span[0] <= i < c.span[1]
                                 for c in mp.comments)
                in_op = any(o.span[0] <= i < o.span[1] for o in mp.operators)
                in_paren = any(
                    n.parens and (n.parens[0][0] <= i < n.parens[0][1]
                                  or n.parens[1][0] <= i < n.parens[1][1])
                    for n in mp.nodes)
                if in_comment:
                    self.assertEqual(r.category, "comment")
                elif in_op:
                    self.assertEqual(r.category, "operator")
                elif in_paren:
                    self.assertEqual(r.category, "paren")
                else:
                    self.assertEqual(r.category, "core")
                    self.assertIsNotNone(r.node)
                    self.assertLessEqual(r.node.core[0], i)
                    self.assertGreater(r.node.core[1], i)
                    for n in mp.nodes:  # innermost: no smaller core fits
                        if n.core[0] <= i < n.core[1]:
                            self.assertGreaterEqual(
                                n.core[1] - n.core[0],
                                r.node.core[1] - r.node.core[0])


class QueryTests(unittest.TestCase):
    def test_half_open_boundaries(self):
        mp = print_ast_mapped(parse("a+b*c"))
        self.assertEqual(mp.text, "a+b*c")
        # Offset 1 is the '+'; offset 2 is where 'b' starts (half-open).
        self.assertEqual(mp.query(1).category, "operator")
        r = mp.query(2)
        self.assertEqual((r.category, r.node.path),
                         ("core", "root.right.left"))
        self.assertEqual(mp.query(0).node.path, "root.left")
        # '*' belongs to the right child; offset 4 is 'c'.
        r = mp.query(3)
        self.assertEqual((r.category, r.operator.op), ("operator", "*"))
        self.assertEqual(r.node.path, "root.right")
        self.assertEqual(mp.query(4).node.path, "root.right.right")

    def test_operator_categories_on_required_constructs(self):
        mp = print_ast_mapped(parse("---a"))
        for i in range(3):
            r = mp.query(i)
            self.assertEqual((r.category, r.operator.op, r.operator.unary),
                             ("operator", "-", True))
        mp = print_ast_mapped(parse("a=b=c"))
        eqs = [o for o in mp.operators if o.op == "="]
        self.assertEqual([o.span for o in eqs], [(1, 2), (3, 4)])
        self.assertEqual([o.path for o in eqs], ["root", "root.right"])
        mp = print_ast_mapped(parse("a?b:c?d:e"))
        q_marks = [o.span for o in mp.operators if o.op == "?"]
        colons = [o.span for o in mp.operators if o.op == ":"]
        self.assertEqual(q_marks, [(1, 2), (5, 6)])
        self.assertEqual(colons, [(3, 4), (7, 8)])
        r = mp.query(4)
        self.assertEqual((r.category, r.node.path),
                         ("core", "root.else.cond"))

    def test_comment_and_paren_categories_not_attributed_to_neighbors(self):
        tree = binop("-", var("a"),
                     binop("-", var("b"), var("c"),
                           lead="inner", trail="end"))
        mp = print_ast_mapped(tree)
        # "a-(/*inner*/b-c/*end*/)"
        self.assertEqual(mp.query(2).category, "paren")    # '('
        self.assertEqual(mp.query(2).node.path, "root.right")
        for i in range(3, 12):                             # /*inner*/
            r = mp.query(i)
            self.assertEqual(r.category, "comment")
            self.assertEqual((r.comment.path, r.comment.side),
                             ("root.right", "lead"))
        self.assertEqual(mp.query(12).category, "core")    # 'b'
        for i in range(15, 22):                            # /*end*/
            self.assertEqual(mp.query(i).category, "comment")
        self.assertEqual(mp.query(22).category, "paren")   # ')'

    def test_whitespace_category(self):
        # The printer never emits whitespace outside comments; build a
        # hand-made map to pin the query contract for that case.
        node = {"kind": "bin", "op": "+", "left": var("a"),
                "right": var("b")}
        ns = NodeSpan(path="root", kind="bin", node=node, core=(0, 5),
                      span=(0, 5), lead=None, trail=None, parens=None)
        mp = MappedPrint(text="a + b", tree=node, nodes=(ns,),
                         operators=(), comments=())
        r = mp.query(1)
        self.assertEqual(r.category, "whitespace")
        self.assertIsNone(r.node)

    def test_query_rejects_bad_offsets(self):
        mp = print_ast_mapped(parse("a+b"))
        for bad in (-1, 3, 100):
            with self.assertRaises(ValueError):
                mp.query(bad)
        with self.assertRaises(TypeError):
            mp.query(True)
        with self.assertRaises(TypeError):
            mp.query(1.0)

    def test_resolve_walks_paths(self):
        mp = print_ast_mapped(parse("f(a,b=c)"))
        self.assertIs(mp.resolve("root"), mp.tree)
        self.assertEqual(mp.resolve("root.args[1].right")["name"], "c")
        self.assertIs(mp.resolve("root.args[0]"),
                      mp.node_at("root.args[0]").node)
        with self.assertRaises(ValueError):
            mp.resolve("root..x")


if __name__ == "__main__":
    unittest.main(verbosity=2)
