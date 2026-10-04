# 简化 SBOM 导出说明

本文档说明 `sbom` 命令（含 `--reachable` 筛选、`--with-paths` 路径与
`--with-dependencies` 直接依赖选项）与 `sbom_document` 函数的行为。
导出结果为产品自有格式，**不声明符合 CycloneDX 或 SPDX** 等任何标准
SBOM 规范。

## 数据流与源码对应关系

```text
锁文件（本地 JSON）
   │  python -m depinventory sbom <lockfile> [--reachable] [--with-paths]
   │                                          [--with-dependencies]
   ▼
load_lockfile(path)                      depinventory/lockfile.py
   │  读取并校验，返回 (root_deps, packages_map)；
   │  任何读取、解析或结构问题抛 InputError
   ▼
sbom_document(root_deps, packages_map, reachable=False, with_paths=False,
              with_dependencies=False)
                                         depinventory/lockfile.py
   │  纯函数：由已校验数据生成组件列表，返回 SBOM 文档字典；
   │  带 --reachable 时以 reachable=True 调用，只保留可达组件；
   │  带 --with-paths 时以 with_paths=True 调用，为每个组件附加 path；
   │  带 --with-dependencies 时以 with_dependencies=True 调用，
   │  为每个组件附加 dependencies
   ▼
main()                                   depinventory/__main__.py
   │  json.dumps(..., ensure_ascii=False) 后写入标准输出，末尾加一个换行
   ▼
命令行 JSON 输出（退出码 0）
```

- `load_lockfile` 的校验规则与 `list`、`why`、`diff` 完全相同，详见
  README「支持范围」一节：仅支持 UTF-8 严格 JSON、`lockfileVersion` 为
  整数 `3`、平铺的 `node_modules/name` 与 `node_modules/@scope/name`
  三平铺结构；嵌套路径与 `link: true` 条目不支持。
- `sbom_document` 是函数级接口，返回 Python 字典；命令行结果是把该字典
  序列化为单个 JSON 文档后写到标准输出，二者内容一致，但函数本身不写
  文件、不打印。

## 文档结构

顶层仅含三个字段：

| 字段 | 值 |
| --- | --- |
| `format` | 固定字符串 `"depinventory-sbom"` |
| `formatVersion` | 整数 `1` |
| `components` | 组件对象数组 |

`components` 的构成规则：

- 默认覆盖**全部已安装包**（`packages_map` 中的每个条目），不包含根项目；
  带 `--reachable`（即 `reachable=True`）时只保留自根节点沿
  `dependencies` 可达的包，语义与 `list --reachable` 一致：根直接声明的
  包及其逐层依赖都保留，多条路径引入同一包只输出一次，可达的自环与循环
  正常结束并保留相关包，与根断开的包和循环整体排除；可达性只看
  `dependencies`，不解析版本范围，也不从其他元数据补充连边；
- 按完整包名的 Unicode 码点升序排列，每个包只出现一次；
- 省略 `--reachable` 时，根节点不可达的包仍保留在结果中；
- 循环依赖不产生重复组件（组件直接来自 `packages_map`，天然不重复）；
- 锁文件只有根节点，或带 `--reachable` 时根 `dependencies` 省略、为空
  对象，`components` 为空数组 `[]`，`format` 与 `formatVersion` 标记
  仍保留。

每个组件对象的字段：

| 字段 | 含义 |
| --- | --- |
| `name` | 完整包名，原样保留（含 `@scope/name`） |
| `version` | 版本字符串，原样保留，不解析版本范围 |
| `ecosystem` | 固定为 `"npm"` |
| `direct` | 仅取决于根节点 `dependencies` 是否声明该包，与其他包的依赖关系无关 |
| `license` | 固定为 `"unknown"`；即使输入附带许可证信息也不解读 |
| `securityStatus` | 固定为 `"unknown"`；未知不代表没有风险，也不依据版本推断安全结论 |
| `path` | **仅在 `--with-paths`（`with_paths=True`）时出现**：自 `"$root"` 到组件完整包名的最短依赖路径 |
| `dependencies` | **仅在 `--with-dependencies`（`with_dependencies=True`）时出现**：该包条目直接声明的完整包名数组 |

### `path` 的语义

仅当命令带 `--with-paths`（函数以 `with_paths=True` 调用）时，每个组件
在上述六个字段之后再附带一个 `path` 字段；省略该选项时组件对象不含
`path`，输出与旧版本逐字段一致。

- 路径与同一输入**省略 `--from` 的 `why` 查询完全一致**：从 `"$root"`
  开始，到组件的完整包名结束，只沿各条目 `dependencies` 声明的边取
  **边数最少**的路径；等长时按整条包名序列的 **Unicode 码点字典序**取
  第一条；
- 根直接依赖的路径只经过根标记和包名本身，如 `["$root", "alpha"]`；
- 作用域包名整体作为**单个路径元素**（`["$root", "@scope/pkg", ...]`）；
- 已安装但根不可达的组件（完整导出仍保留这类组件）`path` 为 `[]`；
  与 `--reachable` 组合时仅为筛选后保留的组件附加路径，不可达组件已被
  筛选排除；
- 自环、循环和共享依赖正常结束：每个组件在 `components` 中只出现一次，
  多条路径引入的同一包只得到一条最短路径；
- 路径的选择与组件排序都不受 packages 条目顺序和 `dependencies` 声明
  顺序影响；名称、版本原样保留，`direct` 的含义不因该选项改变。

### `dependencies` 的语义

仅当命令带 `--with-dependencies`（函数以 `with_dependencies=True` 调用）
时，每个组件再附带一个 `dependencies` 数组；省略该选项时组件对象不含
`dependencies`，输出与旧版本逐字段一致。

- 数组只列出该包条目 `dependencies` **直接声明**的完整包名：不展开
  传递依赖、不附带版本范围，也不从其他元数据补充连边；
- 按完整包名的 Unicode 码点升序排列并去重，区分大小写；作用域包名
  （`@scope/name`）作为整体保留为单个元素；
- 声明省略或为空对象时输出 `[]`；
- 自环保留自身（包声明自身时自身进入数组），循环双方分别保留各自
  声明；组件仍来自 `packages_map`，不重复，根项目不成为组件；
- 与 `--reachable` 组合时仅筛选组件集合，保留组件的 `dependencies`
  数组不变；与 `--with-paths` 组合时 `path` 与 `dependencies` 各自
  独立附加；
- 数组内容与组件排序都不受 packages 条目顺序和 `dependencies` 声明
  顺序影响；名称、版本原样保留，`direct` 的含义不因该选项改变。

## 本地演示

仓库自带两份样例锁文件。`demo-lock.json` 含 `alpha@1.0.0`（根直接依赖）
与 `beta@2.0.0`（经 `alpha` 传递引入）：

```sh
$ python -m depinventory sbom demo-lock.json
{"format": "depinventory-sbom", "formatVersion": 1, "components": [{"name": "alpha", "version": "1.0.0", "ecosystem": "npm", "direct": true, "license": "unknown", "securityStatus": "unknown"}, {"name": "beta", "version": "2.0.0", "ecosystem": "npm", "direct": false, "license": "unknown", "securityStatus": "unknown"}]}
```

`new-lock.json` 含 `beta@2.1.0`（根直接依赖）与 `gamma@3.0.0`（未被任何
节点引用）：

```sh
$ python -m depinventory sbom new-lock.json
{"format": "depinventory-sbom", "formatVersion": 1, "components": [{"name": "beta", "version": "2.1.0", "ecosystem": "npm", "direct": true, "license": "unknown", "securityStatus": "unknown"}, {"name": "gamma", "version": "3.0.0", "ecosystem": "npm", "direct": false, "license": "unknown", "securityStatus": "unknown"}]}
```

两份结果的 `direct` 依次为 `true`、`false`：`gamma` 虽不可达，仍被导出。

带 `--reachable` 时只导出自根节点沿 `dependencies` 可达的组件；同一
`new-lock.json` 下 `gamma` 与根断开，被排除：

```sh
$ python -m depinventory sbom new-lock.json --reachable
{"format": "depinventory-sbom", "formatVersion": 1, "components": [{"name": "beta", "version": "2.1.0", "ecosystem": "npm", "direct": true, "license": "unknown", "securityStatus": "unknown"}]}
```

带 `--with-paths` 时每个组件额外携带 `path`。`demo-lock.json` 中
`alpha` 是根直接依赖，`beta` 经 `alpha` 传递引入：

```sh
$ python -m depinventory sbom demo-lock.json --with-paths
{"format": "depinventory-sbom", "formatVersion": 1, "components": [{"name": "alpha", "version": "1.0.0", "ecosystem": "npm", "direct": true, "license": "unknown", "securityStatus": "unknown", "path": ["$root", "alpha"]}, {"name": "beta", "version": "2.0.0", "ecosystem": "npm", "direct": false, "license": "unknown", "securityStatus": "unknown", "path": ["$root", "alpha", "beta"]}]}
```

`new-lock.json` 中 `beta` 是根直接依赖，`gamma` 已安装但根不可达，
其 `path` 为 `[]`：

```sh
$ python -m depinventory sbom new-lock.json --with-paths
{"format": "depinventory-sbom", "formatVersion": 1, "components": [{"name": "beta", "version": "2.1.0", "ecosystem": "npm", "direct": true, "license": "unknown", "securityStatus": "unknown", "path": ["$root", "beta"]}, {"name": "gamma", "version": "3.0.0", "ecosystem": "npm", "direct": false, "license": "unknown", "securityStatus": "unknown", "path": []}]}
```

`--with-paths` 与 `--reachable` 可组合：只保留可达组件并为其附加路径，
上例只剩 `beta`：

```sh
$ python -m depinventory sbom new-lock.json --with-paths --reachable
{"format": "depinventory-sbom", "formatVersion": 1, "components": [{"name": "beta", "version": "2.1.0", "ecosystem": "npm", "direct": true, "license": "unknown", "securityStatus": "unknown", "path": ["$root", "beta"]}]}
```

只有根节点时 `components` 仍为 `[]`，不受 `--with-paths` 影响。

带 `--with-dependencies` 时每个组件额外携带 `dependencies` 数组。
`sample-lock.json` 中 `alpha` 声明 `beta`、`beta` 与 `leaf` 互相声明
（循环双方各自保留）、`orphan` 声明 `leaf`、`isolated` 无声明：

```sh
$ python -m depinventory sbom sample-lock.json --with-dependencies
{"format": "depinventory-sbom", "formatVersion": 1, "components": [{"name": "alpha", "version": "1.0.0", "ecosystem": "npm", "direct": true, "license": "unknown", "securityStatus": "unknown", "dependencies": ["beta"]}, {"name": "beta", "version": "1.0.0", "ecosystem": "npm", "direct": false, "license": "unknown", "securityStatus": "unknown", "dependencies": ["leaf"]}, {"name": "isolated", "version": "1.0.0", "ecosystem": "npm", "direct": false, "license": "unknown", "securityStatus": "unknown", "dependencies": []}, {"name": "leaf", "version": "1.0.0", "ecosystem": "npm", "direct": false, "license": "unknown", "securityStatus": "unknown", "dependencies": ["beta"]}, {"name": "orphan", "version": "1.0.0", "ecosystem": "npm", "direct": false, "license": "unknown", "securityStatus": "unknown", "dependencies": ["leaf"]}]}
```

与 `--reachable` 组合只保留 `alpha`、`beta`、`leaf`，其 `dependencies`
数组不变；与 `--with-paths` 组合时 `path` 与 `dependencies` 同时附加。

## 成功与失败的输出约定

成功时：

- 退出码为 `0`，标准错误为空；
- 标准输出为单个 JSON 文档及末尾一个换行；
- 不自动落盘，不写任何文件。

失败时（`load_lockfile` 抛 `InputError` 的情形）：文件不可读、非法
UTF-8、JSON 损坏、缺少包版本、悬空依赖、嵌套路径或 `link: true` 条目，
命令退出码为 `2`，标准输出为空，标准错误仅含 `INPUT_ERROR` 和一个换行，
不输出部分结果或堆栈。带 `--reachable` 或 `--with-paths` 时同样在筛选、
附路径前校验整份输入：不可达条目的缺失版本、非法 `dependencies` 或悬空
依赖也导致整体失败。带 `--with-dependencies` 时同样在附加数组前校验
整份输入。

## 兼容性

`sbom` 为只读入口：`sbom_document(root_deps, packages_map)` 的两参数
调用及结果保持不变，`--reachable` 只缩小 `components` 范围，`--with-paths`
只在带该选项时为组件增加 `path` 字段，`--with-dependencies` 只在带该
选项时为组件增加 `dependencies` 字段，均不改变文档格式与既有字段含义；
`reachable` 的位置参数与关键字调用（`sbom_document(rd, pm, True)`、
`sbom_document(rd, pm, reachable=True)`）继续有效，`with_paths` 与
`with_dependencies` 默认 `False`。现有样例文件及 `list`、`why`、
`why --from`、`diff` 的接口与结果均保持不变。导出只读：不改写输入
文件、不联网、不安装或执行依赖。
