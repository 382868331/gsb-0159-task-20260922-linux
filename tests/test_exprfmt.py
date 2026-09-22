"""exprfmt 功能、错误语义与往返一致性测试。"""

import os
import random
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from exprfmt import (
    MAX_NODES, Ident, Int, Neg, Call, Bin, Cond,
    ExprError, ValidationError, ParseError,
    validate, print_expr, parse_expr, count_nodes,
)


def idn(name, **kw):
    return Ident(name, **kw)


class PrintTests(unittest.TestCase):
    """打印结果的精确文本（含最小括号与结合性）。"""

    def p(self, node):
        return print_expr(node)

    def test_sub_right_nested(self):
        t = Bin("-", idn("a"), Bin("-", idn("b"), idn("c")))
        self.assertEqual(self.p(t), "a - (b - c)")

    def test_sub_left_nested_no_parens(self):
        t = Bin("-", Bin("-", idn("a"), idn("b")), idn("c"))
        self.assertEqual(self.p(t), "a - b - c")

    def test_add_right_nested_kept(self):
        t = Bin("+", idn("a"), Bin("+", idn("b"), idn("c")))
        self.assertEqual(self.p(t), "a + (b + c)")

    def test_add_right_sub(self):
        t = Bin("+", idn("a"), Bin("-", idn("b"), idn("c")))
        self.assertEqual(self.p(t), "a + (b - c)")

    def test_mul_over_add(self):
        t = Bin("*", idn("a"), Bin("+", idn("b"), idn("c")))
        self.assertEqual(self.p(t), "a * (b + c)")

    def test_div_right_mul(self):
        t = Bin("/", idn("a"), Bin("*", idn("b"), idn("c")))
        self.assertEqual(self.p(t), "a / (b * c)")

    def test_div_right_div(self):
        t = Bin("/", idn("a"), Bin("/", idn("b"), idn("c")))
        self.assertEqual(self.p(t), "a / (b / c)")

    def test_div_left_chain(self):
        t = Bin("/", Bin("/", idn("a"), idn("b")), idn("c"))
        self.assertEqual(self.p(t), "a / b / c")

    def test_neg_of_add(self):
        self.assertEqual(self.p(Neg(Bin("+", idn("a"), idn("b")))), "-(a + b)")

    def test_double_neg(self):
        self.assertEqual(self.p(Neg(Neg(idn("x")))), "--x")

    def test_neg_as_operands(self):
        self.assertEqual(self.p(Bin("-", idn("a"), Neg(idn("b")))), "a - -b")
        self.assertEqual(self.p(Bin("*", Neg(idn("a")), Neg(idn("b")))), "-a * -b")

    def test_neg_call(self):
        self.assertEqual(self.p(Neg(Call(idn("f"), (idn("x"),)))), "-f(x)")

    def test_assign_right_assoc(self):
        t = Bin("=", idn("x"), Bin("=", idn("y"), Int(1)))
        self.assertEqual(self.p(t), "x = y = 1")

    def test_assign_value_cond(self):
        t = Bin("=", idn("x"), Cond(idn("y"), idn("z"), idn("w")))
        self.assertEqual(self.p(t), "x = y ? z : w")

    def test_cond_right_assoc(self):
        t = Cond(idn("a"), idn("b"), Cond(idn("c"), idn("d"), idn("e")))
        self.assertEqual(self.p(t), "a ? b : c ? d : e")

    def test_cond_left_nested_needs_parens(self):
        t = Cond(Cond(idn("a"), idn("b"), idn("c")), idn("d"), idn("e"))
        self.assertEqual(self.p(t), "(a ? b : c) ? d : e")

    def test_cond_then_nested_no_parens(self):
        t = Cond(idn("a"), Cond(idn("b"), idn("c"), idn("d")), idn("e"))
        self.assertEqual(self.p(t), "a ? b ? c : d : e")

    def test_cond_else_assign_needs_parens(self):
        t = Cond(idn("a"), idn("b"), Bin("=", idn("x"), idn("y")))
        self.assertEqual(self.p(t), "a ? b : (x = y)")

    def test_cond_then_assign_no_parens(self):
        t = Cond(idn("a"), Bin("=", idn("x"), idn("y")), idn("b"))
        self.assertEqual(self.p(t), "a ? x = y : b")

    def test_cond_cond_assign_needs_parens(self):
        t = Cond(Bin("=", idn("x"), idn("y")), idn("z"), idn("w"))
        self.assertEqual(self.p(t), "(x = y) ? z : w")

    def test_call(self):
        t = Call(idn("f"), (Bin("+", idn("a"), idn("b")), Call(idn("g"), ())))
        self.assertEqual(self.p(t), "f(a + b, g())")

    def test_int_leaf(self):
        self.assertEqual(self.p(Bin("+", Int(0), Int(42))), "0 + 42")


class CommentPrintTests(unittest.TestCase):
    """注释的打印位置与为保持绑定而强制加的括号。"""

    def test_bin_comments_force_parens(self):
        t = Bin("+", idn("a"), idn("b"), before=("t",), after=("u",))
        self.assertEqual(print_expr(t), "/* t */ (a + b) /* u */")

    def test_cond_comments_force_parens(self):
        t = Cond(idn("c"), idn("x"), idn("y"), before=("q",))
        self.assertEqual(print_expr(t), "/* q */ (c ? x : y)")

    def test_assign_after_forces_parens(self):
        t = Bin("=", idn("x"), Int(1), after=("done",))
        self.assertEqual(print_expr(t), "(x = 1) /* done */")

    def test_neg_after_forces_parens(self):
        self.assertEqual(print_expr(Neg(idn("x"), after=("n",))), "(-x) /* n */")

    def test_neg_before_no_parens(self):
        self.assertEqual(print_expr(Neg(idn("x"), before=("n",))), "/* n */ -x")

    def test_leaf_comments(self):
        t = Bin("+", Ident("a", after=("c",)), Ident("b", before=("d",)))
        self.assertEqual(print_expr(t), "a /* c */ + /* d */ b")

    def test_call_comments(self):
        t = Call(idn("f"), (idn("x"),), before=("pre",), after=("post",))
        self.assertEqual(print_expr(t), "/* pre */ f(x) /* post */")

    def test_callee_after_comment(self):
        t = Call(Ident("f", after=("c",)), (idn("x"),))
        self.assertEqual(print_expr(t), "f /* c */(x)")

    def test_multiple_comments_order(self):
        t = Ident("a", before=("b1", "b2"), after=("a1",))
        self.assertEqual(print_expr(t), "/* b1 */ /* b2 */ a /* a1 */")


class ParseTests(unittest.TestCase):
    """解析结果与解析-打印往返（往返一致性作为补充校验）。"""

    def test_basic(self):
        self.assertEqual(parse_expr("a - (b - c)"),
                         Bin("-", idn("a"), Bin("-", idn("b"), idn("c"))))

    def test_left_assoc(self):
        self.assertEqual(parse_expr("a - b - c"),
                         Bin("-", Bin("-", idn("a"), idn("b")), idn("c")))

    def test_double_neg(self):
        self.assertEqual(parse_expr("--x"), Neg(Neg(idn("x"))))

    def test_cond_right_assoc(self):
        self.assertEqual(parse_expr("a ? b : c ? d : e"),
                         Cond(idn("a"), idn("b"), Cond(idn("c"), idn("d"), idn("e"))))

    def test_assign_right_assoc(self):
        self.assertEqual(parse_expr("x = y = 1"),
                         Bin("=", idn("x"), Bin("=", idn("y"), Int(1))))

    def test_call_and_leading_zeros(self):
        self.assertEqual(parse_expr("f(007, g())"),
                         Call(idn("f"), (Int(7), Call(idn("g"), ()))))

    def test_paren_is_transparent(self):
        self.assertEqual(parse_expr("((a))"), idn("a"))

    def test_comment_binding(self):
        t = parse_expr("/* k */ (a + b) /* j */")
        self.assertEqual(t, Bin("+", idn("a"), idn("b"),
                                before=("k",), after=("j",)))

    def test_comment_no_space_form(self):
        t = parse_expr("/*k*/x")
        self.assertEqual(t, Ident("x", before=("k",)))

    def test_callee_after_comment(self):
        t = parse_expr("f /* c */ (x)")
        self.assertEqual(t, Call(Ident("f", after=("c",)), (idn("x"),)))

    def test_comment_before_callee_belongs_to_call(self):
        t = parse_expr("/* c */ f(x)")
        self.assertEqual(t, Call(idn("f"), (idn("x"),), before=("c",)))

    def test_whitespace_tolerated(self):
        self.assertEqual(parse_expr("  a\t+\nb "), Bin("+", idn("a"), idn("b")))

    def test_canonical_roundtrip_strings(self):
        samples = [
            "a - (b - c)", "a - b - c", "a + (b + c)", "a * (b + c)",
            "a / (b * c)", "-(a + b)", "--x", "a - -b", "-a * -b",
            "x = y = 1", "x = y ? z : w", "a ? b : c ? d : e",
            "(a ? b : c) ? d : e", "a ? b ? c : d : e", "a ? b : (x = y)",
            "f(a + b, g())", "-f(x)", "/* t */ (a + b) /* u */",
            "(-x) /* n */", "/* n */ -x", "a /* c */ + /* d */ b",
            "/* pre */ f(x) /* post */", "f /* c */(x)",
        ]
        for s in samples:
            with self.subTest(s=s):
                self.assertEqual(print_expr(parse_expr(s)), s)


class CommentMigrationTests(unittest.TestCase):
    """注释不得跨过兄弟节点迁移。"""

    def test_after_comment_stays_on_left_operand(self):
        t = Bin("+", Ident("a", after=("keep",)), idn("b"))
        s = print_expr(t)
        self.assertEqual(s, "a /* keep */ + b")
        back = parse_expr(s)
        self.assertEqual(back, t)
        self.assertEqual(back.left.after, ("keep",))
        self.assertEqual(back.right.after, ())

    def test_after_comment_on_whole_binop_not_on_right_sibling(self):
        t = Bin("+", idn("a"), idn("b"), after=("whole",))
        s = print_expr(t)
        self.assertEqual(s, "(a + b) /* whole */")
        back = parse_expr(s)
        self.assertEqual(back, t)
        self.assertEqual(back.after, ("whole",))
        self.assertEqual(back.right.after, ())

    def test_before_comment_on_whole_not_on_left_sibling(self):
        t = Bin("*", idn("a"), idn("b"), before=("all",))
        back = parse_expr(print_expr(t))
        self.assertEqual(back, t)
        self.assertEqual(back.before, ("all",))
        self.assertEqual(back.left.before, ())

    def test_comments_deep_in_call_args(self):
        t = Call(idn("f"), (Ident("x", before=("p",), after=("q",)),
                            Bin("+", idn("a"), idn("b"), after=("s",))))
        back = parse_expr(print_expr(t))
        self.assertEqual(back, t)


class ValidationTests(unittest.TestCase):
    """不合法 AST 的可定位拒绝。"""

    def assert_invalid(self, node, needle):
        with self.assertRaises(ValidationError) as cm:
            validate(node)
        self.assertIn(needle, str(cm.exception))

    def test_not_a_node(self):
        self.assert_invalid("a + b", "root")

    def test_bad_identifier(self):
        self.assert_invalid(Ident("9lives"), "root.name")
        self.assert_invalid(Ident("héllo"), "root.name")
        self.assert_invalid(Ident(""), "root.name")

    def test_negative_int(self):
        self.assert_invalid(Int(-1), "root.value")

    def test_bool_rejected(self):
        self.assert_invalid(Int(True), "root.value")

    def test_float_nan_inf_rejected(self):
        self.assert_invalid(Int(1.5), "root.value")
        self.assert_invalid(Int(float("nan")), "root.value")
        self.assert_invalid(Int(float("inf")), "root.value")

    def test_unknown_operator(self):
        self.assert_invalid(Bin("%", idn("a"), idn("b")), "root.op")

    def test_assign_target_must_be_ident(self):
        self.assert_invalid(Bin("=", Bin("+", idn("a"), idn("b")), idn("c")),
                            "root.left")

    def test_callee_must_be_ident(self):
        self.assert_invalid(Call(Bin("+", idn("f"), idn("g")), ()), "root.func")

    def test_callee_before_comment_unrepresentable(self):
        self.assert_invalid(Call(Ident("f", before=("c",)), ()), "root.func.before")

    def test_comment_with_newline(self):
        self.assert_invalid(Ident("a", before=("line1\nline2",)), "root.before")

    def test_comment_with_terminator(self):
        self.assert_invalid(Ident("a", after=("evil */",)), "root.after")

    def test_comment_not_str(self):
        self.assert_invalid(Ident("a", before=(1,)), "root.before")

    def test_deep_path_reported(self):
        t = Bin("+", idn("a"), Call(idn("f"), (Int(-3),)))
        self.assert_invalid(t, "root.right.args[0].value")

    def test_node_limit_ok_and_exceeded(self):
        node = idn("x")
        for _ in range(MAX_NODES - 1):
            node = Neg(node)
        self.assertEqual(count_nodes(node), MAX_NODES)
        validate(node)  # 恰好 300：合法
        too_big = Neg(node)
        self.assert_invalid(too_big, "limit")

    def test_unknown_node_type(self):
        class Weird:
            pass
        self.assert_invalid(Weird(), "root")

    def test_print_validates_first(self):
        with self.assertRaises(ValidationError):
            print_expr(Int(-1))


class ParseErrorTests(unittest.TestCase):
    def assert_bad(self, text, needle):
        with self.assertRaises(ParseError) as cm:
            parse_expr(text)
        self.assertIn(needle, str(cm.exception))

    def test_empty(self):
        self.assert_bad("", "expected an expression")

    def test_missing_operand(self):
        self.assert_bad("a +", "expected an expression")

    def test_unclosed_paren(self):
        self.assert_bad("(a", "expected ')'")

    def test_trailing_garbage(self):
        self.assert_bad("a b", "trailing")

    def test_bad_char(self):
        self.assert_bad("a @ b", "unexpected character")

    def test_unterminated_comment(self):
        self.assert_bad("a /* oops", "unterminated comment")

    def test_missing_colon(self):
        self.assert_bad("a ? b", "expected ':'")

    def test_unclosed_call(self):
        self.assert_bad("f(a", "expected ')'")

    def test_assign_target(self):
        self.assert_bad("a + b = c", "target")

    def test_callee_not_ident(self):
        self.assert_bad("(a + b)(x)", "callee")

    def test_newline_in_comment(self):
        self.assert_bad("a /* x\ny */ + b", "newlines")

    def test_node_limit(self):
        ok = "-" * (MAX_NODES - 1) + "x"
        self.assertEqual(count_nodes(parse_expr(ok)), MAX_NODES)
        self.assert_bad("-" * MAX_NODES + "x", "limit")

    def test_error_has_offset(self):
        with self.assertRaises(ParseError) as cm:
            parse_expr("a + )")
        self.assertIsNotNone(cm.exception.pos)
        self.assertIn("offset", str(cm.exception))

    def test_non_str_input(self):
        with self.assertRaises(TypeError):
            parse_expr(123)


class RoundTripPropertyTests(unittest.TestCase):
    """随机树的 print→parse 往返一致性（补充校验，非最优性依据）。"""

    def _gen(self, rng, depth, counter):
        def cmts():
            if rng.random() < 0.25:
                return tuple("note%d" % next(counter)
                             for _ in range(rng.randrange(1, 3)))
            return ()

        if depth <= 0 or rng.random() < 0.3:
            if rng.random() < 0.5:
                return Ident("v%d" % rng.randrange(20),
                             before=cmts(), after=cmts())
            return Int(rng.randrange(1000), before=cmts(), after=cmts())
        kind = rng.choice(("neg", "call", "bin", "cond"))
        if kind == "neg":
            return Neg(self._gen(rng, depth - 1, counter),
                       before=cmts(), after=cmts())
        if kind == "call":
            func = Ident("f%d" % rng.randrange(5), after=cmts())
            args = tuple(self._gen(rng, depth - 1, counter)
                         for _ in range(rng.randrange(3)))
            return Call(func, args, before=cmts(), after=cmts())
        if kind == "bin":
            op = rng.choice(("=", "+", "-", "*", "/"))
            if op == "=":
                left = Ident("v%d" % rng.randrange(20),
                             before=cmts(), after=cmts())
            else:
                left = self._gen(rng, depth - 1, counter)
            return Bin(op, left, self._gen(rng, depth - 1, counter),
                       before=cmts(), after=cmts())
        return Cond(self._gen(rng, depth - 1, counter),
                    self._gen(rng, depth - 1, counter),
                    self._gen(rng, depth - 1, counter),
                    before=cmts(), after=cmts())

    def test_roundtrip(self):
        rng = random.Random(20260922)
        counter = iter(range(100000))
        for i in range(300):
            t = self._gen(rng, 5, counter)
            validate(t)
            s = print_expr(t)
            with self.subTest(i=i, s=s):
                back = parse_expr(s)
                self.assertEqual(back, t)          # 恢复完全相同的树
                self.assertEqual(print_expr(back), s)  # 幂等


if __name__ == "__main__":
    unittest.main()
