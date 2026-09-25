"""minparen -- minimal-parenthesis printing for a small expression language.

Public API:

* :func:`parse` -- source text -> validated AST dict
* :func:`print_ast` -- validated AST dict -> minimal string
* :func:`print_ast_mapped` -- same string plus exact position mapping
  (node/operator/comment spans and a per-offset query), see
  :mod:`minparen.mapped`
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
from .mapped import (
    CommentSpan,
    MappedPrint,
    NodeSpan,
    OperatorSpan,
    QueryResult,
    print_ast_mapped,
)
from .parser import parse
from .printer import print_ast
from .validation import MAX_NODES, validate_tree

__version__ = "1.1.0"

__all__ = [
    "parse",
    "print_ast",
    "print_ast_mapped",
    "validate_tree",
    "MAX_NODES",
    "MappedPrint",
    "NodeSpan",
    "OperatorSpan",
    "CommentSpan",
    "QueryResult",
    "MinParenError",
    "ParseError",
    "ASTError",
    "BudgetExhaustedError",
    "NoSolutionError",
    "__version__",
]
