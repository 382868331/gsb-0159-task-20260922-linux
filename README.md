# minparen — 格式化语言的最小括号输出

一个离线、零依赖的 Python 标准库模块，为小型表达式语言提供：

- `print_ast(ast)`：把表达式 AST 序列化为**括号数最少**的文本；括号数相同取
  长度最短，再相同取字典序最小；
- `print_ast_mapped(ast)`：与 `print_ast` **同一遍遍历**输出逐字节相同的文本，
  并返回每个节点/运算符/自动括号/注释在文本中的精确区间与逐偏移位置查询；
- `parse(source)`：独立的递归下降解析器，把文本还原为**完全相同的 AST**
  （不是数值等价），并把注释确定性地绑定回节点；
- `validate_tree(ast)`：严格的 AST 校验，返回独立的规范化副本。

## 环境与依赖

- Python **3.14.7**（Linux 原生验证；不使用 3.14 以外版本特有功能之外的东西，
  但只承诺 3.14.7）。
- **仅标准库**，无需 `pip install` 任何包；无网络、账号、密钥、数据库、
  中间件、云 API 或 Docker。
- 核心、测试、演示全部离线运行，数据为仓库内确定性代码/字面量。

## 运行命令

```bash
python -m unittest discover -s tests -v   # 必要测试（约几秒到几十秒）
python demo.py                            # 固定输入演示，约 0.1 秒
```

## 语言与 AST

运算符（优先级由低到高）：

| 优先级 | 运算符 | 结合性 |
|---|---|---|
| 1 | 赋值 `=` | 右结合 |
| 2 | 条件 `?:` | 右结合（两个分支均可链式承载条件表达式） |
| 3 | `+` `-` | 左结合 |
| 4 | `*` `/` | 左结合 |
| 5 | 前缀负号 `-` | —— |
| 6 | 函数调用 `f(...)` | —— |
| 7 | 叶：ASCII 标识符、非负整数 | —— |

文法（`then` 分支按 C 风格可直接承载赋值，这是刻意保留的经典语义细节）：

```
assign := cond ('=' assign)?
cond   := add  ('?' assign ':' cond)?
add    := mul  (('+'|'-') mul)*
mul    := unary (('*'|'/') unary)*
unary  := '-' unary | postfix
postfix:= primary ('(' (assign (',' assign)*)? ')')*
primary:= NUMBER | IDENT | '(' assign ')'
```

AST 节点为普通 `dict`，字段：

- `{"kind":"num","value":int}`（非负；**拒绝 `bool`、`float`**）
- `{"kind":"var","name":str}`（`[A-Za-z_][A-Za-z0-9_]*`）
- `{"kind":"neg","operand":node}`
- `{"kind":"bin","op":"+|-|*|/|=","left":node,"right":node}`
- `{"kind":"cond","cond":node,"then":node,"else":node}`
- `{"kind":"call","callee":node,"args":[node,...]}`（允许空参数）

可用 `minparen.ast_nodes` 中的构造器 `num/var/neg/binop/cond/call`。

### 注释

每个节点可带两个可选字段：

- `lead`：印在节点跨度最前面；
- `trail`：印在节点跨度最后面。

注释文本为 `/* ... */`，**不含换行**、不含 `*/` 终止符、不含 NUL。
解析时按 token 间隙的归属表绑定：每个间隙在文法上只属于一个节点的一侧，
因此注释**不可能迁移到兄弟节点**；无法绑定的位置（如空参数表
`f(/*x*/)` 里的注释）和同一节点一侧出现两条注释都会被拒绝。

示例：

```text
a-(/*lead*/b-c/*trail*/)     # 注释属于右子节点
f/*callee*/(/*sum*/x+y)      # 分别属于 callee 与参数
```

## 接口与错误语义

```python
from minparen import parse, print_ast, validate_tree
from minparen import ParseError, ASTError, BudgetExhaustedError, NoSolutionError
```

- `parse(source, *, max_nodes=300) -> dict`
  语法错误、未终止注释、含换行注释、无法绑定的注释、超节点上限，抛
  `ParseError`，带 `offset`（0 基）、`line`/`column`（1 基）。
  `source` 非 `str` 抛 `TypeError`。
- `print_ast(root) -> str`
  先做严格校验；任何结构/字段/注释违规抛 `ASTError`，带从根开始的定位路径
  （如 `root.right.args[1]`）。输入不会被修改。
- `validate_tree(root, *, max_nodes=300) -> dict`
  返回独立深拷贝；拒绝未知 `kind`、缺字段/多字段、错误类型、`bool` 数值、
  负数、非法标识符、共享/循环引用、超过 300 节点。

### 位置映射：`print_ast_mapped`

```python
from minparen import print_ast_mapped, PrintMap, NodeSpan, OpSpan, CommentSpan, PositionInfo

mapped = print_ast_mapped(tree)   # mapped.text 与 print_ast(tree) 逐字节相同
```

`print_ast_mapped` 与 `print_ast` 由 `minparen/printer.py` 里**同一个** `_emit`
遍历驱动（`print_ast` 只是丢弃映射），因此最少括号、确定性排序等原有行为不变，
映射也绝不靠"在输出里搜索相同子串"推算——两个打印得完全一样的子树各自拿到
自己发射时记录的区间。

**坐标契约**（全部满足才为合法映射）：

- 所有坐标是打印文本的 **0 基 Python 字符串字符偏移**（`str` 下标语义，
  不是 UTF-8 字节；非 ASCII 注释每个码点算 1 个偏移）；
- 所有区间为**半开区间** `[start, end)`：相邻特征共享边界偏移，查询落在边界上
  时归属**从该偏移开始**的那个特征；
- `NodeSpan.span`：节点整体跨度 = 自动补的括号对（若有）+ 前后注释 + 语法核心。
  自动补的括号归被它包住的**当前节点**（记入该节点的 `parens`，不归父节点或
  子节点）；父节点 `span` 必然包含子节点 `span`；
- `NodeSpan.core`：**语法核心**——节点自身的运算符、标点与子节点，**不含**
  自己的 `lead`/`trail` 注释，也不含自动补的括号对。对任意节点，
  `parse(text[core[0]:core[1]])` 都还原为同一棵子树（子树根自身注释除外，
  它们在核心之外）；
- 注释单独映射：`NodeSpan.lead` / `NodeSpan.trail` 与扁平列表
  `PrintMap.comments`（`CommentSpan.side/path/span/text`），不计入核心区间；
- 运算符区间：每个节点的 `operators`（`OpSpan.symbol/span/path`）覆盖
  `+ - * / = ? :` 与 `call` 的 `( ) ,`；`PrintMap.operators` 是按位置排序的
  扁平汇总。`call` 的括号是调用语法（在 `core` 内），与自动补的分组括号
  （在 `parens`）分开记录；
- 节点身份用**路径**表示（与 `ASTError` 相同的定位格式，如
  `root.right.args[1]`）；`PrintMap.node(path)` 查节点，未知路径抛 `KeyError`。

**位置查询** `PrintMap.query(offset) -> PositionInfo`：

- `offset` 非 `int`（含 `bool`）抛 `TypeError`；超出 `[0, len(text))` 抛
  `ValueError`；
- 返回 `category` 为 `"code"`（叶文本）、`"operator"`、`"paren"`（自动补的
  分组括号）、`"comment"`（注释内部）、`"whitespace"` 之一；`path` 为拥有者
  节点路径（空白为 `None`），`detail` 为运算符符号或注释侧（`lead`/`trail`）；
- 落在注释里明确返回 `"comment"`，落在空白明确返回 `"whitespace"`，**不会**
  把这些位置强行归给相邻节点。注意：打印机自身从不在注释之外输出空白，
  所以 `"whitespace"` 只会对手工构造的 `PrintMap`（构造函数公开）出现；
- 查询是 O(节点数) 的线性扫描；节点上限 300，对编辑器交互足够。

错误都是“可定位、显式失败”，不做静默纠正；本库没有任何有状态/批处理式的
部分提交操作，故不存在未说明的部分变更。

### 输入上限

- 单棵树最多 **300 个节点**（打印与解析的接口边界，均可通过关键字参数调整，
  公共契约默认 300）。
- 注释文本无长度限制，但受 300 节点之外的 Python 字符串内存限制约束。

### 搜索预算与“无解”是两回事

独立参考枚举器 `reference/refenum.py` 提供：

- `best_wrapping(tree, budget=...)`：候选数超过 `budget` 时抛
  `BudgetExhaustedError`（答案未知，只是没搜完）；
- 搜尽全部候选仍没有满足附加条件的串时抛 `NoSolutionError`
  （已证明无解）。

## 最小括号是怎么保证的

1. **结构性括号**：某子节点在某个父槽位中“不加括号能否原样解析回来”只取决于
   子节点优先级与该槽位（父节点种类+位置+结合性），与其他节点和祖先无关
   （父节点自身已有括号时已形成隔离）。槽位表固化在
   `minparen/printer.py` 的 `_SLOTS` 中；凡槽位允许就不加括号，故括号数全局
   最少；不输出任何空格等可选字符，故长度最短；渲染确定性，故字典序唯一。
2. **注释括号**：注释只在其归属间隙会被外层节点认领、且可能跨过兄弟边界时才
   迫使注释拥有者自身加一对括号，规则局部于拥有者，同样最少；闭合 `)` 位置
   最早也给出字典序最优。
3. **独立认证**：`reference/refenum.py` 用第二套元组 AST、第二个独立递归下降
   解析器，对小树的**每个可选括号子集**（位掩码枚举）逐一用独立解析器判定
   是否还原为同一棵树，按 `(括号对数, 长度, 字符串)` 排序取最优。测试中所有
   深度 ≤4 的小树穷举、更大的树固定步长抽样，核心输出必须与枚举最优逐字节
   相同。参考实现不 import 被测核心；`print→parse→print` 一致只作补充检查。

## 设计取舍 / 已知限制

- 只实现题目契约的表达式子集：没有字符串、布尔、浮点、指数、后缀 `++`、
  逗号表达式、注释嵌套（`/* /* */ */` 不支持）；不宣称与任何完整行业标准
  （C/JS 等）兼容。
- 词法不支持 `//` 注释；注释只有 `/* ... */`。
- 整数词法接受前导零（如 `007`），其 AST 与 `7` 相同，规范输出为 `7`。
- 节点为可变 `dict`；校验/解析返回独立副本，调用方自行持有原树时应避免在校
  验后修改再复用旧引用。
- **没有浮点运算或数值求值**：输出是符号化的精确文本，因此不存在浮点误差口
  径问题；任何"数值"字段均为整数且不参与计算。
- 枚举参考是测试/教学工具，候选数为 2^k，只适用于小树，不应用于 300 节点
  规模；核心打印本身是 O(n) 单次遍历。
- 位置映射是**快照**：文本或树之后被修改不会反映到旧 `PrintMap` 上；
  节点用路径标识而不是对象身份（打印前会校验并复制树）。
- `PrintMap.query` 为每次调用 O(节点数) 的线性扫描，未建索引；300 节点上限下
  足够编辑器交互使用，批量逐偏移查询请自行缓存分类结果。
- 映射坐标是字符偏移而非字节偏移；如需 UTF-8 字节位置请调用方自行换算。

## 目录

```
minparen/            # 本题库：ast_nodes, validation, printer, parser, mapping, errors
reference/           # 仅测试用：独立解析器 + 括号枚举最优参考（非核心）
tests/test_minparen.py   # 原有行为：最小括号、注释绑定、错误契约
tests/test_mapping.py    # 位置映射：区间契约、逐偏移查询、独立 parse 核对
demo.py
README.md
```
