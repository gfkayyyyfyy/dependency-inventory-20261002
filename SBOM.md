# 简化 SBOM 导出说明

本文档说明 `sbom` 命令（含 `--reachable` 筛选、`--with-paths` 路径、
`--with-dependencies` 直接依赖与 `--with-purl` Package URL 选项）与
`sbom_document` 函数的行为。
导出结果为产品自有格式，**不声明符合 CycloneDX 或 SPDX** 等任何标准
SBOM 规范。

## 数据流与源码对应关系

```text
锁文件（本地 JSON）
   │  python -m depinventory sbom <lockfile> [--reachable] [--with-paths]
   │                                          [--with-dependencies]
   │                                          [--with-purl]
   ▼
load_lockfile(path)                      depinventory/lockfile.py
   │  读取并校验，返回 (root_deps, packages_map)；
   │  任何读取、解析或结构问题抛 InputError
   ▼
sbom_document(root_deps, packages_map, reachable=False, with_paths=False,
              with_dependencies=False, with_purl=False)
                                         depinventory/lockfile.py
   │  纯函数：由已校验数据生成组件列表，返回 SBOM 文档字典；
   │  带 --reachable 时以 reachable=True 调用，只保留可达组件；
   │  带 --with-paths 时以 with_paths=True 调用，为每个组件附加 path；
   │  带 --with-dependencies 时以 with_dependencies=True 调用，
   │  为每个组件附加 dependencies；
   │  带 --with-purl 时以 with_purl=True 调用，为每个组件附加 purl
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
| `purl` | **仅在 `--with-purl`（`with_purl=True`）时出现**：由完整包名与原始版本生成的 npm Package URL 字符串 |

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
时，每个组件附带一个 `dependencies` 字段；省略该选项时组件对象不含
`dependencies`，输出与旧版本逐字段一致。

- 数组列出该包条目 `dependencies` **直接声明**的完整包名：不展开传递
  依赖、不附带版本范围，也不从其他元数据补充连边；
- 按完整包名的 Unicode 码点升序排列并去重，区分大小写；作用域包名
  （`@scope/name`）作为整体保留为单个元素；
- 声明省略或为空对象时输出 `[]`；
- 自环保留自身（包声明自身时自身出现在数组中），循环双方分别保留各自
  的声明；结果不产生重复组件，根项目也不会成为组件；
- 与 `--reachable` 组合时仅为筛选后保留的组件附加该数组，被排除组件
  的关系不影响保留组件的数组内容；与 `--with-paths` 可同时启用，两个
  附加字段互不影响；
- 数组内容不受 packages 条目顺序和 `dependencies` 声明顺序影响；
  名称、版本原样保留，`direct` 的含义不因该选项改变。

### `purl` 的语义

仅当命令带 `--with-purl`（函数以 `with_purl=True` 调用）时，每个组件
附带一个 `purl` 字符串字段；省略该选项时组件对象不含 `purl`，输出与
旧版本逐字段一致。字段排在既有字段（含 `dependencies`、`path` 等
其他已启用的附加字段）之后，文档格式与既有组件字段保持原样。

- 标识只使用组件的完整包名与原始版本，不解析版本范围、不改大小写，
  也不从许可证等其他元数据补充标识；`name` 与 `version` 字段原样保留；
- 普通包为 `pkg:npm/包名@版本`，例如 `alpha` 的 `1.0.0` 输出
  `pkg:npm/alpha@1.0.0`；
- 作用域包为 `pkg:npm/作用域/包名@版本`，作用域包含开头的 `@`，
  该 `@` 与其他普通字节一样编码，例如 `@scope/leaf` 的 `2.0.0` 输出
  `pkg:npm/%40scope/leaf@2.0.0`；
- 各名称片段（作用域、包名）与版本按 **UTF-8 字节**作百分号编码：
  除 ASCII 字母、数字及 `-._~` 外的每个字节写成 `%XX`，十六进制字母
  大写；结构分隔的 `/`（作用域与包名之间）与 `@`（版本前）保留不编码；
  空格、`+`、`%` 分别编码为 `%20`、`%2B`、`%25`；输入中已有的百分号
  文本一律当作普通字符 `%` 编码，不作任何解码；
- 与 `--reachable`、`--with-paths`、`--with-dependencies` 均可同时
  启用，互不影响：筛选、排序与其他附加字段语义不变；完整导出时根不可达
  的包同样获得标识，根项目仍不成为组件；空清单时 `components` 仍为 `[]`；
- 名称或版本文本无法按 UTF-8 编码时（如字符串中出现 lone surrogate），
  `sbom_document` 抛 `InputError`，命令按输入错误处理（退出码 `2`）。

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

带 `--with-dependencies` 时每个组件额外携带 `dependencies`。
`sample-lock.json` 中根声明 `alpha`，`alpha`→`beta`→`leaf`，`leaf`
回指 `beta` 构成循环，`orphan` 依赖 `leaf` 但根不可达，`isolated`
没有任何依赖声明：

```sh
$ python -m depinventory sbom sample-lock.json --with-dependencies
{"format": "depinventory-sbom", "formatVersion": 1, "components": [{"name": "alpha", "version": "1.0.0", "ecosystem": "npm", "direct": true, "license": "unknown", "securityStatus": "unknown", "dependencies": ["beta"]}, {"name": "beta", "version": "1.0.0", "ecosystem": "npm", "direct": false, "license": "unknown", "securityStatus": "unknown", "dependencies": ["leaf"]}, {"name": "isolated", "version": "1.0.0", "ecosystem": "npm", "direct": false, "license": "unknown", "securityStatus": "unknown", "dependencies": []}, {"name": "leaf", "version": "1.0.0", "ecosystem": "npm", "direct": false, "license": "unknown", "securityStatus": "unknown", "dependencies": ["beta"]}, {"name": "orphan", "version": "1.0.0", "ecosystem": "npm", "direct": false, "license": "unknown", "securityStatus": "unknown", "dependencies": ["leaf"]}]}
```

与 `--reachable` 组合时只保留 `alpha`、`beta`、`leaf`，保留组件的
`dependencies` 数组不变；与 `--with-paths` 组合时两个附加字段同时出现。

带 `--with-purl` 时每个组件额外携带 `purl`。`loader-example.json` 中
根声明 `alpha`，`alpha` 依赖 `@scope/leaf`，`orphan` 已安装但根不可达：

```sh
$ python -m depinventory sbom loader-example.json --with-purl
{"format": "depinventory-sbom", "formatVersion": 1, "components": [{"name": "@scope/leaf", "version": "2.0.0", "ecosystem": "npm", "direct": false, "license": "unknown", "securityStatus": "unknown", "purl": "pkg:npm/%40scope/leaf@2.0.0"}, {"name": "alpha", "version": "1.0.0", "ecosystem": "npm", "direct": true, "license": "unknown", "securityStatus": "unknown", "purl": "pkg:npm/alpha@1.0.0"}, {"name": "orphan", "version": "3.0.0", "ecosystem": "npm", "direct": false, "license": "unknown", "securityStatus": "unknown", "purl": "pkg:npm/orphan@3.0.0"}]}
```

`@scope/leaf` 的作用域 `@` 编码为 `%40`；`orphan` 虽根不可达仍获得标识。
与 `--reachable`、`--with-paths`、`--with-dependencies` 组合时，筛选、
排序及其他附加字段语义不变，`purl` 始终由该组件原始的完整包名与版本生成。

只有根节点时 `components` 仍为 `[]`，不受 `--with-paths`、`--with-purl`
与 `--with-dependencies` 影响。

## 成功与失败的输出约定

成功时：

- 退出码为 `0`，标准错误为空；
- 标准输出为单个 JSON 文档及末尾一个换行；
- 不自动落盘，不写任何文件。

失败时（`load_lockfile` 抛 `InputError` 的情形）：文件不可读、非法
UTF-8、JSON 损坏、缺少包版本、悬空依赖、嵌套路径或 `link: true` 条目，
命令退出码为 `2`，标准输出为空，标准错误仅含 `INPUT_ERROR` 和一个换行，
不输出部分结果或堆栈。带 `--reachable`、`--with-paths`、
`--with-dependencies` 或 `--with-purl` 时同样在筛选、附加字段前校验
整份输入：不可达条目的缺失版本、非法 `dependencies` 或悬空依赖也导致
整体失败；`--with-purl` 下若组件的名称或版本无法按 UTF-8 编码生成标识，
同样按输入错误处理，退出码 `2`，不输出部分结果。

## 兼容性

`sbom` 为只读入口：`sbom_document(root_deps, packages_map)` 的两参数
调用及结果保持不变，`--reachable` 只缩小 `components` 范围，
`--with-paths` 只在带该选项时为组件增加 `path` 字段，
`--with-dependencies` 只在带该选项时为组件增加 `dependencies` 字段，
`--with-purl` 只在带该选项时为组件增加字符串 `purl` 字段，
均不改变文档格式与既有字段含义；`reachable` 的位置参数与关键字调用
（`sbom_document(rd, pm, True)`、`sbom_document(rd, pm, reachable=True)`）
继续有效，`with_paths`、`with_dependencies` 与 `with_purl` 均默认
`False`，既有位置参数语义不变。现有样例文件及 `list`、`why`、
`why --from`、`diff` 的接口与结果均保持不变。
导出只读：不改写输入文件、不联网、不安装或执行依赖。
