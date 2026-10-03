# 简化 SBOM 导出说明

本文档说明 `python -m depinventory sbom` 子命令与 `sbom_document` 函数的现有
行为，仅作文档补充，不改变任何产品行为。`list`、`why`、`why --from`、`diff`
的接口与结果保持不变，仍以 README 为准。

## 处理流程与源码对应关系

一次 `sbom` 导出分为三个阶段，全部只读、不联网、不改写输入文件：

1. **读取与校验**：`depinventory/lockfile.py` 的 `load_lockfile(path)` 读取
   本地锁文件并完成全部结构与字段校验，返回
   `(root_deps, packages_map)`——根节点 `dependencies` 声明的包名列表，以及
   `{包名: {"version", "deps"}}` 映射。任何读取、解析或结构问题都在此阶段
   抛出 `InputError`，后续阶段不会执行。
2. **生成组件**：同一文件的 `sbom_document(root_deps, packages_map)` 根据
   上述两个返回值构造文档对象，不再次访问文件系统，也不修改入参。
3. **命令行输出**：`depinventory/__main__.py` 的 `main` 把该文档对象用
   `json.dumps(..., ensure_ascii=False)` 序列化后写入标准输出。

注意区分两层结果：`sbom_document` 的**函数返回值**是 Python 字典
（`formatVersion` 为整数 `1`、`direct` 为布尔值）；命令行的**输出结果**是
该字典序列化后的单个 JSON 文本文档（`formatVersion` 呈现为 `1`，`direct`
呈现为 `true`/`false`），两者内容一一对应。

## 文档格式

本格式为产品自有格式，`format` 固定为字符串 `"depinventory-sbom"`，
`formatVersion` 固定为整数 `1`。**不声明符合 CycloneDX 或 SPDX** 等任何
SBOM 标准。顶层仅含三个键：

```json
{"format": "depinventory-sbom", "formatVersion": 1, "components": [...]}
```

`components` 的规则：

- 覆盖**全部已安装包**（不含根项目自身），每个包名只出现一次；
- 按完整包名的 Unicode 码点升序排列（如 `@scope/name` 中 `@` 排在字母之前）；
- 根节点不可达的包（已安装但没有任何依赖链引用）**仍然保留**；
- 循环依赖不产生重复组件（组件直接来自包映射，天然不重复）；
- 锁文件只有根节点时，`components` 为空数组 `[]`，格式标记不变。

每个组件恰好含六个字段：

| 字段 | 取值 |
| --- | --- |
| `name` | 完整包名，原样保留 |
| `version` | 版本字符串，原样保留（不解析版本范围） |
| `ecosystem` | 固定为 `"npm"` |
| `direct` | 布尔值，仅取决于根节点 `dependencies` 是否声明该包，与其他包的依赖关系无关 |
| `license` | 固定为 `"unknown"` |
| `securityStatus` | 固定为 `"unknown"` |

`license` 与 `securityStatus` 不做任何解读：即使输入文件附带许可证信息也
不读取。**`unknown` 不代表没有风险**，也不依据版本号推断任何安全结论。

## 支持范围

与 README 公开的支持范围一致：UTF-8 编码的严格 JSON（拒绝裸
`NaN`/`Infinity`/`-Infinity`）、`lockfileVersion` 为整数 `3`、`packages`
含空串根节点、平铺安装路径 `node_modules/name` 与
`node_modules/@scope/name`、版本为非空字符串、只按 `dependencies` 建边；
嵌套安装路径与 `link: true` 条目不支持。

## 本地演示

仓库自带两份样例。`demo-lock.json`：根节点依赖 `alpha@1.0.0`，`alpha`
依赖 `beta@2.0.0`。`new-lock.json`：根依赖改为 `beta@2.1.0`，并加入未被
任何节点引用的 `gamma@3.0.0`。

```sh
$ python -m depinventory sbom demo-lock.json
{"format": "depinventory-sbom", "formatVersion": 1, "components": [{"name": "alpha", "version": "1.0.0", "ecosystem": "npm", "direct": true, "license": "unknown", "securityStatus": "unknown"}, {"name": "beta", "version": "2.0.0", "ecosystem": "npm", "direct": false, "license": "unknown", "securityStatus": "unknown"}]}
$ python -m depinventory sbom new-lock.json
{"format": "depinventory-sbom", "formatVersion": 1, "components": [{"name": "beta", "version": "2.1.0", "ecosystem": "npm", "direct": true, "license": "unknown", "securityStatus": "unknown"}, {"name": "gamma", "version": "3.0.0", "ecosystem": "npm", "direct": false, "license": "unknown", "securityStatus": "unknown"}]}
```

第一份结果中 `alpha` 被根节点直接声明（`direct: true`），`beta` 仅被
`alpha` 引用（`direct: false`）。第二份结果中 `beta@2.1.0` 为根直接依赖
（`direct: true`）；`gamma@3.0.0` 根节点不可达（`direct: false`），但仍
作为组件导出。

## 退出码与输出约定

成功时：

- 退出码为 `0`，标准错误为空；
- 标准输出为**单个 JSON 文档**，末尾带一个换行符；
- 结果不自动写入任何文件（不落盘），输入文件字节不变。

失败时（文件不可读、非法 UTF-8、JSON 损坏、缺少包版本、悬空依赖、嵌套
安装路径或 `link: true` 条目）：`load_lockfile` 抛出 `InputError`，命令
退出码为 `2`，标准输出为空，标准错误仅含一行 `INPUT_ERROR`（含末尾换行），
不输出部分组件或堆栈。
