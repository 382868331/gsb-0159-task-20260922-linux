#!/usr/bin/env python3
"""Demonstration of the width-aware layout iteration.

Run with ``python demo_iteration.py``.  Everything is computed live
(no pre-recorded output, no sleeps):

1. one fixed expression tree is laid out at a comfortable width and at
   a narrow width, showing flat groups vs. indented expansion;
2. each output is re-parsed and checked to be the *identical* tree;
3. the cost tuple (overflow, newlines, text) is shown for both;
4. a real boundary case: a tiny search budget raises
   ``BudgetExhaustedError`` whose ``best`` attribute still carries a
   legal (not proven optimal) layout, and an out-of-range width is
   rejected with ``ValueError``.
"""

from __future__ import annotations

import time

from minparen import BudgetExhaustedError, format_ast, parse

SOURCE = ("result=compute(alpha+beta*gamma,"
          "flag?adjust(x,y-z):fallback,1000)")


def show(tree, width: int) -> None:
    res = format_ast(tree, width)
    print(f"-- width={width}  cost=(overflow={res.overflow}, "
          f"newlines={res.newlines})  proven_optimal={res.proven_optimal} "
          f"states={res.expanded}")
    print(res.text)
    recovered = parse(res.text)
    assert recovered == tree, "round-trip failed: tree changed!"
    print("   re-parses to the identical tree: OK")
    print()


def main() -> None:
    started = time.perf_counter()
    tree = parse(SOURCE)
    print(f"source tree (minimal-paren flat form):\n  {SOURCE}\n")

    show(tree, 60)
    show(tree, 24)

    print("-- boundary 1: search budget too small to prove optimality")
    try:
        format_ast(tree, 24, budget=4)
    except BudgetExhaustedError as exc:
        best = exc.best
        print(f"   BudgetExhaustedError: {exc}")
        print(f"   best.proven_optimal={best.proven_optimal} "
              f"(legal layout, not proven optimal)")
        assert parse(best.text) == tree
        print(f"   best effort text still re-parses to the same tree: OK")
        print(f"   best effort: {best.text!r}")
    print()

    print("-- boundary 2: width outside the supported 1..80 range")
    try:
        format_ast(tree, 0)
    except ValueError as exc:
        print(f"   ValueError: {exc}")
    print()

    elapsed = time.perf_counter() - started
    print(f"done in {elapsed:.3f}s (live computation, no sleep)")


if __name__ == "__main__":
    main()
