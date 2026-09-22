"""最优性参考测试：枚举所有可选括号位置，独立验证 print_expr 的输出最优。

独立性说明：本文件自带一套独立的词法/语法分析器（_RefParser）与括号
枚举器，期望值完全由“枚举所有候选 → 过滤能还原原树的候选 → 按
(括号数, 长度, 字典序) 取最小”得到，不调用 exprfmt 的 print/parse。
exprfmt.print_expr 仅作为被测对象出现。
"""

import itertools
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from exprfmt import Ident, Int, Neg, Call, Bin, Cond, print_expr


# ---------------------------------------------------------------------------
# 独立参考：元组化语法树 + 独立解析器（与 exprfmt 不共享任何代码）
# ---------------------------------------------------------------------------

def to_tuple(n):
    if isinstance(n, Ident):
        return ("id", n.name)
    if isinstance(n, Int):
        return ("int", n.value)
    if isinstance(n, Neg):
        return ("neg", to_tuple(n.operand))
    if isinstance(n, Call):
        return ("call", n.func.name, tuple(to_tuple(a) for a in n.args))
    if isinstance(n, Bin):
        return ("bin", n.op, to_tuple(n.left), to_tuple(n.right))
    if isinstance(n, Cond):
        return ("cond", to_tuple(n.cond), to_tuple(n.then), to_tuple(n.otherwise))
    raise AssertionError("unknown node")


class RefParseError(Exception):
    pass


_REF_TOKEN = re.compile(
    r"\s*(?:(?P<id>[A-Za-z_][A-Za-z0-9_]*)|(?P<num>[0-9]+)|(?P<op>[()+\-*/,?=:]))")


def _ref_lex(s):
    toks, i = [], 0
    while i < len(s):
        m = _REF_TOKEN.match(s, i)
        if not m:
            raise RefParseError("bad token at %d" % i)
        i = m.end()
        kind = m.lastgroup
        toks.append((kind, m.group(kind)))
    toks.append(("eof", ""))
    return toks


class _RefParser:
    """与 exprfmt 相同语法、独立实现的递归下降解析器（无注释支持）。"""

    def __init__(self, toks):
        self.toks = toks
        self.i = 0

    def peek(self):
        return self.toks[self.i]

    def eat(self):
        t = self.toks[self.i]
        self.i += 1
        return t

    def expect(self, sym):
        if self.peek() != ("op", sym):
            raise RefParseError("expected %s" % sym)
        self.eat()

    def assign(self):
        node = self.cond()
        if self.peek() == ("op", "="):
            self.eat()
            if node[0] != "id":
                raise RefParseError("assignment target must be identifier")
            node = ("bin", "=", node, self.assign())
        return node

    def cond(self):
        node = self.add()
        if self.peek() == ("op", "?"):
            self.eat()
            then = self.assign()
            self.expect(":")
            node = ("cond", node, then, self.cond())
        return node

    def add(self):
        node = self.mul()
        while self.peek() in (("op", "+"), ("op", "-")):
            op = self.eat()[1]
            node = ("bin", op, node, self.mul())
        return node

    def mul(self):
        node = self.unary()
        while self.peek() in (("op", "*"), ("op", "/")):
            op = self.eat()[1]
            node = ("bin", op, node, self.unary())
        return node

    def unary(self):
        if self.peek() == ("op", "-"):
            self.eat()
            return ("neg", self.unary())
        return self.postfix()

    def postfix(self):
        node = self.primary()
        while self.peek() == ("op", "("):
            if node[0] != "id":
                raise RefParseError("callee must be identifier")
            self.eat()
            args = []
            if self.peek() != ("op", ")"):
                args.append(self.assign())
                while self.peek() == ("op", ","):
                    self.eat()
                    args.append(self.assign())
            self.expect(")")
            node = ("call", node[1], tuple(args))
        return node

    def primary(self):
        tok = self.peek()
        if tok[0] == "id":
            self.eat()
            return ("id", tok[1])
        if tok[0] == "num":
            self.eat()
            return ("int", int(tok[1]))
        if tok == ("op", "("):
            self.eat()
            node = self.assign()
            self.expect(")")
            return node
        raise RefParseError("expected expression")


def ref_parse(s):
    parser = _RefParser(_ref_lex(s.strip()))
    node = parser.assign()
    if parser.peek()[0] != "eof":
        raise RefParseError("trailing input")
    return node


# ---------------------------------------------------------------------------
# 独立参考：枚举每个子表达式“加 / 不加”一对括号的所有候选排版
# ---------------------------------------------------------------------------

def _cores(t):
    """生成 t 的所有“不加最外层括号”的排版（子位置已枚举加/不加）。"""
    kind = t[0]
    if kind == "id":
        yield t[1]
    elif kind == "int":
        yield str(t[1])
    elif kind == "neg":
        for e in _variants(t[1]):
            yield "-" + e
    elif kind == "call":
        pools = [list(_variants(a)) for a in t[2]]
        for combo in (itertools.product(*pools) if pools else [()]):
            yield t[1] + "(" + ", ".join(combo) + ")"
    elif kind == "bin":
        for l in _variants(t[2]):
            for r in _variants(t[3]):
                yield "%s %s %s" % (l, t[1], r)
    elif kind == "cond":
        for c in _variants(t[1]):
            for th in _variants(t[2]):
                for el in _variants(t[3]):
                    yield "%s ? %s : %s" % (c, th, el)
    else:
        raise AssertionError("bad tuple")


def _variants(t):
    """t 的所有排版：每个位置可选地被一对括号包住。"""
    for core in _cores(t):
        yield core
        yield "(" + core + ")"


def reference_optimum(t):
    """返回 (括号数, 长度, 字典序) 意义下最优且能还原 t 的字符串。"""
    best = None
    for s in _variants(t):
        try:
            back = ref_parse(s)
        except RefParseError:
            continue
        if back != t:
            continue
        # 同一棵树的调用括号数恒定，直接比较 '(' 总数等价于比较分组括号数
        key = (s.count("("), len(s), s)
        if best is None or key < best[0]:
            best = (key, s)
    if best is None:
        raise AssertionError("no valid rendering found")
    return best[1]


# ---------------------------------------------------------------------------
# 被测：print_expr 必须等于独立枚举得到的最优解
# ---------------------------------------------------------------------------

def _trees():
    a, b, c, d, e = (Ident(n) for n in "abcde")
    x, y, z, w = (Ident(n) for n in "xyzw")
    return {
        # 题面点名：a-(b-c)
        "sub-right": Bin("-", a, Bin("-", b, c)),
        "sub-left-chain": Bin("-", Bin("-", a, b), Bin("-", c, d)),
        "add-right": Bin("+", a, Bin("+", b, c)),
        "mul-add": Bin("*", a, Bin("+", b, c)),
        "div-mul": Bin("/", a, Bin("*", b, c)),
        "div-div": Bin("/", a, Bin("/", b, c)),
        # 负号
        "neg-add": Neg(Bin("+", a, b)),
        "neg-chain-4": Neg(Neg(Neg(Neg(x)))),
        "neg-operands": Bin("*", Neg(a), Neg(Bin("*", b, c))),
        "sub-neg": Bin("-", a, Neg(b)),
        # 嵌套条件
        "cond-right": Cond(a, b, Cond(c, d, e)),
        "cond-left": Cond(Cond(a, b, c), d, e),
        "cond-then-nested": Cond(a, Cond(b, c, d), e),
        "cond-else-assign": Cond(a, b, Bin("=", x, y)),
        "cond-then-assign": Cond(a, Bin("=", x, y), b),
        "cond-cond-assign": Cond(Bin("=", x, y), z, w),
        # 赋值
        "assign-chain": Bin("=", x, Bin("=", y, Bin("=", z, Int(0)))),
        "assign-cond": Bin("=", x, Cond(y, z, w)),
        # 深度 4 混合树
        "depth4-mix": Cond(
            Bin("+", a, b),
            Bin("*", c, Neg(d)),
            Bin("=", x, Call(Ident("f"), (Neg(y),)))),
        "depth4-call": Call(Ident("f"),
                            (Bin("-", a, Bin("-", b, c)),
                             Call(Ident("g"), (Bin("+", d, e),)))),
        "depth4-cond-neg": Cond(x, Neg(y), Cond(z, Bin("+", a, b), w)),
        "depth4-assign": Bin("=", x, Cond(y, z, Bin("=", w, Call(Ident("f"), ())))),
        "int-leaves": Bin("+", Int(0), Bin("*", Int(7), Neg(Int(42)))),
    }


class OptimalReferenceTests(unittest.TestCase):
    def test_print_matches_enumerated_optimum(self):
        for name, tree in _trees().items():
            with self.subTest(tree=name):
                t = to_tuple(tree)
                expected = reference_optimum(t)
                got = print_expr(tree)
                # 独立解析器确认被测输出本身能还原原树
                self.assertEqual(ref_parse(got), t)
                self.assertEqual(got, expected)

    def test_reference_sanity_known_optima(self):
        # 参考实现自身的健全性：几个手算最优解
        a, b, c = (Ident(n) for n in "abc")
        cases = {
            Bin("-", a, Bin("-", b, c)): "a - (b - c)",
            Bin("-", Bin("-", a, b), c): "a - b - c",
            Cond(a, b, Cond(c, Ident("d"), Ident("e"))): "a ? b : c ? d : e",
            Neg(Bin("+", a, b)): "-(a + b)",
        }
        for tree, expected in cases.items():
            with self.subTest(expected=expected):
                self.assertEqual(reference_optimum(to_tuple(tree)), expected)


if __name__ == "__main__":
    unittest.main()
