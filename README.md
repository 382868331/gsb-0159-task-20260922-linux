# minparen — 格式化语言的最小括号输出

一个离线、零依赖的 Python 标准库模块，为小型表达式语言提供：

- `print_ast(ast)`：把表达式 AST 序列化为**括号数最少**的文本；括号数相同取
  长度最短，再相同取字典序最小；
- `parse(source)`：独立的递归下降解析器，把文本还原为**完全相同的 AST**
  （不是数值等价），并把注释确定性地绑定回节点；
- `validate_tree(ast)`：严格的 AST 校验，返回独立的规范化副本。

## 环境与依赖

- Python **3.14.7**（Linux 原生验证；不使用 3.14 以外版本特有功能之外的东西，
  但只承诺 3.14.7）。
- **仅标准库**，无需 `pip install` 任何包；无网络、账号、密钥、数据库、
  中间件、云 API 或 Docker。
- 核心、测试、演示全部离线运行，数据为仓库内确定性代码/字面量。

## 准备 venv（可选但推荐）

```bash
python3.14 -m venv .venv
. .venv/bin/activate
python -V          # 应为 Python 3.14.7
```

不需要安装任何依赖；也可以直接用系统 `python3`（确认为 3.14.7）。

## 运行命令

```bash
python -m unittest discover -s tests -v   # 必要测试（约几秒到几十秒）
python demo.py                            # 固定输入演示，约 0.1 秒
python demo_iteration.py                  # 本次迭代：宽度布局演示，约 0.01 秒
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
from minparen import parse, print_ast, format_ast, FormatResult, validate_tree
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
- `format_ast(root, width, *, budget=None) -> FormatResult`
  **本次迭代新增**：在列宽约束下选择换行的布局打印（详见下节）。

错误都是“可定位、显式失败”，不做静默纠正；本库没有任何有状态/批处理式的
部分提交操作，故不存在未说明的部分变更。

## 宽度布局（format_ast）

布局选择与语法打印**分离**：括号仍完全由语法优先级决定（与 `print_ast`
同一套槽位表），布局只在每个 `bin` / `cond` / `call`（含参数）组上
选择**平铺**（单行）或**展开**（子项换行缩进，缩进单位 2 空格）；`neg`
自身不产生换行，操作数在 `-` 后递归布局。布局**不会**靠多加括号来回避
宽度限制（有测试断言两种输出括号数相同）。输入 AST 不被修改。

展开形态示例：

```text
LEFT +            TEST?             callee(
  RIGHT             THEN:             arg1,
                    ELSE              arg2
                                  )
```

### 惩罚与选优次序

候选按三元组 `(溢出字符总数, 换行数, 输出文本字典序)` 字典序选优：

1. **溢出字符总数**：所有输出行 `max(0, 行长 - width)` 之和。单个不可拆
   词（标识符/数字）超过列宽允许溢出，但溢出量完整计入惩罚；
2. **换行数**：输出中 `\n` 的个数；
3. **输出文本**本身的字典序。

宽度 `width` 支持 **1 到 80**（含），每个宽度都有确定结果；`width`
非 `int`（含 `bool`）抛 `TypeError`，越界抛 `ValueError`。

### 返回值与预算语义

`FormatResult` 字段：`text`、`overflow`、`newlines`、
`cost`（上述三元组）、`proven_optimal`、`expanded`（展开的状态数）、
`width`。

- 不指定 `budget`：搜索必定完成，`proven_optimal=True`，结果为已证最优。
- 指定 `budget`（展开状态数上限）：预算耗尽时抛
  `BudgetExhaustedError`，其 `.best` 属性携带**已找到的合法布局**
  （`proven_optimal=False`，保证能重新解析为同一棵树，但最优性未知）——
  与“已证最优”严格区分，也与 `NoSolutionError`（证明无解）区分。

### 最优性论证与共享子问题

子节点列位置只取决于父状态（列、缩进层级、父级追加的尾串），与兄弟选择
无关；溢出与换行数按行可加；字典序在拼接下保持。因此对
`(节点, 槽位, 列, 层级, 尾串)` 做按状态动态规划是精确的，记忆表即共享
子问题结构，避免较大树上的指数重复。小树（深度 ≤4）在测试中与
`reference/reflayout.py` 的**独立穷举枚举器**（独立元组 AST、独立解析器、
枚举全部平铺/展开掩码并逐一回解析认证）逐字节比对。

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
- 宽度布局的展开形态是固定的三种（二元/条件/调用各一种），缩进单位固定
  为 2 空格，不提供风格配置；列宽接口边界为 1..80。布局 DP 的状态数受
  树形影响，病态深树可用 `budget` 显式限制并得到 `BudgetExhaustedError`
  而非静默截断。

## 目录

```
minparen/            # 本题库：ast_nodes, validation, printer, parser, layout, errors
reference/           # 仅测试用：独立解析器 + 括号枚举最优参考 + 布局枚举参考（非核心）
tests/test_minparen.py
tests/test_layout.py
demo.py
demo_iteration.py
README.md
```
