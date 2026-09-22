# exprfmt — 表达式 AST 的最小括号打印与解析

离线、可复用的 Python 库：把表达式 AST 打印为**括号数最少**的文本，并能
把文本**解析回完全相同的树**（含注释绑定位置）。纯标准库实现，无任何
第三方依赖，不访问网络。

- Python 版本：3.14.7（仅用标准库 `dataclasses` / `re`）
- 额外依赖：无（`requirements.txt` 为空约定，安装 Python 即可运行）

## 命令

```bash
python -m unittest discover -s tests -v   # 运行全部测试
python demo.py                            # 固定输入演示（约 1 秒内完成）
```

## 接口（`exprfmt.py`）

节点类（冻结 dataclass，相等性包含注释）：

| 类 | 字段 | 含义 |
|---|---|---|
| `Ident` | `name` | ASCII 标识符 `[A-Za-z_][A-Za-z0-9_]*` |
| `Int` | `value` | 非负整数（拒绝 bool / float / NaN / Infinity / 负数） |
| `Neg` | `operand` | 前缀负号 |
| `Call` | `func, args` | 函数调用；`func` 必须是 `Ident` 且 `func.before` 必须为空 |
| `Bin` | `op, left, right` | `op ∈ {'=','+','-','*','/'}`；`'='` 的 `left` 必须是 `Ident` |
| `Cond` | `cond, then, otherwise` | 条件 `?:` |

每个节点另有 `before` / `after` 字段（字符串元组，默认空），表示绑定在
该节点前/后的注释。

函数与常量：

- `print_expr(node) -> str`：先 `validate`，再输出最小括号文本。
- `parse_expr(text) -> node`：解析文本；`text` 非 `str` 抛 `TypeError`。
- `validate(node) -> None`：校验合法性，不合法抛 `ValidationError`。
- `count_nodes(node) -> int`：节点计数（`Call` 的 callee 也算一个节点）。
- `MAX_NODES = 300`：节点数上限，`validate` 与 `parse_expr` 都强制执行。
- 异常：`ExprError`（基类）、`ValidationError`（消息含节点路径，如
  `root.right.args[0].value`）、`ParseError`（消息含字符偏移）。

## 语法与优先级

```
assign  := cond ('=' assign)?           # 右结合；左值必须是标识符
cond    := add ('?' assign ':' cond)?   # 右结合；then 分支为赋值级
add     := mul (('+'|'-') mul)*         # 左结合
mul     := unary (('*'|'/') unary)*     # 左结合
unary   := '-' unary | postfix          # 前缀负号
postfix := primary ('(' (assign (',' assign)*)? ')')*   # callee 必须是标识符
primary := IDENT | INT | '(' assign ')'
```

优先级（弱→强）：`=` < `?:` < `+ -` < `* /` < 前缀 `-` < 调用 < 叶。
括号按此优先级与结合性精确省略：`a - (b - c)` 保留、`a - b - c` 不加、
`a ? b : c ? d : e` 不加、`(a ? b : c) ? d : e` 保留、`--x` 不加。

## 注释规则

- 注释打印为 `/* text */`，紧贴所属节点；解析时前导注释绑给紧随的节点、
  尾随注释绑给刚结束的节点，**不会跨过兄弟节点迁移**（有专门测试）。
- 为保持绑定，含注释的 `Bin`/`Cond`（前或后）与含尾随注释的 `Neg` 会被
  强制加一层括号，如 `/* t */ (a + b) /* u */`、`(-x) /* n */`。
- 注释文本不得含换行符，不得含终止符 `*/`（无法表示，`validate` 拒绝）。
- 解析时去掉恰好一层包裹空格：`/*x*/` 与 `/* x */` 都得到 `"x"`。
- callee 之前的注释在语法上只能属于整个 `Call` 节点，因此
  `Call.func.before` 非空会被 `validate` 拒绝（请绑到 `Call` 上）。

## 最优性口径

目标依次为：括号数最少 → 输出最短 → 字典序最小。本库排版固定（二元
运算符两侧各一个空格、参数 `", "` 分隔、前缀负号无空格、callee 尾随
注释与 `(` 之间无空格），在此排版下最小括号位置唯一。`tests/test_optimal.py`
内含**独立实现**的解析器与括号枚举器（不调用被测核心），对深度 4 的
小树枚举全部“加/不加括号”候选，按上述口径取最优并与 `print_expr`
输出逐一比对；`print(parse(t)) == t` 的往返一致性仅作补充校验。

## 设计取舍

- 打印为确定性单次树遍历（无搜索、无预算耗尽问题）；参考枚举只存在于
  测试中，用于证明最小性而非参与实现。
- 模块完全无状态：所有函数纯计算，失败即抛异常，不存在部分变更。
- 赋值左值与 callee 限定为标识符：换取无歧义的注释绑定与更简单的语法，
  属于本题的有限子集约定，不宣称兼容 C 等完整语言语法。

## 已知限制

- 仅支持 `/* ... */` 行内注释；注释文本不能含换行或 `*/`。
- `?:` 的 else 分支不能直接写赋值（需括号：`a ? b : (x = y)`）。
- 整数字面量允许前导零并归一化（`007` → `7`）；超大整数字面量/整数值
  超出 `int` 转换上限时报错而非截断。
- 本模块不产生任何浮点结果，因此无浮点误差口径问题；整数输出精确。
- 节点数上限 300（接口边界，非演示规模）；超限在解析与验证两侧均被拒绝。
