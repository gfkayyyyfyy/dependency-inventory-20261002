# 差异比较（diff）流程说明

本文说明 `python -m depinventory diff <旧清单> <新清单> [--reachable] [--direct]`
从输入到输出的完整数据流，并用文件名与函数名定位源码。行为约定与 README 一致；
本文只做解释，不改变任何产品代码、公开接口、README、WHY.md、SBOM.md 或
样例文件。工具只读本地输入文件，不联网。

## 总览：从命令行到 JSON 输出

一次 diff 比较依次经过四个阶段，全部在 `depinventory/__main__.py` 的
`main()` 与 `depinventory/lockfile.py` 中完成：

1. **参数解析**：`__main__.py` 的 `_build_parser()` 定义 `diff` 子命令，
   位置参数为 `before`（旧清单路径）与 `after`（新清单路径）。**旧文件在
   前、新文件在后**，参数顺序决定比较方向：`added`/`removed` 都相对于
   「旧 → 新」的方向标记。
2. **两份输入的整份校验**：`main()` 先后调用 `lockfile.py` 的
   `load_lockfile(args.before)` 与 `load_lockfile(args.after)`，各返回
   `(root_deps, packages_map)`。两份文件适用同一套校验规则，任何一份无效
   即整体失败。
3. **比较范围确定**：省略筛选选项时，比较范围是两侧 `packages_map`
   的全部已安装条目；带 `--reachable` 时，`main()` 先用 `lockfile.py` 的
   `reachable_names()` 分别求出两侧自根节点沿 `dependencies` 可达的包名
   集合，把两侧 `packages_map` 各自过滤到可达条目，再交给比较函数；带
   `--direct` 时，`main()` 把两侧 `packages_map` 各自过滤到根节点
   `dependencies` 直接声明的条目（直接声明的包必然可达，故与
   `--reachable` 同时出现时结果与只用 `--direct` 一致）。
4. **记录生成与 JSON 输出**：`lockfile.py` 的 `diff_items(before_map,
   after_map)` 生成差异记录数组；`main()` 用
   `json.dumps(..., ensure_ascii=False)` 加一个换行写入标准输出，返回 0。

## 公开比较函数与命令行筛选的关系

`diff_items(before_map, after_map)`（`lockfile.py`）是函数级公开接口（经
`depinventory/__init__.py` 导出），它**只比较传入的两份映射**，本身不知道
可达性或直接声明：`--reachable` 与 `--direct` 的筛选完全由 `__main__.py`
的 `main()` 在调用前完成——`--reachable` 先用
`reachable_names(root_deps, packages_map)` 求出该侧的可达包名集合，
`--direct` 直接取该侧根节点 `dependencies` 声明的包名集合，再把映射过滤
为对应子集。因此：

- 省略筛选选项时，命令行行为等价于直接以两份完整 `packages_map`
  调用 `diff_items(before_map, after_map)`；
- 带 `--reachable` 或 `--direct` 时，等价于调用方先过滤、再以过滤后的
  映射调用同一个 `diff_items`，两参数调用语义不变；
- 校验始终发生在筛选之前（见下文「校验先于筛选」），`diff_items` 接收的
  永远是已校验数据。

## 比较记录的生成规则

`diff_items()` 对两侧映射键集的并集按**完整包名的 Unicode 码点升序**逐个
检查，每个包最多产生一条记录：

| 情形 | `change` | `before` | `after` |
| --- | --- | --- | --- |
| 仅新侧存在 | `added` | `null` | 新侧版本字符串 |
| 仅旧侧存在 | `removed` | 旧侧版本字符串 | `null` |
| 两侧都存在且版本字符串不同 | `changed` | 旧侧版本 | 新侧版本 |
| 两侧都存在且版本字符串相同 | 不产生记录 | — | — |

要点：

- **只有版本字符串不同才产生 `changed`**。包的直接/传递身份变化（例如从
  根直接声明变为经其他包传递引入）、依赖声明变化、根项目自身的版本或
  其他元数据变化，都不单独产生记录。
- 包名**区分大小写**（`Beta` 与 `beta` 是两个包）；作用域包以
  `@scope/name` **整体**作为一个包名匹配，不拆分比较。
- 结果按包名 Unicode 码点排序，与条目书写顺序、`dependencies` 声明顺序
  无关。
- **根项目不参与比较**：`packages` 中的空串 `""` 根节点只用于确定根依赖，
  不会出现在记录中。
- 不解析版本范围，也不判断升级、降级或安全风险。

## 可达集合的确定规则（--reachable）

`reachable_names(root_deps, packages_map)`（`lockfile.py`）用广度优先遍历
确定一侧的可达集合，规则与 `list --reachable` 相同：

- 可达性**只沿 `dependencies`** 连边：根节点 `dependencies` 声明的包及其
  逐层依赖都可达；不解析版本范围，也不从其他元数据补充连边。
- 多条路径引入同一包只记录一次，不重复。
- 可达的自环与循环由 `seen` 集合保证每个节点只扩展一次，**正常结束**并
  保留相关包。
- 与根节点断开的包（包括**断开的循环**）整体排除。
- 根 `dependencies` **省略或为空对象**时，该侧可达集合为空——即使清单中
  安装了包，带 `--reachable` 比较时该侧也不贡献任何条目。

带 `--reachable` 时，两侧各自独立确定可达集合，再按上节规则比较：仅新侧
可达的包标记 `added`，仅旧侧可达的包标记 `removed`——**即使包两侧都安装
且版本相同也如此**；两侧都可达时仍仅版本字符串不同才标记 `changed`。

## 示例：安装清单不变、根依赖链变化

下面两份文件可直接保存为 `before.json` 与 `after.json`。两份都是当前支持
的平铺 npm v3 结构：根节点只声明 `alpha`，两侧都安装 `alpha@1.0.0`、
`beta@2.0.0`、`gamma@3.0.0`；旧侧 `alpha` 只声明 `beta`，新侧 `alpha` 只
声明 `gamma`，其余包不声明依赖，所有声明值均为字符串 `"*"`。

`before.json`：

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

`after.json`：

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

### 省略 --reachable：比较安装清单

```sh
$ python -m depinventory diff before.json after.json
[]
```

两侧的已安装条目完全相同（`alpha@1.0.0`、`beta@2.0.0`、`gamma@3.0.0`），
版本字符串逐一相同，因此没有任何记录。**安装清单没有变化**：`beta` 与
`gamma` 在两份文件中都安装、版本都未变，发生变化的只是 `alpha` 的依赖
声明——而依赖声明变化不产生记录。

### 带 --reachable：比较根依赖链

```sh
$ python -m depinventory diff before.json after.json --reachable
[{"name": "beta", "change": "removed", "before": "2.0.0", "after": null}, {"name": "gamma", "change": "added", "before": null, "after": "3.0.0"}]
```

这里记录来自**两侧各自的可达集合变化**，而不是安装或改版：

- 旧侧可达集合为 `{alpha, beta}`（根 → `alpha` → `beta`），新侧为
  `{alpha, gamma}`（根 → `alpha` → `gamma`）；
- `beta` 仅旧侧可达 → `removed`，`before` 保留旧侧版本 `"2.0.0"`，
  `after` 为 `null`；
- `gamma` 仅新侧可达 → `added`，`before` 为 `null`，`after` 保留新侧
  版本 `"3.0.0"`；
- `alpha` 两侧都可达且版本相同，不产生记录。

`beta` 与 `gamma` **都没有被卸载，也没有改版**——它们在两侧都已安装且
版本相同；`removed`/`added` 只表示它们退出/进入了根依赖链的可达范围。

### 交换新旧方向

参数顺序决定方向。交换两份文件后，`added` 与 `removed` 互换：

```sh
$ python -m depinventory diff after.json before.json --reachable
[{"name": "beta", "change": "added", "before": null, "after": "2.0.0"}, {"name": "gamma", "change": "removed", "before": "3.0.0", "after": null}]
```

同一对清单、同一选项，仅因旧/新位置互换，`beta` 变为 `added`、`gamma`
变为 `removed`，缺失侧仍为 `null`，存在侧版本原样保留。

## 直接声明集合的确定规则（--direct）

带 `--direct` 时，每侧成员只取该侧根节点（空串 `""` 条目）`dependencies`
直接声明的已安装包，规则与 `list --direct` 的成员选取相同：

- 版本取安装条目的原始字符串，**不解析根声明中的版本范围**——只改变根
  声明的范围文本（如 `"^1.0.0"` 改为 `"~1.0.0"`）不产生任何记录；
- 根项目本身、仅被其他包引入的传递依赖、未被任何声明引用的已安装包都
  不进入该侧范围；其他字段（如包条目的 `dependencies`）不参与成员筛选；
- 根 `dependencies` 省略或为空对象时，该侧没有成员；两侧都为空或同一
  文件与自身比较时输出 `[]`。

两侧各自独立确定直接声明集合，再按「比较记录的生成规则」比较：仅新侧
直接声明的包标记 `added`（`before` 为 `null`），仅旧侧直接声明的包标记
`removed`（`after` 为 `null`）——**即使另一侧仍安装着相同版本也如此**；
两侧均直接声明时仍仅版本字符串不同才标记 `changed`，两个版本原样保留。

`--direct` 与 `--reachable` 可以同时使用：直接声明的包必然可达，交集即
直接声明集合，故结果与只用 `--direct` 一致。

以仓库自带的 `demo-lock.json`（旧）与 `new-lock.json`（新）为例：旧侧根
只声明 `alpha@1.0.0`，新侧根只声明 `beta@2.1.0`，`gamma` 虽在新侧安装但
未被根直接声明：

```sh
$ python -m depinventory diff demo-lock.json new-lock.json --direct
[{"name": "alpha", "change": "removed", "before": "1.0.0", "after": null}, {"name": "beta", "change": "added", "before": null, "after": "2.1.0"}]
```

`alpha` 仅旧侧直接声明 → `removed`；`beta` 仅新侧直接声明 → `added`
（尽管旧侧也安装了 `beta@2.0.0`，但它只是 `alpha` 的传递依赖）；`gamma`
两侧都未被根直接声明，不出现。

## 校验先于筛选

**整份校验先于任何筛选与比较**。`main()` 先把两份输入完整跑完
`load_lockfile()`，成功后才做可达筛选与 `diff_items()` 比较。因此错误
即使位于根节点不可达的包上（例如不可达包的空版本、不可达包的悬空依赖），
也同样使整份输入失败，不会被 `--reachable`、`--direct` 的筛选「跳过」。

任一输入出现下列情形，命令退出码为 `2`，标准输出为空，标准错误仅含
`INPUT_ERROR` 和一个换行，不输出部分结果或堆栈：

- 文件不可读；
- 不是合法 UTF-8；
- JSON 损坏（含裸 `NaN`/`Infinity`/`-Infinity` 等非标准常量）；
- 违反既有结构规则：`lockfileVersion` 非整数 3、`packages` 缺少空串根
  节点、嵌套安装路径、`link: true` 条目、版本缺失或为空字符串、
  `dependencies` 非对象或值非字符串、声明的包不存在（悬空依赖）等。

函数级接口对应同一约定：`load_lockfile()` 对上述情形抛 `InputError`
（`lockfile.py` 定义，经 `depinventory/__init__.py` 导出）。

成功时退出码为 `0`，标准错误为空，标准输出为单个 JSON 数组及末尾一个
换行；不自动落盘，不写任何文件。

## 边界与免责声明

- 工具只读本地输入文件，不联网、不安装或执行任何依赖，也不改写输入。
- 不解析版本范围：`dependencies` 中的 `"*"`、`"^1.0.0"` 等只是校验时
  要求的字符串，比较阶段完全不解析。
- 差异记录只是两侧清单的事实性对比，**不解释为漏洞或安全结论**：
  `added`/`removed`/`changed` 不代表风险升降。
