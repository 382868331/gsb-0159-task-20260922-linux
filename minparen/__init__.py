"""minparen -- minimal-parenthesis printing for a small expression language.

Public API:

* :func:`parse` -- source text -> validated AST dict
* :func:`print_ast` -- validated AST dict -> minimal string
* :func:`print_ast_mapped` -- same text plus a per-node source map
* :func:`validate_tree` -- strict AST validation, returns a canonical copy
* :class:`~minparen.errors.ParseError`, :class:`~minparen.errors.ASTError`,
  :class:`~minparen.errors.BudgetExhaustedError`,
  :class:`~minparen.errors.NoSolutionError`
* node constructors :func:`~minparen.ast_nodes.num`, :func:`var`,
  :func:`neg`, :func:`binop`, :func:`cond`, :func:`call`
"""

from .errors import (
    ASTError,
    BudgetExhaustedError,
    MinParenError,
    NoSolutionError,
    ParseError,
)
from .parser import parse
from .printer import NodeSpan, PrintResult, print_ast, print_ast_mapped
from .validation import MAX_NODES, validate_tree

__version__ = "1.0.0"

__all__ = [
    "parse",
    "print_ast",
    "print_ast_mapped",
    "PrintResult",
    "NodeSpan",
    "validate_tree",
    "MAX_NODES",
    "MinParenError",
    "ParseError",
    "ASTError",
    "BudgetExhaustedError",
    "NoSolutionError",
    "__version__",
]
