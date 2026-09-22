"""demo.py — exprfmt 固定输入演示：最小括号打印、注释保持、实际触发的拒绝。

运行：python demo.py   （确定性、无网络、无 sleep，瞬间完成）
"""

from exprfmt import (
    MAX_NODES, Ident, Int, Neg, Call, Bin, Cond,
    ValidationError, ParseError, print_expr, parse_expr,
)


def show(title, node):
    s = print_expr(node)
    back = parse_expr(s)
    assert back == node, "round-trip failed"
    print("  %-44s -> %s" % (title, s))
    return s


def main():
    print("== 1. 最小括号打印（打印结果均可解析回完全相同的树）==")
    show("a - (b - c)  右嵌套减法必须加括号",
         Bin("-", Ident("a"), Bin("-", Ident("b"), Ident("c"))))
    show("(a - b) - c  左结合无需括号",
         Bin("-", Bin("-", Ident("a"), Ident("b")), Ident("c")))
    show("a * (b + c)  优先级强制括号",
         Bin("*", Ident("a"), Bin("+", Ident("b"), Ident("c"))))
    show("-(a + b) / 负号嵌套 --x",
         Neg(Bin("+", Ident("a"), Ident("b"))))
    show("双重负号",
         Neg(Neg(Ident("x"))))
    show("嵌套条件（右结合）",
         Cond(Ident("a"), Ident("b"),
              Cond(Ident("c"), Ident("d"), Ident("e"))))
    show("条件嵌套在条件位（必须加括号）",
         Cond(Cond(Ident("a"), Ident("b"), Ident("c")),
              Ident("d"), Ident("e")))
    show("赋值右结合 + 条件 + 调用",
         Bin("=", Ident("x"),
             Cond(Ident("y"), Call(Ident("f"), (Ident("z"),)),
                  Bin("=", Ident("w"), Int(0)))))

    print("== 2. 注释绑定节点前/后，打印-解析后位置不迁移 ==")
    t = Bin("+", Ident("a", after=("left operand",)),
            Ident("b", before=("right operand",)),
            before=("whole expr",), after=("done",))
    s = print_expr(t)
    print("  带注释打印: %s" % s)
    back = parse_expr(s)
    assert back == t
    print("  解析校验: before=%s after=%s left.after=%s right.before=%s （与原树一致）"
          % (back.before, back.after, back.left.after, back.right.before))
    t2 = Cond(Ident("flag"), Neg(Ident("x"), after=("negated",)),
              Call(Ident("log",), (Ident("x"),), before=("trace",)))
    s2 = print_expr(t2)
    assert parse_expr(s2) == t2
    print("  带注释打印: %s" % s2)

    print("== 3. 实际触发的拒绝/异常边界 ==")
    try:
        print_expr(Bin("+", Ident("a"), Ident("b"),
                       after=("cannot contain */ here",)))
    except ValidationError as e:
        print("  ValidationError（注释含终止符）: %s" % e)
    try:
        print_expr(Int(-1))
    except ValidationError as e:
        print("  ValidationError（负整数叶）: %s" % e)
    try:
        print_expr(Int(True))
    except ValidationError as e:
        print("  ValidationError（bool 整数）: %s" % e)
    try:
        parse_expr("a +")
    except ParseError as e:
        print("  ParseError（缺操作数）: %s" % e)
    try:
        parse_expr("-" * MAX_NODES + "x")
    except ParseError as e:
        print("  ParseError（超过 %d 节点上限）: %s" % (MAX_NODES, e))

    print("demo OK")


if __name__ == "__main__":
    main()
