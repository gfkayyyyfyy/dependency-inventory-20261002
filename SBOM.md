# 简化 SBOM 导出说明

本文档说明 `sbom` 命令与 `sbom_document` 函数的现有行为，仅作文档化，
不改变任何产品行为。导出结果为产品自有格式，**不声明符合 CycloneDX 或
SPDX** 等任何标准 SBOM 规范。

## 数据流与源码对应关系

```text
锁文件（本地 JSON）
   │  python -m depinventory sbom <lockfile>
   ▼
load_lockfile(path)                      depinventory/lockfile.py
   │  读取并校验，返回 (root_deps, packages_map)；
   │  任何读取、解析或结构问题抛 InputError
   ▼
sbom_document(root_deps, packages_map)   depinventory/lockfile.py
   │  纯函数：由已校验数据生成组件列表，返回 SBOM 文档字典
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

- 覆盖**全部已安装包**（`packages_map` 中的每个条目），不包含根项目；
- 按完整包名的 Unicode 码点升序排列，每个包只出现一次；
- 根节点不可达的包仍保留在结果中；
- 循环依赖不产生重复组件（组件直接来自 `packages_map`，天然不重复）；
- 锁文件只有根节点时，`components` 为空数组 `[]`。

## `--reachable` 筛选

`sbom` 支持与 `list --reachable` 语义一致的筛选选项：

```sh
python -m depinventory sbom <lockfile> --reachable
```

带 `--reachable` 时只缩小 `components` 的范围，文档结构、字段与含义均不
改变：

- 筛选自根节点 `dependencies` 开始，沿已安装包的 `dependencies` 逐层
  判断可达性；不解析版本范围，也不从其他元数据补充连边；
- 根项目不成为组件；直接和传递依赖均保留，多条路径引入同一包只输出
  一次；可达的自环与循环正常结束并保留相关包，与根断开的包和循环
  全部排除；
- 根 `dependencies` 省略、为空对象或只有根节点时，`components` 为
  `[]`，`format` 与 `formatVersion` 标记仍保留；
- `direct` 仍仅取决于根节点 `dependencies` 是否声明该包；
- 筛选发生在**整份输入校验之后**：不可达条目的缺失版本、非法
  `dependencies` 或悬空依赖同样导致整体失败（退出码 2），不会先产出
  部分组件；
- 省略该选项时行为与之前完全一致：导出全部已安装包。

对应函数级接口为 `sbom_document_reachable(root_deps, packages_map)`；
`sbom_document` 的既有两参数调用与结果保持不变。

每个组件对象的字段：

| 字段 | 含义 |
| --- | --- |
| `name` | 完整包名，原样保留（含 `@scope/name`） |
| `version` | 版本字符串，原样保留，不解析版本范围 |
| `ecosystem` | 固定为 `"npm"` |
| `direct` | 仅取决于根节点 `dependencies` 是否声明该包，与其他包的依赖关系无关 |
| `license` | 固定为 `"unknown"`；即使输入附带许可证信息也不解读 |
| `securityStatus` | 固定为 `"unknown"`；未知不代表没有风险，也不依据版本推断安全结论 |

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

同一份 `new-lock.json` 加 `--reachable` 时只保留根可达的 `beta`：

```sh
$ python -m depinventory sbom new-lock.json --reachable
{"format": "depinventory-sbom", "formatVersion": 1, "components": [{"name": "beta", "version": "2.1.0", "ecosystem": "npm", "direct": true, "license": "unknown", "securityStatus": "unknown"}]}
```

## 成功与失败的输出约定

成功时：

- 退出码为 `0`，标准错误为空；
- 标准输出为单个 JSON 文档及末尾一个换行；
- 不自动落盘，不写任何文件。

失败时（`load_lockfile` 抛 `InputError` 的情形）：文件不可读、非法
UTF-8、JSON 损坏、缺少包版本、悬空依赖、嵌套路径或 `link: true` 条目，
命令退出码为 `2`，标准输出为空，标准错误仅含 `INPUT_ERROR` 和一个换行，
不输出部分结果或堆栈。

## 兼容性

`sbom --reachable` 为新增只读筛选项：省略时 `sbom` 的接口与结果保持
不变，`sbom_document` 的两参数调用与结果保持不变；现有样例文件及
`list`、`why`、`why --from`、`diff` 的接口与结果均保持不变。
