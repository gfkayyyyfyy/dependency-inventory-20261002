# 差异比较（diff）流程说明

本文说明 `python -m depinventory diff <before> <after> [--reachable]` 从两份
锁文件到 JSON 差异输出的完整数据流，并用文件名与函数名定位源码；同时解释
**安装清单变化**与**根依赖链变化**两种比较范围的区别。行为约定与 README
一致；本文只做解释，不改变任何产品代码、公开接口（`depinventory/__init__.py`
的导出）、README、WHY.md、SBOM.md 及任何样例文件。工具只读本地输入文件，
不联网、不解析版本范围，也不把差异解释为漏洞或安全结论。

## 两种差异：安装清单变化与根依赖链变化

`diff` 比较的两份输入都是完整的平铺 npm v3 锁文件，但“比较什么”有两种范围：

- **安装清单变化**（省略 `--reachable`）：比较两侧 `packages` 中全部已安装
  条目（`node_modules/name` 条目，不含根项目），包括从根节点不可达的包。
  一个包只要在某一侧被安装，就进入比较；它是否处在根依赖链上完全不影响。
- **根依赖链变化**（追加 `--reachable`）：两侧各自先从根节点
  `dependencies` 出发、沿包条目 `dependencies` 逐层确定可达集合，**只比较
  各自可达的条目**。一个包两侧都安装且版本相同，但只在一侧可达，仍会产生
  `added`/`removed` 记录——记录描述的是该侧**根依赖链构成**的变化，而不是
  包被卸载或改版。

两种范围共用同一个比较函数 `diff_items()`，差别仅在命令行传入映射前是否按
可达集合过滤（见下文阶段三）。

## 总览：从命令行到 JSON 输出

一次 diff 依次经过四个阶段，全部在 `depinventory/__main__.py` 的 `main()`
与 `depinventory/lockfile.py` 中完成：

1. **参数解析**：`__main__.py` 的 `_build_parser()` 定义 `diff` 子命令，
   两个位置参数 `before`、`after` 分别是**旧清单在前、新清单在后**的文件
   路径，顺序决定差异方向；`--reachable` 存入 `args.reachable`。
2. **两侧各自整份读取与校验**：`main()` 对两个路径分别调用
   `lockfile.py` 的 `load_lockfile(path)`，得到
   `(before_root, before_map)` 与 `(after_root, after_map)`。任一文件失败
   则整条命令失败，不进行任何比较。
3. **比较范围确定**：省略 `--reachable` 时直接用两份完整映射；带
   `--reachable` 时，`main()` 对两侧分别调用 `reachable_names()` 求可达
   集合，并据此把两份映射各过滤一份。
4. **比较与 JSON 输出**：`main()` 调用 `lockfile.py` 的
   `diff_items(before_map, after_map)` 得到记录列表，用
   `json.dumps(..., ensure_ascii=False)` 加一个换行写入标准输出，返回 0。

```text
旧锁文件 before（本地 JSON）        新锁文件 after（本地 JSON）
   │  python -m depinventory diff <before> <after> [--reachable]
   ▼
load_lockfile(before)              load_lockfile(after)        lockfile.py
   │  各自整份读取并校验，返回 (root_deps, packages_map)；
   │  任何读取、解析或结构问题抛 InputError（校验先于筛选）
   ▼
[仅 --reachable] reachable_names(root_deps, packages_map)     lockfile.py
   │  两侧各自确定可达集合，main() 据此过滤两份映射
   ▼
diff_items(before_map, after_map)                             lockfile.py
   │  纯函数：只比较传入的两份映射，返回版本差异记录列表
   ▼
main()                                                        __main__.py
   │  json.dumps(..., ensure_ascii=False) 后写入标准输出，末尾加一个换行
   ▼
命令行 JSON 数组输出（退出码 0）
```

## 两个核心数据结构

每次 `load_lockfile()` 返回一个二元组（两侧各一份）：

- **根依赖 `root_deps`**：字符串列表，取自锁文件根节点（`packages` 中空串
  `""` 条目）的 `dependencies` 的键，按声明顺序保留，表示根项目直接声明了
  哪些包。它只在 `--reachable` 时作为可达遍历的起点集合使用。
- **包映射 `packages_map`**：字典，键为完整包名（如 `alpha`、
  `@scope/name`），值为 `{"version": 版本字符串, "deps": [依赖包名...]}`，
  覆盖全部已安装条目，不含根项目。

`diff_items()` 只读取每个条目映射里的 `version` 与“键是否存在”；`deps`
列表不参与记录判定（依赖关系只在求可达集合时使用）。

## 阶段一：旧文件在前、新文件在后的输入顺序

`_build_parser()` 中两个位置参数按顺序绑定为 `args.before`（旧清单）与
`args.after`（新清单），`main()` 据此依次加载：

```python
before_root, before_map = load_lockfile(args.before)
after_root, after_map = load_lockfile(args.after)
```

方向后果：

- 只在 `after` 中出现的包 → `added`；只在 `before` 中出现的包 →
  `removed`。
- `changed` 记录的 `before` 取旧侧版本、`after` 取新侧版本。
- 交换两个文件的位置，同一次演进的 `added` 与 `removed` 全部互换，
  `changed` 的两个版本也对调（见文末示例）。

参数顺序不影响校验：两个文件适用同一套规则，任一文件无效都整体失败。

## 阶段二：两侧各自整份校验（先于筛选与比较）

`load_lockfile()`（`lockfile.py`）对每份文件按顺序执行：

1. 以 UTF-8 文本模式读取；文件不可读（`OSError`）与非法 UTF-8
   （`UnicodeDecodeError`）都转为 `InputError`。
2. `json.loads(text, parse_constant=_reject_json_constant)` 解析；裸
   `NaN`/`Infinity`/`-Infinity` 由 `_reject_json_constant()` 拒绝，JSON
   损坏同样转为 `InputError`。
3. 结构校验：根必须是对象、`lockfileVersion` 必须为整数 3、`packages` 必须
   是含空串根节点的对象；`_entry_name()` 把每个条目键解释为平铺安装路径
   （`node_modules/name` 或 `node_modules/@scope/name`），不支持的键
   （嵌套路径、空名、畸形作用域）直接拒绝；`link: true`、重复条目也拒绝。
4. 每个包条目的 `version` 必须为非空字符串；`_validate_dependencies()`
   对根节点和**每个**包条目检查 `dependencies`：可省略，出现时必须是对象、
   键值均为字符串，且声明的包必须已安装（悬空依赖拒绝）。

关键时序：**整份校验先于可达筛选，也先于比较**。`main()` 先跑完两次
`load_lockfile()`，成功后才调用 `reachable_names()` 与 `diff_items()`。
因此即使错误位于根节点不可达的条目上——例如不可达包的空版本，或不可达
包声明了指向不存在包的悬空依赖——整份输入也照样失败，绝不会“因为用了
`--reachable` 就跳过不参与比较的部分”。

## 阶段三：比较范围确定

### 省略 --reachable：全部已安装条目

`main()` 不过滤，直接把两份完整的 `packages_map` 传给 `diff_items()`。
根依赖声明如何书写、包是否可达，都不影响条目是否进入比较。

### 追加 --reachable：两侧各自求根可达集合

`main()` 对两侧分别调用 `reachable_names(root_deps, packages_map)`
（`lockfile.py`），再用返回的集合过滤本侧映射：

```python
before_keep = reachable_names(before_root, before_map)
after_keep = reachable_names(after_root, after_map)
before_map = {n: i for n, i in before_map.items() if n in before_keep}
after_map  = {n: i for n, i in after_map.items()  if n in after_keep}
```

`reachable_names()` 的可达性规则：

- 只沿根节点与包条目的 `dependencies` 声明的包名连边，不解析版本范围
  （声明值 `"*"`、`"^1.0.0"` 等只是字符串），也不从 `devDependencies`、
  `peerDependencies` 等其他元数据补边；
- 根直接声明的包及其逐层依赖都可达，根项目本身不在集合中；
- 多条路径引入同一包只记录一次；
- 可达的自环与循环由 `seen` 集合保证每个节点只扩展一次，正常结束并保留
  相关包；完全脱离根节点的包（包括与根断开的循环）整体排除；
- 根节点 `dependencies` 省略或为空对象 `{}` 时，起点集合为空，该侧可达
  集合为空——即使锁文件里安装了包，过滤后也没有任何条目参与比较。

过滤是**两侧各自独立**进行的：旧侧可达性只由旧侧的根声明与依赖边决定，
新侧同理。因此同一个已安装包可以只通过一侧的过滤。

## 阶段四：公开比较函数 diff_items 与 JSON 输出

`diff_items(before_map, after_map)`（`lockfile.py`，同时由
`depinventory/__init__.py` 导出）是唯一的比较逻辑，它对两份映射键的并集
按 `sorted(...)` 排序后逐个判定，每个包名最多产生一条记录：

| 两侧存在情况 | 记录 |
| --- | --- |
| 仅新侧（after）存在 | `{"change": "added", "before": null, "after": 版本}` |
| 仅旧侧（before）存在 | `{"change": "removed", "before": 版本, "after": null}` |
| 两侧都存在且版本字符串不同 | `{"change": "changed", "before": 旧版本, "after": 新版本}` |
| 两侧都存在且版本字符串完全相同 | 不产生记录 |

记录字段固定为 `name`、`change`、`before`、`after`；缺失侧一律为 JSON
`null`，存在侧版本字符串原样保留。

**公开函数与命令行筛选的关系**：`diff_items()` 本身不做任何可达性判断，
也不读取根依赖——它只比较调用方传入的两份映射，两参数调用语义始终不变。
省略 `--reachable` 时命令行传入完整映射；追加 `--reachable` 时，根可达
筛选完全由 `main()` 在调用前用 `reachable_names()` 完成，`diff_items()`
对此无感知。直接以 Python 调用 `diff_items()` 的代码可以自行决定传入
完整映射还是任意子集，函数行为一致。

### 什么变化不产生记录

- 两侧比较范围内同名包**仅在版本字符串不同时**才产生 `changed`。比较是
  字符串原样比较，不做语义版本解析，也不判断升级或降级。
- 直接/传递身份（根直接声明还是经其他包引入）、依赖声明内容、根项目版本
  以及许可证等其他元数据的改变，**都不单独产生记录**。省略
  `--reachable` 时，一个包两侧都安装且版本相同，即使它从“直接依赖”变成
  “传递依赖”或反过来，也没有任何记录。
- 名称按**区分大小写**的完整包名匹配：`Beta` 与 `beta` 是两个不同的包；
  作用域名 `@scope/name` 作为一个整体匹配，不拆成作用域与名称两段。
- 结果按完整包名的 **Unicode 码点升序**排列（如 `beta` 先于 `gamma`），
  不受条目或声明的书写顺序影响；根项目不参与比较，永远不会出现在结果中。

## 完整示例：before.json 与 after.json

以下两份 JSON 都是当前支持的平铺 npm v3 结构，可直接保存为 `before.json`
与 `after.json`。两侧根都只声明 `alpha`，都安装了
`alpha@1.0.0`、`beta@2.0.0`、`gamma@3.0.0`；区别只在 `alpha` 的依赖：
旧侧声明 `beta`，新侧声明 `gamma`，其余包不声明依赖；所有声明值均为
字符串 `"*"`。

`before.json`（旧清单）：

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
        "beta": "*"
      }
    },
    "node_modules/beta": {
      "version": "2.0.0"
    },
    "node_modules/gamma": {
      "version": "3.0.0"
    }
  }
}
```

`after.json`（新清单）：

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
        "gamma": "*"
      }
    },
    "node_modules/beta": {
      "version": "2.0.0"
    },
    "node_modules/gamma": {
      "version": "3.0.0"
    }
  }
}
```

### 安装清单变化：省略 --reachable

两侧安装的包与版本完全相同（`alpha@1.0.0`、`beta@2.0.0`、
`gamma@3.0.0`），变化的只是 `alpha` 的依赖声明，而依赖声明不产生记录：

```sh
$ python -m depinventory diff before.json after.json
[]
```

### 根依赖链变化：追加 --reachable

两侧可达集合不同：旧侧为 `{alpha, beta}`（`alpha → beta`，`gamma` 与根
断开），新侧为 `{alpha, gamma}`（`alpha → gamma`，`beta` 与根断开）。
`alpha` 两侧都可达且版本相同，不输出；于是按名称排序依次得到 `beta` 的
`removed` 与 `gamma` 的 `added`：

```sh
$ python -m depinventory diff before.json after.json --reachable
[{"name": "beta", "change": "removed", "before": "2.0.0", "after": null}, {"name": "gamma", "change": "added", "before": null, "after": "3.0.0"}]
```

两条命令都以退出码 0 结束，标准错误为空，输出末尾恰有一个换行。

这两条记录**不表示 beta 被卸载、gamma 被新安装，也不表示它们改版**：
`beta@2.0.0` 与 `gamma@3.0.0` 在两侧锁文件中都存在且版本一致。记录来自
两侧各自可达集合的构成变化——旧侧根依赖链经 `alpha` 到达 `beta`，新侧改
为到达 `gamma`。省略 `--reachable` 时这一变化不可见（输出 `[]`），正是
安装清单变化与根依赖链变化的区别。

### 交换新旧方向

把两个文件位置对调，旧、新身份互换，`added` 与 `removed` 随之互换，
缺失侧仍为 `null`，存在侧版本不变：

```sh
$ python -m depinventory diff after.json before.json --reachable
[{"name": "beta", "change": "added", "before": null, "after": "2.0.0"}, {"name": "gamma", "change": "removed", "before": "3.0.0", "after": null}]
```

## 成功与失败的输出约定

成功时：退出码为 `0`，标准错误为空；标准输出为单个 JSON 数组（无差异时
为 `[]`）及末尾一个换行；不自动落盘，不写任何文件。

失败时（任一文件在 `load_lockfile()` 阶段抛 `InputError`）：文件不可读、
非法 UTF-8、JSON 损坏，或违反既有结构规则（包括根节点不可达包的空版本与
悬空依赖），命令退出码为 `2`，标准输出为空，标准错误仅含 `INPUT_ERROR`
和一个换行，不输出部分结果或堆栈。以函数级接口加载时，`load_lockfile()`
直接抛 `InputError`；`diff_items()` 与 `reachable_names()` 只接受已校验
的数据结构，不负责读取或校验。

## 不变量

- 工具只读本地输入文件，不联网、不安装或执行依赖、不改写任何输入。
- 不解析版本范围：声明值与版本字符串都按原样比较，不判断升级、降级，
  也不把任何差异解释为漏洞或其他安全结论。
- 本文档不改动产品代码、公开接口（`depinventory/__init__.py` 的导出）、
  README、WHY.md、SBOM.md 及任何样例锁文件。
