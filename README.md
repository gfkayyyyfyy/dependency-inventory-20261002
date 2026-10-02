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
```

`list` 向标准输出写 JSON 数组，每项仅含 `name`、`version`、`direct`
（`direct` 表示是否被根节点直接声明）；空清单输出 `[]`。

`why` 输出仅含 `name` 和 `path` 的 JSON 对象；`path` 为从 `$root` 到目标的
包名数组，取边数最少的路径，同长度按包名序列的 Unicode 码点字典序取第一条。
包已安装但根节点不可达时输出空路径 `[]`。

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

失败时标准输出为空，不输出部分清单或堆栈。

## 样例

仓库自带 `demo-lock.json`：根节点依赖 `alpha@1.0.0`，`alpha@1.0.0` 依赖
`beta@2.0.0`。

```sh
$ python -m depinventory list demo-lock.json
[{"name": "alpha", "version": "1.0.0", "direct": true}, {"name": "beta", "version": "2.0.0", "direct": false}]
$ python -m depinventory why demo-lock.json beta
{"name": "beta", "path": ["$root", "alpha", "beta"]}
```

`why beta` 的 `path` 为 `["$root", "alpha", "beta"]`。
