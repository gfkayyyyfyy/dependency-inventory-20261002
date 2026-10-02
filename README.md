# 软件依赖清单分析器

本地分析 npm 锁文件（`package-lock.json`），仅使用 Python 3 标准库，不联网、
不安装或执行任何依赖，也不改写输入文件。

## 使用方法

在项目根目录运行：

```sh
# 列出全部已安装条目（不含根节点），按名称 Unicode 码点排序
python -m depinventory list demo-lock.json

# 查询某包自根节点的最短依赖来源路径（区分大小写的完整包名）
python -m depinventory why demo-lock.json beta

# 比较两份清单的版本差异（参数顺序决定方向：旧清单在前）
python -m depinventory diff demo-lock.json new-lock.json
```

`list` 向标准输出写 JSON 数组，每项仅含 `name`、`version`、`direct`
（`direct` 表示是否被根节点直接声明）；空清单输出 `[]`。

`why` 输出仅含 `name` 和 `path` 的 JSON 对象；`path` 为从 `$root` 到目标的
包名数组，取边数最少的路径，同长度按包名序列的 Unicode 码点字典序取第一条。
包已安装但根节点不可达时输出空路径 `[]`。

`diff` 输出 JSON 数组，每项仅含 `name`、`change`、`before`、`after`。
比较两份清单的全部已安装条目（含根节点不可达的包，不含根项目）：
仅新清单存在的标记 `added`（`before` 为 `null`），仅旧清单存在的标记
`removed`（`after` 为 `null`），两边版本字符串不同的标记 `changed`
（两个版本原样保留）。按包名 Unicode 码点升序排列，每个包最多出现一次；
版本完全相同的包不输出，依赖声明、直接/传递身份、根项目版本及其他元数据
变化不产生记录；不解析版本范围，也不判断升级、降级或安全风险。
同一文件与自身比较或两边均只有根节点时输出 `[]`。

## 支持范围

- UTF-8 编码的 JSON；
- `lockfileVersion` 为整数 `3`，`packages` 为对象且含空串根节点，条目均为对象；
- 平铺安装路径 `node_modules/name` 与 `node_modules/@scope/name`，
  版本为非空字符串；
- 只按根节点及包条目的 `dependencies` 建立关系（可省略；出现时须为对象且值为
  字符串，声明的包须存在）；不解析版本范围，其余字段不参与连边；
- 嵌套安装路径与 `link: true` 条目不支持。

## 退出码

| 退出码 | 含义 | 标准错误 |
| --- | --- | --- |
| 0 | 成功 | — |
| 1 | 查询的包不存在（`why`） | `NOT_FOUND` |
| 2 | 输入错误（文件不可读、JSON 损坏、结构/版本字段无效、悬空依赖、不支持的结构） | `INPUT_ERROR` |

`diff` 的两份输入都遵循同一套校验规则，任一文件无效即整体失败。
失败时标准输出为空，不输出部分清单或堆栈。

## 样例

仓库自带 `demo-lock.json`：根节点依赖 `alpha@1.0.0`，`alpha@1.0.0` 依赖
`beta@2.0.0`。

```sh
$ python -m depinventory list demo-lock.json
[{"name": "alpha", "version": "1.0.0", "direct": true}, {"name": "beta", "version": "2.0.0", "direct": false}]
$ python -m depinventory why demo-lock.json beta
{"name": "beta", "path": ["$root", "alpha", "beta"]}
$ python -m depinventory diff demo-lock.json new-lock.json
[{"name": "alpha", "change": "removed", "before": "1.0.0", "after": null}, {"name": "beta", "change": "changed", "before": "2.0.0", "after": "2.1.0"}, {"name": "gamma", "change": "added", "before": null, "after": "3.0.0"}]
```

`why beta` 的 `path` 为 `["$root", "alpha", "beta"]`。`new-lock.json` 是
`demo-lock.json` 的演进版：根依赖改为 `beta@2.1.0`，移除 `alpha`，并加入
未被引用的 `gamma@3.0.0`。
