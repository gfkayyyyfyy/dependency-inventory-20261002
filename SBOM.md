# 简化 SBOM 导出说明

本文档说明 `sbom` 命令（含 `--reachable` 筛选选项与 `--with-paths`
来源路径选项）与 `sbom_document` 函数的行为。导出结果为产品自有格式，
**不声明符合 CycloneDX 或 SPDX** 等任何标准 SBOM 规范。

## 数据流与源码对应关系

```text
锁文件（本地 JSON）
   │  python -m depinventory sbom <lockfile> [--reachable] [--with-paths]
   ▼
load_lockfile(path)                      depinventory/lockfile.py
   │  读取并校验，返回 (root_deps, packages_map)；
   │  任何读取、解析或结构问题抛 InputError
   ▼
sbom_document(root_deps, packages_map, reachable=False, with_paths=False)
                                         depinventory/lockfile.py
   │  纯函数：由已校验数据生成组件列表，返回 SBOM 文档字典；
   │  带 --reachable 时以 reachable=True 调用，只保留可达组件；
   │  带 --with-paths 时以 with_paths=True 调用，为每个组件附加 path
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

每个组件对象的字段（省略 `--with-paths` 时只有前六个字段）：

| 字段 | 含义 |
| --- | --- |
| `name` | 完整包名，原样保留（含 `@scope/name`） |
| `version` | 版本字符串，原样保留，不解析版本范围 |
| `ecosystem` | 固定为 `"npm"` |
| `direct` | 仅取决于根节点 `dependencies` 是否声明该包，与其他包的依赖关系无关 |
| `license` | 固定为 `"unknown"`；即使输入附带许可证信息也不解读 |
| `securityStatus` | 固定为 `"unknown"`；未知不代表没有风险，也不依据版本推断安全结论 |
| `path` | 仅在带 `--with-paths`（`with_paths=True`）时出现；含义见下节 |

### `path` 来源路径（`--with-paths`）

带 `--with-paths` 时每个组件在上述字段之外追加一个 `path` 数组，语义与
省略 `--from` 的 `why` 默认查询完全一致：

- 从根标记 `"$root"` 开始，到该组件的完整包名结束，只沿各条目的
  `dependencies` 取边；不解析版本范围，也不从其他元数据补充连边；
- 取边数最少的路径；等长时按**包名序列**的 Unicode 码点字典序取第一条，
  结果不受条目与依赖声明顺序影响；
- 根节点直接声明的包，路径只经过根标记与该包名，如
  `["$root", "alpha"]`；经传递引入的包逐层列出，如
  `["$root", "alpha", "beta"]`；
- 已安装但自根节点不可达的包，`path` 为空数组 `[]`。完整导出仍保留这些
  组件（只是路径为空）；联合 `--reachable` 时这类组件本就被筛选掉，因此
  只为筛选后保留的可达组件附加非空路径；
- 作用域包 `@scope/name` 作为单个路径元素；自环、循环和被多条路径共享的
  依赖都正常结束，每条路径中各组件只出现一次；
- 路径只反映来源关系，不改变组件排序、`direct` 含义、`name`/`version`
  原文或其他字段；根项目始终不作为组件。

省略 `--with-paths`（即 `with_paths=False`，默认值）时组件不含 `path`
字段，文档与既有导出逐字段一致。

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

带 `--with-paths` 时每个组件附加 `path`。`demo-lock.json` 中 `alpha`
为根直接依赖、`beta` 经 `alpha` 传递引入：

```sh
$ python -m depinventory sbom demo-lock.json --with-paths
{"format": "depinventory-sbom", "formatVersion": 1, "components": [{"name": "alpha", "version": "1.0.0", "ecosystem": "npm", "direct": true, "license": "unknown", "securityStatus": "unknown", "path": ["$root", "alpha"]}, {"name": "beta", "version": "2.0.0", "ecosystem": "npm", "direct": false, "license": "unknown", "securityStatus": "unknown", "path": ["$root", "alpha", "beta"]}]}
```

`new-lock.json` 中 `beta` 改为根直接依赖，`gamma` 已安装但根不可达，
其 `path` 为 `[]`：

```sh
$ python -m depinventory sbom new-lock.json --with-paths
{"format": "depinventory-sbom", "formatVersion": 1, "components": [{"name": "beta", "version": "2.1.0", "ecosystem": "npm", "direct": true, "license": "unknown", "securityStatus": "unknown", "path": ["$root", "beta"]}, {"name": "gamma", "version": "3.0.0", "ecosystem": "npm", "direct": false, "license": "unknown", "securityStatus": "unknown", "path": []}]}
```

`--with-paths` 可与 `--reachable` 组合：先筛选、只为保留的可达组件附加
路径，上例中的 `gamma` 不再出现：

```sh
$ python -m depinventory sbom new-lock.json --reachable --with-paths
{"format": "depinventory-sbom", "formatVersion": 1, "components": [{"name": "beta", "version": "2.1.0", "ecosystem": "npm", "direct": true, "license": "unknown", "securityStatus": "unknown", "path": ["$root", "beta"]}]}
```

## 成功与失败的输出约定

成功时：

- 退出码为 `0`，标准错误为空；
- 标准输出为单个 JSON 文档及末尾一个换行；
- 不自动落盘，不写任何文件。

失败时（`load_lockfile` 抛 `InputError` 的情形）：文件不可读、非法
UTF-8、JSON 损坏、缺少包版本、悬空依赖、嵌套路径或 `link: true` 条目，
命令退出码为 `2`，标准输出为空，标准错误仅含 `INPUT_ERROR` 和一个换行，
不输出部分结果或堆栈。带 `--reachable` 时同样在筛选前校验整份输入：
不可达条目的缺失版本、非法 `dependencies` 或悬空依赖也导致整体失败。
`--with-paths` 只在已校验数据上读取 `dependencies` 关系，不放宽任何
校验：不可达包的缺失版本、悬空依赖等问题仍使整份输入失败，路径选项
本身不引入新的失败情形。

## 兼容性

`sbom` 为只读入口：`sbom_document(root_deps, packages_map)` 的两参数
调用及结果保持不变，`--reachable` 只缩小 `components` 范围，不改变文档
格式与字段含义；`--with-paths`（`with_paths` 默认 `False`）只在显式
开启时为组件追加 `path`，省略时输出与既有导出完全一致，`reachable`
的位置参数与关键字调用继续有效。现有样例文件及 `list`、`why`、
`why --from`、`parents`、`ancestors`、`descendants`、`diff` 的接口与
结果均保持不变；导出不改写输入文件、不联网、不安装或执行任何依赖。
