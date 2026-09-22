"""Public error types for minparen.

All errors raised by the package derive from :class:`MinParenError` so callers
can catch the whole family with one ``except`` clause.
"""

from __future__ import annotations


class MinParenError(Exception):
    """Base class for every error raised by :mod:`minparen`."""


class ParseError(MinParenError):
    """The source text is not a valid expression.

    Attributes locate the offending position in the source (0-based offset and
    1-based line/column).
    """

    def __init__(self, message: str, offset: int, line: int, column: int):
        self.message = message
        self.offset = offset
        self.line = line
        self.column = column
        super().__init__(f"{message} (line {line}, column {column}, offset {offset})")


class ASTError(MinParenError):
    """An AST object is malformed or outside the supported contract.

    ``path`` is a deterministic accessor path from the root, e.g.
    ``root.right.args[1]``.
    """

    def __init__(self, message: str, path: str = "root"):
        self.message = message
        self.path = path
        super().__init__(f"{message} (at {path})")


class BudgetExhaustedError(MinParenError):
    """A bounded search stopped because its candidate budget ran out.

    This is distinct from :class:`NoSolutionError`: the answer is *unknown*,
    not proven absent.
    """


class NoSolutionError(MinParenError):
    """A search examined every candidate and proved that none works."""


__all__ = [
    "MinParenError",
    "ParseError",
    "ASTError",
    "BudgetExhaustedError",
    "NoSolutionError",
]
