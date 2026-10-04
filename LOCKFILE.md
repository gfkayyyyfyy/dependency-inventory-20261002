# 本地锁文件加载流程说明

本文说明本地 npm v3 平铺锁文件（`package-lock.json`）从**读取、解析、整份
校验到依赖数据转换**的完整过程，并以文件名与函数名定位源码：

- 加载入口 `load_lockfile()`、安装路径识别 `_entry_name()`、依赖校验
  `_validate_dependencies()` 都在 `depinventory/lockfile.py`；
- 命令行的加载调用、成功输出与失败出口在 `depinventory/__main__.py` 的
  `main()`。

行为约定与 README 一致；本文只做解释，不改变任何产品代码、公开接口
（`depinventory/__init__.py` 的导出）、现有命令、样例文件与输入规则。工具
只读本地输入文件，不安装或执行任何依赖，不联网，也不改写输入。

## 总览：四个阶段，严格按顺序执行

一次加载（`load_lockfile(path)`，`lockfile.py`）依次经过：

1. **读取**：以 UTF-8 文本模式打开并读入整个文件；文件不可读或不是合法
   UTF-8 即失败。
2. **解析**：`json.loads()` 把文本解析为 Python 对象；JSON 损坏或出现裸
   `NaN`/`Infinity`/`-Infinity` 即失败。
3. **整份结构校验**：顶层结构、`lockfileVersion`、`packages` 根节点、每个
   条目的安装路径与版本、以及根节点和每个包条目的 `dependencies`，全部
   检查通过才算成功——**不区分该条目之后是否会被查询用到**。
4. **依赖数据转换**：校验通过后才把内部条目表整理成对外的
   `(root_deps, packages_map)`，供 `list`、`why`、`parents`、`ancestors`、
   `descendants`、`diff`、`sbom` 全部命令共用。

```text
锁文件（本地 UTF-8 JSON）
   │  python -m depinventory <命令> <lockfile> ...
   ▼
load_lockfile(path)                      depinventory/lockfile.py
   1) 读取（open UTF-8）
   2) 解析（json.loads，拒绝裸 NaN/Infinity/-Infinity）
   3) 整份结构校验（_entry_name 识别安装路径，
      _validate_dependencies 校验根节点与每个包条目）
   4) 转换为 (root_deps, packages_map)
   │  读取/解析/校验任一环节失败都抛 InputError
   ▼
main() 的查询与输出阶段                  depinventory/__main__.py
      成功：标准输出写完整 JSON + 一个换行，退出 0
      目标不存在：NotFoundError → 标准错误 NOT_FOUND，退出 1
      加载失败：InputError    → 标准错误 INPUT_ERROR，退出 2
```

关键时序：**整份校验先于任何目标存在性检查**。`main()` 必须先完整跑完
`load_lockfile()`，成功后才进入查询阶段（如 `find_path()` 检查目标是否已
安装）。因此同一条命令上「输入有结构错误」与「查询的包不存在」同时成立
时，结果是 `INPUT_ERROR`，不会先报 `NOT_FOUND`。

## 阶段一：读取

`load_lockfile()` 首先执行
`open(path, "r", encoding="utf-8")` 并读入完整文本：

- `OSError`（路径不存在、无权限等文件不可读情形）转为
  `InputError("cannot read lockfile")`；
- `UnicodeDecodeError`（文件可读但字节不是合法 UTF-8）转为
  `InputError("lockfile is not valid UTF-8")`。

严格按 UTF-8 整份解码：不忽略、不替换坏字节，也不猜测其他编码后继续解析；
坏字节即使位于不参与分析的元数据字段中，整份输入同样作废。解码成功后文件
内容保持原样，加载过程不写回任何字节。

## 阶段二：解析

读取得到的文本交给
`json.loads(text, parse_constant=_reject_json_constant)`：

- JSON 语法损坏（`json.JSONDecodeError`）或解析期
  `ValueError` 都转为 `InputError("invalid JSON")`；
- `_reject_json_constant()` 在裸 `NaN`、`Infinity`、`-Infinity` 作为**值**
  出现的任何层级（含不参与分析的元数据、根不可达包内）抛 `ValueError`，
  由解析路径包成 `InputError`，整份拒绝；字符串内的同名文本（如 `"NaN"`）、
  对象键与普通数字不触发该回调，`1e999` 这样的合法指数文本仍可读取。

解析结果必须是 JSON 对象（Python `dict`），否则
`InputError("lockfile root must be an object")`。

## 阶段三：整份结构校验

解析成功后逐项检查，任何一项失败都立即抛 `InputError`，不返回部分结果：

1. `lockfileVersion` 必须是整数 `3`（`bool` 虽是 `int` 子类但显式排除，
   字符串 `"3"` 也不接受）。
2. `packages` 必须是对象，且含空串 `""` 根节点；根节点本身必须是对象。
3. 遍历 `packages` 的每个条目：
   - 键必须是字符串；空串键即根节点，单独跳过，不按安装路径解释；
   - 其余键由 `_entry_name()` 识别为**平铺安装路径**，无法识别即拒绝；
   - 同一包名出现两次（重复条目）拒绝；
   - 条目值必须是对象；`link: true` 条目拒绝；
   - `version` 必须是非空字符串，缺失、非字符串或空串一律拒绝。

### 安装路径识别：`_entry_name()`

只接受 npm v3 的两种平铺键：

- `node_modules/name`：去掉 `node_modules/` 前缀后，包名非空且不含斜杠；
- `node_modules/@scope/name`：作用域包整体作为一个包名，恰好含一段斜杠，
  `@` 与斜杠之间的作用域至少一个字符（裸 `"@"` 不算作用域），斜杠后的包名
  同样非空。

以下键不在支持范围，直接报 `unsupported package install path`：不以
`node_modules/` 开头的键、`node_modules/` 后为空、嵌套路径
（`node_modules/a/node_modules/b`）、以及畸形作用域（缺名、多段斜杠等）。

### 依赖校验：`_validate_dependencies()`

识别出版本条目后，加载器先收集全部已安装包名集合，再对**根节点和每一个
包条目**调用 `_validate_dependencies(node, present_names)`：

- `dependencies` 可省略；
- 一旦出现，必须是对象——显式 `null`、数组、字符串、数字、布尔都拒绝；
- 每个依赖的键与值都必须是字符串（值即版本范围文本，如 `"*"`、`"^1.0.0"`）；
- 声明的每个包都必须在已安装包名集合中——**悬空依赖拒绝**，即使声明方或
  被声明方从根节点不可达也是如此。

校验覆盖整份文件：根不可达条目上的坏版本、非法 `dependencies`、悬空依赖
同样使加载失败。这也是 `--reachable` 类筛选不会放宽校验的原因——筛选发生
在加载成功之后。

### 支持的输入规则（与 README「支持范围」一致）

- UTF-8 编码的严格 JSON，裸 `NaN`/`Infinity`/`-Infinity` 不得作为值出现；
- `lockfileVersion` 为整数 `3`；
- `packages` 为对象且含空串根节点，根节点与所有包条目均为对象；
- 平铺安装路径 `node_modules/name` 与 `node_modules/@scope/name`；
- 包 `version` 为非空字符串；
- 依赖关系只取自根节点及包条目的 `dependencies`（可省略；出现时须为对象、
  键值为字符串、声明的包必须存在）；
- 嵌套安装路径与 `link: true` 条目不支持。

## 阶段四：依赖数据转换

整份校验通过后，`load_lockfile()` 才构造并返回两个数据结构：

- **根依赖列表 `root_deps`**：字符串列表，取自根节点
  `dependencies` 的**键**，按声明顺序保留；根节点省略 `dependencies` 时为
  `[]`。它表示根项目直接声明了哪些包，也是默认来源查询的起点集合。
- **包映射 `packages_map`**：`{包名: {"version": ..., "deps": [...]}}`
  字典。键是 `_entry_name()` 识别出的完整包名；`version` 是条目里的版本
  字符串；`deps` 是该条目 `dependencies` 的键列表（省略时为 `[]`）。

转换阶段的三条边界：

1. **版本原样保留**：`"1.0.0"`、`"2.0.0"` 等字符串不做规范化、比较或推断，
   后续输出什么完全取决于输入文本。
2. **依赖范围不解析**：`dependencies` 的**值**（`"*"`、`"^1.0.0"` 等）只在
   校验阶段确认它是字符串，转换时即丢弃；建立依赖关系只按**键（包名）**，
   不做范围求解，也不从 `devDependencies`、`peerDependencies` 等其他字段
   补边。
3. **根项目不进入包映射**：空串根节点只用于提供 `root_deps`；
   `packages_map` 只含 `node_modules/` 下的已安装包，根项目的名称、版本等
   不成为其中条目，也不参与任何包级查询。

## 示例：loader-example.json

下面是一份完整的 UTF-8 JSON 示例，可原样保存为仓库根目录的
`loader-example.json`（仓库中已随本文档提供该文件）：

```json
{
  "lockfileVersion": 3,
  "packages": {
    "": {
      "dependencies": {
        "alpha": "*"
      }
    },
    "node_modules/alpha": {
      "version": "1.0.0",
      "dependencies": {
        "@scope/leaf": "*"
      }
    },
    "node_modules/@scope/leaf": {
      "version": "2.0.0"
    },
    "node_modules/orphan": {
      "version": "3.0.0"
    }
  }
}
```

结构说明：

- `lockfileVersion` 为整数 `3`；
- 空串 `""` 根节点只声明 `alpha`（依赖值是字符串 `"*"`）；
- `node_modules/alpha` 为平铺安装路径，`alpha@1.0.0` 声明作用域包
  `@scope/leaf`（值同样为 `"*"`）；
- `node_modules/@scope/leaf` 是作用域包的平铺路径，`@scope/leaf@2.0.0`
  没有 `dependencies` 字段；
- `node_modules/orphan` 已安装、`orphan@3.0.0`，但没有任何节点声明它
  （未被引用的包仍在支持范围内，加载不拒绝）。

调用公开函数 `load_lockfile("loader-example.json")` 得到：

```python
root_deps = ["alpha"]

packages_map = {
    "@scope/leaf": {"version": "2.0.0", "deps": []},
    "alpha": {"version": "1.0.0", "deps": ["@scope/leaf"]},
    "orphan": {"version": "3.0.0", "deps": []},
}
```

- `root_deps` 只有 `"alpha"`：根节点仅声明它；
- `packages_map` 含三个已安装包而**不含根项目**；版本字符串 `"1.0.0"`、
  `"2.0.0"`、`"3.0.0"` 原样保留；`"*"` 范围文本没有进入映射（`deps` 只剩
  包名），依赖范围不解析；
- `@scope/leaf` 与 `orphan` 的 `deps` 均为 `[]`（前者显式无依赖字段，后者
  无人引用但自身也无依赖字段）。

### list：完整清单的确定预期

```sh
$ python -m depinventory list loader-example.json
[{"name": "@scope/leaf", "version": "2.0.0", "direct": false}, {"name": "alpha", "version": "1.0.0", "direct": true}, {"name": "orphan", "version": "3.0.0", "direct": false}]
```

- 名称依次为 `@scope/leaf`、`alpha`、`orphan`：按完整包名的 Unicode 码点
  升序排列（`@` 的码点小于字母，故作用域包在最前）；
- `direct` 依次为 `false`、`true`、`false`：只有 `alpha` 被根节点
  `dependencies` 直接声明；`@scope/leaf` 是经 `alpha` 传递引入，
  `orphan` 未被引用；
- 未被引用的 `orphan` 仍出现在完整清单中（只有带 `--reachable` 的筛选才
  会排除它；本文示例均不带该选项）。

### why：来源路径的确定预期

```sh
$ python -m depinventory why loader-example.json @scope/leaf
{"name": "@scope/leaf", "path": ["$root", "alpha", "@scope/leaf"]}
```

作用域包以 `@scope/leaf` 整体作为一个区分大小写的完整包名匹配。根声明
`alpha`、`alpha` 声明 `@scope/leaf`，故来源路径为
`["$root", "alpha", "@scope/leaf"]`：`"$root"` 是虚拟根标记
（`lockfile.py` 的 `ROOT` 常量），不是已安装包，也不出现在 `packages_map`
中。两条命令都以退出码 0 结束，标准错误为空，标准输出为完整 JSON 且末尾
恰有一个换行。

## 失败结果

所有命令的加载都走同一个 `load_lockfile()`，失败出口在 `main()` 中统一
处理。失败时标准输出为空，不输出部分清单或堆栈。

### InputError：加载接口抛异常，命令返回 2

| 情形 | 发生阶段 |
| --- | --- |
| 文件不可读（不存在、无权限等 `OSError`） | 读取 |
| 非法 UTF-8（`UnicodeDecodeError`） | 读取 |
| JSON 损坏、裸 `NaN`/`Infinity`/`-Infinity` | 解析 |
| 既有结构校验失败：顶层不是对象、`lockfileVersion` 不是整数 3、`packages` 缺根节点、安装路径不支持、重复条目、`link: true`、版本缺失/为空/非字符串、`dependencies` 类型错误、键值非字符串、悬空依赖等 | 整份结构校验 |

这些情形下：

- 函数接口：`load_lockfile()` 抛 `depinventory.InputError`，不返回任何根
  依赖或包映射；
- 命令行：退出码 `2`，标准输出为空，标准错误仅含 `INPUT_ERROR` 和一个
  换行（`INPUT_ERROR\n`）。

例：把示例中 `orphan` 的版本删除（`node_modules/orphan` 变成 `{}`），即使
`orphan` 未被任何节点引用，整份输入也被拒绝——版本校验对每个条目无条件
执行，与可达性无关：

```sh
$ python -m depinventory list loader-example-no-version.json   # 删去 orphan 版本的副本
# 退出码 2；标准输出为空；标准错误恰好为：
INPUT_ERROR
```

### 完整校验先于目标存在性检查

`main()` 先用一个 `try/except` 完成加载，再在后续 `try/except` 中执行查询。
因此对同一份坏输入同时查询一个缺失的包，仍然得到 `INPUT_ERROR`（退出 2），
而不是 `NOT_FOUND`：

```sh
$ python -m depinventory why loader-example-no-version.json ghost
# 退出码 2；标准输出为空；标准错误恰好为：
INPUT_ERROR
```

### NotFoundError：合法输入查询未安装目标，命令返回 1

加载成功只代表整份锁文件合法，不代表查询目标存在。合法的
`loader-example.json` 查询一个未安装的包时，查询函数（如 `find_path()`）
抛 `depinventory.NotFoundError`，命令行退出码 `1`，标准输出为空，标准错误
仅含 `NOT_FOUND` 和一个换行（`NOT_FOUND\n`）：

```sh
$ python -m depinventory why loader-example.json ghost
# 退出码 1；标准输出为空；标准错误恰好为：
NOT_FOUND
```

### 成功输出约定

加载与查询都成功时：退出码 `0`，标准错误为空；标准输出是完整的单个 JSON
文档（`json.dumps(..., ensure_ascii=False)`，非 ASCII 字符不转义），并以
恰好一个换行结束。

| 退出码 | 含义 | 标准错误 |
| --- | --- | --- |
| 0 | 成功 | 空 |
| 1 | 查询的包未安装（查询函数抛 `NotFoundError`） | `NOT_FOUND\n` |
| 2 | 读取、解析或结构校验失败（`load_lockfile()` 抛 `InputError`） | `INPUT_ERROR\n` |

## 不变量

- 工具只读本地输入文件：不联网，不安装或执行任何依赖，不改写输入文件。
- 读取 → 解析 → 整份校验 → 数据转换的顺序固定；整份校验先于一切目标
  存在性检查，任何条目的结构错误都使整份输入失败。
- 本文档不改动产品代码、公开接口、现有命令（`list`、`why`、`parents`、
  `ancestors`、`descendants`、`diff`、`sbom`）、README、WHY.md、SBOM.md
  及任何既有样例锁文件；`loader-example.json` 是随本文档新增的样例。
