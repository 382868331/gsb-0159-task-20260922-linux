"""minparen -- minimal-parenthesis printing for a small expression language.

Public API:

* :func:`parse` -- source text -> validated AST dict
* :func:`print_ast` -- validated AST dict -> minimal string
* :func:`validate_tree` -- strict AST validation, returns a canonical copy
* :func:`replace_subtree` -- swap one subtree in valid source text, returns
  a single :class:`Edit` span (UTF-8 byte offsets)
* :class:`~minparen.errors.ParseError`, :class:`~minparen.errors.ASTError`,
  :class:`~minparen.errors.PathError`,
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
    PathError,
)
from .parser import parse
from .printer import print_ast
from .replace import Edit, replace_subtree
from .validation import MAX_NODES, validate_tree

__version__ = "1.1.0"

__all__ = [
    "parse",
    "print_ast",
    "validate_tree",
    "replace_subtree",
    "Edit",
    "MAX_NODES",
    "MinParenError",
    "ParseError",
    "ASTError",
    "PathError",
    "BudgetExhaustedError",
    "NoSolutionError",
    "__version__",
]
