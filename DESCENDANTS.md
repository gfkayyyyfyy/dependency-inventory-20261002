# 全部下游查询（descendants）流程说明

本文说明 `python -m depinventory descendants <lockfile> <name>` 从本地锁文件
到 JSON 结果的完整数据流，并用文件名与函数名定位源码，便于核对一个已安装包
带入了哪些直接与传递依赖。行为约定与 README 一致；本文只解释现有行为，不改变
任何产品代码、公开接口、README、其他说明文档与样例文件。工具只读本地输入文件，
不联网。

## 总览：从命令行到 JSON 输出

一次 descendants 查询依次经过五个阶段，全部在 `depinventory/__main__.py` 的
`main()` 与 `depinventory/lockfile.py` 中完成：

1. **参数解析**：`__main__.py` 的 `_build_parser()` 定义 `descendants` 子命令
   （`descendants_parser`），两个位置参数依次为锁文件路径 `args.lockfile` 与
   目标包名 `args.name`；该子命令没有 `--reachable`、`--from` 等附加选项。
2. **本地文件读取与整份校验**：`main()` 调用 `lockfile.py` 的
   `load_lockfile(path)`，得到 `(root_deps, packages_map)` 两个数据结构。
3. **目标匹配**：`main()` 把 `root_deps`、`packages_map`、`args.name` 传给
   `lockfile.py` 的公开函数 `find_descendants()`，由它检查目标是否已安装。
4. **依赖展开**：`find_descendants()` 调用同文件的 `_traverse_related()`，沿
   `dependencies` 做广度优先展开，得到目标的全部下游包名（排序后的列表）。
5. **输出包装**：`main()` 把列表包装成仅含 `name` 与 `descendants` 两个字段的
   JSON 对象，`json.dumps(..., ensure_ascii=False)` 后加一个换行写入标准输出，
   返回 0。

## 两个核心数据结构如何进入公开接口

`load_lockfile()` 的返回值是后续所有查询的输入：

- **根依赖 `root_deps`**：字符串列表，取自锁文件根节点（`packages` 中空串
  `""` 条目）的 `dependencies` 的键，按声明顺序保留；根节点省略
  `dependencies` 时为 `[]`。
- **包映射 `packages_map`**：字典，键为完整包名（如 `alpha`、
  `@scope/name`、`$root`），值为
  `{"version": 版本字符串, "deps": [依赖包名...]}`。每个条目来自 `packages`
  中 `node_modules/name` 或 `node_modules/@scope/name` 形式的平铺安装路径，
  `deps` 只取该条目 `dependencies` 的键；版本范围字符串（如 `"*"`、
  `"^1.0.0"`）只在校验时要求存在，不进入 `deps`、也不被解析。

`depinventory/__init__.py` 把 `load_lockfile` 与 `find_descendants` 一并列入
`__all__` 导出；命令行与库使用者拿到的都是这一对数据结构与同一个公开函数：

```python
root_deps, packages_map = load_lockfile(path)
names = find_descendants(root_deps, packages_map, target)
```

`find_descendants(root_deps, packages_map, target)` 中，展开所用的正向邻接就是
`packages_map[name]["deps"]`（见函数内的
`lambda name: packages_map[name]["deps"]`）。`root_deps` 仅为与其他查询函数
保持一致的签名而保留——下游一律从查询包自身展开，不从根节点出发，根项目也不
在 `packages_map` 中，因此根依赖的内容与有无都不影响结果。该函数不修改
`root_deps`、`packages_map` 及其内的 `deps` 列表（回归见
`tests/test_descendants.py` 的 `DescendantsApi.test_does_not_mutate_data`）。

关系的边界：**只有根节点与包条目的 `dependencies` 建立依赖关系**，
`devDependencies`、`peerDependencies`、`optionalDependencies`、`resolved`、
`integrity` 等其余字段一律不补边；**不解析版本范围**，匹配只按完整包名。
支持范围沿用 README 与 LOCKFILE.md 的约定：UTF-8 严格 JSON、
`lockfileVersion` 为整数 3、平铺安装路径、版本为非空字符串；嵌套安装路径与
`link: true` 条目不支持。

## 阶段一：本地文件读取与整份校验

`load_lockfile()`（`lockfile.py`）按顺序执行：

1. 以 UTF-8 文本模式读取文件；`OSError`（文件不可读）与
   `UnicodeDecodeError`（非法 UTF-8）都转为 `InputError`。
2. `json.loads(text, parse_constant=_reject_json_constant)` 解析；裸
   `NaN`/`Infinity`/`-Infinity` 由 `_reject_json_constant()` 拒绝，JSON 损坏
   同样转为 `InputError`。
3. 结构校验：根必须是对象、`lockfileVersion` 必须为整数 3、`packages` 必须是
   含空串根节点的对象；`_entry_name()` 把每个条目键解释为平铺安装路径，不支持
   的键（嵌套路径、空名、畸形作用域）直接拒绝；`link: true`、空版本、重复条目
   也拒绝。
4. `_validate_dependencies()` 对根节点和**每个**包条目检查 `dependencies`：
   可省略，出现时必须是对象、键值均为字符串，且声明的包必须已安装（悬空依赖
   拒绝）。

关键时序：**整份校验先于任何目标检查**。`main()` 先完整跑完
`load_lockfile()`，成功后才进入查询阶段调用 `find_descendants()`；因此即使
结构错误位于根节点不可达、也与查询目标无关的条目上，输入也整体失败，不会先报
包不存在（见 `tests/test_descendants.py` 的
`DescendantsInputErrors.test_invalid_input_beats_missing_query_target` 与
`test_structure_error_in_unreachable_entry_rejects_whole_file`）。

## 阶段二：目标匹配

`find_descendants(root_deps, packages_map, target)`（`lockfile.py`）首先做
成员判断：`target not in packages_map` 时抛 `NotFoundError(target)`。

匹配规则：

- 按**区分大小写的完整包名**精确匹配，不做前缀、子串或大小写折叠。`"LEAF"`
  与 `"leaf"` 是两个名字，大小写不符即视为未安装（见
  `DescendantsApi.test_case_sensitive_full_name_only` 与
  `DescendantsCli.test_case_mismatch_not_found`）。
- 作用域包以 `@scope/name` **整体作为一个字符串**匹配，不拆成作用域与包名两段。
- 字面值 `$root` 没有任何特殊含义：它只是名为 `$root` 的普通已安装包，命中后
  照常展开其下游（见 `DescendantsApi.test_literal_root_package_is_normal_package`
  与 `DescendantsCli.test_literal_root_target_is_normal_package`）。虚拟根标记
  `"$root"`（常量 `ROOT`）只在 why 查询的路径输出中出现，descendants 不使用它。
- **起点不要求根可达**：目标只要已安装即可，即使它不被根节点
  `dependencies`（传递）声明，也沿其自身 `dependencies` 展开。根 `dependencies`
  省略或为空不改变这一点（见
  `DescendantsApi.test_unreachable_start_still_expands_own_deps` 与
  `test_root_deps_absent_or_empty_does_not_change_result`）。

## 阶段三：依赖展开（_traverse_related）

`find_descendants()` 把正向邻接函数交给同文件的
`_traverse_related(target, neighbors_of)`，由它做广度优先展开：

1. `seen` 集合**预先放入 target 自身**，初始队列直接播种 target 的直接邻接
   （它的 `deps`）。这保证结果成员都至少经过一条边，且查询包自身永不在结果中。
2. 循环出队：节点已在 `seen` 中则跳过；否则记入 `seen`、追加到成员列表，并把
   它的邻接 extend 进队列。
3. 队列耗尽后返回 `sorted(members)`——按完整包名的 Unicode 码点升序排列，
   与邻接声明顺序、实际遍历顺序都无关。

由此得到的语义规则（`find_descendants()` 的 docstring 与
`tests/test_descendants.py`、`tests/test_descendants_combinatorial.py` 共同
固定）：

- **直接与传递依赖都保留**：从目标沿 `dependencies` 经过至少一条边可达的全部
  已安装包都进入结果（`DescendantsApi.test_transitive_descendants_sorted_by_unicode_code_point`
  中 alpha 直接声明 delta、charlie、leaf，又经它们传递到达更多下游）。
- **查询包自身始终排除**：即使存在自环（包声明依赖自身）或循环让遍历重新到达
  目标，目标也不列入结果——它一开始就在 `seen` 中。见
  `test_self_loop_does_not_add_target_itself`（仅有自环的 loopy 返回 `[]`）与
  `test_cycle_members_are_kept_target_excluded`（alpha↛charlie 循环中对方
  保留、自己排除）。
- **根项目不进入结果**：根节点不是 `packages_map` 的成员，遍历永远不会到达它；
  `root_deps` 也不参与播种。
- **孤立包返回空数组**：已安装但没有 `dependencies` 的目标（样例图中的 leaf、
  solo，以及 `sample-lock.json` 中没有任何依赖的 isolated）成功返回 `[]`，
  见 `DescendantsApi.test_no_dependencies_returns_empty`。
- **多条路径汇合不产生重复**：每个包名由 `seen` 保证只记录一次，菱形依赖、
  分支汇合都只出现一次。
- **按完整包名的 Unicode 码点升序，而非遍历顺序**：最终 `sorted(members)`
  重排；`graph_lock_data()` 刻意把依赖声明顺序写成与排序结果不同，
  `test_declaration_order_swap_keeps_result_identical` 再递归颠倒全部键序，
  结果不变。
- **自环与其他循环正常结束**：`seen` 使每个节点至多扩展一次，队列必然排空，
  不会死循环；循环中除目标外的其他可达包保留。
- **只读、不修改输入**：遍历不写入 `packages_map` 或 `deps` 列表。

`tests/test_descendants_combinatorial.py` 做了与产品实现互不相关的独立核对：
`test_all_edge_combinations` 在三个包之间枚举含自环的全部 9 条候选有向边的
2^9 = 512 种组合，用独立的可达集不动点 `reference_descendants()` 计算期望，
并分别以空根依赖、根只声明 alpha 两种根依赖以及颠倒条目/依赖顺序两种排列逐一
对照；`test_expected_shapes_on_key_graphs` 钉住无边、仅自环、循环带分支、
断开菱形等代表图的具体期望。

## 阶段四：JSON 包装与失败出口

`main()` 中 `descendants` 分支把公开接口返回的列表包装为对象：

```python
result = {
    "name": args.name,
    "descendants": find_descendants(root_deps, packages_map, args.name),
}
```

- `name` **原样保留命令行传入的查询名**（`args.name`），函数只校验它是否已
  安装，不规范化、不改写；输出对象仅含 `name` 与 `descendants` 两个键
  （`DescendantsCli.assertDescendantsObject` 逐字核对键集合；
  `test_name_echoed_verbatim` 覆盖普通名、`@scope/pkg`、`$root`）。
- 成功时 `sys.stdout.write(json.dumps(result, ensure_ascii=False) + "\n")`：
  退出码 0，标准错误为空，标准输出为完整 JSON 加恰好一个换行（中文等非 ASCII
  字符不转义；本项目样例均为 ASCII）。

失败出口（标准输出均为空，不输出部分结果或堆栈）：

| 情形 | 抛出位置 | 退出码 | 标准错误 |
| --- | --- | --- | --- |
| 合法输入中的目标包未安装（含大小写不符） | `find_descendants()` 抛 `NotFoundError` | 1 | `NOT_FOUND` 加换行 |
| 文件不可读、非法 UTF-8、JSON 损坏、悬空依赖、不支持的结构 | `load_lockfile()` 抛 `InputError` | 2 | `INPUT_ERROR` 加换行 |

对应测试：`DescendantsCli.test_missing_target_not_found`、
`test_case_mismatch_not_found` 断言退出码 1、stderr 恰为 `"NOT_FOUND\n"`、
stdout 为空；`DescendantsInputErrors` 的
`test_corrupt_json_is_input_error`、`test_unreadable_file_is_input_error`、
`test_bad_utf8_is_input_error_even_for_missing_target`、
`test_nested_path_and_link_still_rejected` 等断言退出码 2、stderr 恰为
`"INPUT_ERROR\n"`、stdout 为空。`main()` 中加载阶段的 `try/except` 先于查询
阶段，输入无效与目标不存在同时成立时以 `INPUT_ERROR` 为准。

## 示例：sample-lock.json 的两次查询

`sample-lock.json` 中：根节点仅声明 `alpha`；`alpha` 依赖 `beta`；`beta` 依赖
`leaf`，`leaf` 又依赖 `beta`（`beta` 与 `leaf` 构成循环）；`orphan` 依赖
`leaf` 但不被根节点引用（根不可达）；`isolated` 没有任何依赖。版本均为
`1.0.0`，依赖声明值均为 `*`。

在项目根目录执行：

```sh
$ python -m depinventory descendants sample-lock.json alpha
{"name": "alpha", "descendants": ["beta", "leaf"]}
$ python -m depinventory descendants sample-lock.json orphan
{"name": "orphan", "descendants": ["beta", "leaf"]}
```

两条命令都以退出码 0 结束，标准错误为空，标准输出末尾恰有一个换行。验收固定在
`tests/test_descendants.py` 的 `DescendantsAcceptance`
（`test_sample_alpha_descendants`、`test_sample_orphan_descendants`）。

**两次结果的 `descendants` 为何相同**：下游只取决于从查询包出发沿
`dependencies` 可达的子图，与该包是否被根声明无关。alpha 与 orphan 到达的都是
`beta`、`leaf` 这同一个循环的两个成员，且都无法沿有向边到达对方或根项目：

- alpha：`alpha → beta → leaf → beta …`。展开时 `seen` 预先含 `alpha`；
  `beta` 首次出队记录、把 `leaf` 入队；`leaf` 首次出队记录、又把 `beta`
  入队；`beta` 再次出队时已在 `seen` 中，直接跳过，队列排空。
- orphan：`orphan → leaf → beta → leaf …`。`leaf` 先被记录、`beta` 后被记录，
  随后 `leaf` 第二次到达被跳过；orphan 自身预置在 `seen` 中，自环式回流也不会
  把它计入。

两条路径只是**首次到达顺序相反**（alpha 先 beta 后 leaf；orphan 先 leaf 后
beta），而结果统一经 `sorted()` 按码点重排（`"beta" < "leaf"`），所以列表都是
`["beta", "leaf"]`。

**beta 与 leaf 的循环为何正常结束且各出现一次**：`_traverse_related()` 的
`seen` 集合保证每个节点至多被扩展一次——`leaf → beta` 这条回边到达的是已记录
的 `beta`，跳过即可，没有新节点入队，BFS 必然终止；成员列表先收集后排序，
`beta`、`leaf` 各只入列一次。目标自身（alpha 或 orphan）预置在 `seen` 中，
即使存在自环或经循环回流也永不列入。`name` 字段则与展开无关，分别原样回显
`alpha` 与 `orphan`。

## 不变量

- 查询只沿根节点与包条目的 `dependencies` 展开；不解析版本范围，不从其他字段
  补边。
- 起点不要求根可达；根 `dependencies` 省略或为空不改变其下游结果。
- 完整包名区分大小写；作用域名整体匹配；字面值 `$root` 表示普通已安装包。
- 整份校验先于目标检查，不可达条目的错误也不能跳过；任何失败时标准输出为空。
- 工具只读本地输入文件，不联网、不安装或执行锁文件中的依赖、不改写任何输入。
- 其余命令（list、why、parents、ancestors、diff、sbom）行为保持不变；
  `tests/test_descendants.py` 的 `ExistingCommandsUnchanged` 对 why 与
  ancestors 做了冒烟固定。
- 本文档不改动产品代码、公开接口（`depinventory/__init__.py` 的导出）、
  README、LOCKFILE.md、SBOM.md、DIFF.md、WHY.md 及任何样例锁文件。
