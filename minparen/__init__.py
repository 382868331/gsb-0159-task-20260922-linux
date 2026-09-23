"""minparen -- minimal-parenthesis printing for a small expression language.

Public API:

* :func:`parse` -- source text -> validated AST dict
* :func:`print_ast` -- validated AST dict -> minimal string
* :func:`validate_tree` -- strict AST validation, returns a canonical copy
* :func:`replace_subtree` -- splice one replacement AST into parsed source,
  returning the single UTF-8 edit interval
* :class:`~minparen.rewrite.SubtreeEdit` -- the edit descriptor
* :class:`~minparen.errors.ParseError`, :class:`~minparen.errors.ASTError`,
  :class:`~minparen.errors.BudgetExhaustedError`,
  :class:`~minparen.errors.NoSolutionError`,
  :class:`~minparen.errors.RewriteError`
* node constructors :func:`~minparen.ast_nodes.num`, :func:`var`,
  :func:`neg`, :func:`binop`, :func:`cond`, :func:`call`
"""

from .errors import (
    ASTError,
    BudgetExhaustedError,
    MinParenError,
    NoSolutionError,
    ParseError,
    RewriteError,
)
from .parser import parse
from .printer import print_ast
from .rewrite import SubtreeEdit, replace_subtree
from .validation import MAX_NODES, validate_tree

__version__ = "1.1.0"

__all__ = [
    "parse",
    "print_ast",
    "validate_tree",
    "replace_subtree",
    "SubtreeEdit",
    "MAX_NODES",
    "MinParenError",
    "ParseError",
    "ASTError",
    "BudgetExhaustedError",
    "NoSolutionError",
    "RewriteError",
    "__version__",
]
