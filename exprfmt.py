"""exprfmt — 表达式 AST 的最小括号打印与独立解析（纯标准库）。

语法（结合力从弱到强）：

    assign  := cond ('=' assign)?           # 赋值，右结合；左值必须是标识符
    cond    := add ('?' assign ':' cond)?   # 条件 ?:，右结合；then 分支为赋值级
    add     := mul (('+' | '-') mul)*       # 左结合
    mul     := unary (('*' | '/') unary)*   # 左结合
    unary   := '-' unary | postfix          # 前缀负号
    postfix := primary ('(' (assign (',' assign)*)? ')')*  # 调用， callee 必须是标识符
    primary := IDENT | INT | '(' assign ')'

叶节点为 ASCII 标识符（[A-Za-z_][A-Za-z0-9_]*）与非负整数。

每个节点可携带 before / after 注释（字符串元组），打印为 ``/* text */`` 紧贴
所属节点；解析打印结果可恢复完全相同的树（含注释位置），注释不会跨过兄弟
节点迁移。注释文本不得包含换行符，也不得包含终止符 ``*/``（无法表示，拒绝）。

打印规则：括号数最少；本库排版固定（二元运算符两侧各一个空格、调用参数
", " 分隔、前缀负号无空格），在此排版下最小括号位置唯一，平局规则
（同数最短、再字典序）由测试中的独立枚举参考完整验证。

公开接口：
    节点类  Ident / Int / Neg / Call / Bin / Cond
    函数    validate(node) / print_expr(node) / parse_expr(text) / count_nodes(node)
    异常    ExprError / ValidationError（含节点路径） / ParseError（含偏移）
    常量    MAX_NODES = 300
"""

from dataclasses import dataclass, replace
import re

__all__ = [
    "MAX_NODES",
    "Node", "Ident", "Int", "Neg", "Call", "Bin", "Cond",
    "ExprError", "ValidationError", "ParseError",
    "validate", "print_expr", "parse_expr", "count_nodes",
]

MAX_NODES = 300

# ---------------------------------------------------------------------------
# 错误类型
# ---------------------------------------------------------------------------


class ExprError(Exception):
    """本模块所有异常的基类。"""


class ValidationError(ExprError):
    """AST 不合法（消息中带可定位的节点路径，如 root.left.args[0]）。"""


class ParseError(ExprError):
    """输入文本无法解析（消息中带字符偏移）。"""

    def __init__(self, message, pos=None):
        self.pos = pos
        if pos is not None:
            message = "offset %d: %s" % (pos, message)
        super().__init__(message)


# ---------------------------------------------------------------------------
# AST 节点（冻结数据类；相等性包含注释，保证“恢复完全相同的树”可判定）
# ---------------------------------------------------------------------------


class Node:
    """所有表达式节点的基类。"""


@dataclass(frozen=True)
class Ident(Node):
    name: str
    before: tuple = ()
    after: tuple = ()


@dataclass(frozen=True)
class Int(Node):
    value: int
    before: tuple = ()
    after: tuple = ()


@dataclass(frozen=True)
class Neg(Node):
    operand: Node
    before: tuple = ()
    after: tuple = ()


@dataclass(frozen=True)
class Call(Node):
    func: Ident          # callee 必须是 Ident，且 func.before 必须为空（见 validate）
    args: tuple = ()
    before: tuple = ()
    after: tuple = ()


@dataclass(frozen=True)
class Bin(Node):
    op: str              # '='、'+'、'-'、'*'、'/' 之一；'=' 的 left 必须是 Ident
    left: Node = None
    right: Node = None
    before: tuple = ()
    after: tuple = ()


@dataclass(frozen=True)
class Cond(Node):
    cond: Node = None
    then: Node = None
    otherwise: Node = None
    before: tuple = ()
    after: tuple = ()


# ---------------------------------------------------------------------------
# 验证
# ---------------------------------------------------------------------------

_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_OPS = ("=", "+", "-", "*", "/")


def count_nodes(node):
    """统计子树节点数（Call 的 callee 标识符也算一个节点）。"""
    try:
        return _count(node)
    except RecursionError:
        raise ValidationError("tree is nested too deeply to validate") from None


def _count(node):
    if isinstance(node, (Ident, Int)):
        return 1
    if isinstance(node, Neg):
        return 1 + _count(node.operand)
    if isinstance(node, Call):
        return 1 + _count(node.func) + sum(_count(a) for a in node.args)
    if isinstance(node, Bin):
        return 1 + _count(node.left) + _count(node.right)
    if isinstance(node, Cond):
        return 1 + _count(node.cond) + _count(node.then) + _count(node.otherwise)
    return 1  # 未知类型：由 validate 逐节点拒绝


def validate(node):
    """校验 AST 合法性；不合法时抛出带路径的 ValidationError，否则返回 None。"""
    if not isinstance(node, Node):
        raise ValidationError(
            "root: not an expression node (got %s)" % type(node).__name__)
    n = count_nodes(node)
    if n > MAX_NODES:
        raise ValidationError(
            "root: tree has %d nodes, exceeding the %d-node limit" % (n, MAX_NODES))
    _validate(node, "root")


def _check_comments(node, path):
    for attr in ("before", "after"):
        cmts = getattr(node, attr, ())
        if not isinstance(cmts, (tuple, list)):
            raise ValidationError(
                "%s.%s: comments must be a tuple/list of str" % (path, attr))
        for c in cmts:
            if not isinstance(c, str):
                raise ValidationError(
                    "%s.%s: comment must be str, got %s" % (path, attr, type(c).__name__))
            if "\n" in c or "\r" in c:
                raise ValidationError(
                    "%s.%s: comment text must not contain newlines" % (path, attr))
            if "*/" in c:
                raise ValidationError(
                    "%s.%s: comment text must not contain '*/' "
                    "(unrepresentable comment terminator)" % (path, attr))


def _validate(node, path):
    if not isinstance(node, Node):
        raise ValidationError(
            "%s: not an expression node (got %s)" % (path, type(node).__name__))
    _check_comments(node, path)
    if isinstance(node, Ident):
        if not isinstance(node.name, str) or not _IDENT_RE.fullmatch(node.name):
            raise ValidationError(
                "%s.name: invalid ASCII identifier %r" % (path, node.name))
    elif isinstance(node, Int):
        v = node.value
        if isinstance(v, bool) or not isinstance(v, int):
            raise ValidationError(
                "%s.value: must be a plain int (bool/float/NaN/Infinity are rejected), "
                "got %r" % (path, v))
        if v < 0:
            raise ValidationError(
                "%s.value: integer leaf must be non-negative, got %d" % (path, v))
        try:
            str(v)
        except ValueError:
            raise ValidationError(
                "%s.value: integer has too many digits to represent" % path) from None
    elif isinstance(node, Neg):
        _validate(node.operand, path + ".operand")
    elif isinstance(node, Call):
        if not isinstance(node.func, Ident):
            raise ValidationError(
                "%s.func: callee must be an Ident, got %s"
                % (path, type(node.func).__name__))
        if node.func.before:
            raise ValidationError(
                "%s.func.before: comments before a callee are unrepresentable; "
                "attach them to the Call node itself" % path)
        if not isinstance(node.args, (tuple, list)):
            raise ValidationError("%s.args: must be a tuple/list of nodes" % path)
        _validate(node.func, path + ".func")
        for i, a in enumerate(node.args):
            _validate(a, "%s.args[%d]" % (path, i))
    elif isinstance(node, Bin):
        if node.op not in _OPS:
            raise ValidationError("%s.op: unknown operator %r" % (path, node.op))
        if node.op == "=" and not isinstance(node.left, Ident):
            raise ValidationError(
                "%s.left: assignment target must be an identifier" % path)
        _validate(node.left, path + ".left")
        _validate(node.right, path + ".right")
    elif isinstance(node, Cond):
        _validate(node.cond, path + ".cond")
        _validate(node.then, path + ".then")
        _validate(node.otherwise, path + ".otherwise")
    else:
        raise ValidationError(
            "%s: unknown node type %s" % (path, type(node).__name__))


# ---------------------------------------------------------------------------
# 打印（最小括号）
# ---------------------------------------------------------------------------

# 优先级（数值越大结合越紧）：= 1，?: 2，+- 3，*/ 4，前缀- 5，调用 6，叶 7
_PREC = {"=": 1, "+": 3, "-": 3, "*": 4, "/": 4}


def _prec(node):
    if isinstance(node, (Ident, Int)):
        return 7
    if isinstance(node, Call):
        return 6
    if isinstance(node, Neg):
        return 5
    if isinstance(node, Bin):
        return _PREC[node.op]
    return 2  # Cond


def _needs_comment_parens(node):
    """为让 before/after 注释解析后仍绑定回本节点而必须加的括号。

    解析器把前导注释绑给“最左侧可解析节点”、尾随注释绑给“最后结束的
    原子/括号节点”，因此 Bin/Cond 的前后注释、Neg 的尾随注释若不加括号
    会被兄弟或子节点吸收。
    """
    if isinstance(node, (Bin, Cond)):
        return bool(node.before or node.after)
    if isinstance(node, Neg):
        return bool(node.after)
    return False


def _render(node, ctx):
    if isinstance(node, Ident):
        body = node.name
    elif isinstance(node, Int):
        body = str(node.value)
    elif isinstance(node, Neg):
        body = "-" + _render(node.operand, 5)
    elif isinstance(node, Call):
        body = (_render(node.func, 6) + "("
                + ", ".join(_render(a, 0) for a in node.args) + ")")
    elif isinstance(node, Bin):
        p = _PREC[node.op]
        if node.op == "=":          # 右结合
            lctx, rctx = 2, 1
        else:                       # 左结合
            lctx, rctx = p, p + 1
        body = (_render(node.left, lctx) + " " + node.op + " "
                + _render(node.right, rctx))
    else:  # Cond，右结合
        body = (_render(node.cond, 3) + " ? " + _render(node.then, 0)
                + " : " + _render(node.otherwise, 2))
    if _prec(node) < ctx or _needs_comment_parens(node):
        body = "(" + body + ")"
    if node.before:
        body = "".join("/* %s */ " % c for c in node.before) + body
    if node.after:
        body = body + "".join(" /* %s */" % c for c in node.after)
    return body


def print_expr(node):
    """把 AST 打印为括号最少的文本；不合法的 AST 抛 ValidationError。"""
    validate(node)
    return _render(node, 0)


# ---------------------------------------------------------------------------
# 解析
# ---------------------------------------------------------------------------


class _Tok:
    __slots__ = ("kind", "text", "pos")

    def __init__(self, kind, text, pos):
        self.kind = kind    # 'ident' | 'int' | 'comment' | 'punct' | 'eof'
        self.text = text
        self.pos = pos


_PUNCTS = frozenset("()+-*/,?=:")


def _is_ident_start(ch):
    return ch == "_" or "a" <= ch <= "z" or "A" <= ch <= "Z"


def _is_ident_char(ch):
    return _is_ident_start(ch) or "0" <= ch <= "9"


def _lex(s):
    toks = []
    i, n = 0, len(s)
    while i < n:
        ch = s[i]
        if ch in " \t\n\r\f\v":
            i += 1
        elif ch == "/" and i + 1 < n and s[i + 1] == "*":
            j = s.find("*/", i + 2)
            if j < 0:
                raise ParseError("unterminated comment", i)
            toks.append(_Tok("comment", s[i + 2:j], i))
            i = j + 2
        elif _is_ident_start(ch):
            j = i + 1
            while j < n and _is_ident_char(s[j]):
                j += 1
            toks.append(_Tok("ident", s[i:j], i))
            i = j
        elif "0" <= ch <= "9":
            j = i + 1
            while j < n and "0" <= s[j] <= "9":
                j += 1
            toks.append(_Tok("int", s[i:j], i))
            i = j
        elif ch in _PUNCTS:
            toks.append(_Tok("punct", ch, i))
            i += 1
        else:
            raise ParseError("unexpected character %r" % ch, i)
    toks.append(_Tok("eof", "", n))
    return toks


def _describe(tok):
    if tok.kind == "eof":
        return "end of input"
    if tok.kind == "comment":
        return "a comment"
    return repr(tok.text)


class _Parser:
    def __init__(self, toks):
        self.toks = toks
        self.i = 0
        self.n = 0

    def _peek(self):
        return self.toks[self.i]

    def _advance(self):
        t = self.toks[self.i]
        self.i += 1
        return t

    def _is_punct(self, p):
        t = self.toks[self.i]
        return t.kind == "punct" and t.text == p

    def _expect_punct(self, p):
        if not self._is_punct(p):
            raise ParseError(
                "expected %r but found %s" % (p, _describe(self._peek())),
                self._peek().pos)
        self._advance()

    def _mk(self, node):
        self.n += 1
        if self.n > MAX_NODES:
            raise ParseError(
                "expression exceeds the %d-node limit" % MAX_NODES,
                self._peek().pos)
        return node

    def _comments(self):
        out = []
        while self._peek().kind == "comment":
            tok = self._advance()
            raw = tok.text
            if "\n" in raw or "\r" in raw:
                raise ParseError("comment text must not contain newlines", tok.pos)
            # 打印形态为 "/* text */"：去掉恰好一层包裹空格
            if raw.startswith(" "):
                raw = raw[1:]
            if raw.endswith(" "):
                raw = raw[:-1]
            out.append(raw)
        return out

    # assign := cond ('=' assign)?
    def _parse_assign(self):
        node = self._parse_cond()
        if self._is_punct("="):
            eq = self._advance()
            if not isinstance(node, Ident):
                raise ParseError(
                    "assignment target must be an identifier", eq.pos)
            rhs = self._parse_assign()
            node = self._mk(Bin("=", node, rhs))
        return node

    # cond := add ('?' assign ':' cond)?
    def _parse_cond(self):
        node = self._parse_add()
        if self._is_punct("?"):
            self._advance()
            then = self._parse_assign()
            self._expect_punct(":")
            other = self._parse_cond()
            node = self._mk(Cond(node, then, other))
        return node

    # add := mul (('+'|'-') mul)*
    def _parse_add(self):
        node = self._parse_mul()
        while self._is_punct("+") or self._is_punct("-"):
            op = self._advance().text
            rhs = self._parse_mul()
            node = self._mk(Bin(op, node, rhs))
        return node

    # mul := unary (('*'|'/') unary)*
    def _parse_mul(self):
        node = self._parse_unary()
        while self._is_punct("*") or self._is_punct("/"):
            op = self._advance().text
            rhs = self._parse_unary()
            node = self._mk(Bin(op, node, rhs))
        return node

    # unary := '-' unary | postfix
    def _parse_unary(self):
        before = self._comments()
        if self._is_punct("-"):
            self._advance()
            operand = self._parse_unary()
            return self._mk(Neg(operand, before=tuple(before)))
        return self._parse_postfix(before)

    # postfix := primary ('(' args? ')')*，callee 必须是标识符
    def _parse_postfix(self, before):
        node = self._parse_primary(before)
        while self._is_punct("("):
            if not isinstance(node, Ident):
                raise ParseError(
                    "callee must be an identifier", self._peek().pos)
            self._advance()
            args = []
            if not self._is_punct(")"):
                args.append(self._parse_assign())
                while self._is_punct(","):
                    self._advance()
                    args.append(self._parse_assign())
            self._expect_punct(")")
            after = self._comments()
            # callee 之前的前导注释在语法上只能属于整个调用节点
            node = self._mk(Call(replace(node, before=()), tuple(args),
                                 before=node.before, after=tuple(after)))
        return node

    # primary := IDENT | INT | '(' assign ')'
    def _parse_primary(self, before):
        t = self._peek()
        if t.kind == "ident":
            self._advance()
            after = self._comments()
            return self._mk(Ident(t.text, tuple(before), tuple(after)))
        if t.kind == "int":
            self._advance()
            after = self._comments()
            try:
                value = int(t.text)
            except ValueError:
                raise ParseError("integer literal too large", t.pos) from None
            return self._mk(Int(value, tuple(before), tuple(after)))
        if self._is_punct("("):
            self._advance()
            inner = self._parse_assign()
            self._expect_punct(")")
            after = self._comments()
            # 括号不产生节点：外层注释绑到括号内整个表达式的根上
            return replace(inner,
                           before=tuple(before) + inner.before,
                           after=inner.after + tuple(after))
        raise ParseError("expected an expression but found %s" % _describe(t), t.pos)


def parse_expr(text):
    """把文本解析为 AST；失败抛 ParseError（含偏移），text 非 str 抛 TypeError。"""
    if not isinstance(text, str):
        raise TypeError("parse_expr expects str, got %s" % type(text).__name__)
    try:
        parser = _Parser(_lex(text))
        node = parser._parse_assign()
        t = parser._peek()
        if t.kind != "eof":
            raise ParseError("unexpected trailing input %s" % _describe(t), t.pos)
        return node
    except RecursionError:
        raise ParseError("expression is nested too deeply") from None
