# 软件依赖清单分析器

帮助维护者理解项目依赖的本地分析产品，逐步覆盖一个生态的锁文件解析、直接与传递依赖清单、依赖路径查询、两个版本的差异、重复版本提示和简化 SBOM 导出。

计划采用：Python 3 标准库 / json / pathlib / argparse。

## 当前功能：npm 锁文件清单与来源查询

仅使用 Python 3 标准库，不联网、不安装或执行任何依赖，也不会改写输入文件。

### 支持范围

- UTF-8 编码的 JSON 锁文件；
- `lockfileVersion` 为整数 `3`；
- `packages` 为对象且包含空串 `""` 根节点，根节点及各包条目均为对象；
- 其余条目路径为 `node_modules/name` 或 `node_modules/@scope/name`（平铺安装），名称取自路径，`version` 为非空字符串；
- 只按根节点及包条目的 `dependencies` 建立关系：可省略，出现时须为对象且值为字符串，声明的包必须存在；
- 不解析版本范围，其余字段不参与连边；
- 嵌套安装路径与 `link: true` 条目不在支持范围。

### 用法

在项目根目录执行：

```sh
# 列出全部已安装条目（不含根节点），按名称 Unicode 码点序排列
python -m depinventory list demo-lock.json

# 查询某包从根节点出发的最短依赖路径
python -m depinventory why demo-lock.json beta
```

`list` 输出 JSON 数组，每项仅含 `name`、`version`、`direct`（是否被根节点直接声明）；空清单输出 `[]`。

`why` 按区分大小写的完整包名查询，输出仅含 `name` 和 `path` 的 JSON 对象：

- `path` 为从 `$root` 到目标的包名数组，取边数最少的路径；同长度时按包名序列的 Unicode 码点字典序取第一条；
- 循环依赖不会使查询无限进行；
- 包已安装但从根节点不可达时，`path` 为空数组 `[]`。

退出码：

| 情况 | 退出码 | 标准错误 |
| --- | --- | --- |
| 成功 | 0 | 空 |
| 查询的包不存在 | 1 | 一行 `NOT_FOUND` |
| 文件不可读取、JSON 损坏、结构/字段无效、声明指向不存在的包等 | 2 | 一行 `INPUT_ERROR` |

任何失败都不会输出部分清单或堆栈。

### 示例

仓库附带 `demo-lock.json`：根节点依赖 `alpha@1.0.0`，`alpha@1.0.0` 依赖 `beta@2.0.0`。

```sh
$ python -m depinventory list demo-lock.json
[{"name": "alpha", "version": "1.0.0", "direct": true}, {"name": "beta", "version": "2.0.0", "direct": false}]

$ python -m depinventory why demo-lock.json beta
{"name": "beta", "path": ["$root", "alpha", "beta"]}
```
