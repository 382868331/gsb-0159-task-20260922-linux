#!/usr/bin/env python3
"""Demonstration of the width-aware layout iteration (format_ast).

Run with: python demo_iteration.py

Everything below is computed live (no pre-recorded output, no sleep):

1. one expression tree rendered at two different narrow widths, showing
   how calls, binary expressions and conditionals lay out flat or break
   as a group; each output is re-parsed and checked to be the identical
   tree (parentheses come from syntax, never from layout);
2. a boundary case: a layout-search budget that is too small, where the
   library raises BudgetExhaustedError but still hands back a legal
   (unproven) fallback layout -- distinct from a proven optimum;
3. a second boundary case: a single unbreakable word wider than the
   column width, which overflows but is preserved and counted.
"""

from __future__ import annotations

import time

from minparen import BudgetExhaustedError, format_ast, parse


def overflow_of(text: str, width: int) -> int:
    return sum(max(0, len(line) - width) for line in text.split("\n"))


def show(tree: dict, width: int) -> str:
    out = format_ast(tree, width)
    reparsed = parse(out)
    assert reparsed == tree, "layout must not change the tree"
    overflow = overflow_of(out, width)
    newlines = out.count("\n")
    print(f"--- width {width} "
          f"(overflow={overflow} char(s), newlines={newlines}) ---")
    print(out)
    print(f"    re-parses to the identical tree: {reparsed == tree}")
    print()
    return out


def main() -> None:
    t0 = time.perf_counter()

    source = "result=f(alpha+beta, gamma?delta:epsilon, g(x1, x2)*(y-z))"
    tree = parse(source)
    print("=== 1. One tree, two narrow widths (real computation) ===")
    print(f"tree: {source}")
    print()
    narrow = show(tree, 30)
    narrower = show(tree, 14)
    assert narrow != narrower, "different widths should choose layouts"

    print("=== 2. Boundary: budget exhausted -> legal but unproven layout ===")
    try:
        format_ast(tree, 14, budget=2)
    except BudgetExhaustedError as exc:
        print(f"- BudgetExhaustedError caught (as designed): {exc}")
        fallback = exc.best_effort
        assert parse(fallback) == tree
        print(f"- best_effort is still a legal layout "
              f"(re-parses to the same tree): {fallback!r}")
        print("- but its optimality is unproven, unlike the certified "
              "result above")
    else:  # pragma: no cover
        raise SystemExit("expected BudgetExhaustedError was not raised")
    print()

    print("=== 3. Boundary: unbreakable word wider than the column ===")
    word_tree = parse("call_a_very_long_function_name(another_long_argument)")
    out = show(word_tree, 10)
    assert "call_a_very_long_function_name" in out.replace("\n", "")
    print("Long tokens overflow (counted in the penalty) but are never "
          "split.")

    print(f"All checks performed in {time.perf_counter() - t0:.2f}s.")


if __name__ == "__main__":
    main()
