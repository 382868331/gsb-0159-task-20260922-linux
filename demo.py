#!/usr/bin/env python3
"""Fixed-input demonstration for the minparen library.

Run with ``python demo.py``. It performs real (never pre-recorded) work:

1. builds several expression ASTs, prints them with minimal parentheses,
   re-parses the output and verifies the identical tree comes back,
   including a nested-ternary and unary-minus case and comment binding;
2. exercises two real rejection boundaries:
   - an AST with a comment containing the ``*/`` terminator (ASTError);
   - source text with a comment in an unbindable gap, ``f( /*x*/ )``
     (ParseError with line/column/offset).

All inputs are deterministic literals in this file; no network, no sleep.
"""

from __future__ import annotations

import time

from minparen import (
    ASTError, ParseError, PathError, parse, print_ast, replace_subtree,
)
from minparen.ast_nodes import binop, call, cond, neg, num, var
from reference import refenum


def has_comments(node) -> bool:
    if node.get("lead") is not None or node.get("trail") is not None:
        return True
    return any(has_comments(ch) for ch in _children(node))


def _children(node):
    k = node["kind"]
    if k == "neg":
        return [node["operand"]]
    if k == "bin":
        return [node["left"], node["right"]]
    if k == "cond":
        return [node["cond"], node["then"], node["else"]]
    if k == "call":
        return [node["callee"], *node["args"]]
    return []


def to_ref(node):
    k = node["kind"]
    if k == "num":
        return ("n", node["value"])
    if k == "var":
        return ("v", node["name"])
    if k == "neg":
        return ("!", to_ref(node["operand"]))
    if k == "bin":
        return ("b", node["op"], to_ref(node["left"]),
                to_ref(node["right"]))
    if k == "cond":
        return ("?", to_ref(node["cond"]), to_ref(node["then"]),
                to_ref(node["else"]))
    return ("f", to_ref(node["callee"]),
            tuple(to_ref(a) for a in node["args"]))


def show(title: str, tree: dict) -> tuple[str, str]:
    minimal = print_ast(tree)
    reparsed = parse(minimal)
    reprinted = print_ast(reparsed)
    if has_comments(tree):
        cert = "round-trip only (enum reference is comment-free)"
    else:
        # Independent certificate: exhaustive enumeration on this small tree.
        proven, pairs = refenum.best_wrapping(to_ref(tree))
        cert = ("OK" if proven == minimal
                else f"MISMATCH ref={proven!r}")
        cert = f"{pairs} added pair(s), certificate: {cert}"
    print(f"- {title}")
    print(f"    minimal : {minimal}")
    print(f"    re-print: {reprinted}  (tree identical: {reparsed == tree})")
    print(f"    enum    : {cert}")
    assert reprinted == minimal
    assert reparsed == tree
    return minimal, reprinted


def main() -> None:
    t0 = time.perf_counter()

    print("=== 1. Minimal-parenthesis rendering (real computation) ===")
    # Required cases: a-(b-c), nested ternary, unary minus.
    show("left-associative subtraction: a - (b - c)",
         binop("-", var("a"), binop("-", var("b"), var("c"))))
    show("nested ternary: a ? b : (c ? d : e)",
         cond(var("a"), var("b"),
              cond(var("c"), var("d"), var("e"))))
    show("nested ternary the other way: a ? (b ? c : d) : e",
         cond(var("a"), cond(var("b"), var("c"), var("d")), var("e")))
    show("unary minus chains and addition: -(a+b) * --c",
         binop("*", neg(binop("+", var("a"), var("b"))),
               neg(neg(var("c")))))
    show("calls: f(g(1), h()())",
         call(var("f"),
              [call(var("g"), [num(1)]),
               call(call(var("h"), []), [])]))
    show("assignment inside ternary branches",
         cond(var("a"),
              binop("=", var("b"), var("c")),
              binop("=", var("d"), var("e"))))

    print()
    print("=== 2. Comments stay bound to their node ===")
    tree = binop("-", var("a"),
                 binop("-", var("b"), var("c"), lead="inner", trail="end"))
    text, _ = show("lead+trail on a right child that needs parens anyway",
                   tree)
    assert text == "a-(/*inner*/b-c/*end*/)"
    tree2 = call(var("f", trail="callee"),
                 [binop("+", var("x"), var("y"), lead="sum")])
    text2, _ = show("trail on callee; lead on an argument", tree2)
    assert text2 == "f/*callee*/(/*sum*/x+y)"

    print()
    print("=== 3. Rejection boundary that actually fires ===")
    # 3a. Unrepresentable comment terminator in an AST.
    bad_ast = var("x", lead="oops */ */")
    try:
        print_ast(bad_ast)
    except ASTError as exc:
        print(f"- ASTError caught (as designed): {exc}")
    else:  # pragma: no cover
        raise SystemExit("expected ASTError was not raised")

    # 3b. Comment in an empty argument list binds to no node.
    try:
        parse("f(/*orphan*/)")
    except ParseError as exc:
        print(f"- ParseError caught (as designed): {exc}")
        print(f"    located at offset={exc.offset}, "
              f"line={exc.line}, column={exc.column}")
    else:  # pragma: no cover
        raise SystemExit("expected ParseError was not raised")

    # 3c. Budget exhaustion vs. proven impossibility, on the reference.
    from minparen import BudgetExhaustedError, NoSolutionError
    sample_tree = refenum.ref_parse("a-(b-c)-(x-y)")
    try:
        refenum.best_wrapping(sample_tree, budget=2)
    except BudgetExhaustedError as exc:
        print(f"- BudgetExhaustedError (unknown, stopped early): {exc}")
    try:
        refenum.best_wrapping(sample_tree, accept=lambda s: "ZZZ" in s)
    except NoSolutionError as exc:
        print(f"- NoSolutionError (proved impossible): {exc}")

    print()
    print("=== 4. Subtree replacement as a single edit span (UTF-8 bytes) ===")

    def swap(title, source, path, replacement):
        edit = replace_subtree(source, path, replacement)
        applied = edit.apply(source)
        # Real check: the edited text parses to the original tree with the
        # subtree swapped, and everything outside the span is byte-identical.
        original, edited = source.encode("utf-8"), applied.encode("utf-8")
        assert edited[:edit.start] == original[:edit.start]
        assert edited[len(edited) - (len(original) - edit.end):] == \
            original[edit.end:]
        print(f"- {title}")
        print(f"    source : {source}")
        print(f"    edit   : bytes [{edit.start}, {edit.end}) "
              f"-> {edit.replacement!r}")
        print(f"    result : {applied}")
        return applied

    # 4a. Boundary parens added by precedence: right of '-' is strict.
    out = swap("replace b-c with x+y (parens required)",
               "a-(b-c)", "root.right", binop("+", var("x"), var("y")))
    assert out == "a-(x+y)"
    assert parse(out) == parse("a-(x+y)")

    # 4b. Nested redundant parens leave with the replaced subtree.
    out = swap("replace b+c inside doubled parens",
               "a*((b+c))", "root.right", binop("-", var("x"), var("y")))
    assert out == "a*(x-y)"

    # 4c. Untouched whitespace, a multibyte comment on the left sibling and
    # the line-end comment stay byte-for-byte; spans are UTF-8 byte offsets.
    source = "a /*保留*/ +  b /*tail*/"
    out = swap("replace b; multibyte + trailing comments untouched",
               source, "root.right", num(3))
    assert out == "a /*保留*/ +  3 /*tail*/"

    # 4d. A comment owned by the replaced node leaves with it.
    out = swap("replace the node owning /*gone*/",
               "a+/*gone*/b", "root.right", var("x"))
    assert out == "a+x"

    print()
    print("=== 5. Replacement-time rejection that actually fires ===")
    # 5a. A path that does not resolve against the parsed tree.
    try:
        replace_subtree("a+b", "root.left.right", var("x"))
    except PathError as exc:
        print(f"- PathError caught (as designed): {exc}")
    else:  # pragma: no cover
        raise SystemExit("expected PathError was not raised")

    # 5b. An invalid replacement AST; nothing is applied or half-updated.
    try:
        replace_subtree("a+b", "root.right", {"kind": "num", "value": -1})
    except ASTError as exc:
        print(f"- ASTError caught (as designed): {exc}")
    else:  # pragma: no cover
        raise SystemExit("expected ASTError was not raised")

    print()
    print(f"All checks performed in {time.perf_counter() - t0:.2f}s.")


if __name__ == "__main__":
    main()
