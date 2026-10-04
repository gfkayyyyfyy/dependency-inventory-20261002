# 本地锁文件加载流程说明

本文说明 npm v3 平铺锁文件（`package-lock.json`）从**读取、解析、整份校验到
依赖数据转换**的完整过程，并用文件名与函数名定位源码：

- 加载与校验全部在 `depinventory/lockfile.py` 的 `load_lockfile()` 中完成，
  安装路径识别由 `_entry_name()` 负责，依赖声明校验由 `_validate_dependencies()`
  负责；
- 命令行入口 `depinventory/__main__.py` 的 `main()` 负责调用加载接口并处理
  加载错误出口，加载成功后才进入各子命令的查询阶段。

行为约定与 [README.md](README.md) 一致；本文只做解释，不改变任何产品代码、
公开接口（`depinventory/__init__.py` 的导出）、现有命令、样例锁文件与输入
规则。现有命令 `list`、`why`、`parents`、`ancestors`、`descendants`、
`diff`、`sbom` 及各自参数保持不变。工具只读本地输入文件，不联网、不安装或
执行任何依赖，也不改写输入。

## 总览：五个阶段的先后关系

任何子命令启动后，`main()` 都先调用 `load_lockfile(path)`；该函数严格按以下
顺序执行，前一步失败就不会进入后一步：

1. **读取**：以 UTF-8 文本模式读取整个文件。
2. **解析**：严格 JSON 解析（拒绝裸 `NaN`/`Infinity`/`-Infinity`）。
3. **整份结构校验**：根对象、`lockfileVersion`、`packages` 根节点，以及每个
   包条目的安装路径、条目类型、`link`、`version`。
4. **整份依赖校验**：根节点与**每个**包条目的 `dependencies`（含根不可达、
   未被引用的包）都要通过，悬空依赖整体拒绝。
5. **依赖数据转换**：把原始 JSON 转换成
   `(root_deps, packages_map)` 两个数据结构返回。

关键时序：**整份校验（阶段 3、4）先于任何目标包存在性检查**。`main()` 必须
等 `load_lockfile()` 完整成功后，才会调用 `list_items()`、`find_path()` 等
查询函数。因此坏输入与“查询了一个不存在的包”同时成立时，结果是
`INPUT_ERROR`，而不是 `NOT_FOUND`（见下文“失败结果”）。

## 输入规则（与 README“支持范围”一致）

- UTF-8 编码的严格 JSON：裸 `NaN`、`Infinity`、`-Infinity` 不得作为值出现
  在任意层级（即使落在不参与分析的元数据或根不可达包内），违反即整份拒绝；
  字符串内的同名文本、对象键以及 `1e999` 等合法数字文本不受影响；
- `lockfileVersion` 为整数 `3`（布尔值 `true` 不算整数）；
- `packages` 为对象且含空串 `""` 根节点，根节点与每个包条目均为对象；
- 包条目键只接受平铺安装路径 `node_modules/name` 与
  `node_modules/@scope/name`；嵌套路径、空名、畸形作用域名、`link: true`
  条目均不支持；
- `version` 为非空字符串；
- 依赖关系只取自根节点与包条目的 `dependencies`：可省略；出现时必须是对象，
  键与值都是字符串，且声明的包必须已安装。**不解析版本范围**，
  `devDependencies`、`peerDependencies` 等其他字段不参与连边。

## 阶段一：读取本地文件

`load_lockfile()` 首先执行：

```python
with open(path, "r", encoding="utf-8") as handle:
    text = handle.read()
```

- 文件不存在、无权限等任何 `OSError` → 转为 `InputError("cannot read
  lockfile")`；
- 文件可读但字节序列不是合法 UTF-8，触发 `UnicodeDecodeError` → 转为
  `InputError("lockfile is not valid UTF-8")`。严格模式下整份输入作废，
  不忽略、替换坏字节，也不猜测其他编码继续解析。

此阶段不联网，路径只指向本地文件系统。

## 阶段二：JSON 解析

```python
data = json.loads(text, parse_constant=_reject_json_constant)
```

- `_reject_json_constant()` 在解析阶段拒绝 Python 默认容忍的三个非标准数值
  常量；`json.JSONDecodeError`（JSON 损坏）与该回调抛出的 `ValueError` 都
  转为 `InputError("invalid JSON")`；
- 解析成功但顶层不是对象 → `InputError("lockfile root must be an object")`。

## 阶段三：整份结构校验与安装路径识别

随后依次检查：

1. `lockfileVersion` 必须是整数且等于 `3`（`_is_int()` 显式排除布尔值），
   否则 `InputError("only lockfileVersion 3 is supported")`；
2. `packages` 必须是对象且包含空串 `""` 根节点，否则
   `InputError("packages must be an object containing the root entry")`；
3. 根节点必须是对象；
4. 遍历 `packages` 的每个条目：
   - 键必须是字符串；空串根节点跳过，不进入包映射；
   - 其余键交给 `_entry_name()` 识别安装路径，返回 `None` 即拒绝
     （`unsupported package install path`）；同名包重复出现也拒绝；
   - 条目必须是对象；`node.get("link") is True` 的链接条目不支持；
   - `version` 必须是非空字符串，缺失、非字符串或空串一律拒绝。

`_entry_name(key)` 的平铺路径识别规则：

- 必须以 `node_modules/` 开头；
- 作用域包剩余部分必须形如 `@scope/name`：恰好一段斜杠，作用域与包名两侧
  都非空（单独的 `"@"` 不算作用域），整体作为一个完整包名返回；
- 非作用域包剩余部分必须非空且不含斜杠——因此嵌套路径
  `node_modules/a/node_modules/b`、空包名等都返回 `None` 而被拒绝。

这一遍只收集“安装了哪些包、各自版本是什么”，**尚未检查依赖声明**。

## 阶段四：整份依赖校验

`_validate_dependencies(node, present_names)` 先作用于根节点，再作用于阶段三
收集到的**每一个**包条目：

- `dependencies` 可省略；显式给出时必须是对象（`null`、数组等非对象值
  拒绝）；
- 每个依赖的键与值都必须是字符串；
- 键（依赖包名）必须在阶段三得到的已安装包名集合中，否则视为悬空依赖，
  抛 `InputError("dependency points to missing package")`。

校验覆盖整份 `packages`：未被根节点引用、自根不可达的包（如下文示例里的
`orphan`）其条目与依赖同样必须合法。不存在“先丢弃不可达包再校验”的捷径，
所以不可达条目里的结构错误也会让整份输入失败。

## 阶段五：依赖数据转换

校验全部通过后，`load_lockfile()` 才构造返回值：

- **根依赖列表 `root_deps`**：字符串列表，取自根节点 `dependencies` 的键，
  按声明顺序保留；根节点没有 `dependencies` 时为 `[]`。
- **包映射 `packages_map`**：字典，键为 `_entry_name()` 还原出的完整包名，
  值为 `{"version": 版本字符串, "deps": [依赖包名...]}`；`deps` 同样只取
  该条目 `dependencies` 的键，按声明顺序保留，无 `dependencies` 时为 `[]`。

转换阶段的三条边界：

- **版本原样保留**：`version` 字符串不做规范化、比较或语义化解析；
- **依赖范围不解析**：`dependencies` 的值（如 `"*"`、`"^1.0.0"`）在校验通过
  后即被丢弃，`deps` 只保留包名，后续所有查询只按包名连边；
- **根项目不进入包映射**：空串 `""` 根节点只贡献 `root_deps`，自身没有
  包名、版本条目，也不会成为任何查询结果中的成员。未被引用的包（与根断开
  的 `orphan` 等）反而**保留**在 `packages_map` 中：加载只负责完整呈现已安装
  条目，可达性裁剪是 `list --reachable` 等查询阶段的选项，不影响加载结果。

## 命令行入口的加载调用与错误出口

`main()`（`depinventory/__main__.py`）把加载与查询放在两个独立的
`try/except` 中：

1. **加载阶段**：除 `diff` 依次加载两份文件外，其余子命令都调用一次
   `load_lockfile(args.lockfile)`。`InputError`（以及任何未预期异常的兜底）
   导致向标准错误打印 `INPUT_ERROR` 并返回退出码 `2`；此时尚未进入查询，
   标准输出为空。
2. **查询阶段**：加载成功后才执行对应命令；查询函数（如 `find_path()`、
   `find_parents()`）在目标（或 `--from` 起点）未安装时抛 `NotFoundError`，
   `main()` 向标准错误打印 `NOT_FOUND` 并返回退出码 `1`，标准输出为空。
3. **成功**：`sys.stdout.write(json.dumps(result, ensure_ascii=False) + "\n")`，
   返回退出码 `0`；标准错误为空，标准输出是完整 JSON 且末尾恰有一个换行。

## 完整示例：loader-example.json

下面是一份可保存为 `loader-example.json` 的完整 UTF-8 JSON：
`lockfileVersion` 为整数 `3`；空串根节点只声明 `alpha`；`alpha@1.0.0` 声明
`@scope/leaf`；`@scope/leaf@2.0.0` 没有依赖；另有一个未被任何节点引用的
`orphan@3.0.0`。安装路径均为平铺形式，依赖声明值均为字符串 `"*"`。

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

### 加载后的数据结构

`load_lockfile("loader-example.json")` 返回：

- 根依赖列表（根项目只直接声明了 `alpha`）：

```python
["alpha"]
```

- 包映射（作用域包 `@scope/leaf` 是一个完整键；根项目不在其中；
  未被引用的 `orphan` 仍在其中）：

```python
{
  "alpha": {"version": "1.0.0", "deps": ["@scope/leaf"]},
  "@scope/leaf": {"version": "2.0.0", "deps": []},
  "orphan": {"version": "3.0.0", "deps": []},
}
```

`"1.0.0"`、`"2.0.0"`、`"3.0.0"` 原样保留；两处 `"*"` 只用于确认声明值是
字符串，转换后不出现在 `deps` 中，也不做范围解析。

### list：完整清单

`list` 列出全部已安装条目（不含根节点），按完整包名的 Unicode 码点排序。
`"@"`（U+0040）排在字母之前，故 `@scope/leaf` 居首；`direct` 表示该包是否
被根节点直接声明：

```sh
$ python -m depinventory list loader-example.json
[{"name": "@scope/leaf", "version": "2.0.0", "direct": false}, {"name": "alpha", "version": "1.0.0", "direct": true}, {"name": "orphan", "version": "3.0.0", "direct": false}]
```

清单名称依次为 `@scope/leaf`、`alpha`、`orphan`，对应的 `direct` 依次为
`false`、`true`、`false`：只有根节点直接声明的 `alpha` 为 `true`；
`@scope/leaf` 是 `alpha` 的传递依赖，`orphan` 未被引用。退出码为 0，标准
错误为空，输出以一个换行结束。

### why：根到作用域包的来源路径

```sh
$ python -m depinventory why loader-example.json @scope/leaf
{"name": "@scope/leaf", "path": ["$root", "alpha", "@scope/leaf"]}
```

默认自虚拟根 `$root` 查起：根依赖只有 `alpha`，而 `alpha` 声明
`@scope/leaf`，故来源路径为 `["$root", "alpha", "@scope/leaf"]`，
`@scope/leaf` 作为一个完整包名整体出现在路径末尾。退出码为 0，标准错误为
空，输出以一个换行结束。（`orphan` 与该路径无关；它只是仍被加载、仍可被
`list` 列出。）

## 失败结果

加载接口 `load_lockfile()` 的失败与查询函数的失败是两类不同结果：

| 情形 | 抛出位置 | 退出码 | 标准错误 |
| --- | --- | --- | --- |
| 文件不可读（不存在、无权限等 `OSError`） | `load_lockfile()` 抛 `InputError` | 2 | `INPUT_ERROR` 加换行 |
| 非法 UTF-8（`UnicodeDecodeError`） | `load_lockfile()` 抛 `InputError` | 2 | `INPUT_ERROR` 加换行 |
| JSON 损坏（`JSONDecodeError`）或含裸 `NaN`/`Infinity`/`-Infinity` | `load_lockfile()` 抛 `InputError` | 2 | `INPUT_ERROR` 加换行 |
| 既有结构校验失败（版本字段、根节点、安装路径、`link`、空版本、悬空依赖等） | `load_lockfile()` 抛 `InputError` | 2 | `INPUT_ERROR` 加换行 |
| 合法输入但查询目标（或 `--from` 起点）未安装 | 查询函数抛 `NotFoundError` | 1 | `NOT_FOUND` 加换行 |

所有失败情形下**标准输出都为空**，不输出部分清单或堆栈；标准错误仅含
`INPUT_ERROR` 或 `NOT_FOUND` 与一个换行。成功命令返回 0，标准错误为空，
标准输出是完整 JSON 并以一个换行结束。

两个与“整份校验优先”有关的确定性行为：

- **删掉 `orphan` 的版本也整体拒绝**。把示例另存一份并删除
  `node_modules/orphan` 条目的 `"version": "3.0.0"` 后，即使根项目完全
  不引用 `orphan`，阶段三仍因版本缺失抛 `InputError`（下面的命令指向这份
  修改后的副本）：

  ```sh
  $ python -m depinventory list loader-example.json
  # 退出码 2；标准输出为空；标准错误仅：
  INPUT_ERROR
  ```

- **坏输入同时查询缺失包，仍得到 `INPUT_ERROR`**。对上面这份缺版本的文件
  执行 `python -m depinventory why loader-example.json missing-pkg`，因为
  `load_lockfile()` 先于 `find_path()` 失败，目标是否存在根本不会被检查，
  结果仍是退出码 2、标准错误 `INPUT_ERROR`。

与之对照，原始合法示例查询一个未安装的目标时，加载成功、查询阶段才失败：

```sh
$ python -m depinventory why loader-example.json missing-pkg
# 退出码 1；标准输出为空；标准错误仅：
NOT_FOUND
```

## 不变量

- 加载是只读操作：只读取本地锁文件，不联网，不安装或执行任何依赖，不改写
  输入文件；
- 校验面向整份输入：根不可达、未被引用的条目与可达条目适用同一套规则，
  任何一处不合法都整体拒绝；
- `load_lockfile()` 的公开签名与返回结构
  `(root_deps, packages_map)`、`InputError`/`NotFoundError` 两个异常类型保持
  不变，现有命令、公开函数、样例文件与输入规则均不因本文档改变。
