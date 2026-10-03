# 软件依赖清单分析器

本地分析 npm 锁文件（`package-lock.json`），仅使用 Python 3 标准库，不联网、
不安装或执行任何依赖，也不改写输入文件。

## 使用方法

在项目根目录运行：

```sh
# 列出全部已安装条目（不含根节点），按名称 Unicode 码点排序
python -m depinventory list demo-lock.json

# 只列出自根节点沿 dependencies 可达的条目
python -m depinventory list demo-lock.json --reachable

# 查询某包自根节点的最短依赖来源路径（区分大小写的完整包名）
python -m depinventory why demo-lock.json beta

# 以指定已安装包为起点，查询它到目标包之间的最短路径
python -m depinventory why demo-lock.json beta --from alpha

# 查询某包被哪些已安装包直接声明（直接上游，不去重根可达性）
python -m depinventory parents demo-lock.json beta

# 只列出自根节点沿 dependencies 可达的直接上游
python -m depinventory parents parents-lock.json beta --reachable

# 比较两份清单的版本差异（参数顺序决定方向：旧清单在前）
python -m depinventory diff demo-lock.json new-lock.json

# 只比较两份清单各自自根节点沿 dependencies 可达的条目
python -m depinventory diff demo-lock.json new-lock.json --reachable
```

`list` 向标准输出写 JSON 数组，每项仅含 `name`、`version`、`direct`
（`direct` 表示是否被根节点直接声明）；空清单输出 `[]`。带 `--reachable`
时只保留自根节点沿 `dependencies` 可达的包：根直接声明的包及其逐层依赖
都保留，根项目本身不输出；多条路径引入同一包只输出一次，可达的自环或
循环正常结束并保留相关包，完全脱离根节点的包（含与根断开的循环）整体
排除。可达性只看 `dependencies`，不解析版本范围，也不从其他元数据补充
连边；省略该选项时输出原有完整清单。

`why` 输出仅含 `name` 和 `path` 的 JSON 对象；`path` 为从 `$root` 到目标的
包名数组，取边数最少的路径，同长度按包名序列的 Unicode 码点字典序取第一条。
包已安装但根节点不可达时输出空路径 `[]`。

带 `--from 包名` 时改以该已安装包为查询起点：`path` 从起点包开始、到目标包
结束，不含 `$root` 标记；起点即使不能从根项目到达也沿其自身依赖查询。起点与
目标相同返回只含该包名的数组；两包均已安装但起点无法到达目标时返回 `[]`。
起点与目标都按区分大小写的完整已安装包名匹配（支持 `@scope/name`），
`--from` 的字面值始终表示安装包，`$root` 也按普通包名查找，不是虚拟根别名；
起点或目标未安装时以 `NOT_FOUND` 失败。

`parents` 输出仅含 `name`、`direct`、`parents` 的 JSON 对象；`name` 原样
保留查询名。`direct` 表示根节点 `dependencies` 是否声明目标包；根项目只
影响 `direct`，不作为 `parents` 的成员。`parents` 是自身 `dependencies`
中直接声明目标的全部已安装包名（只取直接边，不解析版本范围，不补入其他
字段的依赖，也不列出更远的祖先），按完整包名的 Unicode 码点升序排列并
去重。查询覆盖整份清单，包括从根节点不可达的包：与根断开的包只要直接
声明目标同样进入结果。没有上游的已安装目标返回空 `parents`；自环保留
目标自身，循环关系正常结束。包名按区分大小写的完整名匹配（支持
`@scope/name`），字面值 `$root` 仍表示名为 `$root` 的普通已安装包；目标
未安装时以 `NOT_FOUND` 失败。

带 `--reachable` 时只保留自根节点沿 `dependencies` 可达的直接上游（规则与
`list --reachable` 相同）：不可达的声明者被排除，目标自身仅在可达且声明
自身时进入 `parents`；已安装但不可达的目标仍成功返回 `direct` 为 `false`、
`parents` 为 `[]`。该选项只限制 `parents` 成员，`direct` 含义不变；省略时
输出原有完整查询结果。筛选不放宽校验，不可达包的结构错误同样使整份输入
失败。

`diff` 输出 JSON 数组，每项仅含 `name`、`change`、`before`、`after`。
比较两份清单的全部已安装条目（含根节点不可达的包，不含根项目）：
仅新清单存在的标记 `added`（`before` 为 `null`），仅旧清单存在的标记
`removed`（`after` 为 `null`），两边版本字符串不同的标记 `changed`
（两个版本原样保留）。按包名 Unicode 码点升序排列，每个包最多出现一次；
版本完全相同的包不输出，依赖声明、直接/传递身份、根项目版本及其他元数据
变化不产生记录；不解析版本范围，也不判断升级、降级或安全风险。
同一文件与自身比较或两边均只有根节点时输出 `[]`。

带 `--reachable` 时，两份清单各自从根节点 `dependencies` 出发、沿包条目的
`dependencies` 逐层确定可达集合（规则与 `list --reachable` 相同），只比较
各自可达的条目：仅新侧可达的包标记 `added`，仅旧侧可达的包标记 `removed`，
即使包两侧都安装且版本相同也如此；两侧都可达时仍仅版本字符串不同才标记
`changed`。根项目不参与比较；筛选不放宽校验，不可达包的结构错误同样使
整份输入失败。省略该选项时行为不变。

## 支持范围

- UTF-8 编码的严格 JSON：非标准数值常量 `NaN`、`Infinity`、`-Infinity`
  不得作为值出现在任意层级（包括不参与分析的额外元数据、根节点不可达
  包内），违反即整份拒绝；字符串值 `"NaN"`/`"Infinity"`/`"-Infinity"`、
  含这些文字的描述及同名对象键按普通文本处理，合法数字（含 `1e999`
  这样超出浮点范围的指数文本）仍可读取；
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
| 1 | 查询的包不存在（`why`、`parents`） | `NOT_FOUND` |
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
$ python -m depinventory why demo-lock.json beta --from alpha
{"name": "beta", "path": ["alpha", "beta"]}
$ python -m depinventory why demo-lock.json alpha --from beta
{"name": "alpha", "path": []}
$ python -m depinventory parents demo-lock.json beta
{"name": "beta", "direct": false, "parents": ["alpha"]}
$ python -m depinventory parents new-lock.json beta
{"name": "beta", "direct": true, "parents": []}
$ python -m depinventory parents parents-lock.json beta
{"name": "beta", "direct": false, "parents": ["alpha", "orphan"]}
$ python -m depinventory parents parents-lock.json beta --reachable
{"name": "beta", "direct": false, "parents": ["alpha"]}
$ python -m depinventory diff demo-lock.json new-lock.json
[{"name": "alpha", "change": "removed", "before": "1.0.0", "after": null}, {"name": "beta", "change": "changed", "before": "2.0.0", "after": "2.1.0"}, {"name": "gamma", "change": "added", "before": null, "after": "3.0.0"}]
$ python -m depinventory diff demo-lock.json new-lock.json --reachable
[{"name": "alpha", "change": "removed", "before": "1.0.0", "after": null}, {"name": "beta", "change": "changed", "before": "2.0.0", "after": "2.1.0"}]
```

`why beta` 的 `path` 为 `["$root", "alpha", "beta"]`。`new-lock.json` 是
`demo-lock.json` 的演进版：根依赖改为 `beta@2.1.0`，移除 `alpha`，并加入
未被引用的 `gamma@3.0.0`。`parents-lock.json` 中根节点只声明
`alpha@1.0.0`，`alpha` 与不可达的 `orphan` 都声明 `beta@1.0.0`，用于演示
`parents --reachable` 对不可达直接上游的筛选。
